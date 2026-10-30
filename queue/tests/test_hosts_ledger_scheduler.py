import asyncio

import pytest

from app.config import Settings
from app.gpu import hosts as H
from app.gpu.ledger import Ledger, month_start
from app.scheduler import Lane, QueueFull
from tests.fakes import real_offers


def test_qualify_respeita_teto_e_filtros():
    s = Settings()
    c = H.qualify(real_offers(), s, bad=set(), known_good=set())
    assert c, "deve haver ao menos uma oferta real sob o teto de US$0,45"
    assert all(x.allin_dph <= 0.45 + 1e-9 for x in c)
    offers = {o["id"]: o for o in real_offers()}
    for x in c:
        o = offers[x.offer_id]
        assert o["num_gpus"] == 2 and not str(o["geolocation"]).endswith(", CN")
        assert o["inet_down"] >= s.min_inet_down and o["cpu_ram"] / 1024 >= s.min_ram_gb and o["pcie_bw"] >= s.min_pcie
        assert o["internet_down_cost_per_tb"] <= s.max_dl_usd_tb and o["cuda_max_good"] >= 13
    assert [x.est_cost for x in c] == sorted(x.est_cost for x in c)  # sem known_good, ordena por custo


def test_known_good_vem_primeiro_mesmo_mais_caro():
    s = Settings()
    base = H.qualify(real_offers(), s, set(), set())
    caro = base[-1]
    out = H.qualify(real_offers(), s, set(), {caro.machine_id})
    assert out[0].offer_id == caro.offer_id and out[0].known_good


def test_bad_host_e_excluido_e_preco_nunca_relaxa():
    s = Settings()
    base = H.qualify(real_offers(), s, set(), set())
    m = base[0].machine_id
    assert all(x.machine_id != m for x in H.qualify(real_offers(), s, {m}, set()))
    s2 = Settings(max_allin_dph=0.10)
    for relax in (0, 1, 2):
        assert H.qualify(real_offers(), s2, set(), set(), relax) == []


def test_relaxar_amplia_ofertas():
    s = Settings(min_ram_gb=100.0, min_reliability=0.99)
    n0 = len(H.qualify(real_offers(), s, set(), set(), 0))
    n2 = len(H.qualify(real_offers(), s, set(), set(), 2))
    assert n2 >= n0


def test_classify_status_msg():
    assert H.classify_status_msg("Error response from daemon: failed to inject CDI devices: unresolvable CDI devices x") == "gpu"
    assert H.classify_status_msg('Error response from daemon: Head "https://registry-1.docker.io/v2/x": timeout') == "registry"
    assert H.classify_status_msg("success, running vastai/vllm") is None
    assert H.classify_status_msg("") is None


def test_rejection_summary_conta_motivos():
    s = Settings(max_allin_dph=0.10)
    summ = H.rejection_summary(real_offers(), s, set())
    assert summ.get("preco", 0) > 0 and "ok" not in summ


async def test_ledger_mes_dia_e_sessao_aberta(redis):
    t = [month_start(1_800_000_000) + 86400 * 3]  # dia 4 do mes
    led = Ledger(redis, clock=lambda: t[0])
    await led.begin("run", 1, 0.40)
    t[0] += 3600
    assert abs(await led.month_spend() - 0.40) < 1e-6  # sessao ainda aberta conta ate agora
    await led.end("run", 1)
    await led.begin("stopped", 1, 0.013)
    t[0] += 7200
    await led.end("stopped", 1)
    await led.add_cost(0.05, "download")
    assert abs(await led.month_spend() - (0.40 + 0.026 + 0.05)) < 1e-6
    assert abs(await led.day_spend() - (0.40 + 0.026 + 0.05)) < 1e-6
    t[0] += 86400  # outro dia: o dia zera, o mes nao
    assert await led.day_spend() == 0.0
    assert abs(await led.month_spend() - 0.476) < 1e-6


async def test_lane_interactive_antes_de_batch_e_limite_por_consumidor():
    lane = Lane("chat", limit=1, queue_max=5, per_consumer=1)
    order = []
    held = lane.admit("a", "interactive", 0)
    await lane.acquire(held, 0)
    b = lane.admit("a", "batch", 1)
    i = lane.admit("b", "interactive", 2)

    async def run(t, tag):
        await lane.acquire(t, 0)
        order.append(tag)
        await lane.release(t)
    tasks = [asyncio.create_task(run(b, "batch")), asyncio.create_task(run(i, "inter"))]
    await asyncio.sleep(0.05)
    await lane.release(held)
    await asyncio.gather(*tasks)
    assert order == ["inter", "batch"]


async def test_lane_fila_cheia_e_cancel():
    lane = Lane("embed", limit=1, queue_max=1)
    lane.admit("a", "interactive", 0)
    with pytest.raises(QueueFull):
        lane.admit("a", "interactive", 0)
    t = lane.waiting[0]
    await lane.cancel(t)
    assert lane.waiting == [] and lane.inflight == 0


def test_relaxamento_3_aceita_download_e_disco_mais_lentos_mas_nunca_preco():
    s = Settings()
    n2 = len(H.qualify(real_offers(), s, set(), set(), 2))
    n3 = len(H.qualify(real_offers(), s, set(), set(), 3))
    assert n3 > n2  # com as ofertas reais de 30/10, o relaxamento 3 libera hosts de download entre 300 e 600 Mbps
    assert all(c.allin_dph <= s.effective_max_dph + 1e-9 for c in H.qualify(real_offers(), s, set(), set(), 3))
    assert H.qualify(real_offers(), Settings(max_allin_dph=0.10), set(), set(), 3) == []
