"""Painel: login por cookie, API de pessoas/consumo/maquinas/config e entrega do SPA."""
import glob

from app.ledger import iso
from tests.conftest import PASSWORD, Rig

ROTAS_GET = ["/admin/api/me", "/admin/api/people", "/admin/api/usage", "/admin/api/machines", "/admin/api/settings"]


async def test_admin_401_sem_cookie(rig: Rig):
    for rota in ROTAS_GET:
        r = await rig.client.get(rota)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized", rota
    for rota in ("/admin/api/people", "/admin/api/reset-all-windows", "/admin/api/people/1/suspend", "/admin/api/password"):
        assert (await rig.client.post(rota, json={})).status_code == 401, rota
    assert (await rig.client.patch("/admin/api/people/1", json={})).status_code == 401
    rig.client.cookies.set("llm_access_session", "lixo.lixo")
    assert (await rig.client.get("/admin/api/me")).status_code == 401


async def test_login_logout_me(rig: Rig):
    r = await rig.client.post("/admin/api/login", json={"password": "errada"})
    assert r.status_code == 401 and "set-cookie" not in r.headers
    r = await rig.login()
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc
    assert (await rig.client.get("/admin/api/me")).json() == {"ok": True, "user": "admin"}
    await rig.client.post("/admin/api/logout")
    rig.client.cookies.clear()
    assert (await rig.client.get("/admin/api/me")).status_code == 401


async def test_cookie_expira_e_falsificado_nao_vale(rig: Rig):
    await rig.login()
    cookie = rig.client.cookies["llm_access_session"]
    rig.clock.advance(13 * 3600)
    assert (await rig.client.get("/admin/api/me")).status_code == 401
    rig.clock.advance(-13 * 3600)
    payload, sig = cookie.rsplit(".", 1)
    rig.client.cookies.clear()
    rig.client.cookies.set("llm_access_session", payload + "." + "0" * len(sig))
    assert (await rig.client.get("/admin/api/me")).status_code == 401


async def test_cookie_marcado_secure_atras_do_proxy_https(rig: Rig):
    r = await rig.client.post("/admin/api/login", json={"password": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


async def test_limite_de_tentativas_de_login(rig: Rig):
    for _ in range(5):
        assert (await rig.client.post("/admin/api/login", json={"password": "errada"})).status_code == 401
    r = await rig.client.post("/admin/api/login", json={"password": PASSWORD})   # ate a certa, na 6a
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # outro IP (atras do Traefik) tem a sua propria conta
    assert (await rig.client.post("/admin/api/login", json={"password": PASSWORD}, headers={"X-Forwarded-For": "9.9.9.9"})).status_code == 200
    rig.clock.advance(61)
    assert (await rig.client.post("/admin/api/login", json={"password": PASSWORD})).status_code == 200


async def test_pessoas_crud_e_chave_so_aparece_uma_vez(rig: Rig):
    await rig.login()
    r = await rig.client.post("/admin/api/people", json={"name": "Bia", "note": "teste", "session_limit_tokens": 1000,
                                                          "week_limit_tokens": None, "session_seconds": 7200})
    assert r.status_code == 200
    j = r.json()
    key, p = j["api_key"], j["person"]
    assert key.startswith("sk-") and p["key_last4"] == key[-4:]
    assert p["limits"] == {"session_limit_tokens": 1000, "week_limit_tokens": None, "session_seconds": 7200, "week_seconds": 604800}
    assert p["status"] == "active" and p["last_used_at"] is None
    assert p["session"] == {"used": 0, "limit": 1000, "resets_at": None} and p["week"]["limit"] is None
    lista = await rig.client.get("/admin/api/people")
    assert key not in lista.text and lista.json()[0]["key_last4"] == key[-4:]
    assert (await rig.client.get("/admin/api/people", params={"q": "bi"})).json()[0]["name"] == "Bia"
    assert (await rig.client.get("/admin/api/people", params={"q": "zzz"})).json() == []

    await rig.chat(key)
    p = (await rig.client.get("/admin/api/people")).json()[0]
    assert p["session"]["used"] == 30 and p["session"]["resets_at"] and p["last_used_at"] == iso(rig.clock())

    r = await rig.client.patch(f"/admin/api/people/{p['id']}", json={"name": "Bia B", "week_limit_tokens": 5000, "session_limit_tokens": None})
    assert r.json()["name"] == "Bia B" and r.json()["limits"]["week_limit_tokens"] == 5000 and r.json()["limits"]["session_limit_tokens"] is None
    assert r.json()["note"] == "teste"                          # campo ausente nao muda

    r = await rig.client.post(f"/admin/api/people/{p['id']}/reset-windows")
    assert r.json()["session"]["used"] == 0 and r.json()["session"]["resets_at"] is None
    await rig.chat(key)
    assert (await rig.client.post("/admin/api/reset-all-windows")).json()["ok"] is True
    assert (await rig.usage(key))["session"]["used"] == 0
    assert (await rig.client.patch("/admin/api/people/999", json={"name": "x"})).status_code == 404


async def test_validacao_de_limites_e_modo_qa(rig: Rig, make_rig, tmp_path):
    await rig.login()
    for corpo in ({"name": ""}, {"name": "x", "session_limit_tokens": 0}, {"name": "x", "session_limit_tokens": "9"},
                  {"name": "x", "session_seconds": 60}):
        assert (await rig.client.post("/admin/api/people", json=corpo)).status_code == 422, corpo
    qa = await make_rig(qa_mode=True, db_path=str(tmp_path / "qa.db"))
    await qa.login()
    r = await qa.client.post("/admin/api/people", json={"name": "q", "session_limit_tokens": 100, "session_seconds": 5})
    assert r.status_code == 200 and r.json()["person"]["limits"]["session_seconds"] == 5
    assert (await qa.client.get("/admin/api/settings")).json()["qa_mode"] is True


async def test_so_o_hash_da_chave_fica_no_banco(rig: Rig):
    _, key = await rig.person()
    await rig.chat(key)
    await rig.restart()
    for f in glob.glob(rig.db + "*"):
        assert key.encode() not in open(f, "rb").read()


async def test_consumo_filtros_e_paginacao(rig: Rig):
    a, ka = await rig.person("ana", session_limit_tokens=60, session_seconds=3600)
    b, kb = await rig.person("beto")
    for _ in range(3):
        await rig.chat(ka)                      # 2a passa (reserva 25 <= 30), 3a bloqueia
        rig.clock.advance(10)
    await rig.chat(kb)
    r = (await rig.client.get("/admin/api/usage", params={"person_id": a})).json()
    assert r["total"] == 3 and {x["status"] for x in r["rows"]} == {"ok", "blocked_session"}
    assert r["rows"][0]["name"] == "ana" and r["rows"][0]["limits"] == {"session_limit_tokens": 60, "week_limit_tokens": None}
    dia = iso(rig.clock())[:10]
    assert (await rig.client.get("/admin/api/usage", params={"day": dia})).json()["total"] == 4
    assert (await rig.client.get("/admin/api/usage", params={"day": "2001-01-01"})).json() == {"total": 0, "rows": []}
    assert (await rig.client.get("/admin/api/usage", params={"day": "x"})).status_code == 422
    pag = (await rig.client.get("/admin/api/usage", params={"limit": 2, "offset": 3})).json()
    assert pag["total"] == 4 and len(pag["rows"]) == 1


async def test_retencao_de_90_dias_do_usage_log(rig: Rig):
    _, key = await rig.person()
    await rig.chat(key)
    rig.clock.advance(91 * 86400)
    await rig.chat(key)                         # a gravacao dispara a limpeza horaria
    await rig.login()                           # a sessao do painel venceu junto
    rows = (await rig.client.get("/admin/api/usage")).json()
    assert rows["total"] == 1


async def test_machines_ok(rig: Rig):
    await rig.login()
    r = (await rig.client.get("/admin/api/machines", params={"limit": 5})).json()
    assert r["status"]["state"] == "READY" and r["status"]["spend"]["month_cap_usd"] == 80.0
    assert r["cycles"][0]["id"] == "c1" and r["error"] is None


async def test_machines_upstream_falha_devolve_200_com_erro(rig: Rig):
    await rig.login()
    rig.up.fail_machines = True
    r = await rig.client.get("/admin/api/machines")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] is None and j["cycles"] == [] and "indisponivel" in j["error"]


async def test_settings_e_troca_de_senha(rig: Rig):
    await rig.login()
    s = (await rig.client.get("/admin/api/settings")).json()
    assert s["qa_mode"] is False and s["min_window_seconds"] == 3600
    assert (await rig.client.post("/admin/api/password", json={"current": "errada", "new": "nova-senha-123"})).status_code == 401
    assert (await rig.client.post("/admin/api/password", json={"current": PASSWORD, "new": "curta"})).status_code == 422
    assert (await rig.client.post("/admin/api/password", json={"current": PASSWORD, "new": "nova-senha-123"})).status_code == 200
    assert (await rig.client.get("/admin/api/me")).status_code == 200          # a sessao atual segue
    rig.client.cookies.clear()
    assert (await rig.client.post("/admin/api/login", json={"password": PASSWORD})).status_code == 401
    rig.clock.advance(61)
    await rig.restart()                          # a troca vive no banco, nao so na memoria
    assert (await rig.client.post("/admin/api/login", json={"password": "nova-senha-123"})).status_code == 200


async def test_painel_ausente_nao_quebra(rig: Rig):
    r = await rig.client.get("/admin")
    assert r.status_code == 200 and "nao construido" in r.text
    assert (await rig.client.get("/admin/qualquer/rota")).status_code == 200
    r = await rig.client.get("/admin/api/nao-existe")
    assert r.status_code == 404                      # o catch-all nao engole /admin/api


async def test_painel_estatico_com_fallback_spa(tmp_path, make_rig):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>PAINEL</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "segredo.txt").write_text("fora")
    r = await make_rig(panel_dir=str(dist))
    assert "PAINEL" in (await r.client.get("/admin")).text
    assert "PAINEL" in (await r.client.get("/admin/pessoas/3")).text      # fallback para o index
    assert (await r.client.get("/admin/assets/app.js")).text == "console.log(1)"
    assert "fora" not in (await r.client.get("/admin/..%2Fsegredo.txt")).text
    assert "fora" not in (await r.client.get("/admin/../segredo.txt")).text
