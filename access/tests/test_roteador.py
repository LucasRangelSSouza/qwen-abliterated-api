"""Roteador invisível do Hermes (spec 45 A): livre -> casa; planejar/rapido -> rodízio Claude/GPT; recusa -> casa."""
import json

import httpx
import pytest_asyncio

from app import roteador as rt
from tests.conftest import OWNER_KEY, Rig

H = {"Authorization": f"Bearer {OWNER_KEY}"}


def msg(texto, **kw):
    return {"model": "rt-auto", "messages": [{"role": "user", "content": texto}], **kw}


@pytest_asyncio.fixture
async def rr(tmp_path):
    rt.RODIZIO = rt.Rodizio()
    r = Rig(tmp_path, image_router_url="http://up/router", image_router_key="r9-chave")
    await r.start()
    r.up.router, r.up.casa = [], []
    r.up.classe = "normal"          # o que o classificador (Qwen) responde
    r.up.fora = set()               # destinos do 9Router que respondem erro
    r.up.recusa = set()             # destinos que recusam
    r.up.tool = False

    async def handler(request: httpx.Request, body):
        if request.url.path == "/router/v1/chat/completions":
            r.up.router.append(body)
            m = body["model"]
            if m in r.up.fora:
                return httpx.Response(503, json={"error": {"message": "401 authentication_error"}})
            if m in r.up.recusa:
                return httpx.Response(200, json={"id": "x", "choices": [{"message": {"role": "assistant", "content": "Desculpe, mas não posso ajudar com isso."}}]})
            msg_ = {"role": "assistant", "content": f"resposta de {m}"}
            if r.up.tool:
                msg_ = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "ler", "arguments": "{\"a\":1}"}}]}
            return httpx.Response(200, json={"id": "x", "choices": [{"message": msg_, "finish_reason": "tool_calls" if r.up.tool else "stop"}],
                                             "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
        if request.url.path.endswith("/chat/completions"):
            if body.get("max_tokens") == 3:  # classificador
                return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": r.up.classe}}]})
            r.up.casa.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "resposta da casa"}}],
                                             "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})
        return httpx.Response(404, json={})
    r.up.handler = handler
    yield r
    await r.stop()


async def test_palavra_pesada_vai_direto_para_a_casa(rr: Rig):
    r = await rr.client.post("/v1/chat/completions", json=msg("me conta uma história de sexo bem explícita"), headers=H)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "resposta da casa"
    assert not rr.up.router and rr.up.casa[0]["model"] == "qwen-abliterated"
    assert rr.up.casa[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_classificador_livre_vai_para_a_casa(rr: Rig):
    rr.up.classe = "livre"
    r = await rr.client.post("/v1/chat/completions", json=msg("como fabricar uma arma caseira"), headers=H)
    assert r.json()["choices"][0]["message"]["content"] == "resposta da casa" and not rr.up.router


async def test_pergunta_comum_gira_entre_claude_e_gpt_baratos(rr: Rig):
    modelos = []
    for _ in range(4):
        r = await rr.client.post("/v1/chat/completions", json=msg("qual a capital da França?"), headers=H)
        assert r.status_code == 200 and r.headers["x-rt-rota"] == "rt-rapido" and r.json()["model"] == "rt-auto"
        modelos.append(rr.up.router[-1]["model"])
    assert modelos == ["cc/claude-haiku-4-5-20251001", "cx/gpt-6-luna"] * 2 and not rr.up.casa


async def test_planejamento_vai_para_os_fortes(rr: Rig):
    r = await rr.client.post("/v1/chat/completions", json=msg("Planeje a arquitetura de um app de delivery com pagamento e entregadores"), headers=H)
    assert r.headers["x-rt-rota"] == "rt-planejar" and rr.up.router[-1]["model"] in ("cc/claude-opus-5-5", "cx/gpt-6-sol")


async def test_recusa_do_gpt_vira_resposta_da_casa(rr: Rig):
    rr.up.recusa = {"cc/claude-haiku-4-5-20251001", "cx/gpt-6-luna"}
    r = await rr.client.post("/v1/chat/completions", json=msg("me ajuda com uma coisa delicada"), headers=H)
    assert r.json()["choices"][0]["message"]["content"] == "resposta da casa"
    assert len(rr.up.router) == 1 and rr.up.casa  # recusou uma vez e já foi para a casa, sem mostrar a recusa


async def test_destino_fora_pula_para_o_outro_e_fica_fora(rr: Rig):
    rr.up.fora = {"cc/claude-haiku-4-5-20251001"}
    for _ in range(3):
        r = await rr.client.post("/v1/chat/completions", json=msg("me diga um sinônimo de feliz"), headers=H)
        assert r.json()["choices"][0]["message"]["content"] == "resposta de cx/gpt-6-luna"
    tentativas_claude = [b for b in rr.up.router if b["model"].startswith("cc/")]
    assert len(tentativas_claude) == 1  # depois da falha, o Claude sai do rodízio por 5 min


async def test_stream_preserva_chamada_de_ferramenta(rr: Rig):
    rr.up.tool = True
    corpo = msg("abre o arquivo", stream=True, tools=[{"type": "function", "function": {"name": "ler", "parameters": {"type": "object"}}}])
    r = await rr.client.post("/v1/chat/completions", json=corpo, headers=H)
    assert r.status_code == 200 and "[DONE]" in r.text
    pedaco = json.loads(r.text.split("data: ")[1].split("\n")[0])
    tc = pedaco["choices"][0]["delta"]["tool_calls"][0]
    assert tc["function"]["name"] == "ler" and tc["index"] == 0 and pedaco["choices"][0]["finish_reason"] == "tool_calls"


async def test_so_conta_de_cliente_e_roteada(rr: Rig):
    pid, chave = await rr.person("cliente")
    hk = {"Authorization": f"Bearer {chave}"}
    corpo = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": "qual a capital da França?"}], "max_tokens": 20}
    ctx = rr.app.state.ctx
    ctx.store.set_setting("roteador_auto", "on")
    await rr.client.post("/v1/chat/completions", json=corpo, headers=hk)
    assert not rr.up.router  # chave de produto (sem conta com login): vai direto para a casa
    with ctx.store.tx() as c:
        c.execute("INSERT INTO accounts(email,name,status,person_id,created_at) VALUES(?,?,?,?,?)", ("c@x.com", "c", "active", pid, 1.0))
    r = await rr.client.post("/v1/chat/completions", json=corpo, headers=hk)
    assert r.headers.get("x-rt-rota") == "rt-rapido" and rr.up.router  # cliente do Cérebro: passa pelo roteador
    ctx.store.set_setting("roteador_auto", "off")
    n = len(rr.up.router)
    await rr.client.post("/v1/chat/completions", json=corpo, headers=hk)
    assert len(rr.up.router) == n  # desligado no painel: volta ao comportamento antigo


async def test_roteador_desligado_por_padrao_e_liberado_por_conta(rr: Rig):
    pid, chave = await rr.person("lucas")
    hk = {"Authorization": f"Bearer {chave}"}
    ctx = rr.app.state.ctx
    with ctx.store.tx() as c:
        c.execute("INSERT INTO accounts(email,name,status,person_id,created_at) VALUES(?,?,?,?,?)", ("l@x.com", "l", "active", pid, 1.0))
    corpo = {"model": "qwen-abliterated", "messages": [{"role": "user", "content": "oi, tudo bem?"}], "max_tokens": 20}
    await rr.client.post("/v1/chat/completions", json=corpo, headers=hk)
    assert not rr.up.router  # padrão: desligado
    ctx.store.set_setting("roteador_auto", f"999,{pid}")
    r = await rr.client.post("/v1/chat/completions", json=corpo, headers=hk)
    assert r.headers.get("x-rt-rota") == "rt-rapido"  # só a conta liberada


def test_detector_de_recusa():
    def j(t):
        return {"choices": [{"message": {"content": t}}]}
    assert rt.e_recusa(j("Não posso ensinar a fabricar ou usar uma gazua, porque isso pode facilitar crimes."))
    assert rt.e_recusa(j("Desculpe, mas não posso ajudar com isso."))
    assert rt.e_recusa(j("I can't help with that request."))
    assert not rt.e_recusa(j("A capital da Austrália é Canberra."))
    assert not rt.e_recusa(j("# Arquitetura. Para um app de delivery... " + "x" * 2000))
