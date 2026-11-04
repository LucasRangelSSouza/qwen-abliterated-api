"""Tipos e utilitarios sem estado, usados pelos dois classificadores. Nada aqui decide nada: so estrutura e leitura de configuracao."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

LEVEL_FREE = 1        # sem trava (padrao)
LEVEL_CLASSIFIED = 2  # com classificadores

REFUSAL_DEFAULT = ("Não posso ajudar com isso: o seu acesso tem o classificador de segurança ligado e este pedido cai numa categoria barrada. "
                   "Posso ajudar com outra coisa.")
REFUSAL_SELFHARM = ("Sinto muito que você esteja passando por isso. Eu não posso ajudar com isso, mas você não está sozinho: "
                    "no Brasil, o CVV atende 24 horas pelo telefone 188 (ligação gratuita) e em cvv.org.br.")


@dataclass
class Verdict:
    blocked: bool = False
    category: str | None = None
    score: float = 0.0
    source: str = ""        # 'rules' ou 'model'
    detail: str = ""        # trecho curto que disparou (nunca o pedido inteiro)

    def refusal(self, custom: str | None = None) -> str:
        if self.category == "selfharm":
            return REFUSAL_SELFHARM
        return custom or REFUSAL_DEFAULT


def norm(text: str) -> str:
    """Minusculas, sem acento, espacos colapsados: as regras casam em cima disto."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


def compile_rules(rules: dict[str, list[str]]) -> dict[str, list[re.Pattern]]:
    return {cat: [re.compile(p, re.I) for p in pats] for cat, pats in rules.items()}


def match(text: str, compiled: dict[str, list[re.Pattern]], enabled: set[str]) -> Verdict:
    t = norm(text)
    for cat, pats in compiled.items():
        if cat not in enabled:
            continue
        for p in pats:
            m = p.search(t)
            if m:
                return Verdict(True, cat, 1.0, "rules", m.group(0)[:60])
    return Verdict()


def load_config(store, key: str, defaults: dict) -> dict:
    """Le a configuracao de UM classificador (chave propria em `settings`); o que faltar vem do padrao."""
    raw = store.get_setting(key)
    cfg = dict(defaults)
    if raw:
        try:
            cfg.update(json.loads(raw))
        except ValueError:
            pass
    return cfg


def save_config(store, key: str, cfg: dict) -> None:
    store.set_setting(key, json.dumps(cfg, ensure_ascii=False))


def log_block(store, classifier: str, person_id: int | None, category: str | None, score: float, route: str, source: str, now: float | None = None) -> None:
    """Registra um bloqueio. So metadados: nunca o texto do pedido nem da resposta."""
    try:
        with store.tx() as c:
            c.execute("INSERT INTO safety_log(ts,classifier,person_id,category,score,route,source) VALUES(?,?,?,?,?,?,?)",
                      (now or time.time(), classifier, person_id, category, score, route, source))
    except Exception:  # noqa: BLE001  registro nunca derruba o pedido
        pass
