"""Cota mensal de midia por plano: imagens, videos e audios. A conta do plano e feita para o custo da GPU ficar abaixo de 1/3 da mensalidade (docs/PRECIFICACAO_MIDIA.md §7).
Limite NULL = plano ainda sem cota configurada (nao bloqueia); 0 = o plano nao inclui. Consumo no mes civil (UTC); a reserva e gravada antes de repassar e desfeita se o pedido falha."""
from __future__ import annotations

import math
from datetime import datetime, timezone

KINDS = ("image", "video", "audio")
COLUMN = {"image": "image_quota_month", "video": "video_quota_month", "audio": "audio_quota_month"}
LABEL = {"image": "imagens", "video": "vídeos", "audio": "áudios"}
CLIP_S = 5.0          # 1 unidade de video = 1 clipe de 5 s em 480p
ALTA_UNITS = 3        # clipe em 720p custa ~2,9x o de 480p (GPU medida): conta 3 unidades
SWAP_RECIPES = ("openjutsu-swap",)


def month_bounds(now: float) -> tuple[float, float]:
    d = datetime.fromtimestamp(now, timezone.utc)
    ini = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fim = ini.replace(year=ini.year + (ini.month == 12), month=ini.month % 12 + 1)
    return ini.timestamp(), fim.timestamp()


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def units_for(route: str, path: str, body: dict) -> int:
    """Quantas unidades da cota o pedido consome. Estimativa pelo corpo do pedido (a normalizacao fina e do llm-queue)."""
    if route == "image":
        return max(1, min(4, int(_num(body.get("n"), 1))))
    if route == "audio" or path.endswith("/dub"):
        return 1
    recipe = body.get("recipe")
    clips = max(1, math.ceil(_num(body.get("seconds") or body.get("segundos"), CLIP_S) / CLIP_S))
    if recipe in SWAP_RECIPES:
        return clips
    quality = str(body.get("quality") or "auto").lower()
    alta = quality == "alta" or (quality == "auto" and clips == 1 and recipe in (None, "", "wan22-a14b"))
    return clips * (ALTA_UNITS if alta else 1)


def _quota_sql(kind: str) -> str:
    return (f"SELECT p.{COLUMN[kind]} q FROM accounts a JOIN subscriptions s ON s.account_id=a.id JOIN plans p ON p.id=s.plan "
            "WHERE a.person_id=? ORDER BY s.updated_at DESC, s.id DESC LIMIT 1")


def _used(c, person_id: int, kind: str, since: float) -> int:
    return int(c.execute("SELECT COALESCE(SUM(units),0) u FROM media_usage WHERE person_id=? AND kind=? AND ts>=?", (person_id, kind, since)).fetchone()["u"])


def view(store, person_id: int, now: float) -> dict:
    ini, fim = month_bounds(now)
    resets = datetime.fromtimestamp(fim, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out = {}
    for k in KINDS:
        r = store.one(_quota_sql(k), (person_id,))
        q = None if r is None or r["q"] is None else int(r["q"])
        u = int(store.one("SELECT COALESCE(SUM(units),0) u FROM media_usage WHERE person_id=? AND kind=? AND ts>=?", (person_id, k, ini))["u"])
        out[k] = {"limit": q, "used": u, "remaining": None if q is None else max(0, q - u), "resets_at": resets}
    return out


class QuotaExceeded(Exception):
    def __init__(self, kind: str, limit: int, used: int, resets_at: float):
        super().__init__(kind)
        self.kind, self.limit, self.used, self.resets_at = kind, limit, used, resets_at


def reserve(store, person_id: int, kind: str, units: int, now: float) -> int | None:
    """Confere e grava o consumo numa transacao so. Devolve o id da linha (para desfazer) ou None se o plano nao tem cota."""
    ini, fim = month_bounds(now)
    with store.tx() as c:
        r = c.execute(_quota_sql(kind), (person_id,)).fetchone()
        if r is None or r["q"] is None:
            return None
        gasto = _used(c, person_id, kind, ini)
        if gasto + units > int(r["q"]):
            raise QuotaExceeded(kind, int(r["q"]), gasto, fim)
        return c.execute("INSERT INTO media_usage(ts,person_id,kind,units) VALUES(?,?,?,?)", (now, person_id, kind, units)).lastrowid


def undo(store, row_id: int | None) -> None:
    if row_id:
        with store.tx() as c:
            c.execute("DELETE FROM media_usage WHERE id=?", (row_id,))
