"""Qualificacao e ordenacao de ofertas da Vast (Spec 13 §6.2). Funcao pura: testada contra as 51 ofertas reais de 30/10."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings

# Padroes de status_msg da Vast que significam "este host nao vai subir" (Spec 13 §6.3)
FATAL_STATUS_PATTERNS = (
    ("unresolvable CDI devices", "gpu"),
    ("failed to inject CDI devices", "gpu"),
    ("registry-1.docker.io", "registry"),
    ("auth.docker.io", "registry"),
    ("manifest unknown", "registry"),
    ("no space left on device", "disk"),
)


def classify_status_msg(msg: str | None) -> str | None:
    """Devolve 'gpu' | 'registry' | 'disk' quando o status_msg mostra erro fatal do host, senao None."""
    if not msg:
        return None
    m = msg.lower()
    for pat, kind in FATAL_STATUS_PATTERNS:
        if pat.lower() in m and ("error" in m or "unresolvable" in m or "failed" in m or "no space" in m):
            return kind
    return None


@dataclass
class Candidate:
    offer_id: int
    machine_id: int
    geo: str
    allin_dph: float
    est_cost: float
    known_good: bool
    dph: float
    storage_gb_month: float
    inet_down: float
    ram_gb: float
    pcie: float
    reliability: float
    why: list[str] = field(default_factory=list)


def allin_dph(offer: dict, disk_gb: int) -> float:
    """Preco real da instancia: hardware + disco (a lista mostra so o hardware)."""
    sc = offer.get("storage_cost")
    sc = 0.2 if sc is None else sc
    return float(offer["dph_total"]) + sc * disk_gb / 730.0


def _reject_reason(o: dict, s: Settings, ram_min: float, rel_min: float, cap: float, bad: set) -> str | None:
    geo = str(o.get("geolocation") or "")
    if o.get("num_gpus") != s.num_gpus or o.get("gpu_name") != s.gpu_name:
        return "gpu"
    if o.get("machine_id") in bad:
        return "bad_host"
    if geo.endswith(", CN") or "China" in geo:
        return "china"
    if (o.get("cuda_max_good") or 0) < s.min_cuda:
        return "cuda"
    if (o.get("inet_down") or 0) < s.min_inet_down:
        return "download_lento"
    if (o.get("internet_down_cost_per_tb") if o.get("internet_down_cost_per_tb") is not None else 99) > s.max_dl_usd_tb:
        return "download_caro"
    if (o.get("storage_cost") if o.get("storage_cost") is not None else 0.2) > s.max_storage_usd_gb_month:
        return "disco_caro"
    if (o.get("cpu_ram") or 0) / 1024.0 < ram_min:
        return "ram"
    if (o.get("reliability2") or 0) < rel_min:
        return "confiabilidade"
    if (o.get("disk_bw") or 0) < s.min_disk_bw:
        return "disco_lento"
    if (o.get("pcie_bw") or 0) < s.min_pcie:
        return "pcie"
    if (o.get("disk_space") or 0) < s.disk_gb:
        return "disco_pequeno"
    if allin_dph(o, s.disk_gb) > cap + 1e-9:
        return "preco"
    return None


def qualify(offers: list[dict], s: Settings, bad: set, known_good: set, relax: int = 0) -> list[Candidate]:
    """relax 0 = filtros normais; 1 = RAM minima relaxada; 2 = RAM e confiabilidade relaxadas. Preco nunca e relaxado."""
    ram_min = s.min_ram_gb if relax < 1 else s.min_ram_gb_relaxed
    rel_min = s.min_reliability if relax < 2 else s.min_reliability_relaxed
    cap = s.effective_max_dph
    out: list[Candidate] = []
    for o in offers:
        if _reject_reason(o, s, ram_min, rel_min, cap, bad) is not None:
            continue
        a = allin_dph(o, s.disk_gb)
        est = a * s.expected_hours + s.download_gb * float(o.get("internet_down_cost_per_tb") or 0.0) / 1000.0
        out.append(Candidate(
            offer_id=int(o["id"]), machine_id=int(o["machine_id"]), geo=str(o.get("geolocation") or ""), allin_dph=round(a, 4),
            est_cost=round(est, 4), known_good=o.get("machine_id") in known_good, dph=float(o["dph_total"]),
            storage_gb_month=float(o.get("storage_cost") or 0.2), inet_down=float(o.get("inet_down") or 0),
            ram_gb=round((o.get("cpu_ram") or 0) / 1024.0, 1), pcie=float(o.get("pcie_bw") or 0),
            reliability=float(o.get("reliability2") or 0)))
    # hosts conhecidos primeiro (imagem em cache, sobem em ~50 s), depois o mais barato; PCIe so desempata
    out.sort(key=lambda c: (0 if c.known_good else 1, c.est_cost, -c.pcie))
    return out


def rejection_summary(offers: list[dict], s: Settings, bad: set) -> dict:
    """Quantas ofertas cada filtro eliminou (para o log e para o alerta da situacao E)."""
    cap = s.effective_max_dph
    counts: dict[str, int] = {}
    for o in offers:
        r = _reject_reason(o, s, s.min_ram_gb, s.min_reliability, cap, bad) or "ok"
        counts[r] = counts.get(r, 0) + 1
    return counts
