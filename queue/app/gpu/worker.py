"""Worker de video sob demanda (serverless proprio): aluga uma RTX PRO 6000 na Vast so quando ha pedido de video de alta qualidade (Wan 2.2 14B),
instala ComfyUI + modelos (~145 s medidos com rede de 18 Gbps), atende a fila e DESTROI a maquina depois de `worker_idle_s` sem pedido.
Sem pedido = sem custo; com pedidos seguidos a maquina quente atende sem nova partida.

Por que proprio e nao o modo serverless nativo da Vast: o nativo exige o PyWorker e um template dele, e a conta/fila/ETA ja vivem aqui. Reaproveita
o que ja funciona (RealVast, SshTunnel, Lane) e da controle total: teto de preco, saldo minimo, rede minima, trava de idade e vigia de vazamento.

Garantias contra gasto a toa:
  * nunca aluga acima de `worker_max_dph` (preco TOTAL, GPU + disco) nem com saldo abaixo de `worker_min_credit_usd`;
  * so aceita host com rede rapida (os 81 GB de modelos viram partida de 30 min numa rede lenta);
  * toda tentativa que falha DESTROI a maquina antes de tentar outra;
  * vigia periodico destroi qualquer instancia com o nosso rotulo que nao seja a atual (sobra de um reinicio ou de um erro);
  * maquina velha demais (`worker_max_age_h`) e destruida assim que ficar ociosa."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time

from app.config import Settings
from app.gpu.tunnel import Tunnel

log = logging.getLogger("llm-queue.worker")

LABEL = "mi-worker-video"
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "remote", "worker-provision.sh")
COLD_START_S = 300      # estimativa de partida a frio mostrada ao cliente: alugar (~2 min) + instalar (~2,5 min); o 1o pedido ainda carrega os modelos
LOG_KEY = "llmq:worker:runs"
RECONCILE_EVERY_S = 300
ADOPT_GRACE_S = 300     # apos reiniciar o servico, uma maquina ja de pe pode ser adotada por um pedido novo dentro deste prazo; depois disso e sobra e e destruida


class WorkerUnavailable(Exception):
    """Nao ha como ter o worker (sem saldo, sem oferta, tempo esgotado)."""


class _Fatal(Exception):
    """Falha que repetir nao resolve (ex.: sem saldo)."""


class EphemeralWorker:
    def __init__(self, s: Settings, vast, tunnel: Tunnel, redis=None, clock=time.time, sleep=asyncio.sleep, script: str | None = None):
        self.s, self.vast, self.tun, self.r, self.now, self.sleep = s, vast, tunnel, redis, clock, sleep
        self._script = script
        self.state = "off"            # off | starting | ready | stopping
        self.stage = ""               # o que esta fazendo agora (so painel)
        self.inst_id: int | None = None
        self.dph = 0.0
        self.created_at: float | None = None
        self.ready_at: float | None = None
        self.start_t: float | None = None
        self.busy = 0
        self.last_activity = clock()
        self.boot_t = clock()
        self.runs = 0
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.Task | None = None
        self._last_reconcile = clock()
        self._inst_cache: tuple[float, object] | None = None

    # ---- ciclo de vida do servico
    def start(self) -> None:
        if self._idle is None:
            self._idle = asyncio.create_task(self._idle_loop())

    async def stop(self) -> None:
        """Parada do servico (deploy): NAO destroi a maquina; o servico novo a adota se chegar pedido logo, ou o vigia a destroi."""
        t, self._idle = self._idle, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        await self.tun.close()

    # ---- uso
    def enter(self) -> None:
        self.busy += 1
        self.last_activity = self.now()

    def leave(self) -> None:
        self.busy = max(0, self.busy - 1)
        self.last_activity = self.now()

    def startup_eta_s(self) -> int:
        """Quanto falta para a maquina ficar pronta (0 se ja esta)."""
        if self.state == "ready":
            return 0
        if self.state == "starting" and self.start_t:
            return max(30, int(COLD_START_S - (self.now() - self.start_t)))
        return COLD_START_S

    async def ensure(self) -> None:
        """Devolve quando o ComfyUI do worker esta acessivel pelo tunel. Aluga e instala se preciso; varios pedidos esperam a mesma partida."""
        if self.state == "ready" and await self._alive():
            return
        async with self._lock:
            if self.state == "ready" and await self._alive():
                return
            await self._start()

    # ---- partida
    async def _start(self) -> None:
        self.state, self.start_t, self.last_error = "starting", self.now(), None
        deadline = self.now() + self.s.worker_wait_s
        attempts = 0
        while True:
            attempts += 1
            try:
                await self._attempt()
                self.state, self.stage, self.ready_at, self.runs = "ready", "pronto", self.now(), self.runs + 1
                self.last_activity = self.now()
                log.info("worker pronto: instancia %s a US$ %.3f/h em %.0f s", self.inst_id, self.dph, self.now() - self.start_t)
                return
            except _Fatal as e:
                self.last_error = str(e)
                await self._destroy("falha definitiva")
                self.state, self.stage = "off", ""
                raise WorkerUnavailable(str(e))
            except asyncio.CancelledError:
                await self._destroy("partida cancelada")
                self.state, self.stage = "off", ""
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                log.warning("worker: tentativa %d falhou: %s", attempts, self.last_error)
                await self._destroy("tentativa falhou")  # nunca deixa maquina para tras
                if self.now() >= deadline or attempts >= 8:
                    self.state, self.stage = "off", ""
                    raise WorkerUnavailable("sem maquina de video disponivel agora: " + self.last_error)
                self.state, self.stage = "starting", "tentando outra maquina"
                await self.sleep(min(60, 15 * attempts))

    async def _attempt(self) -> None:
        credit = await self.vast.credit()
        if credit < self.s.worker_min_credit_usd:
            raise _Fatal(f"saldo da Vast baixo (US$ {credit:.2f}); recarregue para gerar videos")
        inst = await self._adopt_existing()
        if inst is None:
            self.stage = "procurando maquina"
            offers = await self.vast.search_gpu(self.s.worker_gpu_names, self.s.worker_disk_gb, self.s.worker_max_dph,
                                                self.s.worker_min_down_mbps, self.s.worker_min_reliability)
            if not offers:
                raise RuntimeError(f"nenhuma oferta com ate US$ {self.s.worker_max_dph}/h, rede >= {self.s.worker_min_down_mbps} Mbps e confiabilidade >= {self.s.worker_min_reliability}")
            for o in offers[:6]:
                self.stage = "alugando"
                res = await self.vast.create_instance(int(o["id"]), self.s.worker_image, self.s.worker_disk_gb, LABEL, "touch /root/.no_auto_tmux", {})
                if res.ok and res.instance_id:
                    self.inst_id, self.dph, self.created_at = int(res.instance_id), float(o["allin_dph"]), self.now()
                    log.info("worker: alugada a oferta %s (%s, %s) -> instancia %s a US$ %.3f/h", o["id"], o.get("gpu_name"), o.get("geolocation"), self.inst_id, self.dph)
                    break
                log.warning("worker: oferta %s recusada: %s %s", o.get("id"), res.error, res.msg)
            if not self.inst_id:
                raise RuntimeError("nenhuma das ofertas foi aceita")
        self.stage = "iniciando a maquina"
        inst = await self._wait_running()
        await self._provision(inst)
        self.stage = "abrindo o tunel"
        if not await self.tun.open(inst.ssh_host, int(inst.ssh_port)):
            raise RuntimeError("nao consegui abrir o tunel ate o worker")

    async def _adopt_existing(self):
        """Maquina com o nosso rotulo que ja esta de pe (reinicio do servico no meio de uma sessao): reaproveita em vez de alugar outra."""
        for i in await self.vast.list_instances():
            if (i.raw or {}).get("label") == LABEL and i.actual_status in ("running", "loading", "created"):
                self.inst_id, self.dph = i.id, float(i.dph_total or 0)
                self.created_at = self.created_at or self.now()
                log.info("worker: adotando instancia %s ja existente", i.id)
                return i
        return None

    async def _wait_running(self):
        end = self.now() + self.s.worker_start_timeout_s / 2
        while self.now() < end:
            inst = await self.vast.get_instance(self.inst_id)
            if inst is None:
                raise RuntimeError("a maquina alugada sumiu")
            if inst.running:
                return inst
            if inst.actual_status in ("exited", "stopped"):
                raise RuntimeError(f"a maquina parou ({inst.actual_status}): {(inst.status_msg or '')[:120]}")
            await self.sleep(5)
        raise RuntimeError("a maquina nao ficou de pe a tempo")

    def _script_text(self) -> str:
        if self._script is None:
            with open(SCRIPT, encoding="utf-8") as f:
                self._script = f.read()
        return self._script

    async def _provision(self, inst) -> None:
        host, port = inst.ssh_host, int(inst.ssh_port)
        self.stage = "verificando a maquina"
        for _ in range(24):  # o SSH demora uns segundos para aceitar depois de "running"
            if await self.tun.run(host, port, "true", 20):
                break
            await self.sleep(5)
        else:
            raise RuntimeError("SSH do worker nao respondeu")
        if await self.tun.run(host, port, "test -f /workspace/provision.done && curl -sf -m 3 http://127.0.0.1:8188/system_stats >/dev/null", 30):
            return  # ja instalada (maquina adotada)
        self.stage = "instalando modelos"
        b64 = base64.b64encode(self._script_text().encode()).decode()
        start = ("mkdir -p /workspace && cd /workspace && echo %s | base64 -d > provision.sh && "
                 "(nohup setsid bash provision.sh > provision.out 2>&1 < /dev/null &) ; sleep 1; true") % b64
        if not await self.tun.run(host, port, start, 60):
            raise RuntimeError("nao consegui iniciar a instalacao no worker")
        end = self.now() + self.s.worker_start_timeout_s
        while self.now() < end:
            await self.sleep(10)
            if await self.tun.run(host, port, "test -f /workspace/provision.done", 30):
                return
            if await self.tun.run(host, port, "grep -q -e INCOMPLETO -e 'NAO subiu' /workspace/provision.log", 30):
                raise RuntimeError("a instalacao no worker falhou")
        raise RuntimeError("a instalacao no worker nao terminou a tempo")

    # ---- saude
    async def instance(self, fresh: bool = False):
        if not fresh and self._inst_cache and self.now() - self._inst_cache[0] < 15:
            return self._inst_cache[1]
        inst = await self.vast.get_instance(self.inst_id) if self.inst_id else None
        self._inst_cache = (self.now(), inst)
        return inst

    async def _alive(self) -> bool:
        inst = await self.instance()
        if inst is None or not inst.running:
            log.warning("worker: a maquina %s nao esta mais de pe (%s)", self.inst_id, getattr(inst, "actual_status", None))
            await self._destroy("maquina caiu")
            return False
        if not await self.tun.healthy() and not await self.tun.open(inst.ssh_host, int(inst.ssh_port)):
            return False
        return True

    # ---- fim
    async def _destroy(self, reason: str) -> None:
        iid, self.inst_id = self.inst_id, None
        await self.tun.close()
        self._inst_cache = None
        if iid:
            ok = False
            for i in range(4):
                try:
                    ok = await self.vast.destroy(iid)
                except Exception as e:  # noqa: BLE001
                    log.warning("worker: destruir %s falhou: %r", iid, e)
                if ok:
                    break
                await self.sleep(3 * (i + 1))
            await self._record(iid, reason, ok)
        if self.state != "starting":
            self.state, self.stage = "off", ""

    async def _record(self, iid: int, reason: str, destroyed: bool) -> None:
        age = (self.now() - self.created_at) if self.created_at else 0.0
        rec = {"instance": iid, "reason": reason, "destroyed": destroyed, "dph": round(self.dph, 4), "alive_s": round(age),
               "cost_usd": round(self.dph * age / 3600, 3), "ready_s": round(self.ready_at - self.start_t) if (self.ready_at and self.start_t) else None,
               "ended": int(self.now())}
        log.info("worker: instancia %s destruida (%s): %s", iid, reason, json.dumps(rec))
        if not destroyed:
            log.error("worker: NAO consegui destruir a instancia %s: ela segue cobrando; o vigia tenta de novo", iid)
        self.created_at = self.ready_at = None
        if self.r is not None:
            try:
                await self.r.lpush(LOG_KEY, json.dumps(rec))
                await self.r.ltrim(LOG_KEY, 0, 199)
            except Exception:  # noqa: BLE001
                pass

    async def _idle_loop(self) -> None:
        while True:
            try:
                await self.sleep(15)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("worker: vigia: %r", e)

    async def tick(self) -> None:
        """Um passo do vigia (separado para os testes): destroi a maquina ociosa ou velha e as sobras com o nosso rotulo."""
        if self.state == "ready" and self.busy == 0:
            async with self._lock:  # o mesmo trinco da partida: um pedido que chega agora espera a destruicao terminar e aluga outra, nunca adota esta
                idle = self.now() - self.last_activity
                age = (self.now() - self.created_at) if self.created_at else 0
                if self.state == "ready" and self.busy == 0 and (idle >= self.s.worker_idle_s or (age >= self.s.worker_max_age_h * 3600 and idle >= 60)):
                    self.state = "stopping"
                    await self._destroy(f"ocioso ha {int(idle)} s")
        if self.state == "off" and self.now() - self._last_reconcile >= RECONCILE_EVERY_S and self.now() - self.boot_t >= ADOPT_GRACE_S:
            self._last_reconcile = self.now()
            for i in await self.vast.list_instances():
                if (i.raw or {}).get("label") == LABEL and i.id != self.inst_id:
                    log.warning("worker: destruindo sobra %s", i.id)
                    ok = await self.vast.destroy(i.id)
                    await self._record(i.id, "sobra sem dono", ok)

    # ---- painel
    def status(self) -> dict:
        age = (self.now() - self.created_at) if (self.created_at and self.inst_id) else 0
        return {"enabled": True, "state": self.state, "stage": self.stage, "instance": self.inst_id, "dph": round(self.dph, 4), "busy": self.busy,
                "idle_s": int(self.now() - self.last_activity) if self.state == "ready" else None, "age_s": int(age), "cost_so_far_usd": round(self.dph * age / 3600, 3),
                "destroy_in_s": max(0, int(self.s.worker_idle_s - (self.now() - self.last_activity))) if (self.state == "ready" and self.busy == 0) else None,
                "startup_eta_s": self.startup_eta_s(), "sessions": self.runs, "last_error": self.last_error}
