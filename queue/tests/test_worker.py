"""Worker de video sob demanda: Vast, tunel e ComfyUI falsos e relogio simulado. Nada gasta GPU nem dinheiro."""
import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mediaparams as mp
from app.config import Settings
from app.gpu.tunnel import NoTunnel
from app.gpu.vast import CreateResult
from app.gpu.worker import COLD_START_S, LABEL, EphemeralWorker, WorkerUnavailable
from app.main import create_app
from app.media import HEAVY_RECIPE, Media, MediaError, MediaUnavailable
from tests.fakes import FakeVast

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"v" * 64


def offer(oid, dph=1.2, name="RTX PRO 6000 S", down=5000, rel=0.99, disk=500, sc=0.133, machine=None):
    return {"id": oid, "machine_id": machine or oid, "gpu_name": name, "num_gpus": 1, "dph_total": dph, "storage_cost": sc, "disk_space": disk,
            "inet_down": down, "reliability2": rel, "geolocation": "Nevada, US"}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s
        await asyncio.sleep(0)


class FakeTunnel(NoTunnel):
    """SSH falso: a instalacao 'termina' na hora; `fail_provision` simula arquivo incompleto; `ssh_down` simula SSH que nao responde."""
    def __init__(self):
        super().__init__()
        self.provisioned = False
        self.fail_provision = False
        self.ssh_down = False
        self.cmds: list[str] = []
        self.up = False

    async def run(self, host, port, cmd, timeout=90.0):
        self.cmds.append(cmd)
        if self.ssh_down:
            return False
        if "base64 -d" in cmd:
            self.provisioned = not self.fail_provision
            return True
        if cmd.startswith("test -f /workspace/provision.done &&"):
            return False  # maquina nova: ainda nao instalada
        if cmd == "test -f /workspace/provision.done":
            return self.provisioned
        if cmd.startswith("grep -q"):
            return self.fail_provision
        return True

    async def open(self, host, port):
        self.opened.append((host, port))
        self.up = True
        return True

    async def healthy(self):
        return self.up

    async def close(self):
        self.up = False


def settings(**kw) -> Settings:
    base = dict(tunnel_mode="none", worker_enabled=True, worker_idle_s=600, worker_wait_s=600, worker_start_timeout_s=600, media_poll_s=0.01,
                media_job_timeout_s=2.0, media_sync_wait_s=5.0, media_queue_max=5, media_per_consumer=3, worker_max_dph=1.8, worker_min_credit_usd=3.0)
    base.update(kw)
    return Settings(**base)


def make(offers=None, credit=50.0, **kw):
    clock = Clock()
    vast = FakeVast(offers=offers if offers is not None else [offer(1, 1.2), offer(2, 1.5), offer(3, 1.3)], credit=credit, boot_polls=1)
    tun = FakeTunnel()
    w = EphemeralWorker(settings(**kw), vast, tun, None, clock=clock, sleep=clock.sleep, script="#!/bin/bash\necho ok\n")
    return w, vast, tun, clock


@pytest.mark.asyncio
async def test_aluga_a_oferta_mais_barata_instala_e_fica_pronto():
    w, vast, tun, _ = make()
    await w.ensure()
    assert w.state == "ready" and w.inst_id is not None
    assert vast.count("create") == 1 and vast.calls[1] == ("create", 1, "ok")  # a de US$ 1,20, nao a de 1,50 nem a de 1,30
    assert vast.instances[w.inst_id]["label"] == LABEL
    assert tun.provisioned and tun.up and tun.opened
    assert round(w.dph, 3) == round(1.2 + 0.133 * 120 / 730, 3)  # preco TOTAL: GPU + disco
    await w.ensure()  # ja pronto: nao aluga outra
    assert vast.count("create") == 1


@pytest.mark.asyncio
async def test_filtra_preco_rede_confiabilidade_e_gpu():
    ofertas = [offer(1, 2.5), offer(2, 1.2, down=200), offer(3, 1.2, rel=0.80), offer(4, 1.1, name="RTX PRO 6000 Max-Q"), offer(5, 1.3, disk=50), offer(6, 1.6)]
    w, vast, _, _ = make(ofertas)
    await w.ensure()
    assert ("create", 6, "ok") in vast.calls and vast.count("create") == 1  # so a 6 passa em preco, rede, confiabilidade, disco e modelo de GPU


@pytest.mark.asyncio
async def test_destroi_quando_ocioso_e_registra_o_custo_depois_aluga_de_novo(redis):
    w, vast, tun, clock = make()
    w.r = redis
    await w.ensure()
    first = w.inst_id
    w.enter(); w.leave()
    clock.t += 300
    await w.tick()
    assert w.state == "ready"  # 5 min ocioso ainda nao destroi (limite 10)
    clock.t += 400
    await w.tick()
    assert w.state == "off" and first not in vast.instances and vast.running_ids() == []
    rec = json.loads((await redis.lrange("llmq:worker:runs", 0, 0))[0])
    assert rec["instance"] == first and rec["destroyed"] and rec["cost_usd"] > 0
    await w.ensure()
    assert w.state == "ready" and w.inst_id != first and vast.count("create") == 2


@pytest.mark.asyncio
async def test_nao_destroi_com_pedido_em_andamento():
    w, vast, _, clock = make()
    await w.ensure()
    w.enter()
    clock.t += 5000
    await w.tick()
    assert w.state == "ready" and len(vast.running_ids()) == 1
    w.leave()
    await w.tick()  # acabou de sair: a ociosidade comeca agora
    assert w.state == "ready"


@pytest.mark.asyncio
async def test_instalacao_falha_destroi_a_maquina_e_tenta_outra():
    w, vast, tun, _ = make()
    tun.fail_provision = True
    calls = {"n": 0}
    orig = tun.run

    async def run(host, port, cmd, timeout=90.0):
        if "base64 -d" in cmd:
            calls["n"] += 1
            tun.fail_provision = calls["n"] == 1  # so a primeira maquina falha
        return await orig(host, port, cmd, timeout)
    tun.run = run
    await w.ensure()
    assert w.state == "ready"
    assert vast.count("create") == 2 and vast.count("destroy") == 1  # a que falhou foi destruida: nada fica cobrando a toa
    assert len(vast.running_ids()) == 1


@pytest.mark.asyncio
async def test_sem_saldo_nao_aluga():
    w, vast, _, _ = make(credit=1.0)
    with pytest.raises(WorkerUnavailable, match="saldo"):
        await w.ensure()
    assert vast.count("create") == 0 and w.state == "off"


@pytest.mark.asyncio
async def test_sem_oferta_dentro_do_teto_falha_com_mensagem_clara_e_sem_gastar():
    w, vast, _, _ = make([offer(1, 5.0)], worker_wait_s=60)
    with pytest.raises(WorkerUnavailable, match="sem maquina"):
        await w.ensure()
    assert vast.count("create") == 0 and w.state == "off"


@pytest.mark.asyncio
async def test_vigia_destroi_sobra_com_o_nosso_rotulo_so_depois_da_carencia():
    w, vast, _, clock = make()
    r = await vast.create_instance(1, "img", 120, LABEL, "", {})
    outra = await vast.create_instance(2, "img", 120, "hold-quebec", "", {})  # de outro dono: nunca mexe
    clock.t += 100
    await w.tick()
    assert r.instance_id in vast.instances  # dentro da carencia: um pedido novo ainda pode adotar
    clock.t += 400
    await w.tick()
    assert r.instance_id not in vast.instances and outra.instance_id in vast.instances


@pytest.mark.asyncio
async def test_adota_maquina_existente_em_vez_de_alugar_outra():
    w, vast, tun, _ = make()
    r = await vast.create_instance(1, "img", 120, LABEL, "", {})
    await w.ensure()
    assert w.inst_id == r.instance_id and vast.count("create") == 1


@pytest.mark.asyncio
async def test_maquina_que_cai_e_substituida():
    w, vast, _, _ = make()
    await w.ensure()
    first = w.inst_id
    vast.instances.pop(first)  # a Vast perdeu a maquina
    await w.ensure()
    assert w.state == "ready" and w.inst_id != first


# ---------------------------------------------------------------- parametros e estimativa

def test_parametros_wan14_qualidade_automatica():
    e, _ = mp.normalize_video_wan14({"prompt": "x", "seconds": 5})
    assert (e["width"], e["height"], e["quality"], e["segments"], e["frames"], e["fps"]) == (1280, 720, "alta", 1, 81, 16)
    e, _ = mp.normalize_video_wan14({"prompt": "x", "seconds": 12})
    assert (e["width"], e["height"], e["quality"], e["segments"]) == (832, 480, "padrao", 3)  # video longo: padrao, para a producao caber em ~20 min
    e, _ = mp.normalize_video_wan14({"prompt": "x", "seconds": 3, "format": "retrato", "quality": "padrao"})
    assert (e["width"], e["height"], e["frames"]) == (480, 832, 49)
    e, _ = mp.normalize_video_wan14({"prompt": "x", "negative_prompt": "extra wheels"})
    assert e["negative_prompt"].startswith("extra wheels, ") and mp.NEG_WAN14 in e["negative_prompt"]  # soma ao negativo oficial, nao substitui
    assert mp.normalize_video_wan14({"prompt": "x"})[0]["negative_prompt"] == mp.NEG_WAN14
    with pytest.raises(mp.ParamError):
        mp.normalize_video_wan14({"prompt": "x", "format": "cubo"})
    with pytest.raises(mp.ParamError):
        mp.normalize_video_wan14({"prompt": ""})


def test_estimativa_do_wan14_segue_o_tempo_medido():
    assert mp.wan14_eta_s(mp.normalize_video_wan14({"prompt": "x", "seconds": 5})[0]) == 1100         # 720p, 1 clipe: ~18 min
    assert mp.wan14_eta_s(mp.normalize_video_wan14({"prompt": "x", "seconds": 5, "quality": "padrao"})[0]) == 260  # 480p: ~4 min
    assert mp.wan14_eta_s(mp.normalize_video_wan14({"prompt": "x", "seconds": 15})[0]) == 3 * 260


# ---------------------------------------------------------------- fluxo do pedido (Media + worker)

class FakeComfyHeavy:
    """Flux (SaveImage) devolve PNG; Wan (SaveVideo) devolve MP4. Guarda os fluxos recebidos."""
    def __init__(self):
        self.prompts: list[dict] = []
        self.uploads = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/prompt":
            body = json.loads(req.content)
            self.prompts.append(body["prompt"])
            return httpx.Response(200, json={"prompt_id": f"p{len(self.prompts)}"})
        if p == "/upload/image":
            self.uploads += 1
            return httpx.Response(200, json={"name": "inicio.png"})
        if p.startswith("/history/"):
            wf = self.prompts[int(p.rsplit("/", 1)[1][1:]) - 1]
            if any(n["class_type"] == "SaveVideo" for n in wf.values()):
                out = {"15": {"images": [{"filename": "v.mp4", "subfolder": "video", "type": "output"}]}}
            else:
                out = {"7": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}
            return httpx.Response(200, json={p.rsplit("/", 1)[1]: {"status": {"status_str": "success", "completed": True}, "outputs": out}})
        if p == "/view":
            return httpx.Response(200, content=MP4 if req.url.params.get("filename", "").endswith(".mp4") else PNG)
        return httpx.Response(404)


def heavy_media(redis, **kw):
    clock = Clock()
    s = settings(**kw)
    vast = FakeVast(offers=[offer(1)], boot_polls=1)
    tun = FakeTunnel()
    w = EphemeralWorker(s, vast, tun, redis, clock=clock, sleep=clock.sleep, script="#!/bin/bash\n")
    comfy = FakeComfyHeavy()
    http = httpx.AsyncClient(transport=httpx.MockTransport(comfy.handler))
    return Media(s, vast, tun, redis, http, worker=w), w, comfy, vast, http


@pytest.mark.asyncio
async def test_video_pesado_cria_a_imagem_inicial_e_gera_o_wan(redis):
    m, w, comfy, vast, http = heavy_media(redis)
    try:
        job = await m.submit("portfolio", "Lucas", {"prompt": "um carro prata numa estrada", "seconds": 5, "seed": 7}, "batch", "video")
        assert job["recipe"] == HEAVY_RECIPE and job["params"]["quality"] == "alta"
        final = await m.wait(job["id"], 5)
        assert final["state"] == "done", final
        assert [any(n["class_type"] == "SaveVideo" for n in wf.values()) for wf in comfy.prompts] == [False, True]  # 1o Flux, 2o Wan
        wan = comfy.prompts[1]
        assert wan["1"]["inputs"]["unet_name"].startswith("wan2.2_i2v_high_noise_14B")
        assert wan["2"]["inputs"]["unet_name"].startswith("wan2.2_i2v_low_noise_14B")
        assert (wan["8"]["inputs"]["width"], wan["8"]["inputs"]["height"], wan["8"]["inputs"]["length"]) == (1280, 720, 81)
        assert wan["11"]["inputs"]["end_at_step"] == 10 and wan["12"]["inputs"]["start_at_step"] == 10
        assert comfy.uploads == 1 and await m.image(job["id"]) == MP4
        assert w.busy == 0 and w.state == "ready"  # liberou a maquina; ela so some por ociosidade
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_video_pesado_com_imagem_do_cliente_nao_gasta_o_flux(redis):
    import base64
    m, w, comfy, vast, http = heavy_media(redis)
    try:
        job = await m.submit("p", "", {"prompt": "x", "image": base64.b64encode(PNG).decode(), "seconds": 3}, "batch", "video")
        assert (await m.wait(job["id"], 5))["state"] == "done"
        assert len(comfy.prompts) == 1 and any(n["class_type"] == "SaveVideo" for n in comfy.prompts[0].values())
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_video_longo_encadeia_clipes_no_worker(redis, monkeypatch):
    from app import longvideo

    async def concat(clips):
        return MP4 + bytes(len(clips))

    async def last_frame(blob):
        return __import__("base64").b64encode(PNG).decode()
    monkeypatch.setattr(longvideo, "concat", concat)
    monkeypatch.setattr(longvideo, "last_frame_b64", last_frame)
    m, w, comfy, vast, http = heavy_media(redis)
    try:
        job = await m.submit("p", "", {"prompt": "x", "seconds": 11}, "batch", "video")
        assert job["params"]["segments"] == 3 and job["params"]["quality"] == "padrao"
        final = await m.wait(job["id"], 5)
        assert final["state"] == "done", final
        assert sum(any(n["class_type"] == "SaveVideo" for n in wf.values()) for wf in comfy.prompts) == 3
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_sem_saldo_o_pedido_falha_com_erro_de_indisponivel(redis):
    m, w, comfy, vast, http = heavy_media(redis)
    vast.credit_v = 0.5
    try:
        job = await m.submit("p", "", {"prompt": "x"}, "batch", "video")
        final = await m.wait(job["id"], 5)
        assert final["state"] == "failed" and final["error"].startswith("maquina")  # o main.py devolve 503 + mensagem padrao, sem texto de infraestrutura
        assert vast.count("create") == 0 and w.busy == 0
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_receita_pesada_so_roda_no_worker_e_o_worker_so_roda_ela(redis):
    m, w, comfy, vast, http = heavy_media(redis)
    fixa = Media(settings(media_video_enabled=True), vast, NoTunnel(), redis, http)
    try:
        with pytest.raises(MediaError):
            await fixa.submit("p", "", {"prompt": "x", "recipe": HEAVY_RECIPE}, "batch", "video")
        with pytest.raises(MediaError):
            await m.submit("p", "", {"prompt": "x", "recipe": "ltx-video"}, "batch", "video")
        with pytest.raises(MediaError):
            await m.submit("p", "", {"prompt": "x"}, "interactive", "image")
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_estimativa_inclui_a_partida_da_maquina_e_a_fila(redis):
    m, w, comfy, vast, http = heavy_media(redis)
    comfy_slow = {"n": 0}
    try:
        # trava o worker na partida para observar o pedido "preparando" e o seguinte na fila
        gate = asyncio.Event()
        orig = w.ensure

        async def slow_ensure():
            await gate.wait()
            await orig()
        w.ensure = slow_ensure
        a = await m.submit("p", "", {"prompt": "a", "seconds": 5}, "batch", "video")
        b = await m.submit("p", "", {"prompt": "b", "seconds": 5, "quality": "padrao"}, "batch", "video")
        await asyncio.sleep(0.05)
        pa, pb = await m.progress(a["id"]), await m.progress(b["id"])
        assert pa["status"] == "running" and pa["message"] == "Preparando o seu vídeo..." and pa["progress"] == 0
        assert pa["eta_s"] == COLD_START_S + 1100  # partida da maquina + o proprio video em 720p
        assert pb["status"] == "queued" and pb["position"] == 1
        assert pb["eta_s"] >= 1100 + 260  # o que esta na frente + o proprio (+ a partida, ainda nao pronta)
        for word in ("GPU", "fila de espera", "Vast", "worker"):
            assert word not in pa["message"] and word not in pb["message"]
        gate.set()
        assert (await m.wait(a["id"], 5))["state"] == "done"
        assert (await m.wait(b["id"], 5))["state"] == "done"
    finally:
        await http.aclose()


# ---------------------------------------------------------------- rotas

def test_rota_de_video_vai_para_o_worker_e_imagem_para_a_gpu_fixa(redis):
    key, adm = "k-teste", "adm"
    s = settings(admin_key_sha256=hashlib.sha256(adm.encode()).hexdigest())
    from tests.test_media import FakeComfy, FakeVastMedia
    http = httpx.AsyncClient(transport=httpx.MockTransport(FakeComfy().handler))
    fixa = Media(s, FakeVastMedia(), NoTunnel(), redis, http)
    hm, w, comfy, vast, hhttp = heavy_media(redis)
    consumers = {hashlib.sha256(key.encode()).hexdigest(): {"name": "portfolio", "key_sha256": hashlib.sha256(key.encode()).hexdigest(), "priority": 0}}
    from tests.test_api import StubCtl
    app = create_app(s, controller=StubCtl(), redis=redis, http=http, consumers=consumers, media=fixa, heavy=hm)
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {key}"}
        r = c.post("/v1/videos/generations", json={"prompt": "um carro", "seconds": 5}, headers=h)
        assert r.status_code == 202, r.text
        jid = r.json()["id"]
        for _ in range(100):
            j = c.get(f"/v1/media/jobs/{jid}", headers=h).json()
            if j["status"] in ("done", "failed"):
                break
            import time as _t; _t.sleep(0.05)
        assert j["status"] == "done", j
        assert c.get(f"/v1/jobs/{jid}", headers=h).json()["recipe"] == HEAVY_RECIPE
        assert c.get(f"/v1/jobs/{jid}/file", headers=h).content == MP4
        # o video rapido continua na GPU fixa
        r2 = c.post("/v1/videos/generations", json={"prompt": "x", "recipe": "ltx-video"}, headers=h)
        assert r2.status_code in (202, 422, 503)
        # imagem: GPU fixa
        r3 = c.post("/v1/images/generations", json={"prompt": "hamburguer", "size": "512x512"}, headers=h)
        assert r3.status_code == 200
        st = c.get("/admin/media/status", headers={"X-Admin-Key": adm}).json()
        assert st["worker"]["state"] == "ready" and st["worker"]["sessions"] == 1
