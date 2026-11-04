"""Cliente do modelo-guarda (opcional): um LLM pequeno de seguranca (Llama Guard, Qwen3Guard, ShieldGemma...) atras de uma API
compativel com OpenAI. Sem estado e sem configuracao propria: cada classificador passa a SUA url/modelo/chave. Se a chamada falhar,
devolve None e o classificador segue so com as regras (a falha do modelo nunca derruba nem libera nada por conta propria)."""
from __future__ import annotations

import httpx

from app.safety.common import Verdict

# nomes de categoria dos modelos-guarda mapeados para as nossas categorias
CAT_MAP = {"violent": "violence", "violence": "violence", "sexual": "csam", "csam": "csam", "child": "csam", "weapons": "cbrn", "cbrn": "cbrn",
           "chemical": "cbrn", "biological": "cbrn", "nuclear": "cbrn", "cyber": "malware", "malware": "malware", "fraud": "fraud",
           "scam": "fraud", "self-harm": "selfharm", "selfharm": "selfharm", "suicide": "selfharm"}


def parse_verdict(text: str, enabled: set[str]) -> Verdict:
    """Entende as duas saidas comuns: Llama Guard ("safe" / "unsafe\\nS1,S9") e Qwen3Guard ("Safety: Unsafe\\nCategories: Violent")."""
    t = (text or "").strip().lower()
    unsafe = t.startswith("unsafe") or "safety: unsafe" in t or "safety:unsafe" in t
    if not unsafe:
        return Verdict()
    cat = None
    for k, v in CAT_MAP.items():
        if k in t:
            cat = v
            break
    cat = cat or "violence"
    if cat not in enabled:
        return Verdict()  # o modelo achou, mas a categoria esta desligada no painel
    return Verdict(True, cat, 0.9, "model", t[:60])


async def ask(http: httpx.AsyncClient, url: str, model: str, key: str, content: str, role: str, enabled: set[str], timeout: float = 8.0) -> Verdict | None:
    """Pergunta ao modelo-guarda. `role` e 'user' (classificar pedido) ou 'assistant' (classificar resposta)."""
    if not url:
        return None
    try:
        msgs = [{"role": "user", "content": content[:6000]}] if role == "user" else [{"role": "user", "content": "(contexto omitido)"}, {"role": "assistant", "content": content[:6000]}]
        r = await http.post(url.rstrip("/") + "/chat/completions", json={"model": model, "messages": msgs, "max_tokens": 24, "temperature": 0},
                            headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=timeout)
        r.raise_for_status()
        return parse_verdict(r.json()["choices"][0]["message"]["content"], enabled)
    except Exception:  # noqa: BLE001
        return None
