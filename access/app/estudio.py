"""Estudio (spec 36): modelos de ponta de video, voz e imagem pela Runware (api.runware.ai), atras do llm-access.
O cliente nunca ve a Runware: pede um EFEITO ("trocar-personagem", "sincronia-labial"...), recebe o preco ANTES (orcar), confirma (gerar),
acompanha (jobs) e baixa o resultado. A chave da Runware vive so aqui (RUNWARE_API_KEY); vazia = Estudio "em breve" (503, estudio_em_breve).
Cobranca: reserva da cota de midia do plano/credito avulso ANTES de chamar (mediaquota.reserve), desfeita se a Runware falhar; o custo real em
dolar (includeCost) fica registrado no job para a margem ser medida. RUNWARE_MOCK=1 simula a Runware (QA, custo zero).
Precos por unidade copiados do `info.x-pricing` dos schema.json de cada modelo em 12/11/2026 (https://runware.ai/docs/models/<id>/schema.json);
`atualizar_tabela()` recarrega de la quando o admin pedir."""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid

import httpx
from fastapi import APIRouter, Request

from app.ctx import Ctx, fail
from app import credits, fornecedores, mediaquota
from app.dberr import DB_ERRORS

log = logging.getLogger("llm-access.estudio")
router = APIRouter()

MARGEM = 5.0                  # so para o catalogo publico (preco por unidade); o preco real vem de fornecedores.preco_final (painel)
DURACAO = {"min": 4, "max": 15}  # modelos que GERAM video aceitam 4-15 s por pedido; video maior e cortado em partes pelo app
JOB_TTL_S = 48 * 3600
POLL_S = 3.0
MAX_WAIT_S = 900.0

# Efeito -> modelo da Runware. `taxas`: preco por unidade (dolar) por qualidade; `unidade`: segundo de video, caractere de texto ou imagem.
# `qualidade` padrao = a mais barata que resolve. `entrada`: o que o cliente precisa mandar (URL publica ou dataURI, como a Runware aceita).
CATALOGO: dict[str, dict] = {
    "video-rapido": {"rotulo": "Vídeo a partir de texto ou foto (rápido)", "kind": "video", "task": "videoInference", "modelo": "prunaai:p-video@2-pro",
                     "unidade": "segundo", "taxas": {"480p": 0.01, "768p": 0.025, "1080p": 0.05}, "padrao": "480p", "entrada": ["positivePrompt", "frameImages?"],
                     "tempo_s": {"480p": 35, "768p": 60, "1080p": 90}},
    "video-ponta": {"rotulo": "Vídeo de ponta (Wan 3.0)", "kind": "video", "task": "videoInference", "modelo": "alibaba:wan@3.0",
                    "unidade": "segundo", "taxas": {"480p": 0.05, "720p": 0.10, "1080p": 0.20}, "padrao": "480p", "entrada": ["positivePrompt", "frameImages?", "referenceImages?"],
                    "tempo_s": {"480p": 90, "720p": 130, "1080p": 190}},
    "video-premium": {"rotulo": "Vídeo premium (Seedance 2.0)", "kind": "video", "task": "videoInference", "modelo": "bytedance:seedance@2.0",
                      "unidade": "segundo", "taxas": {"480p": 0.07, "720p": 0.16, "1080p": 0.40}, "padrao": "480p", "entrada": ["positivePrompt", "referenceImages?", "referenceVideos?"],
                      "tempo_s": {"480p": 120, "720p": 160, "1080p": 240}},
    "video-google": {"rotulo": "Vídeo com som (Veo 3.1)", "kind": "video", "task": "videoInference", "modelo": "google:veo@3.1",
                     "unidade": "segundo", "taxas": {"720p": 0.20, "720p-audio": 0.40}, "padrao": "720p", "entrada": ["positivePrompt", "frameImages?"],
                     "tempo_s": {"720p": 120, "720p-audio": 150}},
    "trocar-personagem": {"rotulo": "Me coloca nesse vídeo (troca de personagem)", "kind": "video", "task": "videoInference", "modelo": "prunaai:p-video@2-pro-replace",
                          "unidade": "segundo", "taxas": {"480p": 0.05, "768p": 0.10, "1080p": 0.12}, "padrao": "480p", "entrada": ["video", "referenceImages"],
                          "tempo_s": {"480p": 100, "768p": 130, "1080p": 170}},
    "avatar-foto": {"rotulo": "Foto que fala (avatar)", "kind": "video", "task": "videoInference", "modelo": "prunaai:p-video@avatar",
                    "unidade": "segundo", "taxas": {"720p": 0.025, "1080p": 0.045}, "padrao": "720p", "entrada": ["referenceImages", "audio|speech"],
                    "tempo_s": {"720p": 60, "1080p": 90}},
    "sincronia-labial": {"rotulo": "Boca no ritmo do áudio (sincronia labial)", "kind": "video", "task": "videoInference", "modelo": "pixverse:lipsync@1",
                         "unidade": "segundo", "taxas": {"padrao": 0.0136}, "padrao": "padrao", "entrada": ["video", "audio|speech"],
                         "tempo_s": {"padrao": 60}},
    "sincronia-labial-pro": {"rotulo": "Sincronia labial profissional (lipsync-2)", "kind": "video", "task": "videoInference", "modelo": "sync:lipsync-2@1",
                             "unidade": "segundo", "taxas": {"padrao": 0.044}, "padrao": "padrao", "entrada": ["video", "audio"],
                             "tempo_s": {"padrao": 120}},
    "editar-video": {"rotulo": "Trocar objeto, roupa ou cenário no seu vídeo", "kind": "video", "task": "videoInference", "modelo": "alibaba:wan@3.0",
                     "unidade": "segundo", "taxas": {"480p": 0.05, "720p": 0.10, "1080p": 0.20}, "padrao": "720p", "entrada": ["video", "positivePrompt", "referenceImages?"],
                     "tempo_s": {"480p": 120, "720p": 160, "1080p": 220}, "partes_s": 15},
    "voz": {"rotulo": "Narração com voz natural (e clone de voz)", "kind": "audio", "task": "audioInference", "modelo": "heygen:voice@1.0",
            "unidade": "caractere", "taxas": {"padrao": 0.00003}, "padrao": "padrao", "entrada": ["speech.text", "audio?"],
            "tempo_s": {"padrao": 15}},
    "voz-hd": {"rotulo": "Narração HD (MiniMax Speech)", "kind": "audio", "task": "audioInference", "modelo": "minimax:speech@2.8",
               "unidade": "caractere", "taxas": {"turbo": 0.00006, "hd": 0.0001}, "padrao": "turbo", "entrada": ["speech.text"],
               "tempo_s": {"turbo": 8, "hd": 10}},
    "imagem-ponta": {"rotulo": "Imagem de ponta (Seedream 5.5)", "kind": "image", "task": "imageInference", "modelo": "bytedance:seedream@5.5",
                     "unidade": "imagem", "taxas": {"1.5K": 0.02, "2K": 0.035}, "padrao": "1.5K", "entrada": ["positivePrompt", "referenceImages?"],
                     "tempo_s": {"1.5K": 25, "2K": 35}},
}
TABELA_LIDA_EM = "2026-11-12"


def _quantidade(efeito: dict, body: dict) -> float:
    if efeito["unidade"] == "segundo":
        n = max(1.0, float(body.get("segundos") or body.get("seconds") or 5))
        if efeito["entrada"][0] not in ("video", "referenceImages") and "partes_s" not in efeito:  # gera do zero: 4 a 15 s por pedido
            n = min(max(n, DURACAO["min"]), DURACAO["max"])
        return n
    if efeito["unidade"] == "caractere":
        return max(1.0, float(len(str(body.get("texto") or body.get("text") or ""))))
    return max(1.0, min(4.0, float(body.get("n") or 1)))


def orcar(nome: str, body: dict, store=None) -> dict:
    """Preco ANTES de rodar: custo Runware (tabela), preco ao cliente (painel: markup, piso, teto em R$), unidades de credito de midia, tempo. Sem rede."""
    e = CATALOGO.get(nome)
    if e is None:
        raise fail(422, f"efeito desconhecido: {nome}", "invalid_request_error", "invalid_effect", efeitos=sorted(CATALOGO))
    q = str(body.get("qualidade") or body.get("quality") or e["padrao"])
    if q not in e["taxas"]:
        raise fail(422, f"qualidade {q} nao existe para {nome}", "invalid_request_error", "invalid_quality", qualidades=sorted(e["taxas"]))
    n = _quantidade(e, body)
    custo = round(e["taxas"][q] * n, 4)
    if store is not None:
        pf = fornecedores.preco_final(store, nome, custo)
        vu = fornecedores.valor_unidade_usd(store, e["kind"])
    else:
        pf = {"preco_usd": round(custo * MARGEM, 4), "preco_brl": None, "markup_aplicado": MARGEM, "acima_do_teto": False, "teto_brl": None, "cotacao": None}
        vu = 0.6
    partes = int(math.ceil(n / e["partes_s"])) if e.get("partes_s") else 1
    tempo = e["tempo_s"].get(q, 60) * (1 if partes == 1 else max(1, math.ceil(partes / 3)))  # ate 3 partes em paralelo
    return {"efeito": nome, "rotulo": e["rotulo"], "kind": e["kind"], "modelo": e["modelo"], "qualidade": q, "unidade": e["unidade"], "quantidade": n,
            "custo_usd": custo, **pf, "unidades": max(1, int(math.ceil(pf["preco_usd"] / vu - 1e-9))), "valor_unidade_usd": round(vu, 4),
            "partes": partes, "tempo_estimado_s": tempo, "tabela_de": TABELA_LIDA_EM}


def _tarefa(nome: str, body: dict, q: str) -> dict:
    """Monta a tarefa no formato da Runware (campos do schema.json de cada modelo)."""
    e = CATALOGO[nome]
    t: dict = {"taskType": e["task"], "taskUUID": str(uuid.uuid4()), "model": e["modelo"], "includeCost": True, "deliveryMethod": "async",
               "outputType": "URL", "numberResults": 1}
    inputs = {k: body["inputs"][k] for k in (body.get("inputs") or {}) if isinstance(body.get("inputs"), dict)}
    if body.get("prompt"):
        t["positivePrompt"] = str(body["prompt"])[:4000]
    if nome == "editar-video" and "video" in inputs:  # Wan 3.0 edita a partir de referenceVideos; o app manda partes de ate 15 s
        inputs["referenceVideos"] = [inputs.pop("video")]
    if e["unidade"] == "segundo" and e["task"] == "videoInference" and nome not in ("trocar-personagem", "sincronia-labial", "sincronia-labial-pro", "avatar-foto", "editar-video"):
        t["duration"] = int(_quantidade(e, body))
    if nome == "video-google":
        t["resolution"] = "720p"
        t["settings"] = {"audio": q.endswith("-audio")}
    elif q not in ("padrao", "turbo", "hd", "1.5K", "2K"):
        t["resolution"] = q
    if e["task"] == "imageInference":
        t["resolution"] = q
        t["outputFormat"] = "PNG"
    if e["task"] == "audioInference":
        t["deliveryMethod"] = "sync"
        t["outputFormat"] = "MP3"
        t["speech"] = {"text": str(body.get("texto") or body.get("text") or ""), **({"voice": body["voz"]} if body.get("voz") else {}), **({"language": body["idioma"]} if body.get("idioma") else {})}
        if nome == "voz-hd":
            t["model"] = e["modelo"]
            t["settings"] = {"quality": q}
    if body.get("texto_fala") and nome in ("sincronia-labial", "avatar-foto"):
        t["speech"] = {"text": str(body["texto_fala"])}
    if inputs:
        t["inputs"] = inputs
    return t


class Runware:
    """Cliente minimo: POST de tarefas, getResponse por UUID, erros em portugues. Nunca registra a chave."""

    def __init__(self, http: httpx.AsyncClient, url: str, key: str, mock: bool = False):
        self.http, self.url, self.key, self.mock = http, url.rstrip("/"), key, mock

    @property
    def ligado(self) -> bool:
        return bool(self.key) or self.mock

    async def _post(self, tasks: list[dict]) -> list[dict]:
        if self.mock:
            return [{"taskType": t["taskType"], "taskUUID": t["taskUUID"], "status": "success", "cost": 0.0,
                     (("videoURL" if t["taskType"] == "videoInference" else "audioURL" if t["taskType"] == "audioInference" else "imageURL")): f"https://mock.runware.local/{t['taskUUID']}.bin"} for t in tasks]
        r = await self.http.post(self.url, json=tasks, headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}, timeout=130)
        try:
            j = r.json()
        except ValueError:
            raise RunwareError(502, "resposta invalida do fornecedor", "estudio_upstream_invalid")
        if r.status_code == 402:
            raise RunwareError(503, "O Estúdio está sem saldo no fornecedor. Avisamos a equipe; tente mais tarde.", "estudio_sem_saldo")
        if r.status_code == 429:
            raise RunwareError(429, "O Estúdio está cheio agora. Tente de novo em instantes.", "estudio_rate_limited", retry_after=r.headers.get("retry-after"))
        if r.status_code >= 400 or (isinstance(j, dict) and j.get("errors")):
            msg = (j.get("errors") or [{}])[0].get("message", "") if isinstance(j, dict) else ""
            log.warning("runware %s: %s", r.status_code, msg[:200])
            raise RunwareError(502 if r.status_code >= 500 else 422, "O fornecedor não aceitou o pedido." + (f" ({msg[:160]})" if msg else ""), "estudio_rejected")
        return j.get("data", []) if isinstance(j, dict) else j

    async def subir(self, midia: str) -> str:
        """Guarda video/audio/imagem do cliente na Runware (`mediaStorage/upload`, aceita dataURI ou base64) e devolve o UUID para usar como entrada."""
        if self.mock:
            return str(uuid.uuid4())
        d = await self._post([{"taskType": "mediaStorage", "taskUUID": str(uuid.uuid4()), "operation": "upload", "media": midia}])
        u = (d[0] if d else {}).get("mediaUUID")
        if not u:
            raise RunwareError(502, "Não consegui enviar o seu arquivo ao Estúdio.", "estudio_upload_failed")
        return u

    async def submeter(self, task: dict) -> dict:
        d = await self._post([task])
        return d[0] if d else {"taskUUID": task["taskUUID"], "status": "processing"}

    async def consultar(self, task_uuid: str) -> dict:
        if self.mock:
            return {"taskUUID": task_uuid, "status": "success", "cost": 0.0, "videoURL": f"https://mock.runware.local/{task_uuid}.bin"}
        d = await self._post([{"taskType": "getResponse", "taskUUID": task_uuid}])
        return d[0] if d else {"taskUUID": task_uuid, "status": "processing"}


class RunwareError(Exception):
    def __init__(self, status: int, message: str, code: str, retry_after: str | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.retry_after = status, message, code, retry_after


def _url(d: dict) -> str | None:
    return d.get("videoURL") or d.get("audioURL") or d.get("imageURL")


def _cliente(ctx: Ctx) -> Runware:
    """Chave lida a cada pedido (o painel pode trocar sem reiniciar): banco cifrado -> RUNWARE_API_KEY."""
    return Runware(ctx.http, ctx.s.runware_url, fornecedores.chave(ctx, "runware")[0], ctx.s.runware_mock)


def _jobs(request: Request) -> dict:
    st = request.app.state
    if not hasattr(st, "estudio_jobs"):
        st.estudio_jobs = {}
    return st.estudio_jobs


def _limpar(jobs: dict, now: float) -> None:
    for k in [k for k, v in jobs.items() if now - v["criado"] > JOB_TTL_S]:
        jobs.pop(k, None)


def _json(body) -> dict:
    if not isinstance(body, dict):
        raise fail(400, "JSON invalido", "invalid_request_error", "invalid_json")
    return body


@router.get("/v1/estudio/catalogo")
async def catalogo(request: Request):
    """Lista de efeitos com preco por unidade ao cliente (ja com margem). Sem login: o site e o app mostram antes da compra."""
    ctx: Ctx = request.app.state.ctx
    ligado = _cliente(ctx).ligado
    mk = fornecedores.precos(ctx.store)
    out = []
    for nome, e in CATALOGO.items():
        MARGEM_EF = float(mk["por_efeito"].get(nome, mk["markup"]))
        out.append({"efeito": nome, "rotulo": e["rotulo"], "kind": e["kind"], "unidade": e["unidade"], "qualidades": sorted(e["taxas"]), "padrao": e["padrao"],
                    "preco_por_unidade_usd": {q: round(v * MARGEM_EF, 5) for q, v in e["taxas"].items()}, "teto_brl": mk["cap_brl"], "entrada": e["entrada"], "disponivel": ligado})
    return {"disponivel": ligado, "aviso": None if ligado else "em breve", "teto_brl": mk["cap_brl"], "tabela_de": TABELA_LIDA_EM, "efeitos": out}


@router.post("/v1/estudio/orcar")
async def rota_orcar(request: Request):
    from app.proxy import authenticate
    ctx: Ctx = request.app.state.ctx
    authenticate(ctx, request)
    body = _json(await request.json())
    o = orcar(str(body.get("efeito") or ""), body, ctx.store)
    o["disponivel"] = _cliente(ctx).ligado
    return o


@router.post("/v1/estudio/gerar")
async def rota_gerar(request: Request):
    """Reserva a cota (ou credito avulso), manda a tarefa a Runware e devolve o job. O resultado vem por GET /v1/estudio/jobs/{id}."""
    from app.proxy import authenticate, _log
    ctx: Ctx = request.app.state.ctx
    p = authenticate(ctx, request)
    body = _json(await request.json())
    nome = str(body.get("efeito") or "")
    o = orcar(nome, body, ctx.store)
    rw = _cliente(ctx)
    if not rw.ligado:
        raise fail(503, "O Estúdio ainda não está disponível. Em breve.", "service_unavailable", "estudio_em_breve")
    kind = o["kind"]
    hold = None
    if p.kind == "person":
        try:
            hold = mediaquota.reserve(ctx.store, p.person_id, kind, o["unidades"], ctx.clock())
        except mediaquota.QuotaExceeded as q:
            quando = mediaquota.datetime.fromtimestamp(q.resets_at, mediaquota.timezone.utc).strftime("%d/%m")
            _log(ctx, p, kind, 0, 0, 0, "quota", None)
            raise fail(429, f"Você já usou a cota de {mediaquota.LABEL[q.kind]} do seu plano neste mês ({q.used} de {q.limit}). Ela renova em {quando}.",
                       "rate_limit_error", "media_quota_exceeded", kind=q.kind, credits_available=q.kind in credits.offers(ctx.store))
        except DB_ERRORS:
            raise fail(503, "banco de chaves indisponivel", "service_unavailable", "database_unavailable")
    try:
        body = await _subir_entradas(rw, body)
        task = _tarefa(nome, body, o["qualidade"])
        r = await rw.submeter(task)
    except RunwareError as e:
        mediaquota.undo(ctx.store, hold)
        _log(ctx, p, kind, 0, 0, 0, "upstream_error", None)
        raise fail(e.status, e.message, "server_error" if e.status >= 500 else "rate_limit_error" if e.status == 429 else "invalid_request_error", e.code,
                   headers={"Retry-After": e.retry_after} if e.retry_after else None)
    except httpx.HTTPError:
        mediaquota.undo(ctx.store, hold)
        _log(ctx, p, kind, 0, 0, 0, "upstream_error", None)
        raise fail(502, "falha ao falar com o fornecedor do Estúdio", "server_error", "upstream_error")
    now = ctx.clock()
    jobs = _jobs(request)
    _limpar(jobs, now)
    job = {"id": task["taskUUID"], "person_id": p.person_id, "kind": kind, "efeito": nome, "modelo": o["modelo"], "qualidade": o["qualidade"],
           "preco_usd": o["preco_usd"], "preco_brl": o.get("preco_brl"), "custo_previsto_usd": o["custo_usd"], "custo_usd": r.get("cost"), "status": "done" if _url(r) else "running",
           "url": _url(r), "hold": hold, "criado": now, "tempo_estimado_s": o["tempo_estimado_s"], "erro": None}
    if r.get("status") == "error" or r.get("error"):
        mediaquota.undo(ctx.store, hold)
        job.update(status="failed", erro=str(r.get("error") or "falhou"), hold=None)
    jobs[job["id"]] = job
    _log(ctx, p, kind, 0, 0, 0, "ok", None)
    return _visao(job)


def _e_bruto(v) -> bool:
    return isinstance(v, str) and (v.startswith("data:") or (len(v) > 512 and not v.startswith("http")))


async def _subir_entradas(rw: Runware, body: dict) -> dict:
    """Arquivos do cliente chegam como dataURI/base64: sobem para a Runware (video e audio so aceitam UUID ou URL)."""
    inp = body.get("inputs")
    if not isinstance(inp, dict):
        return body
    novo = {}
    for k, v in inp.items():
        if isinstance(v, list):
            novo[k] = [await rw.subir(x) if _e_bruto(x) else x for x in v]
        else:
            novo[k] = await rw.subir(v) if _e_bruto(v) else v
    return {**body, "inputs": novo}


def _visao(job: dict) -> dict:
    return {"id": job["id"], "kind": job["kind"], "efeito": job["efeito"], "modelo": job["modelo"], "qualidade": job["qualidade"], "status": job["status"],
            "url": job["url"], "preco_usd": job["preco_usd"], "preco_brl": job.get("preco_brl"), "custo_usd": job["custo_usd"], "tempo_estimado_s": job["tempo_estimado_s"], "erro": job["erro"],
            "message": {"running": "Criando no Estúdio...", "done": "Pronto.", "failed": "Não deu certo. Nada foi cobrado."}[job["status"]]}


@router.get("/v1/estudio/jobs/{job_id}")
async def rota_job(job_id: str, request: Request):
    from app.proxy import authenticate
    ctx: Ctx = request.app.state.ctx
    p = authenticate(ctx, request)
    job = _jobs(request).get(job_id)
    if job is None or (p.kind == "person" and job["person_id"] != p.person_id):
        raise fail(404, "pedido nao encontrado", "invalid_request_error", "not_found")
    if job["status"] == "running":
        try:
            r = await _cliente(ctx).consultar(job_id)
        except (RunwareError, httpx.HTTPError) as e:  # consulta falhou: continua "running"; o cliente tenta de novo
            log.info("consulta do job %s falhou: %s", job_id, e)
            r = {}
        if _url(r):
            job.update(status="done", url=_url(r), custo_usd=r.get("cost", job["custo_usd"]))
        elif r.get("status") == "error" or r.get("error"):
            mediaquota.undo(ctx.store, job.get("hold"))
            job.update(status="failed", erro=str(r.get("error") or "falhou"), hold=None)
        elif ctx.clock() - job["criado"] > MAX_WAIT_S:
            mediaquota.undo(ctx.store, job.get("hold"))
            job.update(status="failed", erro="tempo esgotado", hold=None)
    return _visao(job)


async def atualizar_tabela(http: httpx.AsyncClient) -> dict:
    """Relê `info.x-pricing` dos schema.json publicos de cada modelo e devolve o que mudou (o admin decide se grava em CATALOGO). Sem chave, sem custo."""
    slug = {"prunaai:p-video@2-pro": "prunaai-p-video-2-pro", "alibaba:wan@3.0": "alibaba-wan3-0", "bytedance:seedance@2.0": "bytedance-seedance-2-0",
            "google:veo@3.1": "google-veo-3-1", "prunaai:p-video@2-pro-replace": "prunaai-p-video-2-pro-replace", "prunaai:p-video@avatar": "prunaai-p-video-avatar",
            "pixverse:lipsync@1": "pixverse-lipsync", "sync:lipsync-2@1": "sync-lipsync-2", "heygen:voice@1.0": "heygen-voice-1-0",
            "minimax:speech@2.8": "minimax-speech-2-8", "bytedance:seedream@5.5": "bytedance-seedream-5-5"}
    out = {}
    for nome, e in CATALOGO.items():
        s = slug.get(e["modelo"])
        if not s:
            continue
        try:
            r = await http.get(f"https://runware.ai/docs/models/{s}/schema.json", timeout=20)
            rates = r.json()["info"]["x-pricing"]["rates"]
        except Exception as ex:  # noqa: BLE001
            out[nome] = {"erro": str(ex)[:120]}
            continue
        out[nome] = {"runware": [{"label": x.get("label"), "unit": x.get("unit"), "amount": x.get("amount")} for x in rates], "nossa": e["taxas"]}
    return out
