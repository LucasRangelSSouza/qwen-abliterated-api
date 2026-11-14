"""Voz de saida (texto -> fala) para o app: vozes neurais da Microsoft (edge-tts), em stream.

Fica no servidor de proposito: o protocolo do Edge muda de tempos em tempos (token Sec-MS-GEC) e aqui basta
atualizar o pacote edge-tts e reimplantar, sem release do app. O primeiro pedaco de audio sai em ~1 s e o resto
chega mais rapido que a fala, entao o app toca enquanto recebe.
"""
from __future__ import annotations

import re
from typing import AsyncIterator

VOZES = {
    "feminina": "pt-BR-ThalitaMultilingualNeural",
    "thalita": "pt-BR-ThalitaMultilingualNeural",
    "masculina": "pt-BR-AntonioNeural",
    "antonio": "pt-BR-AntonioNeural",
    "francisca": "pt-BR-FranciscaNeural",
}
PADRAO = VOZES["feminina"]
MAX_TEXTO = 6000


def escolher_voz(nome: str | None) -> str:
    n = (nome or "").strip()
    if not n:
        return PADRAO
    if n.lower() in VOZES:
        return VOZES[n.lower()]
    # nome completo de voz do Edge (ex.: pt-BR-FranciscaNeural, en-US-AvaMultilingualNeural)
    if re.fullmatch(r"[a-z]{2,3}-[A-Z]{2,4}-[A-Za-z]+Neural", n):
        return n
    return PADRAO


def limpar_texto(t: str) -> str:
    """Tira marcacao que a voz leria em voz alta (markdown, links, emojis de enfeite)."""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"[*_~|>]+", " ", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def taxa(speed) -> str:
    """speed estilo OpenAI (0.5 a 2.0) -> rate do Edge ("+10%")."""
    try:
        v = float(speed)
    except (TypeError, ValueError):
        v = 1.0
    v = min(2.0, max(0.5, v))
    pct = round((v - 1.0) * 100)
    return f"{'+' if pct >= 0 else ''}{pct}%"


async def falar(texto: str, voz: str, rate: str = "+0%") -> AsyncIterator[bytes]:
    import edge_tts  # importado aqui: o pacote so e necessario quando alguem pede voz

    async for c in edge_tts.Communicate(texto, voz, rate=rate).stream():
        if c.get("type") == "audio" and c.get("data"):
            yield c["data"]
