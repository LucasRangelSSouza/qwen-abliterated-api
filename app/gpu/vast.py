"""Cliente da API da Vast. Le SEMPRE o corpo da resposta: ligar e parar devolvem HTTP 200 mesmo quando falham
({"success": false, "error": "resources_unavailable"}), medido em 30/10/2026 (Spec 13 §3.3 regra 6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx


class VastError(Exception):
    pass


@dataclass
class PowerResult:
    ok: bool
    error: str | None = None
    msg: str | None = None

    @property
    def resources_unavailable(self) -> bool:
        return (self.error or "") == "resources_unavailable" or "resources are currently unavailable" in (self.msg or "")


@dataclass
class CreateResult:
    ok: bool
    instance_id: int | None = None
    error: str | None = None
    msg: str | None = None

    @property
    def offer_gone(self) -> bool:
        return "no_such_ask" in (self.msg or "") or "no_such_ask" in (self.error or "") or "is not available" in (self.msg or "")


@dataclass
class Instance:
    id: int
    actual_status: str | None
    intended_status: str | None
    status_msg: str | None
    ssh_host: str | None
    ssh_port: int | None
    dph_total: float
    storage_total_cost: float
    machine_id: int | None
    raw: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.actual_status == "running" and bool(self.ssh_host)


def _inst(d: dict) -> Instance:
    return Instance(id=int(d["id"]), actual_status=d.get("actual_status"), intended_status=d.get("intended_status"),
                    status_msg=d.get("status_msg"), ssh_host=d.get("ssh_host"), ssh_port=d.get("ssh_port"),
                    dph_total=float(d.get("dph_total") or 0), storage_total_cost=float(d.get("storage_total_cost") or 0),
                    machine_id=d.get("machine_id"), raw=d)


class VastAPI(Protocol):
    async def search_offers(self) -> list[dict]: ...
    async def create_instance(self, offer_id: int, image: str, disk_gb: int, label: str, onstart: str, env: dict) -> CreateResult: ...
    async def get_instance(self, instance_id: int) -> Instance | None: ...
    async def list_instances(self) -> list[Instance]: ...
    async def power(self, instance_id: int, state: str) -> PowerResult: ...
    async def destroy(self, instance_id: int) -> bool: ...
    async def credit(self) -> float: ...
    async def ssh_keys(self) -> list[str]: ...


class RealVast:
    def __init__(self, api_key: str, base: str, gpu_name: str, num_gpus: int, timeout: float = 60.0):
        self.base, self.gpu_name, self.num_gpus = base.rstrip("/"), gpu_name, num_gpus
        self._h = {"Authorization": f"Bearer {api_key}"}
        self._c = httpx.AsyncClient(timeout=timeout)

    async def _req(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for i in range(4):  # 429/5xx e falhas de rede transitorias
            try:
                r = await self._c.request(method, self.base + path, headers=self._h, **kw)
                if r.status_code in (429, 500, 502, 503):
                    await _sleep(min(8, 2 * (i + 1)))
                    continue
                return r
            except httpx.TransportError as e:
                last = e
                await _sleep(min(8, 2 * (i + 1)))
        raise VastError(f"Vast inacessivel: {last!r}")

    @staticmethod
    def _body(r: httpx.Response) -> dict:
        try:
            j = r.json()
            return j if isinstance(j, dict) else {"data": j}
        except Exception:  # noqa: BLE001
            return {"raw": r.text[:300]}

    async def search_offers(self) -> list[dict]:
        q = {"verified": {"in": [True, False]}, "external": {"in": [True, False]}, "rentable": {"eq": True}, "type": "on-demand",
             "limit": 400, "order": [["dph_total", "asc"]], "num_gpus": {"eq": self.num_gpus},
             "gpu_name": {"in": [self.gpu_name]}}
        r = await self._req("POST", "/v0/bundles/", json=q)
        if r.status_code != 200:
            raise VastError(f"busca falhou: HTTP {r.status_code} {r.text[:200]}")
        return list(self._body(r).get("offers", []))

    async def create_instance(self, offer_id: int, image: str, disk_gb: int, label: str, onstart: str, env: dict) -> CreateResult:
        body = {"client_id": "me", "image": image, "disk": disk_gb, "label": label, "runtype": "ssh_direct", "onstart": onstart, "env": env}
        r = await self._req("PUT", f"/v0/asks/{offer_id}/", json=body)
        b = self._body(r)
        iid = b.get("new_contract")
        if r.status_code == 200 and b.get("success") and iid:
            return CreateResult(True, int(iid))
        return CreateResult(False, None, str(b.get("error")), str(b.get("msg") or b.get("raw") or r.text[:200]))

    async def list_instances(self) -> list[Instance]:
        r = await self._req("GET", "/v1/instances/")
        if r.status_code != 200:
            raise VastError(f"listar instancias: HTTP {r.status_code}")
        return [_inst(x) for x in self._body(r).get("instances", [])]

    async def get_instance(self, instance_id: int) -> Instance | None:
        for i in await self.list_instances():
            if i.id == instance_id:
                return i
        return None

    async def power(self, instance_id: int, state: str) -> PowerResult:
        r = await self._req("PUT", f"/v0/instances/{instance_id}/", json={"state": state})
        b = self._body(r)
        ok = r.status_code == 200 and bool(b.get("success"))
        return PowerResult(ok, None if ok else str(b.get("error")), None if ok else str(b.get("msg") or b.get("raw") or ""))

    async def destroy(self, instance_id: int) -> bool:
        r = await self._req("DELETE", f"/v0/instances/{instance_id}/")
        return r.status_code == 200 and bool(self._body(r).get("success", True))

    async def credit(self) -> float:
        r = await self._req("GET", "/v0/users/current/")
        return float(self._body(r).get("credit") or 0.0)

    async def ssh_keys(self) -> list[str]:
        r = await self._req("GET", "/v0/ssh/")
        j = r.json() if r.status_code == 200 else []
        ks = j if isinstance(j, list) else j.get("ssh_keys", [])
        return [str(k.get("public_key") or "") for k in ks if isinstance(k, dict)]


async def _sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)
