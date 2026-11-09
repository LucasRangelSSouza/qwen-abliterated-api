"""Creditos avulsos (Spec 24): pacotes de R$ 100 que o cliente compra quando estoura o limite do plano. Saldo que expira; so e gasto depois que o plano
estoura (tokens: nenhuma janela de 5 h ou 7 dias cobre o pedido; midia: cota do mes acabou). Uso em credito NAO conta nas janelas.
Todas as funcoes que mexem em saldo recebem o cursor `c` de uma transacao aberta pelo chamador, para a reserva do plano e a do credito serem atomicas."""
from __future__ import annotations

import json

KINDS = ("tokens", "image", "video", "audio")
LABEL = {"tokens": "uso", "image": "imagens", "video": "vídeos", "audio": "áudio"}
BASE_BRL = 100                      # o pacote vale o equivalente a R$ 100 (preco original); a cobranca e em dolar
DEFAULT_USD_BRL = 5.5               # cotacao de referencia (ajuste do painel: usd_brl_rate)
DEFAULT_UNITS = {"image": 500, "video": 30, "audio": 150}
TOKENS_MULT = 2                     # pacote de uso = 2 x o limite da sessao de 5 h do plano
DEFAULT_EXPIRY_DAYS = 30
DAY = 86400
# nome do produto (minusculo) -> tipo, quando o id do produto nao estiver configurado
NAME_HINTS = (("crédito de uso", "tokens"), ("credito de uso", "tokens"), ("crédito de imagens", "image"), ("credito de imagens", "image"),
              ("crédito de vídeos", "video"), ("credito de videos", "video"), ("crédito de áudio", "audio"), ("credito de audio", "audio"))
NEXT_PLAN = {"light": "max", "max": "ultra", "ultra": "enterprise"}


def _json_setting(store, key: str) -> dict:
    raw = store.get_setting(key)
    try:
        v = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def usd_brl_rate(store) -> float:
    try:
        v = float(store.get_setting("usd_brl_rate") or DEFAULT_USD_BRL)
    except ValueError:
        return DEFAULT_USD_BRL
    return v if v > 0 else DEFAULT_USD_BRL


def price_usd(store) -> int:
    """Preco do pacote em dolar inteiro: o ajuste credit_price_usd, ou R$ 100 convertido pela cotacao (US$ 18 a 5,5)."""
    try:
        fixo = float(store.get_setting("credit_price_usd") or 0)
    except ValueError:
        fixo = 0
    return int(round(fixo)) if fixo > 0 else max(1, int(round(BASE_BRL / usd_brl_rate(store))))


def offers(store) -> dict:
    """Ofertas da Cakto por tipo (settings credit_offers). Tipo sem oferta configurada nao tem botao de compra."""
    return {k: str(v) for k, v in _json_setting(store, "credit_offers").items() if k in KINDS and v}


def expiry_days(store) -> float:
    try:
        return float(store.get_setting("credit_expiry_days") or DEFAULT_EXPIRY_DAYS)
    except ValueError:
        return float(DEFAULT_EXPIRY_DAYS)


def units_for_pack(store, kind: str, session_limit: int | None) -> int | None:
    """Quantas unidades o pacote de R$ 100 entrega. Uso: 2 x o limite de sessao do plano (None = plano sem limite, nao compra)."""
    cfg = _json_setting(store, "credit_pack_units")
    if kind == "tokens":
        if not session_limit:
            return None
        mult = cfg.get("tokens_mult", TOKENS_MULT)
        return int(session_limit * (mult if isinstance(mult, (int, float)) and mult > 0 else TOKENS_MULT))
    v = cfg.get(kind)
    return int(v) if isinstance(v, (int, float)) and v > 0 else DEFAULT_UNITS[kind]


def kind_of_product(store, product_id: str, hint: str) -> str | None:
    """Reconhece o produto de credito por id (settings credit_products) ou pelo nome. Nunca confunde com plano."""
    ids = _json_setting(store, "credit_products")
    for kind, pid in ids.items():
        if pid and product_id and pid == product_id and kind in KINDS:
            return kind
    h = (hint or "").lower()
    return next((k for frag, k in NAME_HINTS if frag in h), None)


# ---- saldo (dentro de uma transacao)
def balance(c, person_id: int, kind: str, now: float) -> int:
    r = c.execute("SELECT COALESCE(SUM(remaining),0) s FROM credits WHERE person_id=? AND kind=? AND revoked_at IS NULL AND expires_at>?", (person_id, kind, now)).fetchone()
    return int(r["s"])


def take(c, person_id: int, kind: str, amount: int, now: float) -> list[tuple[int, int]] | None:
    """Debita `amount` dos pacotes validos, o que vence primeiro antes. Tudo ou nada: sem saldo suficiente devolve None e nao mexe em nada."""
    if amount <= 0:
        return []
    rows = c.execute("SELECT id, remaining FROM credits WHERE person_id=? AND kind=? AND revoked_at IS NULL AND expires_at>? AND remaining>0 ORDER BY expires_at ASC, id ASC",
                     (person_id, kind, now)).fetchall()
    if sum(r["remaining"] for r in rows) < amount:
        return None
    parts, falta = [], amount
    for r in rows:
        usar = min(falta, r["remaining"])
        c.execute("UPDATE credits SET remaining=remaining-? WHERE id=?", (usar, r["id"]))
        parts.append((r["id"], usar))
        falta -= usar
        if falta == 0:
            break
    return parts


def give_back(c, parts: list[tuple[int, int]], amount: int | None = None) -> None:
    """Devolve ao saldo (pedido que falhou ou gastou menos que o reservado). `amount` = quanto devolver (None = tudo); devolve do ultimo pacote para o primeiro."""
    falta = sum(t for _, t in parts) if amount is None else amount
    for cid, taken in reversed(parts):
        if falta <= 0:
            break
        volta = min(falta, taken)
        c.execute("UPDATE credits SET remaining=remaining+? WHERE id=? AND revoked_at IS NULL", (volta, cid))
        falta -= volta


# ---- concessao e revogacao
def grant(store, person_id: int, kind: str, units: int, now: float, source: str = "purchase", external_id: str | None = None) -> int | None:
    """Cria o pacote. Mesmo `external_id` duas vezes (webhook repetido) nao duplica: devolve None."""
    if kind not in KINDS or units <= 0:
        raise ValueError("pacote invalido")
    with store.tx() as c:
        if external_id and c.execute("SELECT 1 FROM credits WHERE external_id=?", (external_id,)).fetchone():
            return None
        return c.execute("INSERT INTO credits(person_id,kind,units,remaining,granted_at,expires_at,source,external_id) VALUES(?,?,?,?,?,?,?,?)",
                         (person_id, kind, units, units, now, now + expiry_days(store) * DAY, source, external_id)).lastrowid


def revoke(store, credit_id: int, now: float) -> bool:
    with store.tx() as c:
        r = c.execute("SELECT id FROM credits WHERE id=? AND revoked_at IS NULL", (credit_id,)).fetchone()
        if r is None:
            return False
        c.execute("UPDATE credits SET remaining=0, revoked_at=? WHERE id=?", (now, credit_id))
        return True


def revoke_by_external(store, external_id: str, now: float) -> bool:
    r = store.one("SELECT id FROM credits WHERE external_id=?", (external_id,))
    return bool(r) and revoke(store, r["id"], now)


def listing(store, person_id: int, now: float) -> list[dict]:
    rows = store.query("SELECT * FROM credits WHERE person_id=? ORDER BY granted_at DESC, id DESC LIMIT 100", (person_id,))
    return [{"id": r["id"], "kind": r["kind"], "units": r["units"], "remaining": r["remaining"], "granted_at": r["granted_at"], "expires_at": r["expires_at"],
             "source": r["source"], "revoked": r["revoked_at"] is not None, "expired": r["expires_at"] <= now} for r in rows]


def balances(store, person_id: int, now: float) -> dict:
    with store.tx() as c:
        return {k: balance(c, person_id, k, now) for k in KINDS}
