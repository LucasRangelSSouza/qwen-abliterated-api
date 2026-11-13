"""Creditos avulsos (Spec 24): pacotes de R$ 100 que o cliente compra quando estoura o limite do plano. Saldo que expira; so e gasto depois que o plano
estoura (tokens: nenhuma janela de 5 h ou 7 dias cobre o pedido; midia: cota do mes acabou). Uso em credito NAO conta nas janelas.
Todas as funcoes que mexem em saldo recebem o cursor `c` de uma transacao aberta pelo chamador, para a reserva do plano e a do credito serem atomicas."""
from __future__ import annotations

import json

KINDS = ("tokens", "image", "video", "audio", "studio")
LABEL = {"tokens": "uso", "image": "imagens", "video": "vídeos", "audio": "áudio", "studio": "créditos do Estúdio"}
BASE_BRL = 100                      # o pacote vale o equivalente a R$ 100 (preco original); a cobranca e em dolar
DEFAULT_USD_BRL = 5.5               # cotacao de referencia (ajuste do painel: usd_brl_rate)
DEFAULT_UNITS = {"image": 500, "video": 30, "audio": 150, "studio": 900}  # studio: 1 credito = US$ 0,02; o pacote de US$ 18 = 900 creditos
STUDIO_USD = 0.02
STUDIO_ARREDONDA = 5            # o cliente ve sempre multiplos de 5 ("185 creditos")
PLANO_STUDIO_PADRAO = {"light": 100, "max": 300, "ultra": 1000, "enterprise": 2000}  # creditos gratis por mes (o painel pode mudar: setting studio_plan_credits)
TOKENS_MULT = 2                     # pacote de uso = 2 x o limite da sessao de 5 h do plano
DEFAULT_EXPIRY_DAYS = 30
DAY = 86400
# nome do produto (minusculo) -> tipo, quando o id do produto nao estiver configurado
NAME_HINTS = (("créditos do estúdio", "studio"), ("créditos do segundo cérebro", "studio"), ("creditos do segundo cerebro", "studio"), ("creditos do estudio", "studio"), ("crédito do estúdio", "studio"), ("credito do estudio", "studio"), ("crédito de uso", "tokens"), ("credito de uso", "tokens"), ("crédito de imagens", "image"), ("credito de imagens", "image"),
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
    if product_id and product_id in _json_setting(store, "studio_pack_products").values():
        return "studio"
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



# ---- credito unico do Estudio
def studio_creditos(preco_usd: float) -> int:
    """Quantos creditos custa um pedido: preco ao cliente em dolar / US$ 0,02, arredondado PARA CIMA ao multiplo de 5 (minimo 5)."""
    n = int(-(-round(float(preco_usd) / STUDIO_USD, 6) // 1))
    return max(STUDIO_ARREDONDA, -(-n // STUDIO_ARREDONDA) * STUDIO_ARREDONDA)


def studio_plan_credits(store) -> dict:
    cfg = _json_setting(store, "studio_plan_credits")
    return {**PLANO_STUDIO_PADRAO, **{k: int(v) for k, v in cfg.items() if isinstance(v, (int, float)) and v >= 0}}


def _plan_of(c, person_id: int):
    r = c.execute("SELECT s.plan p FROM accounts a JOIN subscriptions s ON s.account_id=a.id WHERE a.person_id=? ORDER BY s.updated_at DESC, s.id DESC LIMIT 1", (person_id,)).fetchone()
    return r["p"] if r else None


def ensure_monthly_studio(store, c, person_id: int, now: float) -> int:
    """Concede (uma vez por mes) os creditos gratis do plano. O mesmo external_id nunca duplica; vencem no fim do mes (UTC). Devolve quantos creditou agora."""
    from datetime import datetime, timezone
    plan = _plan_of(c, person_id)
    qtd = studio_plan_credits(store).get(plan or "", 0)
    if qtd <= 0:
        return 0
    d = datetime.fromtimestamp(now, timezone.utc)
    mes = d.strftime("%Y-%m")
    ext = f"plano-studio:{person_id}:{mes}"
    if c.execute("SELECT 1 FROM credits WHERE external_id=?", (ext,)).fetchone():
        return 0
    fim = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fim = fim.replace(year=fim.year + (fim.month == 12), month=fim.month % 12 + 1).timestamp()
    c.execute("INSERT INTO credits(person_id,kind,units,remaining,granted_at,expires_at,source,external_id) VALUES(?,?,?,?,?,?,?,?)",
              (person_id, "studio", qtd, qtd, now, fim, "plan", ext))
    return qtd


def studio_balance(store, person_id: int, now: float) -> int:
    with store.tx() as c:
        ensure_monthly_studio(store, c, person_id, now)
        return balance(c, person_id, "studio", now)


def studio_reserve(store, person_id: int, creditos: int, now: float):
    """Debita os creditos (os que vencem primeiro saem primeiro). Devolve (parts, saldo): parts=None quando falta saldo."""
    with store.tx() as c:
        ensure_monthly_studio(store, c, person_id, now)
        saldo = balance(c, person_id, "studio", now)
        parts = take(c, person_id, "studio", creditos, now)
        return parts, saldo


# ---- pacotes de creditos do Estudio (spec 37): so de R$ 100 para cima, preco de marketing em reais, creditos dolarizados e redondos
# (id, preco em reais, bonus %): o cliente paga em reais; os creditos saem do valor em dolar pela cotacao, + bonus, arredondados a 50
STUDIO_PACKS_PADRAO = (("p99", 99, 0), ("p149", 149, 5), ("p199", 199, 10), ("p499", 499, 20))
STUDIO_PACK_ARREDONDA = 50


def _arredonda_pacote(n: float) -> int:
    return max(STUDIO_PACK_ARREDONDA, int(round(n / STUDIO_PACK_ARREDONDA)) * STUDIO_PACK_ARREDONDA)


def studio_packs(store) -> list[dict]:
    """As faixas de compra. O painel pode trocar preco/bonus (setting studio_packs: [{id, preco_brl, bonus_pct}]) e fixar creditos (creditos)."""
    try:
        cfg = json.loads(store.get_setting("studio_packs") or "null")
    except ValueError:
        cfg = None
    base = [{"id": i, "preco_brl": p, "bonus_pct": b} for i, p, b in STUDIO_PACKS_PADRAO]
    if isinstance(cfg, list) and cfg:
        base = [x for x in cfg if isinstance(x, dict) and x.get("id") and isinstance(x.get("preco_brl"), (int, float)) and x["preco_brl"] >= 100 - 3]
    taxa = usd_brl_rate(store)
    ofertas = _json_setting(store, "studio_pack_offers")
    saida = []
    for x in base:
        preco = float(x["preco_brl"])
        bonus = float(x.get("bonus_pct") or 0)
        usd = preco / taxa
        cred = int(x["creditos"]) if isinstance(x.get("creditos"), int) and x["creditos"] > 0 else _arredonda_pacote(usd / STUDIO_USD * (1 + bonus / 100))
        saida.append({"id": str(x["id"]), "preco_brl": preco, "preco_usd": round(usd, 2), "bonus_pct": bonus, "creditos": cred, "oferta": ofertas.get(str(x["id"])) or None})
    return sorted(saida, key=lambda p: p["preco_brl"])


def studio_pack_of(store, product_id: str, hint: str, amount_cents) -> dict | None:
    """Qual faixa foi comprada: pelo id do produto (studio_pack_products), pelo numero de creditos no nome, ou pelo valor pago (o mais perto, ate 20%)."""
    import re
    packs = studio_packs(store)
    if not packs:
        return None
    ids = _json_setting(store, "studio_pack_products")
    for pid_pack, prod in ids.items():
        if prod and product_id and prod == product_id:
            achado = next((p for p in packs if p["id"] == pid_pack), None)
            if achado:
                return achado
    nums = [int(n.replace(".", "")) for n in re.findall(r"\d{1,3}(?:\.\d{3})+|\d{3,6}", hint or "")]
    for n in nums:
        achado = next((p for p in packs if p["creditos"] == n), None)
        if achado:
            return achado
    try:
        pago = float(amount_cents) / 100 if amount_cents else 0
    except (TypeError, ValueError):
        pago = 0
    if pago > 0:
        perto = min(packs, key=lambda p: abs(p["preco_brl"] - pago))
        if abs(perto["preco_brl"] - pago) <= perto["preco_brl"] * 0.2:
            return perto
    return None
