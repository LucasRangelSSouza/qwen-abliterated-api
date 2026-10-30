import asyncio
import json

import pytest

from app.gpu.controller import K_BAD, K_GOOD, K_INST, LABEL, OFF, READY, GPUUnavailable
from tests.conftest import Probe, fast_settings
from tests.fakes import FakeVast, real_offers


def _first_cand(s):
    from app.gpu import hosts as H
    return H.qualify(real_offers(), s, set(), set())


async def test_cold_start_do_zero_chega_a_ready_e_registra_host_bom(make_ctl, redis):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY and vast.count("create") == 1
    cand = _first_cand(ctl.s)[0]
    assert vast.calls[1][1] == cand.offer_id  # a mais barata que passa nos filtros
    assert str(cand.machine_id).encode() in await redis.smembers(K_GOOD)
    assert (await ctl.ledger.snapshot())["month_usd"] >= 0
    inst = next(iter(vast.instances.values()))
    assert inst["env"]["LLM_REPO"] and inst["onstart"].startswith("#!/bin/bash")


async def test_ocioso_para_e_religa_sem_criar_outra(make_ctl, redis):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    for _ in range(300):  # ociosidade de ~0,12 s
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            break
    assert ctl.state == OFF and ctl.inst and ctl.inst.get("stopped_at")
    assert vast.count("create") == 1
    await ctl.ensure_ready()  # religar o parado
    assert ctl.state == READY and vast.count("create") == 1
    assert any(c[0] == "power" and c[2] == "running" for c in vast.calls)


async def test_situacao_A_religar_indisponivel_cai_no_fallback(make_ctl):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            break
    first_id = ctl.inst["id"]
    vast.power_script = ["unavailable"]
    await ctl.ensure_ready()
    assert ctl.state == READY and vast.count("create") == 2
    assert first_id not in vast.instances  # a antiga foi destruida
    assert ctl.inst["id"] != first_id


async def test_situacao_B_oferta_sumiu_usa_a_proxima_sem_marcar_host_ruim(make_ctl, redis):
    s = fast_settings()
    cands = _first_cand(s)
    vast = FakeVast()
    vast.create_script[cands[0].offer_id] = "gone"
    ctl, vast, _ = await make_ctl(s, vast)
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY
    creates = [c for c in vast.calls if c[0] == "create"]
    assert creates[0][1] == cands[0].offer_id and creates[1][1] == cands[1].offer_id
    assert await redis.hlen(K_BAD) == 0  # oferta que sumiu nao e culpa do host


async def test_situacao_C_host_com_erro_de_gpu_e_destruido_e_marcado(make_ctl, redis):
    s = fast_settings()
    cands = _first_cand(s)
    vast = FakeVast()
    vast.create_script[cands[0].offer_id] = "cdi"
    ctl, vast, _ = await make_ctl(s, vast)
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY
    assert str(cands[0].machine_id).encode() in await redis.hkeys(K_BAD)
    assert vast.count("destroy") >= 1 and vast.count("create") == 2


async def test_situacao_C_registry_inacessivel(make_ctl, redis):
    s = fast_settings()
    cands = _first_cand(s)
    vast = FakeVast()
    vast.create_script[cands[0].offer_id] = "registry"
    ctl, vast, _ = await make_ctl(s, vast)
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY and str(cands[0].machine_id).encode() in await redis.hkeys(K_BAD)


async def test_situacao_E_sem_oferta_abaixo_do_teto_nao_sobe_preco(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(max_allin_dph=0.05))
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason == "sem_oferta" and vast.count("create") == 0 and ctl.state == OFF


async def test_credito_baixo_nao_liga(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(min_credit_usd=3.0), FakeVast(credit=2.5))
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason == "credito_baixo" and vast.count("create") == 0


async def test_teto_mensal_e_diario(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(monthly_budget_usd=0.01))
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason == "orcamento_mensal"
    ctl2, vast2, _ = await make_ctl(fast_settings(max_daily_usd=0.01))
    ctl2.start()
    with pytest.raises(GPUUnavailable) as e2:
        await ctl2.ensure_ready()
    assert e2.value.reason == "orcamento_diario"


async def test_consumidor_prioritario_usa_120_por_cento_do_teto(make_ctl):
    # acordada estimada ~US$0,049; ja gasto 0,05 => 0,099: estoura o teto de 0,08 mas cabe em 0,16 (fator 2)
    s = fast_settings(monthly_budget_usd=0.08, priority_budget_factor=2.0)
    ctl, vast, _ = await make_ctl(s)
    ctl.start()
    await ctl.ledger.add_cost(0.05, "ja gasto")
    with pytest.raises(GPUUnavailable):
        await ctl.ensure_ready(priority=False)
    await ctl.ensure_ready(priority=True)
    assert ctl.state == READY


async def test_desperdicio_acima_do_teto_aborta(make_ctl):
    s = fast_settings(max_waste_per_wake_usd=0.0000001, running_timeout_s=0.3)
    vast = FakeVast()
    for c in _first_cand(s)[:6]:
        vast.create_script[c.offer_id] = "stuck"
    ctl, vast, _ = await make_ctl(s, vast)
    ctl.start()
    with pytest.raises(GPUUnavailable) as e:
        await ctl.ensure_ready()
    assert e.value.reason in ("desperdicio", "tentativas_esgotadas") and vast.running_ids() == []


async def test_situacao_D_host_cai_em_producao(make_ctl, redis):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    iid = ctl.inst["id"]
    machine = ctl.inst["machine_id"]
    vast.instances[iid]["actual_status"] = "exited"  # a Vast derrubou
    ctl.begin_request()  # requisicao em voo nao impede o reconciliador
    for _ in range(200):
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            break
    assert ctl.state == OFF and iid not in vast.instances
    assert str(machine).encode() in await redis.hkeys(K_BAD)


async def test_saude_falhando_tres_vezes_derruba_a_instancia(make_ctl):
    ctl, vast, probe = await make_ctl(fast_settings(idle_min=5.0))  # sem ociosidade: o reconciliador age primeiro
    ctl.start()
    await ctl.ensure_ready()
    probe.ok = False
    for _ in range(300):
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            break
    assert ctl.state == OFF and vast.instances == {}


async def test_ttl_da_instancia_parada_destroi(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(stopped_ttl_h=0.00002))  # ~0,07 s
    ctl.start()
    await ctl.ensure_ready()
    for _ in range(400):
        await asyncio.sleep(0.02)
        if ctl.state == OFF and ctl.inst is None:
            break
    assert ctl.inst is None and vast.instances == {}


async def test_adocao_apos_reinicio(make_ctl, redis):
    vast = FakeVast()
    cand = _first_cand(fast_settings())[0]
    res = await vast.create_instance(cand.offer_id, "img", 70, f"{LABEL}-1", "", {})
    for _ in range(5):
        await vast.get_instance(res.instance_id)
    ctl, vast, _ = await make_ctl(vast=vast)
    await redis.set(K_INST, json.dumps({"id": res.instance_id, "machine_id": cand.machine_id, "dph": 0.4, "storage_h": 0.01,
                                        "created_at": 1, "spec": ctl.spec_id}))
    await ctl.adopt()
    assert ctl.state == READY and ctl.inst["id"] == res.instance_id and vast.count("create") == 1


async def test_adocao_de_instancia_com_spec_diferente_destroi(make_ctl, redis):
    vast = FakeVast()
    cand = _first_cand(fast_settings())[0]
    res = await vast.create_instance(cand.offer_id, "img", 70, f"{LABEL}-1", "", {})
    for _ in range(5):
        await vast.get_instance(res.instance_id)
    ctl, vast, _ = await make_ctl(vast=vast)
    await redis.set(K_INST, json.dumps({"id": res.instance_id, "machine_id": cand.machine_id, "dph": 0.4, "storage_h": 0.01,
                                        "created_at": 1, "spec": "script-antigo"}))
    await ctl.adopt()
    assert ctl.state == OFF and ctl.inst is None and res.instance_id not in vast.instances


async def test_instancia_orfa_sem_registro_nao_e_adotada(make_ctl):
    vast = FakeVast()
    cand = _first_cand(fast_settings())[0]
    res = await vast.create_instance(cand.offer_id, "img", 70, f"{LABEL}-1", "", {})
    for _ in range(5):
        await vast.get_instance(res.instance_id)
    ctl, vast, _ = await make_ctl(vast=vast)
    await ctl.adopt()  # existe na Vast com o nosso rotulo, mas sem spec: criada por versao desconhecida
    assert ctl.state == OFF and res.instance_id not in vast.instances


async def test_religar_com_spec_diferente_destroi_e_recria(make_ctl):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    for _ in range(300):
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            break
    first_id = ctl.inst["id"]
    ctl.inst["spec"] = "script-antigo"
    await ctl.ensure_ready()
    assert ctl.state == READY and vast.count("create") == 2 and first_id not in vast.instances
    assert ctl.inst["spec"] == ctl.spec_id


async def test_hedge_segunda_oferta_vence_e_a_perdedora_e_destruida(make_ctl):
    s = fast_settings(hedge_enabled=True, hedge_max_s=0.1, running_timeout_s=2.0, max_instances=1)
    cands = _first_cand(s)
    vast = FakeVast()
    vast.create_script[cands[0].offer_id] = "stuck"
    ctl, vast, _ = await make_ctl(s, vast)
    ctl.start()
    await ctl.ensure_ready()
    assert ctl.state == READY
    created = [c for c in vast.calls if c[0] == "create"]
    assert len(created) == 2 and created[1][1] == cands[1].offer_id
    assert len(vast.instances) == 1  # so a vencedora sobrou


async def test_falha_no_portao_nao_martela_a_vast(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(min_credit_usd=3.0), FakeVast(credit=1.0))
    calls = {"n": 0}
    orig = vast.credit

    async def counting():
        calls["n"] += 1
        return await orig()
    vast.credit = counting
    ctl.start()
    with pytest.raises(GPUUnavailable):
        await ctl.ensure_ready()
    await asyncio.sleep(0.3)  # 30 ciclos de poll: nenhuma nova consulta sem novo pedido
    assert calls["n"] == 1


async def test_ttl_efetivo_respeita_o_custo_do_disco_parado(make_ctl):
    ctl, vast, _ = await make_ctl(fast_settings(stopped_ttl_h=24.0, stopped_max_usd=0.0000002))
    ctl.inst = {"id": 1, "storage_h": 0.036}
    assert abs(ctl._stopped_ttl_s() - 0.0000002 / 0.036 * 3600) < 1e-9  # ~0,02 s: o teto de US$ manda
    ctl.inst = {"id": 1, "storage_h": 0.0}
    assert ctl._stopped_ttl_s() == 24.0 * 3600  # sem preco conhecido, vale o TTL em horas
    ctl2, _, _ = await make_ctl(fast_settings(stopped_ttl_h=24.0, stopped_max_usd=0.30))
    ctl2.inst = {"id": 1, "storage_h": 0.013}
    assert abs(ctl2._stopped_ttl_s() - 0.30 / 0.013 * 3600) < 1e-6  # disco barato: ~23 h
