"""CLASSIFICADOR 2 (saida e acao): olha o que o modelo RESPONDE e as ACOES do agente (chamadas de ferramenta) antes de entregar.
So roda no nivel 2. Separado do classificador 1: configuracao propria (`settings.safety_output`), log proprio (classifier='output'),
interruptor proprio. No fluxo agentico ele le a chamada de ferramenta inteira ANTES de o Hermes executar."""
from __future__ import annotations

import json

from app.safety import guard_client
from app.safety.common import Verdict, compile_rules, load_config, match

CONFIG_KEY = "safety_output"
CATEGORIES = ["csam", "cbrn", "malware", "fraud", "violence", "selfharm", "destructive_action", "credential_exfil"]
DEFAULTS = {"enabled": True, "categories": CATEGORIES, "backend": "rules",  # rules | model | both
            "model_url": "", "model_name": "", "model_key": "", "refusal": ""}

# o que o MODELO escreve (instrucao concreta, nao menção): precisa de agente + verbo de preparo juntos
TEXT_RULES = compile_rules({
    "csam": [r"(descri\w+|cena|conto|historia|story|scene).{0,80}(sexual|sexo|explicit).{0,80}(crianca|menor de idade|menina de \d{1,2} anos|menino de \d{1,2} anos|child|minor|underage)"],
    "cbrn": [r"(sarin|\bvx\b|novichok|ricin\w*|anthrax|antraz).{0,120}(precursor|sintese|synthesis|passo \d|step \d|reagente|reagent|dimetil|dimethyl)",
             r"(passo|step) ?1.{0,200}(sarin|novichok|ricin\w*|enriquecimento de uranio|uranium enrichment)"],
    "malware": [r"(encrypt|criptograf\w+).{0,80}(all files|todos os arquivos).{0,120}(ransom|resgate|bitcoin)",
                r"(keylogger|infostealer|ransomware).{0,60}(import |def |#include|function |class ).{0,300}(hook|keystroke|sendto|smtp|socket|encrypt)"],
    "fraud": [r"(querido|dear) (cliente|customer).{0,200}(sua conta|your account).{0,200}(clique|click).{0,80}(link|aqui|here)"],
    "violence": [r"(passo|step) ?1.{0,200}(envenen\w+|poison|explosiv\w+|explosive).{0,200}(dose letal|lethal dose|detonador|detonator)"],
    "selfharm": [r"(metodo|method|forma) (mais )?(rapido|indolor|eficaz|painless|quick).{0,60}(se matar|suicid\w+|kill (yourself|oneself))"],
})

# o que o AGENTE vai EXECUTAR (nome e argumentos da chamada de ferramenta, em texto)
ACTION_RULES = compile_rules({
    "destructive_action": [r"rm -[a-z]*r[a-z]*f?[a-z]* +(/|~|/\*|\$home|\"/\"|c:)(\s|$|\"|')", r"rm -rf --no-preserve-root", r"\bmkfs(\.\w+)? ", r"dd if=.{0,40} of=/dev/(sd|nvme|hd)",
                           r":\(\)\s*\{\s*:\|:&\s*\};:", r"format [a-z]: ?(/|$)", r"del /[sfq] .{0,20}(c:\\|\\\*)", r"remove-item .{0,40}-recurse.{0,40}-force.{0,40}(c:\\|\$home|/)(\s|$)",
                           r"(shutdown|poweroff|halt) .{0,10}(-h|now|/s)"],
    "credential_exfil": [r"(\.ssh/id_|\.aws/credentials|\.npmrc|\.git-credentials|\.netrc|wallet\.dat|login data|cookies\.sqlite).{0,120}(curl|wget|nc |scp |invoke-webrequest|requests\.post|fetch\()",
                         r"(curl|wget|invoke-webrequest|iwr)\b.{0,80}\|\s*(sh|bash|iex|invoke-expression)\b",
                         r"(curl|wget).{0,60}(pastebin|transfer\.sh|file\.io|webhook\.site|ngrok).{0,80}(@|--data|-d |-f )"],
})


def tool_call_text(tool_calls: list) -> str:
    """Nome e argumentos de cada chamada de ferramenta, juntos, para as regras de acao lerem."""
    parts = []
    for tc in tool_calls or []:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        parts.append(f"{fn.get('name', '')} {args}")
    return "\n".join(parts)


async def check(ctx, content: str | None, tool_calls: list | None) -> Verdict:
    cfg = load_config(ctx.store, CONFIG_KEY, DEFAULTS)
    if not cfg["enabled"]:
        return Verdict()
    enabled = set(cfg["categories"]) & set(CATEGORIES)
    text = content or ""
    acts = tool_call_text(tool_calls or [])
    if cfg["backend"] in ("rules", "both"):
        v = match(text, TEXT_RULES, enabled) if text.strip() else Verdict()
        if not v.blocked and acts.strip():
            v = match(acts, ACTION_RULES, enabled)
            if not v.blocked:
                v = match(acts, TEXT_RULES, enabled)  # o agente tambem pode escrever o conteudo proibido dentro de um arquivo
        if v.blocked:
            return v
    if cfg["backend"] in ("model", "both"):
        v = await guard_client.ask(ctx.http, cfg["model_url"], cfg["model_name"], cfg["model_key"], (text + "\n" + acts).strip(), "assistant", enabled)
        if v and v.blocked:
            return v
    return Verdict()
