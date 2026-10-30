"""Vast simulada: reproduz as situacoes A a E da Spec 13 §5.3 sem gastar nada."""
from __future__ import annotations

import json
from pathlib import Path

from app.gpu.vast import CreateResult, Instance, PowerResult

DATA = Path(__file__).parent / "data" / "offers_2x3090_2026-10-30.json"


def real_offers() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


class FakeVast:
    def __init__(self, offers: list[dict] | None = None, credit: float = 50.0, boot_polls: int = 2):
        self.offers = offers if offers is not None else real_offers()
        self.credit_v = credit
        self.boot_polls = boot_polls
        self.instances: dict[int, dict] = {}
        self.next_id = 1000
        self.create_script: dict[int, str] = {}  # offer_id -> ok | gone | cdi | stuck | registry
        self.power_script: list[str] = []         # fila de resultados do power(running): ok | unavailable
        self.calls: list[tuple] = []
        self.keys = ["ssh-ed25519 AAAA-fake-key"]

    # --- API
    async def search_offers(self) -> list[dict]:
        self.calls.append(("search",))
        return [dict(o) for o in self.offers]

    async def create_instance(self, offer_id, image, disk_gb, label, onstart, env) -> CreateResult:
        mode = self.create_script.get(offer_id, "ok")
        self.calls.append(("create", offer_id, mode))
        if mode == "gone":
            return CreateResult(False, None, "invalid_args", f"error 404/3603: no_such_ask  Instance type by id {offer_id} is not available.")
        o = next(x for x in self.offers if x["id"] == offer_id)
        iid = self.next_id
        self.next_id += 1
        sc = o.get("storage_cost") or 0.2
        self.instances[iid] = {"id": iid, "mode": mode, "polls": 0, "actual_status": "loading", "intended_status": "running",
                               "status_msg": "", "ssh_host": None, "ssh_port": None, "machine_id": o["machine_id"],
                               "dph_total": o["dph_total"] + sc * disk_gb / 730.0, "storage_total_cost": sc * disk_gb / 730.0,
                               "label": label, "env": env, "onstart": onstart}
        return CreateResult(True, iid)

    def _progress(self, d: dict) -> None:
        if d["intended_status"] != "running" or d["actual_status"] in ("running", "exited"):
            return
        d["polls"] += 1
        if d["mode"] == "cdi":
            d["actual_status"] = "created"
            d["status_msg"] = ("Error response from daemon: failed to create task for container: OCI runtime create failed: "
                               "error modifying OCI spec: failed to inject CDI devices: unresolvable CDI devices x/gpu=1")
        elif d["mode"] == "registry":
            d["status_msg"] = 'Error response from daemon: Head "https://registry-1.docker.io/v2/vastai/vllm/manifests/x": Get "https://auth.docker.io/token": timeout'
        elif d["mode"] == "stuck":
            pass
        elif d["polls"] >= self.boot_polls:
            d["actual_status"], d["ssh_host"], d["ssh_port"] = "running", "ssh.fake", 20000 + d["id"] % 1000

    def _inst(self, d: dict) -> Instance:
        return Instance(id=d["id"], actual_status=d["actual_status"], intended_status=d["intended_status"], status_msg=d["status_msg"],
                        ssh_host=d["ssh_host"], ssh_port=d["ssh_port"], dph_total=d["dph_total"], storage_total_cost=d["storage_total_cost"],
                        machine_id=d["machine_id"], raw=dict(d))

    async def get_instance(self, iid: int) -> Instance | None:
        d = self.instances.get(iid)
        if d is None:
            return None
        self._progress(d)
        return self._inst(d)

    async def list_instances(self) -> list[Instance]:
        return [self._inst(d) for d in self.instances.values()]

    async def power(self, iid: int, state: str) -> PowerResult:
        self.calls.append(("power", iid, state))
        d = self.instances.get(iid)
        if d is None:
            return PowerResult(False, "not_found", "sem instancia")
        if state == "stopped":
            d["intended_status"], d["actual_status"], d["ssh_host"], d["ssh_port"] = "stopped", "exited", None, None
            return PowerResult(True)
        verdict = self.power_script.pop(0) if self.power_script else "ok"
        if verdict == "unavailable":
            return PowerResult(False, "resources_unavailable", "Required resources are currently unavailable, state change queued.")
        d["intended_status"], d["actual_status"], d["polls"], d["mode"] = "running", "loading", 0, "ok"
        return PowerResult(True)

    async def destroy(self, iid: int) -> bool:
        self.calls.append(("destroy", iid))
        return self.instances.pop(iid, None) is not None

    async def credit(self) -> float:
        return self.credit_v

    async def ssh_keys(self) -> list[str]:
        return self.keys

    # --- ajudantes de teste
    def count(self, kind: str) -> int:
        return sum(1 for c in self.calls if c[0] == kind)

    def running_ids(self) -> list[int]:
        return [i for i, d in self.instances.items() if d["actual_status"] == "running"]
