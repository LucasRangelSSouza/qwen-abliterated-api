"""Controlador da GPU (Spec 13 §5): OFF -> STARTING -> (FALLBACK) -> LOADING -> READY -> STOPPING -> OFF.
Liga na primeira requisicao, desliga apos GPU_IDLE_MIN ociosos, para (nao destroi) a instancia e a destroi apos GPU_STOPPED_TTL_H.
Trata host indisponivel (situacoes A a E), tetos de gasto e adocao de instancia existente apos reinicio do servico."""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Awaitable, Callable

from app.alerts import Alerts
from app.config import Settings
from app.gpu import hosts as H
from app.gpu.ledger import Ledger
from app.gpu.tunnel import Tunnel
from app.gpu.vast import Instance, VastAPI

log = logging.getLogger("llm-queue.gpu")

OFF, STARTING, FALLBACK, LOADING, READY, STOPPING = "OFF", "STARTING", "FALLBACK", "LOADING", "READY", "STOPPING"

K_INST = "llmq:instance"
K_BAD = "llmq:bad_hosts"
K_GOOD = "llmq:known_good"
K_COLD = "llmq:cold_starts"
LABEL = "llmq-gpu"

# estimativas de subida por caminho (s), medidas em 30/10 (Spec 13 §7); recalibradas pela mediana dos ultimos cold starts
ETA_DEFAULT = {"restart": 120, "known": 380, "new": 650}


class GPUUnavailable(Exception):
    def __init__(self, reason: str, message: str = "", retry_after: int = 60):
        super().__init__(message or reason)
        self.reason, self.retry_after = reason, retry_after


def render_env(s: Settings) -> dict:
    """Variaveis de ambiente do contêiner (lidas pelo remote/bootstrap.sh)."""
    return {"LLM_REPO": s.llm_repo, "EMBED_REPO": s.embed_repo, "EMBED_MODE": s.embed_mode,
            "LLM_UTIL": str(s.effective_llm_util), "EMBED_UTIL": str(s.embed_util),
            "WATCHDOG_IDLE_MIN": str(int(s.watchdog_idle_min)), "HF_HUB_ENABLE_HF_TRANSFER": "1"}


def _strip(text: str, keep_shebang: bool = True) -> str:
    """Remove comentarios e linhas vazias (o onstart da Vast viaja como texto; menor e mais seguro)."""
    out = []
    for i, line in enumerate(text.splitlines()):
        t = line.strip()
        if not t or (t.startswith("#") and not (i == 0 and keep_shebang and t.startswith("#!")) and "#__WATCHDOG__" not in t):
            continue
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def load_onstart(path: str | None = None) -> str:
    """Script de partida da instancia: bootstrap.sh com o vigia (watchdog.sh) embutido no lugar de #__WATCHDOG__."""
    base = Path(path).parent if path else Path(__file__).resolve().parents[2] / "remote"
    boot = Path(path) if path else base / "bootstrap.sh"
    wd = base / "watchdog.sh"
    if not boot.exists():
        return "#!/bin/bash\necho bootstrap ausente\n"
    text = boot.read_text(encoding="utf-8")
    if wd.exists():
        text = text.replace("#__WATCHDOG__", _strip(wd.read_text(encoding="utf-8"), keep_shebang=False).rstrip("\n"))
    return _strip(text)


class GPUController:
    def __init__(self, s: Settings, vast: VastAPI, tunnel: Tunnel, ledger: Ledger, alerts: Alerts, redis,
                 health_probe: Callable[[], Awaitable[bool]], clock: Callable[[], float] = time.time, onstart: str | None = None):
        self.s, self.vast, self.tunnel, self.ledger, self.alerts, self.r = s, vast, tunnel, ledger, alerts, redis
        self.health_probe, self.clock = health_probe, clock
        self.onstart = onstart if onstart is not None else load_onstart()
        self.state = OFF
        self.inst: dict | None = None  # {"id","machine_id","dph","storage_h","stopped_at","created_at"}
        self.inflight = 0
        self.last_activity = clock()
        self.fail: GPUUnavailable | None = None
        self.fail_ts = 0.0
        self.fail_seq = 0  # contador (nao depende da resolucao do relogio) para saber se uma falha e posterior ao pedido
        self.cold: dict = {}
        self.waste = 0.0
        self._wake = asyncio.Event()
        self._priority = False
        self._cond = asyncio.Condition()
        self._last_reconcile = 0.0
        self._tunnel_bad = 0
        self._task: asyncio.Task | None = None
        self.last_cold_s: float | None = None
        self.last_stop_reason: str | None = None
        self.wakes: list[float] = []

    # ------------------------------------------------------------------ API para o resto do servico
    def touch(self) -> None:
        self.last_activity = self.clock()

    def begin_request(self) -> None:
        self.inflight += 1
        self.touch()

    def end_request(self) -> None:
        self.inflight = max(0, self.inflight - 1)
        self.touch()

    async def ensure_ready(self, priority: bool = False, timeout: float | None = None) -> None:
        """Espera a GPU READY (acordando-a se preciso). Levanta GPUUnavailable se o ciclo falhar ou estourar o tempo."""
        self.touch()
        if self.state == READY:
            return
        t_call, seq0 = self.clock(), self.fail_seq
        timeout = timeout if timeout is not None else self.s.wait_max_s
        self._priority = self._priority or priority
        self._wake.set()
        end = t_call + timeout
        async with self._cond:
            while self.state != READY:
                if self.fail is not None and self.fail_seq > seq0:
                    raise self.fail
                left = end - self.clock()
                if left <= 0:
                    raise GPUUnavailable("timeout", "GPU nao ficou pronta no tempo limite", retry_after=120)
                if self.state == OFF:
                    self._wake.set()  # so rearma enquanto nao houve falha depois deste pedido (checada acima)
                try:
                    await asyncio.wait_for(self._cond.wait(), min(left, 1.0))
                except asyncio.TimeoutError:
                    pass
        self.touch()

    def status(self) -> dict:
        now = self.clock()
        eta = None
        if self.state in (STARTING, FALLBACK, LOADING) and self.cold:
            base = self._eta_for(self.cold.get("path") or "new")
            eta = max(5, int(base - (now - self.cold["t0"])))
        return {"state": self.state, "eta_s": eta, "instance_id": (self.inst or {}).get("id"),
                "stopped": bool(self.inst and self.inst.get("stopped_at")), "inflight": self.inflight,
                "idle_s": int(now - self.last_activity) if self.state == READY else None,
                "last_cold_start_s": self.last_cold_s, "last_stop_reason": self.last_stop_reason,
                "fail": {"reason": self.fail.reason, "retry_after": self.fail.retry_after} if self.fail and self.state == OFF else None}

    def _eta_for(self, path: str) -> float:
        return ETA_DEFAULT.get(path, 650)

    # ------------------------------------------------------------------ persistencia e estado
    async def _set_state(self, st: str) -> None:
        self.state = st
        async with self._cond:
            self._cond.notify_all()

    async def _persist(self) -> None:
        if self.inst:
            await self.r.set(K_INST, json.dumps(self.inst))
        else:
            await self.r.delete(K_INST)

    async def _bad_hosts(self) -> set:
        now = self.clock()
        out = set()
        for k, v in (await self.r.hgetall(K_BAD)).items():
            k = k.decode() if isinstance(k, bytes) else k
            v = v.decode() if isinstance(v, bytes) else v
            if float(v) > now:
                out.add(int(k))
        return out

    async def mark_bad(self, machine_id, hours: float, why: str) -> None:
        if machine_id is None:
            return
        await self.r.hset(K_BAD, str(machine_id), str(self.clock() + hours * 3600))
        log.warning("host %s marcado ruim por %sh: %s", machine_id, hours, why)

    async def _known_good(self) -> set:
        return {int(x.decode() if isinstance(x, bytes) else x) for x in await self.r.smembers(K_GOOD)}

    # ------------------------------------------------------------------ laco principal
    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop_loop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.tunnel.close()

    async def run(self) -> None:
        try:
            await self.adopt()
        except Exception as e:  # noqa: BLE001
            log.exception("adopt falhou: %r", e)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("erro no ciclo do controlador: %r", e)
                await self.alerts.notify("controller_error", f"erro no controlador: {e!r}")
            await asyncio.sleep(self.s.poll_s)

    async def _tick(self) -> None:
        now = self.clock()
        if self.state == OFF:
            if self._wake.is_set():
                self._wake.clear()
                await self._start_cycle()
            elif self.inst and self.inst.get("stopped_at") and now - self.inst["stopped_at"] > self._stopped_ttl_s():
                await self._destroy_instance("ttl_parada")
        elif self.state == READY:
            if now - self._last_reconcile >= self.s.reconcile_s:
                self._last_reconcile = now
                await self._reconcile()
            if self.state == READY:
                await self._guards(now)

    def _stopped_ttl_s(self) -> float:
        """TTL da instancia parada: GPU_STOPPED_TTL_H, mas nunca mais que GPU_STOPPED_MAX_USD de disco (hosts com disco caro)."""
        ttl_h = self.s.stopped_ttl_h
        rate = float((self.inst or {}).get("storage_h") or 0.0)
        if rate > 0 and self.s.stopped_max_usd > 0:
            ttl_h = min(ttl_h, self.s.stopped_max_usd / rate)
        return ttl_h * 3600.0

    # ------------------------------------------------------------------ adocao apos reinicio
    async def adopt(self) -> None:
        raw = await self.r.get(K_INST)
        rec = json.loads(raw) if raw else None
        live = [i for i in await self.vast.list_instances() if (i.raw.get("label") or "").startswith(LABEL)]
        if rec and not any(i.id == rec["id"] for i in live):
            log.warning("instancia %s do estado nao existe mais na Vast", rec["id"])
            await self.ledger.end("run", rec["id"])
            await self.ledger.end("stopped", rec["id"])
            rec = None
        if not rec and live:
            i = live[0]
            rec = {"id": i.id, "machine_id": i.machine_id, "dph": i.dph_total, "storage_h": i.storage_total_cost, "created_at": self.clock()}
        for extra in live:  # max_instances=1: o excedente (so com o nosso rotulo) e destruido
            if rec and extra.id != rec["id"]:
                await self.vast.destroy(extra.id)
        self.inst = rec
        await self._persist()
        if not rec:
            return
        i = next((x for x in live if x.id == rec["id"]), None)
        if i and i.running:
            await self._set_state(LOADING)
            if await self._connect_and_wait(i, deadline=self.clock() + self.s.health_timeout_s):
                await self._become_ready(i, path="adopt")
            else:
                await self._destroy_instance("adopt_sem_saude")
        elif i:
            rec.setdefault("stopped_at", self.clock())
            await self._persist()

    # ------------------------------------------------------------------ ciclo de partida
    async def _gate(self) -> tuple[bool, str, int]:
        s, now = self.s, self.clock()
        try:
            credit = await self.vast.credit()
        except Exception as e:  # noqa: BLE001
            return False, f"vast_inacessivel: {e!r}", 60
        if credit < s.min_credit_usd:
            await self.alerts.notify("credit_low", f"credito da Vast US${credit:.2f} abaixo do minimo US${s.min_credit_usd:.2f}: GPU nao liga")
            return False, "credito_baixo", 600
        est = (6.5 + s.idle_min) / 60.0 * s.effective_max_dph
        month, day = await self.ledger.month_spend(now), await self.ledger.day_spend(now)
        cap = s.monthly_budget_usd * (s.priority_budget_factor if self._priority else 1.0)
        for pct in s.budget_warn_pct:
            if month >= s.monthly_budget_usd * pct / 100.0:
                await self.alerts.notify(f"budget_{pct}", f"gasto do mes US${month:.2f} passou de {pct}% do teto US${s.monthly_budget_usd:.2f}", "info")
        if month + est > cap:
            await self.alerts.notify("budget_month", f"teto mensal estourado (US${month:.2f} + acordada US${est:.2f} > US${cap:.2f}): GPU nao liga")
            return False, "orcamento_mensal", 3600
        if day + est > s.max_daily_usd:
            await self.alerts.notify("budget_day", f"teto diario estourado (US${day:.2f} + US${est:.2f} > US${s.max_daily_usd:.2f})")
            return False, "orcamento_diario", 3600
        self.wakes = [t for t in self.wakes if now - t < 86400] + [now]
        if len(self.wakes) > s.max_wakes_per_day:
            await self.alerts.notify("wakes_day", f"{len(self.wakes)} acordadas em 24 h (alerta em {s.max_wakes_per_day})", "info")
        return True, "ok", 0

    async def _fail_cycle(self, reason: str, retry_after: int, msg: str = "") -> None:
        self.fail = GPUUnavailable(reason, msg or reason, retry_after)
        self.fail_ts = self.clock()
        self.fail_seq += 1
        log.error("ciclo de partida falhou: %s", reason)
        await self._set_state(OFF)

    async def _start_cycle(self) -> None:
        try:
            await self._start_cycle_inner()
        finally:
            # quem pediu durante a partida ja recebeu o resultado; sem isto, uma falha (inclusive no portao de gasto)
            # dispararia outra partida na hora e martelaria a Vast
            self._wake.clear()

    async def _start_cycle_inner(self) -> None:
        s = self.s
        t0 = self.clock()
        self.fail, self.waste = None, 0.0
        self.cold = {"t0": t0, "path": "restart" if (self.inst and self.inst.get("stopped_at")) else "new"}
        await self._set_state(STARTING)
        ok, why, retry = await self._gate()
        priority, self._priority = self._priority, False
        if not ok:
            return await self._fail_cycle(why, retry)
        try:
            inst: Instance | None = None
            if self.inst and self.inst.get("stopped_at"):
                inst = await self._restart_stopped()
            path = "restart" if inst else "new"
            if not inst:
                await self._set_state(FALLBACK)
                inst = await self._provision_new()
                known = self.inst and self.inst.get("machine_id") in await self._known_good()
                path = "known" if known else "new"
            self.cold["path"] = path
            await self._set_state(LOADING)
            if not await self._connect_and_wait(inst, deadline=self.clock() + s.health_timeout_s):
                await self._account_waste(inst, "sem_saude")
                await self._destroy_instance("sem_saude")
                raise GPUUnavailable("sem_saude", "instancia subiu mas a LLM nao ficou saudavel", 120)
            await self._become_ready(inst, path=path)
            self.last_cold_s = round(self.clock() - t0, 1)
            await self.r.lpush(K_COLD, f"{path}:{self.last_cold_s}")
            await self.r.ltrim(K_COLD, 0, 9)
        except GPUUnavailable as e:
            await self.alerts.notify(f"cycle_{e.reason}", f"falha ao ligar a GPU: {e.reason} ({e})")
            await self._fail_cycle(e.reason, e.retry_after, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("ciclo de partida: %r", e)
            await self._fail_cycle("erro_interno", 60, repr(e))

    async def _account_waste(self, inst: Instance | None, note: str, started: float | None = None) -> None:
        if inst is None:
            return
        hours = max(0.0, (self.clock() - (started or self.cold.get("t0", self.clock())))) / 3600.0
        cost = inst.dph_total * hours
        self.waste += cost
        await self.ledger.add_cost(cost, f"tentativa perdida ({note}) instancia {inst.id}")
        if self.waste > self.s.max_waste_per_wake_usd:
            raise GPUUnavailable("desperdicio", f"US${self.waste:.2f} perdidos em tentativas (teto US${self.s.max_waste_per_wake_usd:.2f})", 300)

    # --- A: religar a instancia parada
    async def _restart_stopped(self) -> Instance | None:
        s, iid = self.s, self.inst["id"]
        res = await self.vast.power(iid, "running")
        if not res.ok:
            await self.alerts.notify("restart_unavailable", f"nao religou a instancia {iid}: {res.error} {res.msg}")
            await self._destroy_instance("religar_falhou_" + (res.error or "?"))
            return None
        t = self.clock()
        while self.clock() - t < s.scheduling_timeout_s:
            i = await self.vast.get_instance(iid)
            if i and i.running:
                await self.ledger.end("stopped", iid)
                self.inst.pop("stopped_at", None)
                await self._persist()
                return i
            await asyncio.sleep(s.poll_s)
        await self.vast.power(iid, "stopped")  # cancela a fila de religar
        await self.alerts.notify("scheduling", f"instancia {iid} ficou em scheduling por {s.scheduling_timeout_s:.0f}s: fallback")
        await self._destroy_instance("scheduling_timeout")
        return None

    # --- B, C, E: criar instancia nova
    async def _provision_new(self) -> Instance:
        s = self.s
        window_end = self.clock() + s.search_window_s
        tries = 0
        while True:
            offers = await self.vast.search_offers()
            bad, known = await self._bad_hosts(), await self._known_good()
            cands: list[H.Candidate] = []
            for relax in (0, 1, 2):
                cands = H.qualify(offers, s, bad, known, relax)
                if cands:
                    break
            if not cands:
                summ = H.rejection_summary(offers, s, bad)
                await self.alerts.notify("no_offer", f"nenhuma oferta passa nos filtros (teto US${s.effective_max_dph:.2f}/h): {summ}")
                if self.clock() >= window_end:
                    raise GPUUnavailable("sem_oferta", f"sem oferta abaixo do teto: {summ}", 300)
                await asyncio.sleep(s.search_retry_s)
                continue
            winner = await self._race(cands, max(0, s.max_create_attempts - tries))
            if winner:
                return winner
            tries += min(len(cands), s.max_create_attempts)
            if tries >= s.max_create_attempts:
                raise GPUUnavailable("tentativas_esgotadas", f"{tries} tentativas sem instancia saudavel", 300)

    async def _launch_one(self, c: H.Candidate) -> Instance | None:
        """Cria a instancia da oferta e espera 'running' com SSH. None = falhou (host ruim ja marcado e instancia destruida)."""
        s = self.s
        env, t_start = render_env(s), self.clock()
        res = await self.vast.create_instance(c.offer_id, s.image, s.disk_gb, f"{LABEL}-{int(t_start)}", self.onstart, env)
        if not res.ok:
            if not res.offer_gone:  # B: oferta que sumiu nao e culpa do host
                log.warning("criar falhou: %s %s", res.error, res.msg)
            return None
        iid = res.instance_id
        self.inst = {"id": iid, "machine_id": c.machine_id, "dph": c.allin_dph, "storage_h": 0.0, "created_at": t_start}
        await self._persist()
        end = t_start + s.running_timeout_s
        while self.clock() < end:
            i = await self.vast.get_instance(iid)
            if i is None:
                break
            kind = H.classify_status_msg(i.status_msg)
            if kind:  # C: erro fatal conhecido do host, destroi na hora
                await self.mark_bad(c.machine_id, s.bad_host_hard_ttl_h if kind in ("gpu", "registry") else s.bad_host_ttl_h, f"{kind}: {(i.status_msg or '')[:80]}")
                await self._cleanup_failed(iid, i, t_start)
                return None
            if i.running:
                return i
            await asyncio.sleep(s.poll_s)
        i = await self.vast.get_instance(iid)
        await self.mark_bad(c.machine_id, s.bad_host_ttl_h, "nao chegou a running")
        await self._cleanup_failed(iid, i, t_start)
        return None

    async def _cleanup_failed(self, iid: int, i: Instance | None, t_start: float) -> None:
        await self.vast.destroy(iid)
        if i:
            cost = i.dph_total * max(0.0, self.clock() - t_start) / 3600.0
            self.waste += cost
            await self.ledger.add_cost(cost, f"tentativa perdida instancia {iid}")
        if self.inst and self.inst.get("id") == iid:
            self.inst = None
            await self._persist()

    async def _race(self, cands: list[H.Candidate], attempts: int) -> Instance | None:
        """Tenta as ofertas em ordem; com hedge, lanca uma segunda se a primeira demora (so na fase ate 'running')."""
        s = self.s
        queue = list(cands[: max(1, attempts)])
        running: dict[asyncio.Task, H.Candidate] = {}
        winner: Instance | None = None
        hedge_at = self.clock() + s.hedge_max_s / 5.0

        def launch() -> bool:
            if not queue or len(running) >= (2 if s.hedge_enabled else 1) or len(running) >= s.max_instances + (1 if s.hedge_enabled else 0):
                return False
            c = queue.pop(0)
            running[asyncio.create_task(self._launch_one(c))] = c
            return True

        launch()
        try:
            while running and winner is None:
                done, _ = await asyncio.wait(set(running), timeout=max(0.05, min(1.0, s.poll_s)), return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    c = running.pop(t)
                    r = t.result() if not t.cancelled() else None
                    if r is not None and winner is None:
                        winner = r
                if winner is not None:
                    break
                if self.waste > s.max_waste_per_wake_usd:
                    raise GPUUnavailable("desperdicio", f"US${self.waste:.2f} perdidos em tentativas", 300)
                if not running:
                    launch()
                elif s.hedge_enabled and self.clock() >= hedge_at:
                    launch()
                    hedge_at = float("inf")
        finally:
            for t in list(running):
                t.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            # destroi tudo que o hedge criou e perdeu a corrida
            if winner is not None:
                for i in await self.vast.list_instances():
                    if (i.raw.get("label") or "").startswith(LABEL) and i.id != winner.id:
                        await self.vast.destroy(i.id)
        if winner is not None:
            self.inst = {"id": winner.id, "machine_id": winner.machine_id, "dph": winner.dph_total, "storage_h": 0.0, "created_at": self.cold.get("t0", self.clock())}
            await self._persist()
        return winner

    # --- conexao e saude
    async def _connect_and_wait(self, inst: Instance, deadline: float) -> bool:
        """Abre o tunel (relendo o endereco na API) e espera a saude da LLM (e do embedding, se ligado)."""
        s, iid = self.s, inst.id
        t_ssh = self.clock() + s.ssh_timeout_s
        if s.tunnel_mode != "none":
            opened = False
            cur = inst
            while self.clock() < t_ssh:
                if not (cur and cur.ssh_host and cur.ssh_port):
                    cur = await self.vast.get_instance(iid)
                    if cur is None:
                        return False
                if await self.tunnel.open(cur.ssh_host, int(cur.ssh_port)):
                    opened = True
                    break
                await asyncio.sleep(s.poll_s)
                cur = await self.vast.get_instance(iid)  # o endereco pode ter mudado
                if cur is None:
                    return False
            if not opened:
                if await self._ssh_key_ok():  # so culpa o host se a nossa chave funciona
                    await self.mark_bad((self.inst or {}).get("machine_id"), s.bad_host_ttl_h, "ssh nunca abriu")
                else:
                    await self.alerts.notify("ssh_key", "a chave SSH local nao esta cadastrada na conta Vast: host NAO marcado como ruim")
                return False
        while self.clock() < deadline:
            cur = await self.vast.get_instance(iid)
            if cur is None or (cur.actual_status not in ("running", "loading", None) and not cur.running):
                return False
            if await self.health_probe():
                return True
            await asyncio.sleep(s.poll_s)
        return False

    async def _ssh_key_ok(self) -> bool:
        try:
            pub = Path(self.s.ssh_key_path + ".pub")
            if not pub.exists():
                return True  # sem como verificar: nao culpar o host
            mine = pub.read_text().split()[1]
            return any(mine in k for k in await self.vast.ssh_keys())
        except Exception:  # noqa: BLE001
            return True

    async def _become_ready(self, inst: Instance, path: str) -> None:
        rec = self.inst or {"id": inst.id, "machine_id": inst.machine_id, "created_at": self.clock()}
        rec.update({"id": inst.id, "machine_id": inst.machine_id, "dph": inst.dph_total, "storage_h": inst.storage_total_cost})
        rec.pop("stopped_at", None)
        self.inst = rec
        await self._persist()
        await self.ledger.begin("run", inst.id, inst.dph_total)
        if inst.machine_id is not None:
            await self.r.sadd(K_GOOD, str(inst.machine_id))
        self.fail, self._tunnel_bad = None, 0
        self._last_reconcile = self.clock()
        self.touch()
        await self._set_state(READY)
        log.info("GPU READY (instancia %s, caminho %s)", inst.id, path)

    # ------------------------------------------------------------------ manutencao em READY
    async def _guards(self, now: float) -> None:
        s = self.s
        if self.inflight == 0 and now - self.last_activity >= s.idle_min * 60:
            return await self.stop_now("ociosa")
        if (await self.ledger.open_run_hours(now)) > s.max_session_h:
            await self.alerts.notify("session_max", f"GPU ligada ha mais de {s.max_session_h:.0f} h: parando")
            return await self.stop_now("sessao_maxima")
        if await self.ledger.day_spend(now) > s.max_daily_usd:
            await self.alerts.notify("daily_cap", f"teto diario de US${s.max_daily_usd:.2f} atingido: parando a GPU")
            return await self.stop_now("teto_diario")

    async def _reconcile(self) -> None:
        iid = (self.inst or {}).get("id")
        i = await self.vast.get_instance(iid) if iid else None
        if i is None or not (i.running or i.actual_status == "running"):
            await self.alerts.notify("host_down", f"instancia {iid} caiu ({getattr(i, 'actual_status', 'sumiu')}): marcando host e recriando no proximo pedido")
            await self.mark_bad((self.inst or {}).get("machine_id"), self.s.bad_host_ttl_h, "caiu em producao")
            await self.tunnel.close()
            await self.ledger.end("run", iid)
            if i is not None:
                await self.vast.destroy(iid)
            self.inst = None
            await self._persist()
            return await self._set_state(OFF)
        if self.s.tunnel_mode != "none" and not await self.tunnel.healthy():
            self._tunnel_bad += 1
            if i.ssh_host and await self.tunnel.open(i.ssh_host, int(i.ssh_port)):  # le o endereco de novo na API
                self._tunnel_bad = 0
        if not await self.health_probe():
            self._tunnel_bad += 1
        else:
            self._tunnel_bad = 0 if await self.tunnel.healthy() else self._tunnel_bad
        if self._tunnel_bad >= 3:
            await self.alerts.notify("health_down", f"LLM sem saude por 3 verificacoes: derrubando a instancia {iid}")
            await self.mark_bad((self.inst or {}).get("machine_id"), self.s.bad_host_ttl_h, "sem saude em producao")
            await self.tunnel.close()
            await self.ledger.end("run", iid)
            await self.vast.destroy(iid)
            self.inst = None
            await self._persist()
            await self._set_state(OFF)

    # ------------------------------------------------------------------ parar e destruir
    async def stop_now(self, reason: str) -> None:
        iid = (self.inst or {}).get("id")
        if iid is None:
            return await self._set_state(OFF)
        await self._set_state(STOPPING)
        self.last_stop_reason = reason
        await self.tunnel.close()
        res = await self.vast.power(iid, "stopped")
        if not res.ok:
            await self.alerts.notify("stop_failed", f"nao consegui parar {iid}: {res.error} {res.msg}; destruindo")
            return await self._destroy_instance("parar_falhou")
        end = self.clock() + 120
        while self.clock() < end:
            i = await self.vast.get_instance(iid)
            if i is None or i.actual_status in ("exited", "stopped"):
                break
            await asyncio.sleep(self.s.poll_s)
        now = self.clock()
        await self.ledger.end("run", iid, now)
        rate = (self.inst or {}).get("storage_h") or 0.0
        if rate <= 0:
            i = await self.vast.get_instance(iid)
            rate = i.storage_total_cost if i else 0.0
        await self.ledger.begin("stopped", iid, rate, now)
        self.inst["stopped_at"] = now
        await self._persist()
        self._wake.clear()
        log.info("GPU parada (%s); instancia %s guardada ate %.0f h", reason, iid, self.s.stopped_ttl_h)
        await self._set_state(OFF)

    async def _destroy_instance(self, reason: str) -> None:
        iid = (self.inst or {}).get("id")
        await self.tunnel.close()
        if iid is not None:
            await self.vast.destroy(iid)
            await self.ledger.end("run", iid)
            await self.ledger.end("stopped", iid)
        log.info("instancia %s destruida (%s)", iid, reason)
        self.inst = None
        await self._persist()
        if self.state != OFF:
            await self._set_state(STARTING if self.state in (STARTING, FALLBACK) else OFF)
