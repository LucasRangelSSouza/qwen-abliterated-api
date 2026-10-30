"""Tunel SSH da VPS para a GPU (Spec 13 §3.3). Os consumidores so enxergam o llm-queue; o endereco da Vast nunca e fixo:
a cada partida o controlador le ssh_host/ssh_port na API e abre um tunel novo para as portas locais fixas."""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Protocol

log = logging.getLogger("llm-queue.tunnel")


class Tunnel(Protocol):
    async def open(self, host: str, port: int) -> bool: ...
    async def healthy(self) -> bool: ...
    async def close(self) -> None: ...


class NoTunnel:
    """Sem tunel (testes e desenvolvimento): o upstream ja esta acessivel."""
    def __init__(self):
        self.opened: list[tuple[str, int]] = []

    async def open(self, host: str, port: int) -> bool:
        self.opened.append((host, port))
        return True

    async def healthy(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class SshTunnel:
    def __init__(self, key_path: str, forwards: list[tuple[int, int]], ssh_bin: str | None = None, user: str = "root"):
        self.key_path, self.forwards, self.user = key_path, forwards, user
        self.ssh_bin = ssh_bin or shutil.which("ssh") or "ssh"
        self.proc: asyncio.subprocess.Process | None = None
        self.target: tuple[str, int] | None = None

    def _cmd(self, host: str, port: int) -> list[str]:
        cmd = [self.ssh_bin, "-N", "-i", self.key_path, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=15", "-p", str(port)]
        for local, remote in self.forwards:
            cmd += ["-L", f"127.0.0.1:{local}:127.0.0.1:{remote}"]
        return cmd + [f"{self.user}@{host}"]

    async def open(self, host: str, port: int) -> bool:
        await self.close()
        self.proc = await asyncio.create_subprocess_exec(*self._cmd(host, port), stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.PIPE)
        self.target = (host, port)
        # o ssh sai sozinho se a porta nao abrir (ExitOnForwardFailure) ou a chave for recusada
        for _ in range(12):
            await asyncio.sleep(0.5)
            if self.proc.returncode is not None:
                err = (await self.proc.stderr.read()).decode(errors="replace")[:200] if self.proc.stderr else ""
                log.warning("ssh saiu (%s): %s", self.proc.returncode, err.strip())
                self.proc = None
                return False
            if await self._ports_open():
                return True
        return False

    async def _ports_open(self) -> bool:
        for local, _ in self.forwards:
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", local), 2.0)
                w.close()
            except Exception:  # noqa: BLE001
                return False
        return True

    async def healthy(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and await self._ports_open()

    async def close(self) -> None:
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 5.0)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
