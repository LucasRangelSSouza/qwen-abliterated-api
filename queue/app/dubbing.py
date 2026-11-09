"""Dublagem: traducao das falas pelo LLM do proprio servidor (a etapa entre a transcricao e a voz clonada). Funcoes puras + uma coroutine que recebe o LLM."""
from __future__ import annotations

import json
import re

LANG_NAMES = {"pt": "Brazilian Portuguese", "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian"}
LOTE = 40  # falas por chamada: respostas curtas e a ordem se mantem

SISTEMA = ("You are a professional audiovisual dubbing translator. Translate each numbered line from {src} into {tgt} so it can be SPOKEN in the same time slot: natural spoken language, "
           "keep proper names, numbers and brand names, keep the tone, and keep the length close to the source (never more than 115% of the source characters, shorter is better). "
           "Do not add explanations, notes or quotes. Reply with ONLY a JSON array of strings, one translation per input line, in the same order and with exactly the same number of items.")


def montar(segmentos: list[dict], src: str, tgt: str) -> tuple[str, str]:
    linhas = [{"i": i, "text": s["text"], "max_chars": max(8, int(len(s["text"]) * 1.15))} for i, s in enumerate(segmentos)]
    return SISTEMA.format(src=LANG_NAMES.get(src, src), tgt=LANG_NAMES.get(tgt, tgt)), json.dumps(linhas, ensure_ascii=False)


def _texto(x) -> str:
    """Um item da resposta: string, ou objeto com a traducao em qualquer chave razoavel (o modelo as vezes devolve {"i":0,"translation":"..."})."""
    if isinstance(x, dict):
        for k in ("text", "translation", "translated", "traducao", "tradução", "t", "output", "result"):
            if isinstance(x.get(k), str):
                return x[k].strip()
        return next((v.strip() for k, v in x.items() if k not in ("i", "index", "id", "max_chars") and isinstance(v, str)), "")
    return str(x or "").strip()


def ler(texto: str, n: int) -> list[str] | None:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip())
    for cand in (t, t[t.find("["): t.rfind("]") + 1] if "[" in t and "]" in t else ""):
        try:
            v = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, dict):
            v = next((x for x in v.values() if isinstance(x, list)), None)
        if isinstance(v, list) and len(v) == n:
            return [_texto(x) for x in v]
    return None


async def traduzir(llm, segmentos: list[dict], src: str, tgt: str) -> list[dict]:
    """`llm(sistema, usuario) -> str`. Devolve os segmentos com `text` traduzido (e `text_src` com o original). Uma nova tentativa por lote se a contagem vier errada."""
    out: list[dict] = []
    for ini in range(0, len(segmentos), LOTE):
        lote = segmentos[ini: ini + LOTE]
        sistema, usuario = montar(lote, src, tgt)
        traduzido = None
        for tentativa in range(2):
            traduzido = ler(await llm(sistema, usuario if not tentativa else usuario + "\n(Your previous answer was invalid: return ONLY a JSON array of exactly %d non-empty strings, nothing else.)" % len(lote)), len(lote))
            if traduzido is not None and all(t for t in traduzido):  # traducao vazia de uma fala que tinha texto e falha: tenta de novo
                break
            traduzido = None
        if traduzido is None:
            raise ValueError("a traducao das falas nao veio no formato esperado")
        for s, t in zip(lote, traduzido):
            out.append({"start": s["start"], "end": s["end"], "text_src": s["text"], "text": t})
    return out
