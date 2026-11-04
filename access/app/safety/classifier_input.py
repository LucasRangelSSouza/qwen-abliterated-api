"""CLASSIFICADOR 1 (entrada): olha o que a pessoa PEDE, antes de o pedido chegar ao modelo. So roda no nivel 2.
Separado do classificador 2: configuracao propria (`settings.safety_input`), log proprio (classifier='input'), interruptor proprio.
As regras abaixo sao a base conservadora (alta precisao, pouca cobertura); a cobertura de verdade vem do modelo-guarda opcional."""
from __future__ import annotations

from app.safety import guard_client
from app.safety.common import Verdict, compile_rules, load_config, match, norm

CONFIG_KEY = "safety_input"
CATEGORIES = ["csam", "cbrn", "malware", "fraud", "violence", "selfharm"]
DEFAULTS = {"enabled": True, "categories": CATEGORIES, "backend": "rules",  # rules | model | both
            "model_url": "", "model_name": "", "model_key": "", "refusal": ""}

_AGE = r"(crianca|menor de idade|menina|menino|adolescente|colegial|kid|child|minor|underage|teen(ager)?|\b(1[0-7]|[1-9]) ?(anos|years? old|yo)\b)"
_SEX = r"(sexo|sexual|porn\w*|nu[ae]?s?\b|nude|naked|erotic\w*|explicit\w*|transar|seduz\w*)"
_HOW = r"(como|passo a passo|step by step|how to|how do i|me ensine|ensina|teach me|instrucoes|instructions|ajude a|help me)"
# produzir/obter algo (nao basta "como funciona"): usado em armas e fraude, onde perguntar por curiosidade ou historia e legitimo
_MAKE = r"(como (se )?(faz|fazer|faco|produzir|produz|sintetizar|fabricar|preparar|criar|montar|obter|conseguir|cultivar)|how to (make|produce|synthesi[sz]e|build|prepare|create|get|cultivate)|how do i (make|produce|synthesi[sz]e|build)|passo a passo|step by step|me ensine a|teach me to)"

RULES = compile_rules({
    "csam": [rf"{_SEX}.{{0,60}}{_AGE}", rf"{_AGE}.{{0,60}}{_SEX}"],
    "cbrn": [rf"{_MAKE}.{{0,60}}(sarin|\bvx\b|novichok|nerve agent|agente neurotoxico|anthrax|antraz|bioweapon|arma biologica|bomba nuclear|dirty bomb|bomba suja|mustard gas|gas mostarda|ricin\w*|enriquecer uranio)",
             r"(sintetiz\w+|synthesi[sz]e|fabricar|produzir|produce|make|cultivar|weaponi[sz]e).{0,40}(sarin|\bvx\b|novichok|anthrax|antraz|ricin\w*|arma quimica|arma biologica|chemical weapon|bioweapon)"],
    "malware": [r"(escreva|crie|faca|gere|desenvolva|programe|write|create|generate|build|code|make).{0,40}(ransomware|keylogger|botnet|trojan|cavalo de troia|spyware|infostealer|rootkit|virus de computador|computer virus|malware)",
                r"(ransomware|keylogger|botnet|trojan|spyware|infostealer|rootkit).{0,40}(codigo|code|script|source|funcional|working|completo)"],
    "fraud": [rf"{_MAKE}.{{0,50}}(phishing|pagina falsa de (banco|login)|clonar cartao|clone (a )?(credit )?card|carding|lavagem de dinheiro|launder money|money laundering|documento falso|fake id|golpe do pix|nota fiscal falsa)",
              r"(crie|faca|gere|monte|create|make|build).{0,30}(phishing|pagina falsa|fake (login|bank)|site falso do banco)"],
    "violence": [rf"{_HOW}.{{0,40}}(matar|assassinar|envenenar|kill|murder|poison|esfaquear|stab).{{0,30}}(meu|minha|my|alguem|someone|pessoa|colega|chefe|vizinh\w+|esposa|marido|wife|husband|ex\b|her|him|them)",
                 rf"{_MAKE}.{{0,30}}(bomba caseira|explosivo caseiro|make a bomb|build a bomb|pipe bomb|molotov)"],
    "selfharm": [r"(como|melhor forma de|best way to|how (can|do) i|how to).{0,25}(me matar|me suicidar|suicid\w+|kill myself|end my life|tirar minha vida|me enforcar|cortar os pulsos|overdose de proposito)"],
})


def last_user_text(messages: list) -> str:
    """O texto que a pessoa acabou de pedir (ultima mensagem de usuario); os pedidos de ferramenta do agente nao entram aqui."""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


async def check(ctx, messages: list) -> Verdict:
    cfg = load_config(ctx.store, CONFIG_KEY, DEFAULTS)
    if not cfg["enabled"]:
        return Verdict()
    enabled = set(cfg["categories"]) & set(CATEGORIES)
    text = last_user_text(messages)
    if not text.strip():
        return Verdict()
    if cfg["backend"] in ("rules", "both"):
        v = match(text, RULES, enabled)
        if v.blocked:
            return v
    if cfg["backend"] in ("model", "both"):
        v = await guard_client.ask(ctx.http, cfg["model_url"], cfg["model_name"], cfg["model_key"], text, "user", enabled)
        if v and v.blocked:
            return v
    return Verdict()


__all__ = ["check", "CONFIG_KEY", "CATEGORIES", "DEFAULTS", "norm"]
