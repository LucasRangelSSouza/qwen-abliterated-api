"""Classe PEQUENA do worker (imagem, voz, dublagem, troca de rosto): perfis instalados sob demanda na mesma maquina, receitas de voz e rotas. Tudo com fakes."""
import asyncio
import base64
import hashlib
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import dubbing
from app import mediaparams as mp
from app.config import Settings
from app.gpu.worker import PERFIL_EXTRA_S, EphemeralWorker, WorkerSpec
from app.main import create_app
from app.media import RECIPES, Media, MediaError
from tests.fakes import FakeVast
from tests.test_worker import Clock, FakeComfyHeavy, FakeTunnel, offer, settings

WAV = b"RIFF" + b"\x00" * 60
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * 64
B64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


class VozTunnel(FakeTunnel):
    """SSH falso com um 'disco': guarda o que o servidor envia (cat > f) e devolve o que os scripts produziriam."""
    def __init__(self):
        super().__init__()
        self.files: dict[str, bytes] = {}
        self.exec: list[str] = []

    async def run_out(self, host, port, cmd, timeout=600.0, stdin=None):
        self.exec.append(cmd)
        m = re.match(r"cat > (\S+)$", cmd)
        if m:
            self.files[m.group(1).strip("'")] = stdin or b""
            return 0, b""
        m = re.match(r"cat (\S+)$", cmd)
        if m:
            f = m.group(1).strip("'")
            return (0, self.files[f]) if f in self.files else (1, b"")
        if cmd.startswith("rm -rf /workspace/voz/work/") and "&& mkdir" not in cmd:
            wd = cmd.split()[-1]
            self.files = {k: v for k, v in self.files.items() if not k.startswith(wd)}
            return 0, b""
        m = re.search(r"cd (\S+) && \((.*)\) > \S+\.log", cmd)
        if m:
            wd, c = m.group(1), m.group(2)
            if "clone.py" in c:
                self.files[f"{wd}/out.wav"], self.files[f"{wd}/m.json"] = WAV, b'{"rtf": 0.3, "semelhanca": 0.71}'
            elif "convert.py" in c:
                self.files[f"{wd}/out.mp4" if "out.mp4" in c else f"{wd}/out.wav"] = MP4 if "out.mp4" in c else WAV
                self.files[f"{wd}/m.json"] = b'{"semelhanca_com_alvo": 0.66}'
            elif "dub_a.py" in c:
                self.files[f"{wd}/segments.json"] = json.dumps({"language": "en", "duration": 6.0, "segments": [{"start": 0.2, "end": 2.0, "text": "Hello there."}, {"start": 2.5, "end": 5.5, "text": "This is a test."}]}).encode()
            elif "dub_b.py" in c:
                self.files[f"{wd}/out.mp4" if "out.mp4" in c else f"{wd}/out.wav"] = MP4 if "out.mp4" in c else WAV
                self.files[f"{wd}/m.json"] = b'{"aceleracao_max": 1.1}'
            return 0, b""
        return 0, b""


def small_media(redis, tun=None, llm=None, comfy=None):
    clock = Clock()
    s = settings(worker_small_enabled=True)
    vast = FakeVast(offers=[offer(1, 0.25, name="RTX 3090", sc=0.1)], boot_polls=1)
    tun = tun or VozTunnel()
    w = EphemeralWorker(s, vast, tun, redis, clock=clock, sleep=clock.sleep, script="#!/bin/bash\n", spec=WorkerSpec.small(s))
    comfy = comfy or FakeComfyHeavy()
    http = httpx.AsyncClient(transport=httpx.MockTransport(comfy.handler))
    return Media(s, vast, tun, redis, http, worker=w, llm=llm), w, tun, vast, comfy, http


# ---------------------------------------------------------------- perfis no mesmo worker

@pytest.mark.asyncio
async def test_perfis_sao_instalados_sob_demanda_na_mesma_maquina():
    s = settings(worker_small_enabled=True)
    clock = Clock()
    vast = FakeVast(offers=[offer(1, 0.25, name="RTX 3090", sc=0.1)], boot_polls=1)
    tun = VozTunnel()
    w = EphemeralWorker(s, vast, tun, None, clock=clock, sleep=clock.sleep, script="x", spec=WorkerSpec.small(s))
    assert w.startup_eta_s("imagem") == w.spec.cold_start_s
    await w.ensure("imagem")
    assert tun.instalacoes == ["imagem"] and w.profiles == {"imagem"} and vast.count("create") == 1
    assert w.startup_eta_s("voz") == PERFIL_EXTRA_S["voz"]  # maquina de pe, perfil novo: so o tempo de instalar o perfil
    await w.ensure("voz")
    assert tun.instalacoes == ["imagem", "voz"] and w.profiles == {"imagem", "voz"} and vast.count("create") == 1  # mesma maquina
    await w.ensure("imagem")
    assert tun.instalacoes == ["imagem", "voz"]  # ja instalado: nao reinstala
    assert w.startup_eta_s("voz") == 0


@pytest.mark.asyncio
async def test_a_classe_pequena_filtra_ram_e_preco(redis):
    s = settings(worker_small_enabled=True)
    clock = Clock()
    caro = offer(1, 0.9, name="RTX 3090", sc=0.1)
    pouca_ram = {**offer(2, 0.25, name="RTX 3090", sc=0.1), "cpu_ram": 16 * 1024}
    boa = {**offer(3, 0.35, name="RTX 4090", sc=0.1), "cpu_ram": 128 * 1024}
    vast = FakeVast(offers=[caro, pouca_ram, boa], boot_polls=1)
    w = EphemeralWorker(s, vast, VozTunnel(), None, clock=clock, sleep=clock.sleep, script="x", spec=WorkerSpec.small(s))
    await w.ensure("imagem")
    assert ("create", 3, "ok") in vast.calls and vast.count("create") == 1
    assert w.dph < 0.65


@pytest.mark.asyncio
async def test_perfil_que_falha_destroi_a_maquina_e_tenta_outra_vez():
    s = settings(worker_small_enabled=True)
    clock = Clock()
    vast = FakeVast(offers=[offer(1, 0.25, name="RTX 3090", sc=0.1)], boot_polls=1)
    tun = VozTunnel()
    n = {"v": 0}
    orig = tun.run

    async def run(host, port, cmd, timeout=90.0):
        if "base64 -d" in cmd:
            n["v"] += 1
            tun.fail_provision = n["v"] == 1
        return await orig(host, port, cmd, timeout)
    tun.run = run
    w = EphemeralWorker(s, vast, tun, None, clock=clock, sleep=clock.sleep, script="x", spec=WorkerSpec.small(s))
    await w.ensure("voz")
    assert w.profiles == {"voz"} and vast.count("create") == 2 and vast.count("destroy") == 1


# ---------------------------------------------------------------- imagem pela classe pequena

@pytest.mark.asyncio
async def test_imagem_roda_na_classe_pequena_com_o_perfil_imagem(redis):
    m, w, tun, vast, comfy, http = small_media(redis)
    try:
        job = await m.submit("p", "Lucas", {"prompt": "um gato", "size": "512x512"}, "interactive", "image")
        assert job["recipe"] == "flux-schnell"
        assert (await m.wait(job["id"], 5))["state"] == "done"
        assert tun.instalacoes == ["imagem"] and w.state == "ready"
        with pytest.raises(MediaError):
            await m.submit("p", "", {"prompt": "x", "recipe": "wan22-a14b"}, "batch", "video")  # o Wan 14B e da classe grande
    finally:
        await http.aclose()


# ---------------------------------------------------------------- voz

@pytest.mark.asyncio
async def test_clonar_voz_envia_arquivos_roda_o_script_devolve_o_wav_e_apaga_tudo(redis):
    m, w, tun, vast, comfy, http = small_media(redis)
    try:
        job = await m.submit("p", "", {"recipe": "voz-clonar", "text": "Olá, mundo. Isto é um teste.", "language": "pt", "reference_audio": B64(WAV)}, "batch", "audio")
        assert job["recipe"] == "voz-clonar"
        final = await m.wait(job["id"], 5)
        assert final["state"] == "done", final
        assert tun.instalacoes == ["voz"] and final["mime"] == "audio/wav" and await m.image(job["id"]) == WAV
        assert final["metrics"]["semelhanca"] == 0.71
        cmds = " ".join(tun.exec)
        assert "clone.py" in cmds and "--lang pt" in cmds
        assert not [k for k in tun.files if "/work/" in k]  # nada do cliente fica na maquina
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_clonar_voz_via_recipe_explicita_e_sem_travas(redis):
    m, w, tun, vast, comfy, http = small_media(redis)
    try:
        job = await m.submit("p", "", {"recipe": "voz-clonar", "text": "Oi", "reference_audio": B64(WAV)}, "batch", "audio")  # sem consent: o servidor nao trava
        assert (await m.wait(job["id"], 5))["state"] == "done"
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_trocar_voz_de_video_devolve_mp4_e_de_audio_devolve_wav(redis):
    m, w, tun, vast, comfy, http = small_media(redis)
    try:
        v = await m.submit("p", "", {"recipe": "voz-trocar", "media": B64(MP4), "target_voice": B64(WAV)}, "batch", "audio")
        assert v["kind"] == "video"  # entrada com video -> saida com video
        fv = await m.wait(v["id"], 5)
        assert fv["state"] == "done" and fv["mime"] == "video/mp4"
        a = await m.submit("p", "", {"recipe": "voz-trocar", "media": B64(WAV), "target_voice": B64(WAV), "keep_background": False}, "batch", "audio")
        fa = await m.wait(a["id"], 5)
        assert a["kind"] == "audio" and fa["mime"] == "audio/wav"
        assert "--keep-bg 0" in " ".join(tun.exec)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_dublar_separa_transcreve_traduz_pelo_llm_e_remonta(redis):
    chamadas = []

    async def llm(sistema, usuario):
        chamadas.append((sistema, usuario))
        n = len(json.loads(usuario))
        return json.dumps([f"Olá {i}" for i in range(n)])
    m, w, tun, vast, comfy, http = small_media(redis, llm=llm)
    try:
        job = await m.submit("p", "", {"recipe": "dublar", "video": B64(MP4), "target_language": "pt"}, "batch", "video")
        final = await m.wait(job["id"], 5)
        assert final["state"] == "done", final
        assert final["mime"] == "video/mp4" and await m.image(job["id"]) == MP4
        assert len(chamadas) == 1 and "Brazilian Portuguese" in chamadas[0][0] and "English" in chamadas[0][0]  # idioma de origem veio da transcricao
        trad = [json.loads(v) for k, v in tun.files.items() if k.endswith("translated.json")]
        assert trad == []  # apagado ao final
        ordem = [c for c in tun.exec if "dub_a.py" in c or "dub_b.py" in c]
        assert "dub_a.py" in ordem[0] and "dub_b.py" in ordem[1]
        assert final["metrics"]["segmentos_traduzidos"] == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_dublar_sem_llm_falha_como_indisponivel_e_ainda_apaga_os_arquivos(redis):
    m, w, tun, vast, comfy, http = small_media(redis, llm=None)
    try:
        job = await m.submit("p", "", {"recipe": "dublar", "video": B64(MP4), "target_language": "es"}, "batch", "video")
        final = await m.wait(job["id"], 5)
        assert final["state"] == "failed"
        assert not [k for k in tun.files if "/work/" in k]
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_falha_de_uma_etapa_vira_erro_curto_sem_vazar_detalhe(redis):
    tun = VozTunnel()
    orig = tun.run_out

    async def run_out(host, port, cmd, timeout=600.0, stdin=None):
        if "clone.py" in cmd:
            return 1, b""
        if cmd.startswith("tail -c"):
            return 0, b"Traceback: CUDA out of memory"
        return await orig(host, port, cmd, timeout, stdin)
    tun.run_out = run_out
    m, w, tun, vast, comfy, http = small_media(redis, tun=tun)
    try:
        job = await m.submit("p", "", {"recipe": "voz-clonar", "text": "Oi", "reference_audio": B64(WAV)}, "batch", "audio")
        final = await m.wait(job["id"], 5)
        assert final["state"] == "failed" and "falha na etapa clone" in final["error"]
        prog = await m.progress(job["id"])
        assert "CUDA" not in json.dumps(prog)  # o cliente so ve a mensagem padrao
    finally:
        await http.aclose()


# ---------------------------------------------------------------- parametros e traducao

def test_parametros_de_voz_sem_consentimento_e_com_validacao():
    e, _ = mp.normalize_voice_clone({"text": "Olá", "reference_audio": B64(WAV)})
    assert e["language"] == "pt" and "consent" not in e and mp.public(e).get("text") is None  # o texto e o audio nao aparecem no status
    with pytest.raises(mp.ParamError):
        mp.normalize_voice_clone({"text": "", "reference_audio": B64(WAV)})
    with pytest.raises(mp.ParamError):
        mp.normalize_voice_clone({"text": "x", "reference_audio": B64(WAV), "language": "klingon"})
    with pytest.raises(mp.ParamError):
        mp.normalize_voice_clone({"text": "x" * 3001, "reference_audio": B64(WAV)})
    with pytest.raises(mp.ParamError):
        mp.normalize_dub({"video": B64(MP4)})  # sem idioma
    with pytest.raises(mp.ParamError):
        mp.normalize_dub({"video": B64(MP4), "target_language": "pt"}, duration_s=500)
    d, _ = mp.normalize_dub({"video": B64(MP4), "target_language": "PT", "voice": B64(WAV)}, duration_s=30)
    assert d["target_language"] == "pt" and d["has_video"] is True
    c, _ = mp.normalize_voice_convert({"media": B64(WAV), "target_voice": B64(WAV)}, duration_s=10)
    assert c["has_video"] is False and c["keep_background"] is True


def test_troca_de_rosto_nao_exige_nenhum_consentimento():
    e, _ = mp.normalize_swap({"video": B64(MP4), "image": B64(b"\x89PNG\r\n\x1a\n" + b"x" * 64)})
    assert e["resolution"] == "480p"


@pytest.mark.asyncio
async def test_traducao_em_lotes_com_nova_tentativa_se_a_contagem_vier_errada():
    segs = [{"start": i, "end": i + 1, "text": f"line {i}"} for i in range(3)]
    n = {"c": 0}

    async def llm(s, u):
        n["c"] += 1
        return '["so uma"]' if n["c"] == 1 else '```json\n["a", "b", "c"]\n```'
    out = await dubbing.traduzir(llm, segs, "en", "pt")
    assert n["c"] == 2 and [x["text"] for x in out] == ["a", "b", "c"] and out[0]["text_src"] == "line 0"

    async def ruim(s, u):
        return "nao sei"
    with pytest.raises(ValueError):
        await dubbing.traduzir(ruim, segs, "en", "pt")
    assert dubbing.ler('{"traducoes": ["x", "y"]}', 2) == ["x", "y"]


# ---------------------------------------------------------------- rotas

def test_rotas_de_voz_e_dublagem_e_roteamento_por_receita(redis):
    key, adm = "k-teste", "adm"
    s = settings(admin_key_sha256=hashlib.sha256(adm.encode()).hexdigest(), worker_small_enabled=True)
    pequena, w, tun, vast, comfy, http = small_media(redis, llm=None)
    heavy, wh, comfyh, vasth, httph = None, None, None, None, None
    from tests.test_api import StubCtl
    from tests.test_worker import heavy_media
    heavy, wh, comfyh, vasth, httph = heavy_media(redis)
    consumers = {hashlib.sha256(key.encode()).hexdigest(): {"name": "portfolio", "key_sha256": hashlib.sha256(key.encode()).hexdigest(), "priority": 0}}
    app = create_app(s, controller=StubCtl(), redis=redis, http=http, consumers=consumers, heavy=heavy, small=pequena)
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {key}"}
        r = c.post("/v1/audio/voice-clone", json={"text": "Olá", "reference_audio": B64(WAV)}, headers=h)
        assert r.status_code == 202, r.text
        jid = r.json()["id"]
        import time as _t
        for _ in range(100):
            j = c.get(f"/v1/media/jobs/{jid}", headers=h).json()
            if j["status"] in ("done", "failed"):
                break
            _t.sleep(0.05)
        assert j["status"] == "done", j
        assert c.get(f"/v1/jobs/{jid}/file", headers=h).content == WAV
        # video sem receita -> classe grande; imagem sem receita -> classe pequena
        assert c.post("/v1/videos/generations", json={"prompt": "um carro", "seconds": 5}, headers=h).status_code == 202
        r2 = c.post("/v1/images/generations", json={"prompt": "um gato", "size": "512x512"}, headers=h)
        assert r2.status_code == 200, r2.text
        st = c.get("/admin/media/status", headers={"X-Admin-Key": adm}).json()
        assert st["worker_small"]["pool"] == "pequena" and "voz" in st["worker_small"]["profiles"] and st["worker"]["pool"] == "grande"
        # erro de parametro vira 422 com mensagem clara
        assert c.post("/v1/audio/voice-clone", json={"text": ""}, headers=h).status_code == 422


@pytest.mark.asyncio
async def test_traducao_aceita_objetos_com_outras_chaves_e_rejeita_vazios():
    segs = [{"start": 0, "end": 1, "text": "hello"}, {"start": 1, "end": 2, "text": "world"}]
    assert dubbing.ler('[{"i":0,"translation":"ola"},{"i":1,"translated":"mundo"}]', 2) == ["ola", "mundo"]
    assert dubbing.ler('[{"i":0,"foo":"ola"},{"i":1,"foo":"mundo"}]', 2) == ["ola", "mundo"]  # qualquer chave de texto
    n = {"c": 0}

    async def llm(s, u):
        n["c"] += 1
        return '["", ""]' if n["c"] == 1 else '["ola", "mundo"]'  # 1a resposta vazia: tenta de novo
    out = await dubbing.traduzir(llm, segs, "en", "pt")
    assert n["c"] == 2 and [x["text"] for x in out] == ["ola", "mundo"]


@pytest.mark.asyncio
async def test_vigia_destroi_sobra_mesmo_com_a_maquina_atual_de_pe():
    s = settings(worker_small_enabled=True)
    clock = Clock()
    vast = FakeVast(offers=[offer(1, 0.25, name="RTX 3090", sc=0.1)], boot_polls=1)
    w = EphemeralWorker(s, vast, VozTunnel(), None, clock=clock, sleep=clock.sleep, script="x", spec=WorkerSpec.small(s))
    await w.ensure("imagem")
    atual = w.inst_id
    r = await vast.create_instance(1, "img", 170, w.spec.label, "", {})  # sobra do mesmo rotulo (ex.: restante de um reinicio)
    vast.instances[r.instance_id]["start_date"] = clock.t - 3600
    clock.t += 400
    await w.tick()
    assert r.instance_id not in vast.instances and atual in vast.instances and w.state == "ready"
