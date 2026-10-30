"""API do painel (/admin/api/*) e entrega do painel estatico em /admin."""
from __future__ import annotations

import functools
import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.auth import COOKIE, new_api_key, sha256
from app.ctx import Ctx, fail
from app.ledger import KINDS, iso, view

api = APIRouter(prefix="/admin/api")
static = APIRouter()


def _ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def require(request: Request) -> Ctx:
    ctx = _ctx(request)
    if not ctx.admin.valid_cookie(request.cookies.get(COOKIE)):
        raise fail(401, "login necessario", "invalid_request_error", "unauthorized")
    return ctx


def _db(fn):
    """Traduz falha do SQLite em 503 em vez de 500."""
    def down():
        return fail(503, "banco indisponivel", "service_unavailable", "database_unavailable")

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrap(*a, **k):
            try:
                return await fn(*a, **k)
            except sqlite3.Error:
                raise down()
        return awrap

    @functools.wraps(fn)
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except sqlite3.Error:
            raise down()
    return wrap


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")


def _set_cookie(request: Request, resp: Response, ctx: Ctx) -> None:
    secure = request.headers.get("x-forwarded-proto", "").lower() == "https" or request.url.scheme == "https"
    resp.set_cookie(COOKIE, ctx.admin.make_cookie(), max_age=int(ctx.s.session_ttl_s), httponly=True, samesite="lax",
                    secure=secure, path="/")


async def _json(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = None
    if not isinstance(b, dict):
        raise fail(400, "JSON invalido", "invalid_request_error", "invalid_json")
    return b


@api.post("/login")
async def login(request: Request):
    ctx = _ctx(request)
    wait = ctx.admin.throttled(_ip(request))
    if wait:
        raise fail(429, "tentativas demais; aguarde um minuto", "rate_limit_error", "too_many_attempts", headers={"Retry-After": str(wait)})
    b = await _json(request)
    if not ctx.admin.verify(str(b.get("password", ""))):
        raise fail(401, "senha invalida", "invalid_request_error", "invalid_credentials")
    ctx.admin.forget(_ip(request))
    resp = JSONResponse({"ok": True})
    _set_cookie(request, resp, ctx)
    return resp


@api.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@api.get("/me")
async def me(request: Request):
    require(request)
    return {"ok": True, "user": "admin"}


# ---- pessoas

_PERSON_SQL = (
    "SELECT p.id, p.name, p.note, p.status, l.session_limit_tokens sl, l.week_limit_tokens wl, l.session_seconds ss, l.week_seconds ws,"
    " (SELECT key_last4 FROM keys k WHERE k.person_id=p.id AND k.revoked_at IS NULL ORDER BY k.id DESC LIMIT 1) key_last4,"
    " (SELECT MAX(ts) FROM usage_log u WHERE u.person_id=p.id AND u.status IN ('ok','cut')) last_used"
    " FROM people p JOIN limits l ON l.person_id=p.id")


def _person_view(ctx: Ctx, r, windows: dict) -> dict:
    now = ctx.clock()
    return {
        "id": r["id"], "name": r["name"], "note": r["note"], "status": r["status"], "key_last4": r["key_last4"],
        "limits": {"session_limit_tokens": r["sl"], "week_limit_tokens": r["wl"], "session_seconds": r["ss"], "week_seconds": r["ws"]},
        **{k: {kk: vv for kk, vv in view(windows.get(k), r["sl" if k == "session" else "wl"], now).items() if kk != "remaining"} for k in KINDS},
        "last_used_at": iso(r["last_used"]),
    }


def _people(ctx: Ctx, where: str = "", args: tuple = ()) -> list[dict]:
    rows = ctx.store.query(_PERSON_SQL + where + " ORDER BY p.name COLLATE NOCASE", args)
    wins: dict = {}
    for w in ctx.store.query("SELECT * FROM windows"):
        wins.setdefault(w["person_id"], {})[w["kind"]] = w
    return [_person_view(ctx, r, wins.get(r["id"], {})) for r in rows]


def _get_person(ctx: Ctx, pid: int) -> dict:
    out = _people(ctx, " WHERE p.id=?", (pid,))
    if not out:
        raise fail(404, "pessoa nao encontrada", "invalid_request_error", "not_found")
    return out[0]


def _limit(v, name: str):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise fail(422, f"{name} deve ser inteiro positivo ou null", "invalid_request_error", "invalid_limit")
    return v


def _seconds(ctx: Ctx, v, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < ctx.s.effective_min_window:
        raise fail(422, f"{name} deve ser inteiro >= {ctx.s.effective_min_window} s", "invalid_request_error", "invalid_window")
    return v


@api.get("/people")
@_db
def people_list(request: Request, q: str = ""):
    ctx = require(request)
    q = q.strip()
    if not q:
        return _people(ctx)
    like = f"%{q}%"
    return _people(ctx, " WHERE p.name LIKE ? OR p.note LIKE ?", (like, like))


@api.post("/people")
@_db
async def people_create(request: Request):
    ctx = require(request)
    b = await _json(request)
    name = str(b.get("name", "")).strip()
    if not name:
        raise fail(422, "name obrigatorio", "invalid_request_error", "invalid_name")
    sl, wl = _limit(b.get("session_limit_tokens"), "session_limit_tokens"), _limit(b.get("week_limit_tokens"), "week_limit_tokens")
    ss = _seconds(ctx, b.get("session_seconds", ctx.s.default_session_seconds), "session_seconds")
    ws = _seconds(ctx, b.get("week_seconds", ctx.s.default_week_seconds), "week_seconds")
    key = new_api_key()
    with ctx.store.tx() as c:
        pid = c.execute("INSERT INTO people(name,note,created_at) VALUES(?,?,?)", (name, str(b.get("note", "")), ctx.clock())).lastrowid
        c.execute("INSERT INTO limits(person_id,session_limit_tokens,week_limit_tokens,session_seconds,week_seconds) VALUES(?,?,?,?,?)",
                  (pid, sl, wl, ss, ws))
        c.execute("INSERT INTO keys(person_id,key_hash,key_last4,created_at) VALUES(?,?,?,?)", (pid, sha256(key), key[-4:], ctx.clock()))
    return {"person": _get_person(ctx, pid), "api_key": key}


@api.patch("/people/{pid}")
@_db
async def people_patch(pid: int, request: Request):
    ctx = require(request)
    b = await _json(request)
    _get_person(ctx, pid)
    with ctx.store.tx() as c:
        if "name" in b:
            name = str(b["name"]).strip()
            if not name:
                raise fail(422, "name nao pode ser vazio", "invalid_request_error", "invalid_name")
            c.execute("UPDATE people SET name=? WHERE id=?", (name, pid))
        if "note" in b:
            c.execute("UPDATE people SET note=? WHERE id=?", (str(b["note"]), pid))
        for f in ("session_limit_tokens", "week_limit_tokens"):
            if f in b:
                c.execute(f"UPDATE limits SET {f}=? WHERE person_id=?", (_limit(b[f], f), pid))
        for f in ("session_seconds", "week_seconds"):
            if f in b:
                c.execute(f"UPDATE limits SET {f}=? WHERE person_id=?", (_seconds(ctx, b[f], f), pid))
    return _get_person(ctx, pid)


def _set_status(ctx: Ctx, pid: int, status: str) -> dict:
    _get_person(ctx, pid)
    with ctx.store.tx() as c:
        c.execute("UPDATE people SET status=? WHERE id=?", (status, pid))
    return _get_person(ctx, pid)


@api.post("/people/{pid}/suspend")
@_db
def suspend(pid: int, request: Request):
    return _set_status(require(request), pid, "suspended")


@api.post("/people/{pid}/resume")
@_db
def resume(pid: int, request: Request):
    return _set_status(require(request), pid, "active")


@api.post("/people/{pid}/rotate-key")
@_db
def rotate_key(pid: int, request: Request):
    ctx = require(request)
    _get_person(ctx, pid)
    key = new_api_key()
    now = ctx.clock()
    with ctx.store.tx() as c:  # a chave antiga para na hora: revogada na mesma transacao que cria a nova
        c.execute("UPDATE keys SET revoked_at=? WHERE person_id=? AND revoked_at IS NULL", (now, pid))
        c.execute("INSERT INTO keys(person_id,key_hash,key_last4,created_at) VALUES(?,?,?,?)", (pid, sha256(key), key[-4:], now))
    return {"api_key": key}


@api.post("/people/{pid}/reset-windows")
@_db
def reset_windows(pid: int, request: Request):
    ctx = require(request)
    _get_person(ctx, pid)
    with ctx.store.tx() as c:
        c.execute("DELETE FROM windows WHERE person_id=?", (pid,))
    return _get_person(ctx, pid)


@api.post("/reset-all-windows")
@_db
def reset_all(request: Request):
    ctx = require(request)
    with ctx.store.tx() as c:
        n = c.execute("DELETE FROM windows").rowcount
    return {"ok": True, "windows_cleared": n}


# ---- consumo

@api.get("/usage")
@_db
def usage_list(request: Request, person_id: int | None = None, day: str | None = None, limit: int = 100, offset: int = 0):
    ctx = require(request)
    where, args = [], []
    if person_id is not None:
        where.append("u.person_id=?")
        args.append(person_id)
    if day:
        try:
            d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise fail(422, "day deve ser YYYY-MM-DD", "invalid_request_error", "invalid_day")
        where.append("u.ts >= ? AND u.ts < ?")
        args += [d0.timestamp(), (d0 + timedelta(days=1)).timestamp()]
    w = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(limit, 1000))
    total = ctx.store.one("SELECT COUNT(*) n FROM usage_log u" + w, tuple(args))["n"]
    rows = ctx.store.query(
        "SELECT u.*, p.name FROM usage_log u LEFT JOIN people p ON p.id=u.person_id" + w + " ORDER BY u.ts DESC, u.id DESC LIMIT ? OFFSET ?",
        tuple(args) + (limit, max(0, offset)))
    return {"total": total, "rows": [
        {"ts": iso(r["ts"]), "person_id": r["person_id"], "name": r["name"] or r["actor"], "route": r["route"],
         "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"], "total_tokens": r["total_tokens"], "status": r["status"],
         "limits": json.loads(r["limits_json"]) if r["limits_json"] else {"session_limit_tokens": None, "week_limit_tokens": None}}
        for r in rows]}


# ---- maquinas (vem do llm-queue)

@api.get("/machines")
async def machines(request: Request, limit: int = 50):
    ctx = require(request)
    base, errors = ctx.s.upstream_base, []
    status, cycles = None, []
    try:
        r = await ctx.http.get(base + "/v1/gpu/status", headers={"Authorization": f"Bearer {ctx.s.upstream_key}"}, timeout=10)
        r.raise_for_status()
        j = r.json()
        status = {"state": j.get("state"), "eta_s": j.get("eta_s"), "spend": j.get("spend"), "path": j.get("path"),
                  "instance": j.get("instance"), "attempts": j.get("attempts") or [], "limits": j.get("limits"), "fail": j.get("fail")}
    except Exception as e:  # noqa: BLE001
        errors.append(f"status da GPU indisponivel ({type(e).__name__})")
    try:
        r = await ctx.http.get(base + "/admin/gpu/cycles", params={"limit": max(1, min(limit, 500))},
                               headers={"X-Admin-Key": ctx.s.upstream_admin_key}, timeout=10)
        r.raise_for_status()
        j = r.json()
        cycles = j.get("cycles", []) if isinstance(j, dict) else j
    except Exception as e:  # noqa: BLE001
        errors.append(f"ciclos da GPU indisponiveis ({type(e).__name__})")
    return {"status": status, "cycles": cycles, "error": "; ".join(errors) or None}


@api.get("/queue")
async def queue(request: Request):
    """Fila de pedidos (quem espera, quem roda, ultimos encerrados) lida do llm-queue; so metadados. O painel consulta de poucos em poucos segundos."""
    ctx = require(request)
    try:
        r = await ctx.http.get(ctx.s.upstream_base + "/admin/gpu/jobs", headers={"X-Admin-Key": ctx.s.upstream_admin_key}, timeout=8)
        r.raise_for_status()
        return {**r.json(), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"gpu_state": None, "lanes": {}, "waiting": [], "running": [], "recent": [], "error": f"fila indisponivel ({type(e).__name__})"}


# ---- configuracao

@api.get("/settings")
async def settings(request: Request):
    ctx = require(request)
    s = ctx.s
    queue = None
    try:  # tetos e ociosidade vivem no queue; mostramos o que ele expuser
        r = await ctx.http.get(s.upstream_base + "/v1/gpu/status", headers={"Authorization": f"Bearer {s.upstream_key}"}, timeout=5)
        j = r.json()
        queue = {"spend": j.get("spend"), "idle_min": (j.get("limits") or {}).get("idle_min"), "limits": j.get("limits"), "state": j.get("state")}
    except Exception:  # noqa: BLE001
        pass
    return {"qa_mode": s.qa_mode, "min_window_seconds": s.effective_min_window, "default_session_seconds": s.default_session_seconds,
            "default_week_seconds": s.default_week_seconds, "default_max_tokens": s.default_max_tokens,
            "retention_days": s.retention_days, "upstream_url": s.upstream_url, "queue": queue}


@api.post("/password")
@_db
async def password(request: Request):
    ctx = require(request)
    b = await _json(request)
    if not ctx.admin.verify(str(b.get("current", ""))):
        raise fail(401, "senha atual invalida", "invalid_request_error", "invalid_credentials")
    new = str(b.get("new", ""))
    if len(new) < 8:
        raise fail(422, "a nova senha precisa de pelo menos 8 caracteres", "invalid_request_error", "weak_password")
    ctx.admin.set_password(new)
    resp = JSONResponse({"ok": True})
    _set_cookie(request, resp, ctx)  # a sessao atual segue valida; as outras caem
    return resp


# ---- painel estatico (SPA)

NO_PANEL = ("<!doctype html><meta charset=utf-8><title>llm-access</title><body style='font-family:sans-serif;max-width:40rem;margin:4rem auto'>"
            "<h1>Painel nao construido</h1><p>O servico esta no ar, mas <code>panel/dist</code> nao existe nesta imagem. "
            "A API do painel (<code>/admin/api/*</code>) funciona normalmente.</p></body>")


@static.get("/admin")
@static.get("/admin/{path:path}")
async def panel(request: Request, path: str = ""):
    if path.startswith("api/") or path == "api":
        raise fail(404, "rota inexistente", "invalid_request_error", "not_found")
    root = Path(_ctx(request).s.panel_dir)
    index = root / "index.html"
    if not index.is_file():
        return HTMLResponse(NO_PANEL)
    f = (root / path).resolve() if path else index
    if path and f.is_file() and root.resolve() in f.parents:
        return FileResponse(f)
    return FileResponse(index)
