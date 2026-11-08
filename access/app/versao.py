"""Versão mais recente do app, SEM depender da API do GitHub (que dá 504 e limita 60 consultas por hora por IP).
A versão vem do redirecionamento da página normal /releases/latest (github.com, que segue de pé quando a API cai) e os endereços de download são
os fixos de /releases/download/<tag>/<nome>. Guardamos a última resposta boa: se o GitHub falhar, devolvemos a anterior em vez de erro.
Quem usa: o site (rótulo e links de download), a VM do cliente (atualizador diário) e qualquer instalação que queira saber se há versão nova."""
from __future__ import annotations

import re
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

REPO = "RangelTech/second-brain"
TTL_S = 600               # confere o GitHub no máximo a cada 10 min
VELHO_MAX_S = 14 * 86400  # com o GitHub fora do ar, a última resposta boa vale por até 14 dias
ARQUIVOS = {
    "win-setup": "Segundo-Cerebro-Windows.exe", "win-portable": "Segundo-Cerebro-Windows-Portatil.exe",
    "mac-arm": "Segundo-Cerebro-macOS-arm64.dmg", "mac-intel": "Segundo-Cerebro-macOS-x64.dmg",
    "linux-appimage": "Segundo-Cerebro-Linux.AppImage", "linux-deb": "Segundo-Cerebro-Linux.deb",
}
_TAG = re.compile(r"/releases/tag/v?([0-9][0-9A-Za-z.\-]*)$")


async def _buscar_github(client: httpx.AsyncClient) -> str | None:
    """Versão pelo redirecionamento de https://github.com/<repo>/releases/latest (nada de API)."""
    r = await client.get(f"https://github.com/{REPO}/releases/latest", follow_redirects=False, timeout=15)
    m = _TAG.search(r.headers.get("location", ""))
    return m.group(1) if m else None


def _resposta(versao: str, atualizado: float, velho: bool) -> dict:
    base = f"https://github.com/{REPO}/releases/download/v{versao}/"
    return {"version": versao, "tag": f"v{versao}", "assets": {k: base + n for k, n in ARQUIVOS.items()}, "checked_at": int(atualizado), "stale": velho}


@router.get("/v1/app/latest")
async def ultima_versao(request: Request):
    ctx = request.app.state.ctx
    agora = ctx.clock()
    c = getattr(request.app.state, "versao_cache", None)
    if c and agora - c["t"] < TTL_S:
        return JSONResponse(_resposta(c["v"], c["t"], False), headers={"Cache-Control": "public, max-age=300"})
    try:
        v = await _buscar_github(_cliente(request))
    except Exception:  # noqa: BLE001  GitHub fora do ar: cai para a última boa
        v = None
    if v:
        request.app.state.versao_cache = {"v": v, "t": agora}
        try:
            ctx.store.set_setting("app_latest", f"{v}|{int(agora)}")  # sobrevive a reinicio com o GitHub fora do ar
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(_resposta(v, agora, False), headers={"Cache-Control": "public, max-age=300"})
    if not c:
        try:
            salvo = (ctx.store.get_setting("app_latest") or "").split("|")
            if len(salvo) == 2:
                c = request.app.state.versao_cache = {"v": salvo[0], "t": float(salvo[1])}
        except Exception:  # noqa: BLE001
            c = None
    if c and agora - c["t"] < VELHO_MAX_S:
        return JSONResponse(_resposta(c["v"], c["t"], True), headers={"Cache-Control": "public, max-age=60"})
    return JSONResponse({"error": {"message": "versao indisponivel agora", "type": "service_unavailable", "code": "version_unavailable"}}, status_code=503)


def _cliente(request: Request) -> httpx.AsyncClient:
    cl = getattr(request.app.state, "versao_http", None)
    if cl is None:
        cl = request.app.state.versao_http = httpx.AsyncClient(headers={"User-Agent": "segundo-cerebro-versao/1"})
    return cl
