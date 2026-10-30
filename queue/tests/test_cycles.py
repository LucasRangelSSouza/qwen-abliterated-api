"""Log de ciclos da GPU (tela Maquinas do painel, Spec 14 §5.3.1)."""
import asyncio

from app.gpu.controller import OFF, READY, GPUUnavailable


async def _espera_off(ctl):
    for _ in range(300):
        await asyncio.sleep(0.02)
        if ctl.state == OFF:
            return
    raise AssertionError("nao ficou OFF")


async def test_ciclo_frio_e_fechado_ao_parar_com_motivo_e_custo(make_ctl):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    aberto = (await ctl.cycles.list())[0]
    assert aberto["kind"] == "cold" and aberto["ready_at"] and aberto["cold_start_s"] is not None and aberto["stopped_at"] is None
    assert aberto["stages"]["image_s"] >= 0 and aberto["host"] and aberto["price_dph"] > 0
    await _espera_off(ctl)
    rows = await ctl.cycles.list()
    assert len(rows) == 1 and rows[0]["stop_reason"] == "ociosa" and rows[0]["stopped_at"] and rows[0]["cost_usd"] >= 0 and not rows[0]["failed"]


async def test_religar_a_parada_abre_outra_linha_do_tipo_restart(make_ctl):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    await _espera_off(ctl)
    await ctl.ensure_ready()
    rows = await ctl.cycles.list()
    assert [r["kind"] for r in rows] == ["restart", "cold"] and rows[0]["stopped_at"] is None


async def test_falha_no_portao_registra_ciclo_com_falha(make_ctl):
    ctl, vast, _ = await make_ctl()
    vast.credit_v = 0.1
    ctl.start()
    try:
        await ctl.ensure_ready()
    except GPUUnavailable:
        pass
    rows = await ctl.cycles.list()
    assert rows and rows[0]["failed"] and rows[0]["stop_reason"] == "credito_baixo" and rows[0]["ready_at"] is None


async def test_ciclo_aberto_sobrevive_a_reinicio_do_servico(make_ctl, redis):
    ctl, vast, _ = await make_ctl()
    ctl.start()
    await ctl.ensure_ready()
    ctl2, _, _ = await make_ctl(vast=vast)
    await ctl2.cycles.restore()
    assert ctl2.cycles.cur is not None and ctl2.cycles.cur["id"] == ctl.cycles.cur["id"]


async def test_estado_expoe_o_caminho_esperado(make_ctl):
    ctl, _, _ = await make_ctl()
    assert ctl.status()["path"] == "new"
    ctl.inst = {"id": 1, "stopped_at": 1.0}
    assert ctl.status()["path"] == "restart"
