"""Estudio (spec 36): preco antes, reserva de cota, Runware falsa (MockTransport), 'em breve' sem chave, mock sem rede."""
import json

import httpx
import pytest_asyncio

from app import estudio
from tests.conftest import OWNER_KEY, Rig


@pytest_asyncio.fixture
async def rig_rw(tmp_path):
    r = Rig(tmp_path, runware_api_key="rw-teste", runware_url="http://up/runware")
    await r.start()
    yield r
    await r.stop()


@pytest_asyncio.fixture
async def rig_sem_chave(tmp_path):
    r = Rig(tmp_path)
    await r.start()
    yield r
    await r.stop()


def runware_falsa(up, respostas: list, status: int = 200):
    """Runware falsa: cada POST em /runware devolve a proxima resposta da lista; guarda as tarefas recebidas em up.rw_calls."""
    up.rw_calls = []

    async def handler(request: httpx.Request, body):
        if request.url.path != "/runware":
            return httpx.Response(500, json={"error": "rota errada"})
        assert request.headers["authorization"] == "Bearer rw-teste"
        up.rw_calls.append(body)
        resp = respostas.pop(0) if respostas else {"data": []}
        return httpx.Response(status, json=resp)
    up.handler = handler


H = {"Authorization": f"Bearer {OWNER_KEY}"}


async def test_catalogo_publico_e_marca_em_breve_sem_chave(rig_sem_chave: Rig):
    r = await rig_sem_chave.client.get("/v1/estudio/catalogo")
    assert r.status_code == 200
    j = r.json()
    assert j["disponivel"] is False and j["aviso"] == "em breve"
    nomes = {e["efeito"] for e in j["efeitos"]}
    assert {"trocar-personagem", "sincronia-labial", "voz", "video-ponta", "imagem-ponta"} <= nomes
    troca = next(e for e in j["efeitos"] if e["efeito"] == "trocar-personagem")
    assert troca["preco_por_unidade_usd"]["480p"] == 0.25 and troca["disponivel"] is False and j["teto_brl"] == 20.0


async def test_orcar_calcula_preco_com_margem_e_creditos(rig_rw: Rig):
    r = await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "trocar-personagem", "segundos": 10}, headers=H)
    assert r.status_code == 200
    o = r.json()
    assert o["custo_usd"] == 0.5 and o["preco_usd"] == 2.5 and o["preco_brl"] == 13.75 and o["qualidade"] == "480p" and o["acima_do_teto"] is False
    assert o["unidades"] == 5 and o["valor_unidade_usd"] == 0.6 and o["modelo"] == "prunaai:p-video@2-pro-replace" and o["disponivel"] is True
    r = await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "voz", "texto": "a" * 1000}, headers=H)
    assert r.json()["custo_usd"] == 0.03 and r.json()["unidade"] == "caractere"
    r = await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "nao-existe"}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_effect"
    r = await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "video-ponta", "qualidade": "8k"}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_quality"


async def test_orcar_exige_chave(rig_rw: Rig):
    assert (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "voz", "texto": "oi"})).status_code == 401


async def test_gerar_sem_chave_runware_responde_em_breve(rig_sem_chave: Rig):
    r = await rig_sem_chave.client.post("/v1/estudio/gerar", json={"efeito": "video-ponta", "prompt": "um gato", "segundos": 5}, headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "estudio_em_breve"
    assert rig_sem_chave.up.ai_calls == 0


async def test_gerar_manda_tarefa_certa_e_acompanha_ate_pronto(rig_rw: Rig):
    runware_falsa(rig_rw.up, [
        {"data": [{"taskType": "videoInference", "taskUUID": "x", "status": "processing"}]},
        {"data": [{"taskType": "getResponse", "taskUUID": "x", "status": "processing"}]},
        {"data": [{"taskType": "videoInference", "taskUUID": "x", "videoURL": "https://im.runware.ai/v.mp4", "cost": 0.5}]},
    ])
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "trocar-personagem", "segundos": 10, "qualidade": "768p",
                                                            "inputs": {"video": "https://ex/v.mp4", "referenceImages": ["https://ex/eu.jpg"]}}, headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "running" and j["preco_usd"] == 3.6364 and j["preco_brl"] == 20.0 and j["message"] == "Criando no Estúdio..."
    tarefa = rig_rw.up.rw_calls[0][0]
    assert tarefa["taskType"] == "videoInference" and tarefa["model"] == "prunaai:p-video@2-pro-replace" and tarefa["resolution"] == "768p"
    assert tarefa["includeCost"] is True and tarefa["deliveryMethod"] == "async" and tarefa["inputs"]["video"] == "https://ex/v.mp4"
    assert "duration" not in tarefa
    r = await rig_rw.client.get(f"/v1/estudio/jobs/{j['id']}", headers=H)
    assert r.json()["status"] == "running"
    assert rig_rw.up.rw_calls[1][0] == {"taskType": "getResponse", "taskUUID": j["id"]}
    r = await rig_rw.client.get(f"/v1/estudio/jobs/{j['id']}", headers=H)
    assert r.json()["status"] == "done" and r.json()["url"] == "https://im.runware.ai/v.mp4" and r.json()["custo_usd"] == 0.5


async def test_voz_vai_sincrona_com_texto(rig_rw: Rig):
    runware_falsa(rig_rw.up, [{"data": [{"taskType": "audioInference", "taskUUID": "a", "audioURL": "https://im.runware.ai/a.mp3", "cost": 0.0009}]}])
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "voz", "texto": "Olá, tudo bem?", "idioma": "pt"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["url"].endswith(".mp3")
    t = rig_rw.up.rw_calls[0][0]
    assert t["taskType"] == "audioInference" and t["deliveryMethod"] == "sync" and t["speech"] == {"text": "Olá, tudo bem?", "language": "pt"}


async def test_erro_da_runware_vira_mensagem_amigavel_e_nao_cobra(rig_rw: Rig):
    runware_falsa(rig_rw.up, [{"errors": [{"code": "invalidModel", "message": "model not found"}]}], status=400)
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "video-rapido", "prompt": "x", "segundos": 5}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "estudio_rejected" and "model not found" in r.json()["error"]["message"]


async def test_sem_saldo_no_fornecedor_e_fila_cheia(rig_rw: Rig):
    runware_falsa(rig_rw.up, [{"errors": [{"code": "insufficientCredits", "message": "x"}]}], status=402)
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "video-rapido", "prompt": "x"}, headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "estudio_sem_saldo"
    runware_falsa(rig_rw.up, [{"errors": [{"code": "serviceOverloaded", "message": "x"}]}], status=429)
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "video-rapido", "prompt": "x"}, headers=H)
    assert r.status_code == 429 and r.json()["error"]["code"] == "estudio_rate_limited"


async def test_job_de_outra_pessoa_nao_aparece(rig_rw: Rig):
    runware_falsa(rig_rw.up, [{"data": [{"taskUUID": "z", "status": "processing"}]}])
    j = (await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "video-rapido", "prompt": "x"}, headers=H)).json()
    assert (await rig_rw.client.get(f"/v1/estudio/jobs/{j['id']}")).status_code == 401
    assert (await rig_rw.client.get("/v1/estudio/jobs/nao-existe", headers=H)).status_code == 404


async def test_mock_responde_sem_rede(tmp_path):
    rig = Rig(tmp_path, runware_mock=True)
    await rig.start()
    try:
        r = await rig.client.post("/v1/estudio/gerar", json={"efeito": "imagem-ponta", "prompt": "um cachorro"}, headers=H)
        assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["url"].startswith("https://mock.runware.local/")
        assert rig.up.ai_calls == 0
        assert (await rig.client.get("/v1/estudio/catalogo")).json()["disponivel"] is True
    finally:
        await rig.stop()


def test_tarefa_veo_com_audio_e_imagem():
    t = estudio._tarefa("video-google", {"prompt": "p", "segundos": 8}, "720p-audio")
    assert t["model"] == "google:veo@3.1" and t["resolution"] == "720p" and t["settings"] == {"audio": True} and t["duration"] == 8
    t = estudio._tarefa("imagem-ponta", {"prompt": "p"}, "2K")
    assert t["taskType"] == "imageInference" and t["resolution"] == "2K" and t["outputFormat"] == "PNG"
    assert json.dumps(t)  # serializavel


async def test_teto_de_20_reais_e_piso_de_margem(rig_rw: Rig):
    # 10 s em 768p custa US$ 1: 5x passaria do teto -> fica no teto (R$ 20); 3 min em 480p custa US$ 9: piso 1,5x passa do teto e a tela avisa
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "trocar-personagem", "segundos": 10, "qualidade": "768p"}, headers=H)).json()
    assert o["preco_brl"] == 20.0 and o["acima_do_teto"] is False and o["markup_aplicado"] == 3.64
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "trocar-personagem", "segundos": 180}, headers=H)).json()
    assert o["custo_usd"] == 9.0 and o["preco_usd"] == 13.5 and o["acima_do_teto"] is True and o["preco_brl"] == 74.25
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "editar-video", "segundos": 40}, headers=H)).json()
    assert o["partes"] == 3 and o["qualidade"] == "720p"


async def test_gerar_video_curto_e_cortado_em_4_a_15_s(rig_rw: Rig):
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "video-ponta", "segundos": 60}, headers=H)).json()
    assert o["quantidade"] == 15
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "video-ponta", "segundos": 1}, headers=H)).json()
    assert o["quantidade"] == 4


async def test_arquivo_do_cliente_sobe_antes_da_tarefa(rig_rw: Rig):
    runware_falsa(rig_rw.up, [
        {"data": [{"taskType": "mediaStorage", "mediaUUID": "m-video"}]},
        {"data": [{"taskType": "mediaStorage", "mediaUUID": "m-foto"}]},
        {"data": [{"taskType": "videoInference", "taskUUID": "x", "status": "processing"}]},
    ])
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "editar-video", "segundos": 10, "prompt": "troque o carro por um Mustang preto",
                                                            "inputs": {"video": "data:video/mp4;base64,AAAA", "referenceImages": ["data:image/png;base64,BBBB"]}}, headers=H)
    assert r.status_code == 200, r.text
    up1, up2, tarefa = (c[0] for c in rig_rw.up.rw_calls)
    assert up1["taskType"] == "mediaStorage" and up1["operation"] == "upload" and up1["media"].startswith("data:video/mp4")
    assert tarefa["inputs"] == {"referenceVideos": ["m-video"], "referenceImages": ["m-foto"]} and "duration" not in tarefa


async def test_imagem_livre_manda_flux_sem_filtro_e_custa_cinco_creditos(rig_rw: Rig):
    runware_falsa(rig_rw.up, [{"data": [{"taskType": "imageInference", "taskUUID": "i", "imageURL": "https://im.runware.ai/i.jpg", "cost": 0.0013}]}])
    o = (await rig_rw.client.post("/v1/estudio/orcar", json={"efeito": "imagem-livre"}, headers=H)).json()
    assert o["creditos"] == 5 and o["modelo"] == "runware:100@1"
    r = await rig_rw.client.post("/v1/estudio/gerar", json={"efeito": "imagem-livre", "prompt": "retrato artistico adulto", "tamanho": "retrato"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "done"
    t = rig_rw.up.rw_calls[0][0]
    assert t["model"] == "runware:100@1" and t["steps"] == 4 and (t["width"], t["height"]) == (768, 1344)
    assert t["safety"] == {"checkContent": False} and "resolution" not in t
