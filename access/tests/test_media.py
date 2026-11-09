"""Imagem pelo llm-access: autentica, repassa ao llm-queue com a chave de servico e o nome de quem pediu; o painel so enxerga com login."""
from tests.conftest import OWNER_KEY, UP_ADMIN, UP_KEY, Rig


async def test_imagem_exige_chave(rig: Rig):
    r = await rig.client.post("/v1/images/generations", json={"prompt": "x"})
    assert r.status_code == 401 and rig.up.ai_calls == 0


async def test_imagem_repassa_com_chave_de_servico_e_nome(rig: Rig):
    r = await rig.client.post("/v1/images/generations", json={"prompt": "hamburguer"}, headers={"Authorization": f"Bearer {OWNER_KEY}"})
    assert r.status_code == 200 and r.json()["data"][0]["b64_json"] == "AAAA"
    path, body, headers = rig.up.calls[-1]
    assert path == "/v1/images/generations" and body["prompt"] == "hamburguer"
    assert headers["authorization"] == f"Bearer {UP_KEY}" and headers["x-end-user"] == "dono"


async def test_imagem_nao_aceita_corpo_invalido(rig: Rig):
    r = await rig.client.post("/v1/images/generations", content=b"nao-json", headers={"Authorization": f"Bearer {OWNER_KEY}", "Content-Type": "application/json"})
    assert r.status_code == 400


async def test_status_e_imagem_do_pedido(rig: Rig):
    h = {"Authorization": f"Bearer {OWNER_KEY}"}
    assert (await rig.client.get("/v1/jobs/j1", headers=h)).json()["state"] == "done"
    r = await rig.client.get("/v1/jobs/j1/image", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"PNG")
    assert (await rig.client.get("/v1/jobs/j1")).status_code == 401


async def test_progresso_de_midia_passa_pelo_proxy(rig: Rig):
    h = {"Authorization": f"Bearer {OWNER_KEY}"}
    r = await rig.client.get("/v1/media/jobs/j1", headers=h)
    assert r.status_code == 200 and r.json()["progress"] == 40 and r.json()["status"] == "running"
    assert (await rig.client.get("/v1/media/jobs/j1")).status_code == 401


async def test_painel_de_imagem_exige_login(rig: Rig):
    for p in ("/media/status", "/media/jobs", "/media/image/j1"):
        assert (await rig.client.get("/admin/api" + p)).status_code == 401


async def test_painel_de_imagem_le_do_queue(rig: Rig):
    await rig.login()
    assert (await rig.client.get("/admin/api/media/status")).json()["instance"]["dph"] == 0.128
    assert (await rig.client.get("/admin/api/media/jobs")).json()["recent"][0]["id"] == "j1"
    r = await rig.client.get("/admin/api/media/image/j1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"PNG")


async def test_faturamento_e_metricas_exigem_login(rig: Rig):
    assert (await rig.client.get("/admin/api/billing")).status_code == 401
    assert (await rig.client.get("/admin/api/metrics")).status_code == 401


async def test_faturamento_e_metricas_leem_do_queue(rig: Rig):
    await rig.login()
    b = (await rig.client.get("/admin/api/billing")).json()
    assert b["credit_usd"] == 77.0 and b["machines"]["image"]["id"] == 2
    m = (await rig.client.get("/admin/api/metrics?machine=image")).json()
    assert m["machine"] == "image" and m["totals"]["requests"] == 3
    assert (await rig.client.get("/admin/api/metrics?machine=lixo")).json()["machine"] == "llm"  # maquina invalida cai em llm


async def test_voz_e_dublagem_repassam_ao_queue_com_chave_de_servico(rig: Rig):
    h = {"Authorization": f"Bearer {OWNER_KEY}"}
    for rota, corpo in (("/v1/audio/voice-clone", {"text": "oi", "reference_audio": "AAAA"}), ("/v1/audio/voice-convert", {"media": "AAAA", "target_voice": "AAAA"}), ("/v1/videos/dub", {"video": "AAAA", "target_language": "pt"})):
        assert (await rig.client.post(rota, json=corpo)).status_code == 401  # sem chave: nada passa
        await rig.client.post(rota, json=corpo, headers=h)
        path, body, headers = rig.up.calls[-1]
        assert path == rota and body == corpo
        assert headers["authorization"] == f"Bearer {UP_KEY}" and headers["x-end-user"] == "dono"


# ---- cota mensal de midia por plano (imagem, video em clipes de 5 s, audio)

async def _assinante(rig: Rig, plano: str):
    """Pessoa com conta e assinatura ativa no plano; devolve a chave."""
    pid, key = await rig.person(f"cli-{plano}")
    st = rig.app.state.ctx.store
    with st.tx() as c:
        aid = c.execute("INSERT INTO accounts(email,name,person_id,created_at) VALUES(?,?,?,?)", (f"{plano}@x.com", plano, pid, 1.0)).lastrowid
        c.execute("INSERT INTO subscriptions(account_id,plan,status,updated_at) VALUES(?,?,?,?)", (aid, plano, "active", 1.0))
    from app import accounts as A
    A.ensure_plans(rig.app.state.ctx)
    return key


async def test_cota_de_video_do_plano_bloqueia_e_conta_720p_triplo(rig: Rig):
    key = await _assinante(rig, "light")  # Lite: 5 unidades de video por mes
    h = {"Authorization": f"Bearer {key}"}
    v = {"prompt": "x", "recipe": "wan22-a14b", "quality": "padrao"}
    for _ in range(4):
        assert (await rig.client.post("/v1/videos/generations", json=v, headers=h)).status_code == 200
    alta = {"prompt": "x", "recipe": "wan22-a14b", "quality": "alta"}  # 720p = 3 unidades: 4 + 3 > 5
    r = await rig.client.post("/v1/videos/generations", json=alta, headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "media_quota_exceeded" and "4 de 5" in r.json()["error"]["message"]
    assert (await rig.client.post("/v1/videos/generations", json=v, headers=h)).status_code == 200  # a 5a unidade ainda cabe
    assert (await rig.client.post("/v1/videos/generations", json=v, headers=h)).status_code == 429
    m = (await rig.usage(key))["media"]
    assert m["video"] == {"limit": 5, "used": 5, "remaining": 0, "resets_at": m["video"]["resets_at"]} and m["image"]["limit"] == 100 and m["audio"]["limit"] == 20


async def test_cota_renova_no_mes_seguinte_e_falha_nao_gasta(rig: Rig):
    key = await _assinante(rig, "light")
    h = {"Authorization": f"Bearer {key}"}
    for _ in range(5):
        assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x", "quality": "padrao"}, headers=h)).status_code == 200
    assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x", "quality": "padrao"}, headers=h)).status_code == 429
    rig.clock.advance(32 * 86400)
    assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x", "quality": "padrao"}, headers=h)).status_code == 200
    import httpx

    async def recusa(request, body):
        return httpx.Response(500, json={"error": "x"})
    rig.up.handler = recusa  # upstream recusa: a reserva e desfeita
    before = (await rig.usage(key))["media"]["video"]["used"]
    assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x", "quality": "padrao"}, headers=h)).status_code == 500
    assert (await rig.usage(key))["media"]["video"]["used"] == before


async def test_dono_sem_cota_e_plano_sem_cota_configurada_nao_bloqueia(rig: Rig):
    for _ in range(8):
        assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x"}, headers={"Authorization": f"Bearer {OWNER_KEY}"})).status_code == 200
    key = await _assinante(rig, "max")
    with rig.app.state.ctx.store.tx() as c:
        c.execute("UPDATE plans SET video_quota_month=NULL WHERE id='max'")
    for _ in range(40):
        assert (await rig.client.post("/v1/videos/generations", json={"prompt": "x", "quality": "padrao"}, headers={"Authorization": f"Bearer {key}"})).status_code == 200


def test_unidades_por_pedido():
    from app.mediaquota import units_for as u
    assert u("image", "/v1/images/generations", {}) == 1 and u("image", "x", {"n": 3}) == 3
    assert u("audio", "/v1/audio/voice-clone", {}) == 1 and u("video", "/v1/videos/dub", {}) == 1
    assert u("video", "/v1/videos/generations", {"quality": "padrao"}) == 1
    assert u("video", "/v1/videos/generations", {}) == 3                       # auto + 5 s = 720p
    assert u("video", "/v1/videos/generations", {"seconds": 10}) == 2          # auto + 2 clipes = 480p encadeado
    assert u("video", "/v1/videos/generations", {"quality": "alta", "seconds": 10}) == 6
    assert u("video", "/v1/videos/generations", {"recipe": "openjutsu-swap", "seconds": 5}) == 1
