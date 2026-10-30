"""Estimativa de tokens (~3 caracteres/token) e leitura de `usage` na resposta do upstream. So conta; nunca guarda texto."""
from __future__ import annotations

import json

IMAGE_CHARS = 4500   # uma imagem pesa ~1500 tokens; evita contar o base64 inteiro
MAX_JSON_BYTES = 32 * 1024 * 1024


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                n += len(str(p.get("text", "")))
            elif isinstance(p, dict) and p.get("type") in ("image_url", "input_image"):
                n += IMAGE_CHARS
            else:
                n += len(json.dumps(p, ensure_ascii=False))
        return n
    return 0


def estimate_in(body: dict, route: str) -> int:
    if route == "embed":
        inp = body.get("input", "")
        items = inp if isinstance(inp, list) else [inp]
        return sum(len(x) if isinstance(x, str) else len(json.dumps(x)) for x in items) // 3 + 1
    msgs = body.get("messages") or []
    n = sum(_chars(m.get("content")) + (len(json.dumps(m["tool_calls"])) if m.get("tool_calls") else 0) for m in msgs if isinstance(m, dict))
    if body.get("tools"):
        n += len(json.dumps(body["tools"], ensure_ascii=False))
    return n // 3 + 4 * len(msgs) + 1


def max_out(body: dict, default: int) -> int:
    v = body.get("max_tokens") or body.get("max_completion_tokens")
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


class Meter:
    """Observa o que passa pelo proxy: tem token real? chegou `usage`? quantos caracteres foram entregues?"""

    def __init__(self, stream: bool):
        self.stream = stream
        self.buf = b""
        self.raw: list[bytes] = []
        self.size = 0
        self.chars = 0
        self.real = False
        self.usage: dict | None = None
        self.done = False
        self.error = False

    def feed(self, chunk: bytes) -> None:
        if not self.stream:
            if self.size < MAX_JSON_BYTES:
                self.raw.append(chunk)
                self.size += len(chunk)
            return
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        for ln in lines:
            self._line(ln)

    def finish(self) -> None:
        if self.stream:
            self._line(self.buf)
            self.buf = b""
        elif self.size < MAX_JSON_BYTES:
            try:
                self._obj(json.loads(b"".join(self.raw).strip() or b"null"))
            except ValueError:
                pass
            self.done = True

    def _line(self, ln: bytes) -> None:
        ln = ln.strip()
        if not ln.startswith(b"data:"):
            return
        payload = ln[5:].strip()
        if payload == b"[DONE]":
            self.done = True
            return
        try:
            self._obj(json.loads(payload))
        except ValueError:
            pass

    def _obj(self, o) -> None:
        if not isinstance(o, dict):
            return
        u = o.get("usage")
        if isinstance(u, dict) and (u.get("total_tokens") or u.get("prompt_tokens")):
            self.usage = u
            self.real = True
        if "error" in o and not o.get("choices") and not self.usage:
            self.error = True
            return
        for ch in o.get("choices") or []:
            part = ch.get("delta") if self.stream else ch.get("message")
            part = part or {}
            n = len(part.get("content") or "") + len(part.get("reasoning_content") or part.get("reasoning") or "")
            for tc in part.get("tool_calls") or []:
                f = tc.get("function") or {}
                n += len(f.get("name") or "") + len(f.get("arguments") or "")
            if n:
                self.chars += n
                self.real = True
        if o.get("data"):  # embeddings
            self.real = True

    def settle(self, in_est: int, reserved: int, completed: bool) -> tuple[int, int, int, str]:
        """(in, out, total, status). Sem usage: estima pelo entregue, limitado ao reservado."""
        if self.usage:
            i = int(self.usage.get("prompt_tokens") or 0)
            t = int(self.usage.get("total_tokens") or 0) or i + int(self.usage.get("completion_tokens") or 0)
            return i, max(0, t - i), t, "ok"
        out = self.chars // 3 + (1 if self.chars else 0)
        total = min(reserved, in_est + out)
        return in_est, max(0, total - in_est), total, "ok" if completed else "cut"
