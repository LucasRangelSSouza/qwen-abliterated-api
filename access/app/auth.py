"""Autenticacao: chave de API (so SHA-256) e sessao do painel (cookie assinado com HMAC)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass

COOKIE = "llm_access_session"
PBKDF_ITER = 100_000


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def bearer(headers) -> str:
    h = headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


@dataclass
class Principal:
    kind: str                    # person | owner | platform
    person_id: int | None = None
    name: str = ""
    key_id: int | None = None
    status: str = "active"


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF_ITER)


class AdminAuth:
    """Senha do admin: na memoria so salt + derivado. Troca pelo painel fica no banco (settings.admin_pw) e vale mais que o arquivo."""

    def __init__(self, password: str, session_secret: str, store, clock, ttl_s: float, max_attempts: int, window_s: float):
        self.store, self.clock, self.ttl_s = store, clock, ttl_s
        self.max_attempts, self.window_s = max_attempts, window_s
        self.secret = session_secret.encode() if session_secret else os.urandom(32)
        self.enabled = bool(password)
        self.salt = os.urandom(16)
        self.hash = _derive(password, self.salt) if password else b""
        self._override_loaded = False
        self._attempts: dict[str, deque] = defaultdict(deque)

    def _load_override(self) -> None:
        if self._override_loaded:
            return
        try:
            v = self.store.get_setting("admin_pw")
        except Exception:  # noqa: BLE001  banco indisponivel: tenta de novo na proxima
            return
        self._override_loaded = True
        if v:
            salt, h = v.split("$")
            self.salt, self.hash, self.enabled = bytes.fromhex(salt), bytes.fromhex(h), True

    def verify(self, password: str) -> bool:
        self._load_override()
        if not self.enabled:
            return False
        return hmac.compare_digest(_derive(password, self.salt), self.hash)

    def set_password(self, new: str) -> None:
        salt = os.urandom(16)
        h = _derive(new, salt)
        self.store.set_setting("admin_pw", salt.hex() + "$" + h.hex())
        self.salt, self.hash, self.enabled, self._override_loaded = salt, h, True, True

    def throttled(self, ip: str) -> int | None:
        """None se pode tentar; senao segundos ate liberar. Conta toda tentativa (certa ou errada)."""
        now = self.clock()
        q = self._attempts[ip]
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.max_attempts:
            return max(1, int(self.window_s - (now - q[0])) + 1)
        q.append(now)
        return None

    def forget(self, ip: str) -> None:
        self._attempts.pop(ip, None)

    def _sig(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def make_cookie(self) -> str:
        payload = f"{int(self.clock() + self.ttl_s)}:{self.hash.hex()[:12]}"
        return base64.urlsafe_b64encode(payload.encode()).decode() + "." + self._sig(payload)

    def valid_cookie(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        b, sig = token.rsplit(".", 1)
        try:
            payload = base64.urlsafe_b64decode(b.encode()).decode()
            exp, pv = payload.split(":")
        except Exception:  # noqa: BLE001
            return False
        self._load_override()
        return (hmac.compare_digest(sig, self._sig(payload)) and float(exp) > self.clock()
                and hmac.compare_digest(pv, self.hash.hex()[:12]))
