"""Versao do app sem a API do GitHub: usa o redirecionamento da pagina /releases/latest, guarda a ultima boa e nao cai quando o GitHub cai."""
from app import versao
from tests.conftest import Rig


async def test_versao_vem_do_redirecionamento_e_monta_os_links(rig: Rig, monkeypatch):
    async def ok(_c):
        return "0.6.45"
    monkeypatch.setattr(versao, "_buscar_github", ok)
    r = await rig.client.get("/v1/app/latest")
    j = r.json()
    assert r.status_code == 200 and j["version"] == "0.6.45" and j["stale"] is False
    assert j["assets"]["win-setup"] == "https://github.com/RangelTech/second-brain/releases/download/v0.6.45/Segundo-Cerebro-Windows.exe"
    assert j["assets"]["linux-deb"].endswith("/v0.6.45/Segundo-Cerebro-Linux.deb")


async def test_github_fora_do_ar_devolve_a_ultima_boa(rig: Rig, monkeypatch):
    chamadas = {"n": 0}
    async def instavel(_c):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            return "0.6.44"
        raise RuntimeError("504")
    monkeypatch.setattr(versao, "_buscar_github", instavel)
    assert (await rig.client.get("/v1/app/latest")).json()["version"] == "0.6.44"
    rig.clock.advance(versao.TTL_S + 1)  # cache vencido: confere de novo, o GitHub falha
    j = (await rig.client.get("/v1/app/latest")).json()
    assert j["version"] == "0.6.44" and j["stale"] is True


async def test_versao_sobrevive_a_reinicio_com_github_fora(rig: Rig, monkeypatch):
    async def ok(_c):
        return "0.6.45"
    monkeypatch.setattr(versao, "_buscar_github", ok)
    await rig.client.get("/v1/app/latest")
    await rig.restart()
    async def cai(_c):
        raise RuntimeError("504")
    monkeypatch.setattr(versao, "_buscar_github", cai)
    j = (await rig.client.get("/v1/app/latest")).json()
    assert j["version"] == "0.6.45" and j["stale"] is True


async def test_sem_nada_guardado_e_github_fora_responde_503_limpo(rig: Rig, monkeypatch):
    async def cai(_c):
        raise RuntimeError("504")
    monkeypatch.setattr(versao, "_buscar_github", cai)
    assert (await rig.client.get("/v1/app/latest")).status_code == 503
