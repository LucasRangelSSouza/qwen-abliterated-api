"""Cenarios de QA da Spec 14 §5.3.2 (T1..T6 e bordas) contra o upstream falso, com relogio injetavel."""
import asyncio

import httpx

from app.ledger import iso
from tests.conftest import BODY, Rig, content_chunk, sse_response, usage_chunk, chunk

HORA = 3600


async def test_t1_sem_limite_conta_e_passa(rig: Rig):
    _, key = await rig.person()          # sem limite nenhum
    rig.up.total = 400
    for _ in range(3):
        r = await rig.chat(key)
        assert r.status_code == 200
    u = await rig.usage(key)
    assert u["person"] == "ana"
    assert u["session"]["limit"] is None and u["session"]["remaining"] is None
    assert u["session"]["used"] == 1200 and u["week"]["used"] == 1200
    # o upstream recebe a chave do servico, nunca a do cliente
    assert rig.up.calls[0][2]["authorization"] == "Bearer sk-queue-teste"


async def test_t2_so_sessao_bloqueia_e_reabre_sozinha(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    rig.up.total = 50
    t0 = rig.clock()
    assert (await rig.chat(key)).status_code == 200
    assert (await rig.chat(key)).status_code == 200
    r = await rig.chat(key)
    assert r.status_code == 429
    e = r.json()["error"]
    assert e["code"] == "usage_limit_reached" and e["window"] == "session"
    assert e["resets_at"] == iso(t0 + HORA)          # a janela abriu no primeiro pedido
    assert int(r.headers["retry-after"]) == HORA
    assert rig.up.ai_calls == 2                      # o bloqueado nem chegou ao upstream
    # semanal sem limite nao e afetado: so a sessao fecha
    rig.clock.advance(HORA - 1)
    assert (await rig.chat(key)).status_code == 429
    rig.clock.advance(2)                             # venceu: reabre sozinha
    assert (await rig.chat(key)).status_code == 200
    u = await rig.usage(key)
    assert u["session"]["used"] == 50                # janela nova, nao a velha
    assert u["session"]["resets_at"] != e["resets_at"]


async def test_t3_so_semanal(rig: Rig):
    _, key = await rig.person(week_limit_tokens=100, week_seconds=2 * HORA)
    rig.up.total = 50
    assert (await rig.chat(key)).status_code == 200
    assert (await rig.chat(key)).status_code == 200
    r = await rig.chat(key)
    assert r.status_code == 429 and r.json()["error"]["window"] == "week"
    rig.clock.advance(2 * HORA + 1)
    assert (await rig.chat(key)).status_code == 200
    assert (await rig.usage(key))["week"]["used"] == 50


async def test_t4_os_dois_limites_independentes(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA, week_limit_tokens=150, week_seconds=10 * HORA)
    rig.up.total = 50
    assert (await rig.chat(key)).status_code == 200
    assert (await rig.chat(key)).status_code == 200
    r = await rig.chat(key)                          # sessao estourada, semanal ainda com 50 de sobra
    assert r.status_code == 429 and r.json()["error"]["window"] == "session"
    rig.clock.advance(HORA + 1)                      # sessao reabre
    assert (await rig.chat(key)).status_code == 200  # semanal chega a 150
    r = await rig.chat(key)                          # sessao nova com saldo 50, semanal zerada
    assert r.status_code == 429 and r.json()["error"]["window"] == "week"
    u = await rig.usage(key)
    assert u["session"]["remaining"] == 50 and u["week"]["remaining"] == 0


async def test_t5_suspensa_nao_chama_upstream(rig: Rig):
    pid, key = await rig.person()
    assert (await rig.client.post(f"/admin/api/people/{pid}/suspend")).status_code == 200
    r = await rig.chat(key)
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_suspended"
    assert rig.up.ai_calls == 0
    await rig.client.post(f"/admin/api/people/{pid}/resume")
    assert (await rig.chat(key)).status_code == 200


async def test_t6_chave_rotacionada_antiga_para_na_hora(rig: Rig):
    pid, old = await rig.person()
    assert (await rig.chat(old)).status_code == 200
    new = (await rig.client.post(f"/admin/api/people/{pid}/rotate-key")).json()["api_key"]
    assert new != old
    r = await rig.chat(old)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    assert (await rig.chat(new)).status_code == 200
    assert (await rig.usage(new))["session"]["used"] == 60   # o saldo e da pessoa, sobrevive a troca da chave


async def test_stream_cortado_cobra_so_o_entregue(rig: Rig):
    _, key = await rig.person(session_limit_tokens=1000, session_seconds=HORA)

    async def cut(request, body):
        return sse_response([content_chunk("a" * 30), content_chunk("b" * 30)], error=httpx.ReadError("caiu"))
    rig.up.handler = cut
    r = await rig.chat(key, {**BODY, "stream": True, "max_tokens": 500})
    assert r.status_code == 200
    # in_est ("oi" = 5) + 60 caracteres / 3 + 1 = 26; muito menos que a reserva de 505
    u = await rig.usage(key)
    assert u["session"]["used"] == 26 and u["session"]["remaining"] == 974
    log = (await rig.client.get("/admin/api/usage")).json()["rows"][0]
    assert log["status"] == "cut" and log["total_tokens"] == 26


async def test_duas_simultaneas_disputando_o_ultimo_saldo(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    rig.up.delay = 0.05
    rig.up.total = 40
    body = {**BODY, "max_tokens": 60}                # cada uma reserva 65; so cabe uma em 100
    a, b = await asyncio.gather(rig.chat(key, body), rig.chat(key, body))
    assert sorted([a.status_code, b.status_code]) == [200, 429]
    assert rig.up.ai_calls == 1
    u = await rig.usage(key)
    assert u["session"]["used"] == 40 and u["session"]["remaining"] == 60   # reserva liberada, nada alem do consumo


async def test_muitas_simultaneas_nunca_passam_do_reservado(rig: Rig):
    _, key = await rig.person(session_limit_tokens=300, session_seconds=HORA)
    rig.up.delay = 0.05
    rig.up.total = 40
    body = {**BODY, "max_tokens": 60}
    rs = await asyncio.gather(*[rig.chat(key, body) for _ in range(10)])
    ok = [r for r in rs if r.status_code == 200]
    assert len(ok) == 4                              # 4 x 65 = 260 <= 300 < 5 x 65
    assert (await rig.usage(key))["session"]["used"] == 4 * 40


async def test_max_tokens_maior_que_o_saldo_recusa_antes(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    r = await rig.chat(key, {**BODY, "max_tokens": 500})
    assert r.status_code == 429
    e = r.json()["error"]
    assert e["code"] == "usage_limit_reached" and e["remaining_tokens"] == 100 and e["requested_tokens"] == 505
    assert e["missing_tokens"] == 405 and "faltam 405" in e["message"]
    assert rig.up.ai_calls == 0
    assert (await rig.usage(key))["session"]["resets_at"] is None    # recusa nao abre janela


async def test_max_tokens_ausente_usa_teto_padrao(rig: Rig):
    _, key = await rig.person(session_limit_tokens=1000, session_seconds=HORA)
    body = {k: v for k, v in BODY.items() if k != "max_tokens"}
    r = await rig.chat(key, body)
    assert r.status_code == 429 and r.json()["error"]["requested_tokens"] == 5 + 4096


async def test_reinicio_no_meio_da_janela_preserva_tudo(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    rig.up.total = 50
    await rig.chat(key)
    before = await rig.usage(key)
    rig.clock.advance(600)
    await rig.restart()
    after = await rig.usage(key)
    assert after == before and after["session"]["used"] == 50
    assert (await rig.chat(key)).status_code == 200
    assert (await rig.chat(key)).status_code == 429   # o consumo anterior ao reinicio continua valendo
    rig.clock.advance(HORA)
    assert (await rig.chat(key)).status_code == 200


async def test_upstream_503_antes_de_dado_nao_abre_janela_nem_cobra(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, week_limit_tokens=500, session_seconds=HORA)

    async def down(request, body):
        return httpx.Response(503, headers={"retry-after": "30", "x-llm-state": "OFF"}, json={"error": {"message": "gpu"}})
    rig.up.handler = down
    r = await rig.chat(key)
    assert r.status_code == 503 and r.headers["retry-after"] == "30" and r.headers["x-llm-state"] == "OFF"
    u = await rig.usage(key)
    assert u["session"]["used"] == 0 and u["session"]["resets_at"] is None and u["week"]["resets_at"] is None

    async def only_error(request, body):                 # o queue responde 200 e manda o erro dentro do stream
        return sse_response([chunk({"error": {"message": "GPU indisponivel", "code": "no_capacity"}}), b"data: [DONE]\n\n"])
    rig.up.handler = only_error
    assert (await rig.chat(key, {**BODY, "stream": True})).status_code == 200
    u = await rig.usage(key)
    assert u["session"]["used"] == 0 and u["session"]["resets_at"] is None
    rows = (await rig.client.get("/admin/api/usage")).json()["rows"]
    assert {x["status"] for x in rows} == {"upstream_error"} and all(x["total_tokens"] == 0 for x in rows)


async def test_aviso_de_espera_passa_intacto_e_cobra_so_o_real(rig: Rig):
    _, key = await rig.person(session_limit_tokens=1000, session_seconds=HORA)
    parts = [b": acordando\n\n", b": acordando\n\n",
             chunk({"x_queue": {"state": "STARTING", "eta_s": 300}, "choices": []}),
             content_chunk("oi"), usage_chunk(30), b"data: [DONE]\n\n"]

    async def wake(request, body):
        return sse_response(parts, headers={"x-llm-state": "STARTING", "x-llm-queue-position": "0", "x-queue-notice": "acordando"})
    rig.up.handler = wake
    r = await rig.chat(key, {**BODY, "stream": True}, **{"X-Queue-Class": "interactive", "X-End-User": "u1"})
    assert r.content == b"".join(parts)                  # byte a byte, inclusive comentarios e x_queue
    assert r.headers["x-llm-state"] == "STARTING" and r.headers["x-queue-notice"] == "acordando"
    assert rig.up.calls[0][2]["x-queue-class"] == "interactive" and rig.up.calls[0][2]["x-end-user"] == "u1"
    assert rig.up.calls[0][1]["stream_options"] == {"include_usage": True}
    assert (await rig.usage(key))["session"]["used"] == 30


async def test_stream_preserva_stream_options_do_cliente(rig: Rig):
    _, key = await rig.person()
    await rig.chat(key, {**BODY, "stream": True, "stream_options": {"include_usage": False, "x": 1}})
    assert rig.up.calls[0][1]["stream_options"] == {"include_usage": True, "x": 1}


async def test_embeddings_contados(rig: Rig):
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    r = await rig.client.post("/v1/embeddings", json={"model": "qwen-embedding", "input": "texto"},
                              headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["usage"]["total_tokens"] == 7
    assert "stream_options" not in rig.up.calls[0][1]
    assert (await rig.usage(key))["session"]["used"] == 7
    rows = (await rig.client.get("/admin/api/usage")).json()["rows"]
    assert rows[0]["route"] == "embed" and rows[0]["total_tokens"] == 7


async def test_v1_usage_formato_e_auth(rig: Rig):
    assert (await rig.client.get("/v1/usage")).status_code == 401
    assert (await rig.client.get("/v1/usage", headers={"Authorization": "Bearer sk-nada"})).status_code == 401
    _, key = await rig.person(session_limit_tokens=100, session_seconds=HORA)
    assert await rig.usage(key) == {"person": "ana",
        "session": {"limit": 100, "used": 0, "remaining": 100, "resets_at": None},
        "week": {"limit": None, "used": 0, "remaining": None, "resets_at": None}}
    await rig.chat(key)
    u = await rig.usage(key)
    assert u["session"]["used"] == 30 and u["session"]["remaining"] == 70 and u["session"]["resets_at"].endswith("Z")


async def test_chaves_owner_e_platform_sem_janela_mas_registradas(rig: Rig):
    rig.up.total = 5000
    for k in ("sk-dono-teste", "sk-luar-teste"):
        for _ in range(2):
            assert (await rig.chat(k)).status_code == 200
        u = await rig.usage(k)
        assert u["session"]["limit"] is None and u["session"]["resets_at"] is None
    await rig.login()
    rows = (await rig.client.get("/admin/api/usage")).json()["rows"]
    assert {r["name"] for r in rows} == {"owner", "platform"} and len(rows) == 4 and rows[0]["total_tokens"] == 5000


async def test_chaves_especiais_valem_mesmo_com_banco_quebrado(tmp_path, make_rig):
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("x")
    r = await make_rig(db_path=str(arquivo / "access.db"))   # pai e um arquivo: o SQLite nao abre
    assert (await r.chat("sk-dono-teste")).status_code == 200
    assert (await r.chat("sk-luar-teste")).status_code == 200
    assert (await r.chat("sk-qualquer")).status_code == 503   # chave comum precisa do banco
    assert (await r.client.get("/health")).json() == {"ok": True}


async def test_modelos_e_health(rig: Rig):
    _, key = await rig.person()
    r = await rig.client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "qwen-abliterated"
    assert (await rig.client.get("/v1/models")).status_code == 401
    assert (await rig.client.get("/health")).status_code == 200


async def test_nenhum_texto_de_mensagem_no_banco(rig: Rig):
    _, key = await rig.person()
    segredo = "TEXTO-SECRETO-DA-MENSAGEM-98765"
    await rig.chat(key, {**BODY, "messages": [{"role": "user", "content": segredo}]})
    await rig.chat(key, {**BODY, "stream": True, "messages": [{"role": "user", "content": segredo}]})
    await rig.restart()                                   # fecha a conexao e consolida o WAL
    import glob
    for f in glob.glob(rig.db + "*"):
        assert segredo.encode() not in open(f, "rb").read()


async def test_raiz_redireciona_para_o_painel(rig: Rig):
    r = await rig.client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/admin/"
