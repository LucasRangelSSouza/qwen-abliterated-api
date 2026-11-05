"""Modo de instancia fixa e interruptor de desligamento automatico (Spec 15 §4): a GPU fixa nunca para nem e destruida."""
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from app.alerts import Alerts
from app.config import Settings
from app.gpu.controller import K_BAD, K_CONFIG, K_INST, LABEL, LOADING, OFF, READY, GPUController, GPUUnavailable, load_onstart
from app.gpu.ledger import Ledger
from app.gpu.tunnel import NoTunnel
from app.main import create_app
from tests.conftest import Probe, fast_settings
from tests.fakes import FakeVast
from tests.test_api import CONSUMERS, H, StubCtl, make_upstream

SID = 777
ADMIN = "admin-secreto"
AH = {"X-Admin-Key": ADMIN}


def static_settings(**kw) -> Settings:
    base = dict(static_instance_id=SID, static_retry_s=0.05, static_retry_max_s=0.2, reconcile_s=0.05,
                admin_key_sha256=hashlib.sha256(ADMIN.encode()).hexdigest())
    base.update(kw)
    return fast_settings(**base)


def add_static(vast: FakeVast, status: str = "running", label: str = "hold-arg-54987") -> None:
    up = status == "running"
    vast.instances[SID] = {"id": SID, "mode": "ok", "polls": 0, "actual_status": status, "intended_status": "running" if up else "stopped",
                           "status_msg": "", "ssh_host": "ssh.fake" if up else None, "ssh_port": 22222 if up else None, "machine_id": 54987,
                           "dph_total": 0.284, "storage_total_cost": 0.01, "label": label, "env": {}, "onstart": ""}


def forbidden(vast: FakeVast) -> list:
    """O que a instancia fixa nunca pode receber: criar, alugar, destruir ou parar."""
    return [c for c in vast.calls if c[0] in ("create", "search", "destroy") or (c[0] == "power" and c[2] == "stopped")]


async def until(cond, t: float = 3.0) -> bool:
    end = asyncio.get_running_loop().time() + t
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


async def static_ctl(make_ctl, status: str = "running", probe_ok: bool = True, **kw):
    vast = FakeVast()
    add_static(vast, status)
    ctl, vast, probe = await make_ctl(static_settings(**kw), vast)
    probe.ok = probe_ok
    return ctl, vast, probe


# ---------------------------------------------------------------- adocao e estados

async def test_modo_fixo_adota_pelo_id_ignorando_rotulo_e_spec(make_ctl, redis):
    ctl, vast, _ = await static_ctl(make_ctl)
    await redis.set(K_INST, json.dumps({"id": 1, "spec": "script-antigo"}))  # estado velho do modo sob demanda: ignorado
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY and ctl.inst["id"] == SID and ctl.status()["static"] is True
    assert forbidden(vast) == [] and vast.count("search") == 0
    assert (await redis.get(K_INST)) == json.dumps({"id": 1, "spec": "script-antigo"}).encode()  # a fixa nao e gravada como instancia do controlador
    assert ctl.status()["config"]["static_instance_id"] == SID


# ---------------------------------------------------------------- tabela da §4.1: cada caminho que parava ou destruia

@pytest.mark.parametrize("caminho", ["ociosidade", "sessao_maxima", "teto_diario"])
async def test_modo_fixo_guards_nunca_param(make_ctl, caminho):
    kw = {"auto_shutdown": True}  # mesmo com o interruptor ligado, a fixa nao para
    if caminho == "sessao_maxima":
        kw["max_session_h"] = 0.000001
    if caminho == "teto_diario":
        kw["max_daily_usd"] = 0.01
    ctl, vast, _ = await static_ctl(make_ctl, **kw)
    ctl.start()
    await ctl.ensure_ready()
    await ctl.ledger.begin("run", SID, 0.3, ctl.clock() - 36000)  # 10 h de "sessao" aberta
    await ctl.ledger.add_cost(5.0, "teste")
    await asyncio.sleep(0.5)  # >> idle_min (0,12 s) e varias rodadas de _guards
    assert ctl.state == READY and forbidden(vast) == [] and SID in vast.instances


async def test_modo_fixo_host_caiu_vira_alerta_nao_destruicao(make_ctl, redis):
    ctl, vast, _ = await static_ctl(make_ctl)
    ctl.start()
    await ctl.ensure_ready()
    vast.instances[SID]["actual_status"] = "exited"  # a Vast (ou o host) derrubou
    assert await until(lambda: ctl.degraded is not None)
    assert ctl.degraded["reason"] in ("host_caiu", "instancia_parada") and ctl.state == LOADING
    assert SID in vast.instances and forbidden(vast) == [] and await redis.hlen(K_BAD) == 0
    assert any(h["key"].startswith("static_") for h in ctl.alerts.history)
    assert ctl.status()["degraded"]["since"]
    await ctl.ensure_ready()  # religar e permitido: a fake sobe a instancia e o controlador volta a READY sozinho
    assert ctl.state == READY and ctl.degraded is None and forbidden(vast) == []
    assert any(c == ("power", SID, "running") for c in vast.calls)


async def test_modo_fixo_sem_saude_tres_vezes_alerta_e_se_recupera(make_ctl, redis):
    ctl, vast, probe = await static_ctl(make_ctl)
    ctl.start()
    await ctl.ensure_ready()
    probe.ok = False
    assert await until(lambda: ctl.degraded is not None)
    assert ctl.degraded["reason"] == "sem_saude" and ctl.state == LOADING
    await asyncio.sleep(0.5)  # varias retentativas com recuo: nada de destruir nem marcar o host
    assert SID in vast.instances and forbidden(vast) == [] and await redis.hlen(K_BAD) == 0
    assert ctl.degraded["attempts"] > 1
    probe.ok = True
    assert await until(lambda: ctl.state == READY and ctl.degraded is None)


async def test_modo_fixo_pedido_durante_falha_recebe_503_rapido(make_ctl):
    ctl, vast, probe = await static_ctl(make_ctl, probe_ok=False, wait_max_s=30.0)
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason == "gpu_fixa_sem_saude" and forbidden(vast) == []


async def test_modo_fixo_adocao_com_spec_diferente_ou_sem_saude_nao_destroi(make_ctl, redis):
    vast = FakeVast()
    add_static(vast, label=f"{LABEL}-1")  # ate com o nosso rotulo e spec diferente no estado salvo
    ctl, vast, probe = await make_ctl(static_settings(), vast)
    await redis.set(K_INST, json.dumps({"id": SID, "machine_id": 1, "spec": "script-antigo", "created_at": 1}))
    probe.ok = False  # e sem saude na partida
    await ctl.adopt_static()
    assert ctl.degraded["reason"] == "sem_saude" and SID in vast.instances and forbidden(vast) == []
    probe.ok = True
    await ctl.adopt_static()
    assert ctl.state == READY and SID in vast.instances and forbidden(vast) == []


async def test_modo_fixo_parar_e_destruir_internos_sao_recusados(make_ctl):
    ctl, vast, _ = await static_ctl(make_ctl, auto_shutdown=True)
    ctl.start()
    await ctl.ensure_ready()
    await ctl.stop_now("ociosa")           # _guards / stop_now
    await ctl._destroy_instance("parar_falhou")  # destroi apos parar falhar
    await ctl._destroy_instance("adopt_sem_saude")
    await ctl._destroy_instance("ttl_parada")
    await ctl.destroy_now("admin")         # sem a confirmacao
    assert ctl.state == READY and SID in vast.instances and forbidden(vast) == [] and ctl.inst["id"] == SID
    await ctl._start_cycle_inner()  # o ciclo de partida falha em vez de criar ou alugar
    assert vast.count("create") == 0 and vast.count("search") == 0 and forbidden(vast) == []


async def test_modo_fixo_ttl_e_religar_falhou_nao_destroem(make_ctl):
    ctl, vast, _ = await static_ctl(make_ctl, status="exited", stopped_ttl_h=0.00001, scheduling_timeout_s=0.05, auto_shutdown=True)
    vast.power_script = ["unavailable", "unavailable"]  # religar_falhou
    ctl.start()
    await asyncio.sleep(0.6)  # TTL de ~0,04 s e scheduling_timeout muito estourados
    assert SID in vast.instances and forbidden(vast) == [] and ctl.state != READY
    assert ctl.degraded["reason"] == "instancia_parada"
    assert any(h["key"] == "static_religar" for h in ctl.alerts.history)


async def test_modo_fixo_instancia_ausente_na_vast_so_alerta(make_ctl):
    ctl, vast, _ = await make_ctl(static_settings(), FakeVast())  # nao existe a instancia 777
    ctl.start()
    await asyncio.sleep(0.3)
    assert ctl.degraded["reason"] == "instancia_ausente" and forbidden(vast) == []


# ---------------------------------------------------------------- endpoints de admin

async def _app_com_ctl(redis, ctl):
    s = Settings(tunnel_mode="none", admin_key_sha256=hashlib.sha256(ADMIN.encode()).hexdigest())
    app = create_app(s, controller=ctl, redis=redis, http=make_upstream(lambda req: httpx.Response(200, json={})), consumers=CONSUMERS)
    return app


async def test_modo_fixo_stop_e_destroy_exigem_confirmacao_409(make_ctl, redis):
    ctl, vast, _ = await static_ctl(make_ctl)
    ctl.start()
    await ctl.ensure_ready()
    app = await _app_com_ctl(redis, ctl)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        for rota in ("/admin/gpu/stop", "/admin/gpu/destroy"):
            for body in (None, {}, {"i_know_this_loses_the_host": False}, {"i_know_this_loses_the_host": "true"}):
                r = await c.post(rota, headers=AH, json=body) if body is not None else await c.post(rota, headers=AH)
                assert r.status_code == 409 and r.json()["detail"]["error"]["code"] == "static_instance_protected", (rota, body)
        assert (await c.post("/admin/gpu/stop", json={"i_know_this_loses_the_host": True})).status_code == 403  # a confirmacao nao dispensa a chave
        assert ctl.state == READY and forbidden(vast) == []
        await c.aclose()


async def test_modo_fixo_parada_confirmada_para_e_pausa_o_controlador(make_ctl, redis):
    ctl, vast, _ = await static_ctl(make_ctl)
    ctl.start()
    await ctl.ensure_ready()
    app = await _app_com_ctl(redis, ctl)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await c.post("/admin/gpu/stop", headers=AH, json={"i_know_this_loses_the_host": True})
        assert r.status_code == 200 and r.json()["ok"] is True
        await c.aclose()
    assert ("power", SID, "stopped") in vast.calls
    n = len(vast.calls)
    await asyncio.sleep(0.3)
    assert len(vast.calls) == n  # em pausa: nao tenta religar o que o admin mandou parar


# ---------------------------------------------------------------- interruptor em tempo de execucao (sob demanda)

@pytest.mark.parametrize("caminho", ["ociosidade", "sessao_maxima", "teto_diario"])
async def test_interruptor_desligado_nenhum_caminho_para_a_gpu(make_ctl, caminho):
    s = fast_settings(auto_shutdown=False, **({"max_session_h": 0.000001} if caminho == "sessao_maxima" else {}))
    ctl, vast, _ = await make_ctl(s)
    ctl.start()
    await ctl.ensure_ready()
    if caminho == "teto_diario":
        ctl.s.max_daily_usd = 0.01  # o teto barra partidas novas, mas nao para quem esta rodando
        await ctl.ledger.add_cost(5.0, "teste")
    await asyncio.sleep(0.6)  # horas de ociosidade em escala de teste (idle_min = 0,12 s)
    assert ctl.state == READY and not any(c[0] == "power" and c[2] == "stopped" for c in vast.calls)


async def test_interruptor_ligado_em_execucao_volta_a_parar_por_ociosidade(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(auto_shutdown=False))
    ctl.start()
    await ctl.ensure_ready()
    await asyncio.sleep(0.4)
    assert ctl.state == READY
    await ctl.set_config({"auto_shutdown": True, "idle_min": 1})  # 1 min: a janela de ociosidade recomeca agora
    await asyncio.sleep(0.3)
    assert ctl.state == READY
    await ctl.set_config({"idle_min": 1.0})
    ctl.cfg["idle_min"] = 0.002  # (so para o teste nao esperar 1 min)
    assert await until(lambda: ctl.state == OFF) and ctl.last_stop_reason == "ociosa"


async def test_teto_diario_ainda_barra_partida_nova_com_o_interruptor_desligado(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(auto_shutdown=False, max_daily_usd=0.01))
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason == "orcamento_diario" and vast.count("create") == 0


async def test_config_persiste_no_redis_e_vale_no_proximo_controlador(make_ctl, redis):
    ctl, _, _ = await make_ctl(fast_settings())
    await ctl.set_config({"auto_shutdown": False, "idle_min": 90, "reserved": True}, who="teste")
    assert json.loads(await redis.get(K_CONFIG)) == {"auto_shutdown": False, "idle_min": 90.0, "reserved": True}
    outro, _, _ = await make_ctl(fast_settings(auto_shutdown=True))
    await outro.load_config()
    assert outro.cfg == {"auto_shutdown": False, "idle_min": 90.0, "reserved": True}


@pytest.mark.parametrize("patch", [{"idle_min": 0}, {"idle_min": 1441}, {"idle_min": "30"}, {"idle_min": True}, {"auto_shutdown": "sim"},
                                   {"reserved": 1}, {"outro": 1}])
def test_config_invalida_e_recusada(patch):
    with pytest.raises(ValueError):
        GPUController.validate_config(patch)
    assert GPUController.validate_config({"idle_min": 1440, "auto_shutdown": True}) == {"idle_min": 1440.0, "auto_shutdown": True}


# ---------------------------------------------------------------- modo reservada

async def test_reservada_o_controlador_nao_mexe_em_nada(make_ctl):
    ctl, vast, probe = await static_ctl(make_ctl)
    ctl.start()
    await ctl.ensure_ready()
    await ctl.set_config({"reserved": True})
    probe.ok = False  # o Lucas parou o processo do LLM
    n = len(vast.calls)
    await asyncio.sleep(0.4)
    assert ctl.state == READY and ctl.degraded is None and len(vast.calls) == n and ctl.alerts.history == []
    await ctl.set_config({"reserved": False})
    probe.ok = True
    await asyncio.sleep(0.3)
    assert ctl.state == READY and forbidden(vast) == []


async def test_reservada_sob_demanda_nao_acorda_a_gpu(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(reserved=True))
    ctl.start()
    ctl.request_wake()
    await asyncio.sleep(0.3)
    assert ctl.state == OFF and vast.calls == []


async def test_reservada_responde_503_com_retry_after():
    ctl = StubCtl()
    ctl.cfg["reserved"] = True
    s = Settings(tunnel_mode="none")
    import fakeredis.aioredis
    r = fakeredis.aioredis.FakeRedis()
    app = create_app(s, controller=ctl, redis=r, http=make_upstream(lambda req: httpx.Response(200, json={"choices": []})), consumers=CONSUMERS)
    body = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": "oi"}], "max_tokens": 8}
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        for stream in (False, True):
            resp = await c.post("/v1/chat/completions", headers=H, json={**body, "stream": stream})
            assert resp.status_code == 503 and int(resp.headers["retry-after"]) > 0
            assert resp.json()["detail"]["error"]["code"] == "gpu_reserved" and "reservada" in resp.json()["detail"]["error"]["message"]
        assert ctl.begun == 0 and ctl.woken is False  # nem entrou na fila nem pediu para acordar
        ctl.cfg["reserved"] = False
        assert (await c.post("/v1/chat/completions", headers=H, json=body)).status_code == 200
        await c.aclose()
    await r.aclose()


# ---------------------------------------------------------------- vigia (watchdog) fora do onstart

def test_onstart_sem_vigia_nao_instala_o_watchdog_e_e_bash_valido():
    on, off = load_onstart(), load_onstart(watchdog=False)
    assert "WATCHDOG_EOF" in on and "console.vast.ai" in on
    for marca in ("WATCHDOG", "watchdog", "console.vast.ai", "state\":\"stopped", "CONTAINER_API_KEY"):
        assert marca not in off, marca
    assert "vllm serve" in off and "--tensor-parallel-size 2" in off and off.startswith("#!/bin/bash") and "#__" not in off
    bash = shutil.which("bash")
    if bash:
        f = Path(tempfile.mkdtemp()) / "onstart.sh"
        f.write_bytes(off.encode("utf-8"))
        assert subprocess.run([bash, "-n", str(f)]).returncode == 0


async def test_instancia_nova_so_leva_o_vigia_com_o_interruptor_ligado_e_o_spec_nao_muda(redis):
    seen = {}
    for flag in (False, True):
        s = fast_settings(auto_shutdown=flag)
        vast, probe = FakeVast(), Probe()
        ctl = GPUController(s, vast, NoTunnel(), Ledger(redis), Alerts(""), redis, health_probe=probe)  # sem onstart injetado: usa o real
        ctl.start()
        await ctl.ensure_ready()
        onstart = next(iter(vast.instances.values()))["onstart"]
        seen[flag] = (("console.vast.ai" in onstart), ctl.spec_id, ctl.inst["watchdog"])
        await ctl.stop_loop()
        await redis.flushall()
    assert seen[False][0] is False and seen[True][0] is True
    assert seen[False][1] == seen[True][1]  # a impressao nao depende do interruptor: ligar/desligar nao invalida a instancia
    assert seen[False][2] is False and seen[True][2] is True


# ---------------------------------------------------------------- endpoints de config e status

async def test_endpoints_de_config_e_bloco_config_no_status(redis):
    ctl = StubCtl()
    app = await _app_com_ctl(redis, ctl)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.get("/admin/gpu/config")).status_code == 403 and (await c.post("/admin/gpu/config", json={})).status_code == 403
        assert (await c.get("/admin/gpu/config", headers=H)).status_code == 403  # chave de consumidor nao serve
        g = (await c.get("/admin/gpu/config", headers=AH)).json()
        assert g["auto_shutdown"] is False and g["reserved"] is False and g["idle_min"] == 30.0
        r = await c.post("/admin/gpu/config", headers=AH, json={"auto_shutdown": True, "idle_min": 45})
        assert r.status_code == 200 and r.json()["auto_shutdown"] is True and r.json()["idle_min"] == 45.0 and ctl.cfg["auto_shutdown"] is True
        for ruim in ({"idle_min": 0}, {"idle_min": 2000}, {"auto_shutdown": "sim"}, {"x": 1}):
            r = await c.post("/admin/gpu/config", headers=AH, json=ruim)
            assert r.status_code == 422 and r.json()["detail"]["error"]["code"] == "invalid_config", ruim
        assert (await c.post("/admin/gpu/config", headers=AH, content=b"nao e json")).status_code == 400
        for rota, h in (("/v1/gpu/status", H), ("/admin/gpu/status", AH)):
            st = (await c.get(rota, headers=h)).json()
            assert st["config"]["auto_shutdown"] is True and st["limits"]["idle_min"] == 45.0, rota
        await c.aclose()


async def test_stop_confirmado_em_modo_fixo_repassa_force(redis):
    ctl = StubCtl()
    ctl.static = True
    app = await _app_com_ctl(redis, ctl)
    async with app.router.lifespan_context(app):
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.post("/admin/gpu/destroy", headers=AH)).status_code == 409 and ctl.forced == []
        assert (await c.post("/admin/gpu/destroy", headers=AH, json={"i_know_this_loses_the_host": True})).status_code == 200
        assert ctl.forced == [("destroy", True)]
        await c.aclose()


def test_limites_de_concorrencia_vem_do_ambiente(monkeypatch):
    """CHAT_PER_CONSUMER e CHAT_QUEUE_MAX eram ignorados: todo o trafego chega por um unico consumidor (llm-access)."""
    from app.config import Settings
    monkeypatch.setenv("CHAT_PER_CONSUMER", "32")
    monkeypatch.setenv("CHAT_QUEUE_MAX", "200")
    monkeypatch.setenv("EMBED_QUEUE_MAX", "300")
    s = Settings.from_env()
    assert (s.chat_per_consumer, s.chat_queue_max, s.embed_queue_max) == (32, 200, 300)


def test_limite_de_contexto_vem_do_ambiente(monkeypatch):
    """MAX_CONTEXT_TOKENS acompanha o --max-model-len do vLLM (131072 na GPU fixa)."""
    from app.config import Settings
    monkeypatch.setenv("MAX_CONTEXT_TOKENS", "131072")
    assert Settings.from_env().max_context_tokens == 131072


class TunRun(NoTunnel):
    def __init__(self):
        super().__init__()
        self.runs: list[tuple] = []

    async def run(self, host, port, cmd, timeout=90.0):
        self.runs.append((host, port, cmd))
        return True


async def test_modo_fixo_vllm_morto_reinicia_so_o_processo_e_respeita_o_intervalo(make_ctl):
    ctl, vast, probe = await static_ctl(make_ctl, static_llm_restart_min_s=30.0, static_llm_start_cmd="bash /workspace/start_vllm.sh")
    tun = TunRun()
    ctl.tunnel = tun
    ctl.start()
    await ctl.ensure_ready()
    probe.ok = False  # o vLLM morreu
    assert await until(lambda: ctl.degraded is not None)
    assert await until(lambda: len(tun.runs) >= 1)
    await asyncio.sleep(0.6)  # varias retentativas de conexao: o reinicio nao se repete antes do intervalo
    assert len(tun.runs) == 1 and tun.runs[0] == ("ssh.fake", 22222, "bash /workspace/start_vllm.sh")
    assert forbidden(vast) == [] and SID in vast.instances  # a instancia nunca e parada nem destruida
    probe.ok = True
    assert await until(lambda: ctl.state == READY and ctl.degraded is None)


async def test_modo_fixo_sem_comando_de_partida_nao_tenta_reiniciar(make_ctl):
    ctl, vast, probe = await static_ctl(make_ctl, static_llm_start_cmd="")
    tun = TunRun()
    ctl.tunnel = tun
    ctl.start()
    await ctl.ensure_ready()
    probe.ok = False
    assert await until(lambda: ctl.degraded is not None)
    await asyncio.sleep(0.4)
    assert tun.runs == []
