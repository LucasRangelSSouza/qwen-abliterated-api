"""Roteador invisível do Hermes (spec 45 A): cada pedido vai ao modelo certo sem o cliente ver a troca.

Rotas:
- `livre`    -> IA da casa (Qwen abliterado): conteúdo que os modelos comerciais recusam (adulto, temas sensíveis).
- `planejar` -> combo rt-planejar: rodízio entre os modelos fortes (Claude/GPT) do 9Router global; planejar, arquitetar.
- `rapido`   -> combo rt-rapido: rodízio entre os modelos baratos (Claude/GPT); o dia a dia, passos do agente.

Classificador: (1) palavras fortes vão direto para `livre` sem gastar chamada; (2) o Qwen responde `livre` ou `normal`
(máx. 3 tokens, temperatura 0); (3) `normal` vira `planejar` quando é pedido grande (primeira mensagem longa ou com verbos de
plano/arquitetura) e `rapido` no resto.
Recusa do GPT/Claude ("não posso ajudar", "I can't", política de conteúdo) -> refaz o MESMO pedido no Qwen; o cliente vê uma
resposta só. Destino com erro (401, 429, 5xx, timeout) fica 5 min fora do rodízio e o próximo assume.
"""
from __future__ import annotations

import itertools
import json
import logging
import re
import time
import unicodedata

log = logging.getLogger("access.roteador")

# ------------------------------------------------------------------ classificador
_PESADO = re.compile(r"\b(sexo|sexual|transar|transa|nud[ea]s?|pelad[ao]s?|porn\w*|putaria|safad[ao]\w*|tes[aã]o|gozar|gozo|orgasm\w*|"
                     r"punheta|siririca|boquete|xvideos|onlyfans|fetiche\w*|bdsm|er[oó]tic\w*|hentai|nsfw|"
                     r"buceta|xoxota|piroca|"
                     r"nude|naked|horny|blowjob|cum\b|fuck\w*|dick|pussy|boobs|erotic|explicit)\b", re.I)
_PLANO = re.compile(r"\b(planej\w*|plano|arquitet\w*|estrat[eé]gi\w*|roadmap|cronograma|especifica\w*|spec\b|projet\w*|"
                    r"passo a passo|etapas|desenvolv\w*|crie um (sistema|app|site|produto|neg[oó]cio)|monte um|estruture|"
                    r"analis\w* (completa|profunda)|compare|business plan|plan\b|architect\w*|design a)\b", re.I)
CLASSIFICAR = ("Classifique a mensagem do usuário. Responda SÓ uma palavra:\n"
               "livre = pede conteúdo sexual/adulto explícito, violência gráfica, drogas, armas, hacking ofensivo ou qualquer tema que "
               "um assistente comercial (ChatGPT/Claude) recusaria;\n"
               "normal = todo o resto (inclusive perguntas comuns, trabalho, código, saúde, dinheiro).\n\nMensagem:\n{texto}")


def _ultima_do_usuario(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict))
            return str(c or "")
    return ""


def _n_usuario(body: dict) -> int:
    return sum(1 for m in body.get("messages") or [] if m.get("role") == "user")


def precisa_livre_rapido(texto: str) -> bool:
    t = unicodedata.normalize("NFC", texto or "")
    return bool(_PESADO.search(t))


def e_planejamento(body: dict, texto: str) -> bool:
    if body.get("tools") and _n_usuario(body) > 1:
        return False  # passo do agente no meio de uma tarefa: mão de obra
    if _PLANO.search(texto or ""):
        return True
    return _n_usuario(body) <= 1 and len(texto or "") > 400


async def classificar(ctx, body: dict, chamar_casa) -> tuple[str, str]:
    """(rota, motivo). `chamar_casa(mensagens, max_tokens)` -> texto (Qwen, sem passar pelo roteador)."""
    texto = _ultima_do_usuario(body)
    if precisa_livre_rapido(texto):
        return "livre", "palavra"
    try:
        r = (await chamar_casa([{"role": "user", "content": CLASSIFICAR.format(texto=texto[-3000:])}], 3)).strip().lower()
    except Exception:  # noqa: BLE001  classificador fora: segue normal (a recusa ainda cai no Qwen)
        r = "normal"
    if r.startswith("livre"):
        return "livre", "classificador"
    return ("planejar", "plano") if e_planejamento(body, texto) else ("rapido", "padrao")


# ------------------------------------------------------------------ recusa
_RECUSA = re.compile(r"(\bn[aã]o (posso|vou|consigo|devo) (te )?(ajudar|fazer|criar|gerar|fornecer|atender|responder|ensinar|explicar|escrever|dar|passar|orientar|descrever|detalhar|incentivar|participar)|n[aã]o consigo (ajudar|fazer|criar|gerar) (com )?isso|"
                     r"n[aã]o [eé] (algo|poss[ií]vel) (que|para) (eu )?(possa|posso)|desculpe,? mas n[aã]o|sinto muito,? mas n[aã]o posso|"
                     r"contra (as )?(minhas )?(diretrizes|pol[ií]ticas)|viola(r|ria)? (as )?(diretrizes|pol[ií]ticas)|"
                     r"i can'?t (help|assist|do|create|provide|write)|i'?m not able to|i cannot (help|assist|provide|create)|"
                     r"i won'?t (be able|help|create)|against (my|our) (guidelines|policies)|content policy|as an ai)", re.I)


def e_recusa(j: dict) -> bool:
    msg = ((j.get("choices") or [{}])[0].get("message") or {})
    if msg.get("tool_calls"):
        return False
    txt = (msg.get("content") or "").strip()
    return not txt or bool(_RECUSA.search(txt[:350]))  # recusa aparece logo no começo; resposta longa que recusa no início também conta


# ------------------------------------------------------------------ rodízio com pausa de quem falha
class Rodizio:
    def __init__(self):
        self._contador: dict[str, itertools.count] = {}
        self._fora: dict[str, float] = {}

    def ordem(self, apelido: str, destinos: list[dict]) -> list[dict]:
        router = [d for d in destinos if "router" in d]
        casa = [d for d in destinos if "casa" in d]
        if router:
            n = next(self._contador.setdefault(apelido, itertools.count())) % len(router)
            router = router[n:] + router[:n]
            agora = time.time()
            vivos = [d for d in router if self._fora.get(d["router"], 0) < agora]
            router = vivos + [d for d in router if d not in vivos]  # quem está fora vai para o fim (tenta se os outros falharem)
        return router + casa

    def falhou(self, destino: str, segundos: int = 300) -> None:
        self._fora[destino] = time.time() + segundos


RODIZIO = Rodizio()


def sse_de(j: dict) -> str:
    """Resposta completa em SSE, com texto E chamadas de ferramenta (o agente do Hermes depende delas)."""
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    delta = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{**tc, "index": i} for i, tc in enumerate(msg["tool_calls"])]
    fim = (j.get("choices") or [{}])[0].get("finish_reason") or ("tool_calls" if msg.get("tool_calls") else "stop")
    pedaco = {"id": j.get("id", "rt"), "object": "chat.completion.chunk", "created": j.get("created", int(time.time())), "model": j.get("model"),
              "choices": [{"index": 0, "delta": delta, "finish_reason": fim}], "usage": j.get("usage")}
    return "data: " + json.dumps(pedaco, ensure_ascii=False) + "\n\ndata: [DONE]\n\n"
