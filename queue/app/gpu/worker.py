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
import shlex
import time
from dataclasses import dataclass

from app.config import Settings
from app.gpu.tunnel import Tunnel

log = logging.getLogger("llm-queue.worker")

LABEL = "mi-worker-video"
PERFIS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "remote", "profiles")
COLD_START_S = 480      # partida a frio mostrada ao cliente: alugar + instalar 81 GB + subir o ComfyUI. Medido em producao (08/11/2026): ~8 min ate o 1o pedido comecar a gerar
LOG_KEY = "llmq:worker:runs"
# tempo extra para instalar um perfil numa maquina que ja esta de pe (o primeiro perfil entra na partida a frio da classe)
PERFIL_EXTRA_S = {"imagem": 150, "video": 240, "rosto": 420, "voz": 240}
RECONCILE_EVERY_S = 300
ADOPT_GRACE_S = 300     # apos reiniciar o servico, uma maquina ja de pe pode ser adotada por um pedido novo dentro deste prazo; depois disso e sobra e e destruida


class WorkerUnavailable(Exception):
    """Nao ha como ter o worker (sem saldo, sem oferta, tempo esgotado)."""


class _Fatal(Exception):
    """Falha que repetir nao resolve (ex.: sem saldo)."""


@dataclass
class WorkerSpec:
    """Uma classe de worker: que GPU alugar, quanto pagar no maximo e que perfis ela instala. Duas classes: `grande` (PRO 6000: Wan 2.2 14B) e
    `pequena` (3090/4090 de ~US$ 0,3/h: imagem, voz, troca de rosto). Cada pedido paga so o perfil de que precisa."""
    name: str
    label: str
    gpu_names: tuple
    max_dph: float
    disk_gb: int
    min_down_mbps: float
    min_reliability: float
    min_ram_gb: float
    idle_s: float
    max_age_h: float
    start_timeout_s: float
    wait_s: float
    min_credit_usd: float
    image: str
    cold_start_s: int
    local_port: int
    default_profile: str
    blocked_countries: tuple = ()

    @classmethod
    def big(cls, s: Settings) -> "WorkerSpec":
        return cls("grande", LABEL, s.worker_gpu_names, s.worker_max_dph, s.worker_disk_gb, s.worker_min_down_mbps, s.worker_min_reliability, 0.0, s.worker_idle_s,
                   s.worker_max_age_h, s.worker_start_timeout_s, s.worker_wait_s, s.worker_min_credit_usd, s.worker_image, COLD_START_S, s.worker_local_port, "video")

    @classmethod
    def small(cls, s: Settings) -> "WorkerSpec":
        return cls("pequena", "mi-worker-small", s.worker_small_gpu_names, s.worker_small_max_dph, s.worker_small_disk_gb, s.worker_small_min_down_mbps,
                   s.worker_min_reliability, s.worker_small_min_ram_gb, s.worker_small_idle_s, s.worker_max_age_h, s.worker_start_timeout_s, s.worker_wait_s,
                   s.worker_min_credit_usd, s.worker_image, s.worker_small_cold_start_s, s.worker_small_local_port, "imagem", s.worker_small_blocked_countries)


class EphemeralWorker:
    def __init__(self, s: Settings, vast, tunnel: Tunnel, redis=None, clock=time.time, sleep=asyncio.sleep, script: str | None = None, spec: WorkerSpec | None = None):
        self.s, self.vast, self.tun, self.r, self.now, self.sleep = s, vast, tunnel, redis, clock, sleep
        self.spec = spec or WorkerSpec.big(s)
        self._script = script          # so testes: texto unico usado como instalador de qualquer perfil
        self.profiles: set[str] = set()  # perfis ja instalados NESTA maquina
        self._ssh: tuple[str, int] | None = None
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

    def startup_eta_s(self, profile: str | None = None) -> int:
        """Quanto falta para a maquina ficar pronta para o perfil (0 se ja esta)."""
        profile = profile or self.spec.default_profile
        if self.state == "ready":
            return 0 if profile in self.profiles else PERFIL_EXTRA_S.get(profile, 240)
        if self.state == "starting" and self.start_t:
            return max(30, int(self.spec.cold_start_s - (self.now() - self.start_t)))
        return self.spec.cold_start_s

    async def ensure(self, profile: str | None = None) -> None:
        """Devolve quando a maquina esta de pe e com o PERFIL instalado (e o ComfyUI acessivel pelo tunel nos perfis que o usam). Aluga e instala se preciso;
        varios pedidos esperam a mesma partida. Perfil que falha na instalacao destroi a maquina e tenta outra (uma vez)."""
        profile = profile or self.spec.default_profile
        for tentativa in (1, 2):
            if not (self.state == "ready" and await self._alive()):
                async with self._lock:
                    if not (self.state == "ready" and await self._alive()):
                        await self._start()
            async with self._lock:
                if self.state != "ready":
                    continue  # a maquina caiu entre os dois trincos: recomeca
                try:
                    await self._ensure_profile(profile)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"{type(e).__name__}: {e}"[:300]
                    log.warning("worker: perfil %s falhou na maquina %s: %s", profile, self.inst_id, self.last_error)
                    await self._destroy("perfil falhou")
                    if tentativa == 2:
                        raise WorkerUnavailable(f"nao consegui preparar a maquina ({profile}): {self.last_error}")
        raise WorkerUnavailable("a maquina nao ficou pronta")

    # ---- partida
    async def _start(self) -> None:
        self.state, self.start_t, self.last_error = "starting", self.now(), None
        deadline = self.now() + self.spec.wait_s
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
        if credit < self.spec.min_credit_usd:
            raise _Fatal(f"saldo da Vast baixo (US$ {credit:.2f}); recarregue para gerar videos")
        inst = await self._adopt_existing()
        if inst is None:
            self.stage = "procurando maquina"
            offers = await self.vast.search_gpu(self.spec.gpu_names, self.spec.disk_gb, self.spec.max_dph,
                                                self.spec.min_down_mbps, self.spec.min_reliability, self.spec.min_ram_gb, self.spec.blocked_countries)
            if not offers:
                raise RuntimeError(f"nenhuma oferta com ate US$ {self.spec.max_dph}/h, rede >= {self.spec.min_down_mbps} Mbps e confiabilidade >= {self.spec.min_reliability}")
            for o in offers[:6]:
                self.stage = "alugando"
                res = await self.vast.create_instance(int(o["id"]), self.spec.image, self.spec.disk_gb, self.spec.label, "touch /root/.no_auto_tmux", {})
                if res.ok and res.instance_id:
                    self.inst_id, self.dph, self.created_at = int(res.instance_id), float(o["allin_dph"]), self.now()
                    log.info("worker: alugada a oferta %s (%s, %s) -> instancia %s a US$ %.3f/h", o["id"], o.get("gpu_name"), o.get("geolocation"), self.inst_id, self.dph)
                    break
                log.warning("worker: oferta %s recusada: %s %s", o.get("id"), res.error, res.msg)
            if not self.inst_id:
                raise RuntimeError("nenhuma das ofertas foi aceita")
        self.stage = "iniciando a maquina"
        inst = await self._wait_running()
        await self._wait_ssh(inst)
        self.stage = "abrindo o tunel"
        if not await self.tun.open(inst.ssh_host, int(inst.ssh_port)):
            raise RuntimeError("nao consegui abrir o tunel ate o worker")

    async def _adopt_existing(self):
        """Maquina com o nosso rotulo que ja esta de pe (reinicio do servico no meio de uma sessao): reaproveita em vez de alugar outra."""
        for i in await self.vast.list_instances():
            if (i.raw or {}).get("label") == self.spec.label and i.actual_status in ("running", "loading", "created"):
                self.inst_id, self.dph = i.id, float(i.dph_total or 0)
                self.created_at = self.created_at or self.now()
                log.info("worker: adotando instancia %s ja existente", i.id)
                return i
        return None

    async def _wait_running(self):
        end = self.now() + self.spec.start_timeout_s / 2
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

    def _payload(self, profile: str) -> str:
        """Instalador do perfil = biblioteca comum + script do perfil (remote/profiles)."""
        if self._script is not None:
            return self._script
        with open(os.path.join(PERFIS_DIR, "_lib.sh"), encoding="utf-8") as f:
            lib = f.read()
        with open(os.path.join(PERFIS_DIR, f"{profile}.sh"), encoding="utf-8") as f:
            script = f.read()
        arquivos = ""  # perfil com pasta propria (voz): os scripts viajam dentro do instalador e sao gravados em /workspace/<perfil>/
        pasta = os.path.join(PERFIS_DIR, profile)
        if os.path.isdir(pasta):
            arquivos = f"mkdir -p /workspace/{profile}\n"
            for nome in sorted(os.listdir(pasta)):
                if nome.startswith("__") or not os.path.isfile(os.path.join(pasta, nome)):
                    continue  # cache do Python e subpastas nao viajam
                with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                    arquivos += f"cat > /workspace/{profile}/{nome} <<'__ARQUIVO__'\n{f.read()}\n__ARQUIVO__\n"
        return lib + "\n" + arquivos + "\n" + script

    async def _wait_ssh(self, inst) -> None:
        host, port = inst.ssh_host, int(inst.ssh_port)
        self._ssh = (host, port)
        self.stage = "verificando a maquina"
        for _ in range(24):  # o SSH demora uns segundos para aceitar depois de "running"
            if await self.tun.run(host, port, "true", 20):
                return
            await self.sleep(5)
        raise RuntimeError("SSH do worker nao respondeu")

    async def _ensure_profile(self, profile: str) -> None:
        if profile in self.profiles:
            return
        host, port = self._ssh
        marker = f"/workspace/profile-{profile}.done"
        if await self.tun.run(host, port, f"test -f {marker}", 30):  # maquina adotada que ja tinha o perfil
            self.profiles.add(profile)
            return
        self.stage = f"instalando {profile}"
        b64 = base64.b64encode(self._payload(profile).encode()).decode()
        tok = f"HF_TOKEN={shlex.quote(self.s.hf_token)} " if getattr(self.s, "hf_token", "") else ""  # so o repositorio privado de pesos usa
        start = ("mkdir -p /workspace && cd /workspace && echo %s | base64 -d > provision-%s.sh && "
                 "(%snohup setsid bash provision-%s.sh > provision-%s.out 2>&1 < /dev/null &) ; sleep 1; true") % (b64, profile, tok, profile, profile)
        if not await self.tun.run(host, port, start, 60):
            raise RuntimeError(f"nao consegui iniciar a instalacao do perfil {profile}")
        end = self.now() + self.spec.start_timeout_s
        while self.now() < end:
            await self.sleep(10)
            if await self.tun.run(host, port, f"test -f {marker}", 30):
                self.profiles.add(profile)
                self.stage = "pronto"
                return
            if await self.tun.run(host, port, "grep -q -e INCOMPLETO -e 'NAO subiu' -e 'FALHOU' /workspace/provision.log", 30):
                raise RuntimeError(f"a instalacao do perfil {profile} falhou")
        raise RuntimeError(f"a instalacao do perfil {profile} nao terminou a tempo")

    async def sh(self, cmd: str, stdin: bytes | None = None, timeout: float = 600.0) -> tuple[int, bytes]:
        """Roda um comando na maquina por SSH e devolve (codigo, saida). Entrada opcional pela stdin (envio de arquivos)."""
        if self._ssh is None:
            raise WorkerUnavailable("maquina nao preparada")
        return await self.tun.run_out(self._ssh[0], self._ssh[1], cmd, timeout, stdin)

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
        self.profiles = set()
        self._ssh = None
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
                if self.state == "ready" and self.busy == 0 and (idle >= self.spec.idle_s or (age >= self.spec.max_age_h * 3600 and idle >= 60)):
                    self.state = "stopping"
                    await self._destroy(f"ocioso ha {int(idle)} s")
        if self.state == "off" and self.now() - self._last_reconcile >= RECONCILE_EVERY_S and self.now() - self.boot_t >= ADOPT_GRACE_S:
            self._last_reconcile = self.now()
            for i in await self.vast.list_instances():
                if (i.raw or {}).get("label") == self.spec.label and i.id != self.inst_id:
                    log.warning("worker: destruindo sobra %s", i.id)
                    ok = await self.vast.destroy(i.id)
                    await self._record(i.id, "sobra sem dono", ok)

    # ---- painel
    def status(self) -> dict:
        age = (self.now() - self.created_at) if (self.created_at and self.inst_id) else 0
        return {"enabled": True, "state": self.state, "stage": self.stage, "instance": self.inst_id, "dph": round(self.dph, 4), "busy": self.busy,
                "idle_s": int(self.now() - self.last_activity) if self.state == "ready" else None, "age_s": int(age), "cost_so_far_usd": round(self.dph * age / 3600, 3),
                "destroy_in_s": max(0, int(self.spec.idle_s - (self.now() - self.last_activity))) if (self.state == "ready" and self.busy == 0) else None,
                "startup_eta_s": self.startup_eta_s(), "pool": self.spec.name, "profiles": sorted(self.profiles), "sessions": self.runs, "last_error": self.last_error}
