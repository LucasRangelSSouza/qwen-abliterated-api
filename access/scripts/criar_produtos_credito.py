"""Cria (ou reaproveita) na Cakto os 4 produtos de credito avulso da Spec 24 (cobranca em dolar, valor equivalente a R$ 100) e imprime o JSON para o painel.

Uso (so na janela de deploy da madrugada; cria produto na conta da Cakto do Adriano):
    python criar_produtos_credito.py <arquivo .env com CAKTO_CLIENT_ID e CAKTO_CLIENT_SECRET>            # simulacao: so mostra o que faria
    python criar_produtos_credito.py <arquivo .env> --criar [--preco 18.00] [--moeda USD]               # cria o que faltar (idempotente: procura pelo nome)
    python criar_produtos_credito.py <arquivo .env> --criar --faixas                                    # + as 4 faixas do Estudio (R$ 99/149/199/499)

Preco: o mesmo do painel (GET /admin/api/billing/settings -> credit_price_usd). Se a Cakto recusar USD, rode com --moeda BRL --preco 100.00.

Saida: {"credit_offers": {tipo: id da oferta}, "credit_products": {tipo: id do produto}} para colar em
PUT /admin/api/billing/settings (painel) e a lista de produtos para incluir no webhook da Cakto.
Nao imprime segredo. Mexe SO nos produtos com os nomes abaixo; os ~80 produtos de outros negocios da conta nao sao tocados."""
import json
import sys
import urllib.parse
import urllib.request

BASE = "https://api.cakto.com.br"
UA = {"User-Agent": "curl/8.5.0"}  # o Cloudflare da Cakto barra o User-Agent padrao do Python
PRODUTOS = {
    "tokens": ("Segundo Cérebro - Crédito de uso", "Crédito de uso: o dobro do limite de 5 horas do seu plano, liberado na hora. Vale por 30 dias."),
    "image": ("Segundo Cérebro - Crédito de imagens", "Crédito para gerar imagens além do limite do seu plano. Vale por 30 dias."),
    "video": ("Segundo Cérebro - Crédito de vídeos", "Crédito para gerar vídeos além do limite do seu plano. Vale por 30 dias."),
    "audio": ("Segundo Cérebro - Crédito de áudio", "Crédito para voz, dublagem e troca de voz além do limite do seu plano. Vale por 30 dias."),
}


def _env(caminho: str) -> dict:
    out = {}
    for linha in open(caminho, encoding="utf-8"):
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _chamar(metodo: str, caminho: str, token: str | None = None, corpo=None, form=None):
    dados = urllib.parse.urlencode(form).encode() if form else (json.dumps(corpo).encode() if corpo is not None else None)
    h = dict(UA)
    if token:
        h["Authorization"] = "Bearer " + token
    if corpo is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + caminho, data=dados, headers=h, method=metodo)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _opcao(nome: str, padrao: str) -> str:
    return sys.argv[sys.argv.index(nome) + 1] if nome in sys.argv else padrao


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    criar = "--criar" in sys.argv
    preco, moeda = _opcao("--preco", "18.00"), _opcao("--moeda", "USD")
    e = _env(sys.argv[1])
    token = _chamar("POST", "/public_api/token/", form={"client_id": e["CAKTO_CLIENT_ID"], "client_secret": e["CAKTO_CLIENT_SECRET"]})["access_token"]
    ofertas, produtos = {}, {}
    for tipo, (nome, descricao) in PRODUTOS.items():
        achados = _chamar("GET", "/public_api/products/?search=" + urllib.parse.quote(nome) + "&limit=50", token)
        existente = next((p for p in achados.get("results", []) if p["name"] == nome), None)
        if existente is None:
            if not criar:
                print(f"[simulacao] criaria: {nome} ({moeda} {preco}, unico)")
                continue
            existente = _chamar("POST", "/public_api/products/", token, {"name": nome, "price": preco, "description": descricao, "type": "unique", "currency": moeda, "status": "active"})
            print(f"criado: {nome}", file=sys.stderr)
        else:
            print(f"ja existe: {nome}", file=sys.stderr)
        pid = existente["id"]
        detalhe = _chamar("GET", f"/public_api/offers/?product={pid}", token)
        oferta = next((o for o in detalhe.get("results", detalhe) if o.get("default")), None) or next(iter(detalhe.get("results", [])), None)
        produtos[tipo] = pid
        if oferta:
            ofertas[tipo] = oferta["id"]
    print(json.dumps({"credit_offers": ofertas, "credit_products": produtos}, ensure_ascii=False, indent=2))
    if "--faixas" in sys.argv:
        faixas(token, criar)


# spec 37: creditos do Estudio em 4 faixas de preco de marketing, cobradas em reais (os creditos sao dolarizados no servidor)
FAIXAS = (("p99", "99.00", 900), ("p149", "149.00", 1400), ("p199", "199.00", 2000), ("p499", "499.00", 5450))


def faixas(token: str, criar: bool) -> None:
    ofertas, produtos = {}, {}
    for fid, preco, cred in FAIXAS:
        nome = f"Segundo Cérebro - Créditos do Estúdio {cred:,}".replace(",", ".")
        desc = f"{cred:,} créditos para imagens, vídeos, voz e edição com IA no Segundo Cérebro. Liberados na hora, valem por 30 dias.".replace(",", ".")
        achados = _chamar("GET", "/public_api/products/?search=" + urllib.parse.quote(nome) + "&limit=50", token)
        existente = next((p for p in achados.get("results", []) if p["name"] == nome), None)
        if existente is None:
            if not criar:
                print(f"[simulacao] criaria: {nome} (BRL {preco}, unico)")
                continue
            existente = _chamar("POST", "/public_api/products/", token, {"name": nome, "price": preco, "description": desc, "type": "unique", "currency": "BRL", "status": "active"})
            print(f"criado: {nome}", file=sys.stderr)
        else:
            print(f"ja existe: {nome}", file=sys.stderr)
        produtos[fid] = existente["id"]
        detalhe = _chamar("GET", f"/public_api/offers/?product={existente['id']}", token)
        oferta = next((o for o in detalhe.get("results", detalhe) if o.get("default")), None) or next(iter(detalhe.get("results", [])), None)
        if oferta:
            ofertas[fid] = oferta["id"]
    print(json.dumps({"studio_pack_offers": ofertas, "studio_pack_products": produtos}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
