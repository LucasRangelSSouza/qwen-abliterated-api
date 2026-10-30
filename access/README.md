# llm-access

Porta publica da API de IA (`https://qwen.rangeltech.net`, Spec 14 §5). Fica na frente do `llm-queue`: uma chave por pessoa,
limites opcionais de sessao e semana (estilo Claude Code), consumo por chave e painel em `/admin`. Contrato completo em `DESIGN.md`.

- `app/`: FastAPI + SQLite (stdlib). Chaves guardadas so como SHA-256; nenhum texto de mensagem vai ao banco nem ao log.
- `panel/`: painel React (build em `panel/dist`, servido em `/admin`; sem build, `/admin` avisa e a API segue funcionando).
- `deploy/compose.yml`: stack para a VPS (Traefik, `read_only`, dados em `/opt/platform/data/llm-access`).

## Ambiente
`ACCESS_DB_PATH`, `UPSTREAM_URL`, `UPSTREAM_KEY`, `UPSTREAM_ADMIN_KEY`, `ADMIN_PASSWORD_FILE`, `SESSION_SECRET_FILE`,
`ACCESS_QA_MODE`, `OWNER_KEY_SHA256`, `PLATFORM_KEY_SHA256`, `PORT`. Segredos tambem aceitam o valor direto
(`ADMIN_PASSWORD`, `SESSION_SECRET`). Padroes e significado na tabela do `DESIGN.md`.

## Testes
```
cd access
pip install -r requirements-dev.txt
python -m pytest -q          # upstream falso, relogio injetavel, sem rede e sem GPU
bash scripts/verify.sh       # testes + imagem sobe e responde /health (se houver docker)
```
Rodar local: `uvicorn app.main:create_app --factory --port 8000` (um unico worker).
