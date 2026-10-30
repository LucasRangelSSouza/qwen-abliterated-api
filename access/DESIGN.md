# llm-access: desenho e contrato (Spec 14 §5)

Servico unico exposto ao publico (`https://qwen.rangeltech.net`). Fica na frente do `llm-queue` (rede interna). Python 3.12 + FastAPI, SQLite num volume, painel React estatico servido pelo mesmo processo em `/admin`.

## Ambiente
| var | padrao | uso |
|---|---|---|
| `ACCESS_DB_PATH` | `/data/access.db` | SQLite (WAL) |
| `UPSTREAM_URL` | `http://llm-queue.rt:8000` | base do llm-queue (`/v1/...`) |
| `UPSTREAM_KEY` | | chave de consumidor do queue usada pelo llm-access |
| `UPSTREAM_ADMIN_KEY` | | `X-Admin-Key` do queue para `/admin/gpu/cycles` |
| `ADMIN_PASSWORD_FILE` | | arquivo com a senha do admin (so o hash fica na memoria) |
| `SESSION_SECRET_FILE` | | segredo para assinar o cookie do painel |
| `ACCESS_QA_MODE` | `0` | `1` libera `session_seconds`/`week_seconds` menores que 1 h (so QA) |
| `OWNER_KEY_SHA256`, `PLATFORM_KEY_SHA256` | | chaves especiais (dono sem limite; LUAR) validas mesmo sem banco |
| `PORT` | `8000` | |

## Dados (SQLite)
- `people(id, name, note, status[active|suspended], created_at)`
- `keys(id, person_id, key_hash, key_last4, created_at, revoked_at)` (so SHA-256; a chave aparece uma vez)
- `limits(person_id, session_limit_tokens NULL, week_limit_tokens NULL, session_seconds DEFAULT 18000, week_seconds DEFAULT 604800)`
- `windows(person_id, kind[session|week], start_ts, end_ts, used_tokens, reserved_tokens)`
- `usage_log(id, ts, person_id, key_id, route, in_tokens, out_tokens, total_tokens, status, limits_json)` (nunca guarda texto de mensagem; retem 90 dias)

## Regras de janela (copiadas do Claude Code, Spec 14 §5.3)
1. Dois limites independentes e opcionais por pessoa; estourar qualquer um bloqueia (HTTP 429 com `Retry-After` e corpo OpenAI com `code: usage_limit_reached`, `window: session|week`, `resets_at`).
2. A janela abre no primeiro pedido que de fato usa a GPU depois que a anterior venceu e dura `*_seconds`. Pedido que falha antes de qualquer token real (503 da fila, erro de GPU, cancelamento sem dado) **nao abre janela e nao cobra**: a reserva e desfeita.
3. Relogios independentes; um saldo por chave (qualquer cliente que use a chave gasta do mesmo saldo).
4. Reserva atomica antes de executar: `estimativa_entrada + max_tokens` (teto; se `max_tokens` ausente, usa `DEFAULT_MAX_TOKENS=4096`). Se `reserva > restante` em qualquer janela ativa: 429 antes de chamar o upstream, dizendo quanto falta. No fim reconcilia com `usage.total_tokens` do upstream (stream: o servico injeta `stream_options.include_usage=true` e le o ultimo chunk). Stream cortado: cobra o que foi entregue (estimativa por caracteres, ~3 caracteres/token) ou o `usage` se chegou.
5. Concorrencia: duas requisicoes disputando o ultimo saldo nunca estouram alem do reservado (transacao `BEGIN IMMEDIATE`).
6. Sem limite marcado em uma janela = sem limite nela. Chave do dono e do LUAR: sem janela, mas o uso e registrado.

## API publica (mesma da OpenAI, repassa ao queue)
`POST /v1/chat/completions`, `POST /v1/embeddings`, `GET /v1/models`, `GET /health` (sem auth), `GET /v1/usage`.
- Auth: `Authorization: Bearer sk-...`; 401 se invalida/revogada, 403 `account_suspended` se suspensa.
- Repassa tudo do upstream byte a byte no stream (inclui comentarios SSE `: acordando` e chunks de aviso com `x_queue`), preservando cabecalhos de espera (`X-LLM-State`, `Retry-After`) e repassando `X-Queue-Class`, `X-Queue-Wait`, `X-Queue-Notice`, `X-End-User`.
- `GET /v1/usage` (auth pela propria chave): `{"person":"nome","session":{"limit":int|null,"used":int,"remaining":int|null,"resets_at":iso|null},"week":{...}}`.

## API do painel (`/admin/api/*`, cookie assinado HttpOnly+SameSite=Lax; 401 sem login)
- `POST /admin/api/login {"password"}` / `POST /admin/api/logout` / `GET /admin/api/me`
- `GET /admin/api/people?q=` -> `[{id,name,note,status,key_last4,limits:{session_limit_tokens,week_limit_tokens,session_seconds,week_seconds},session:{used,limit,resets_at},week:{used,limit,resets_at},last_used_at}]`
- `POST /admin/api/people {name,note,session_limit_tokens|null,week_limit_tokens|null,session_seconds?,week_seconds?}` -> `{person, api_key}` (a chave completa so aqui)
- `PATCH /admin/api/people/{id}` (nome, observacao, limites); `POST .../suspend`, `.../resume`, `.../rotate-key` (-> `{api_key}`), `.../reset-windows`; `POST /admin/api/reset-all-windows`
- `GET /admin/api/usage?person_id=&day=YYYY-MM-DD&limit=100&offset=0` -> `{total, rows:[{ts,person_id,name,route,in_tokens,out_tokens,total_tokens,status,limits:{session_limit_tokens,week_limit_tokens}}]}`
- `GET /admin/api/machines?limit=50` -> `{status:{state,eta_s,spend:{month_usd,day_usd,month_cap_usd,remaining_usd}}, cycles:[{id,kind:"cold"|"restart",started_at,ready_at,cold_start_s,stages:{image_s,download_s,vllm_s,warmup_s},host,geo,price_dph,stopped_at,stop_reason,cost_usd}]}` (vem do queue: `GET /v1/gpu/status` e `GET /admin/gpu/cycles`)
- `GET /admin/api/settings` (somente leitura: tetos e ociosidade do queue, modo QA) e `POST /admin/api/password {current,new}`
- Painel estatico: `GET /admin` e `/admin/*` (SPA, fallback `index.html`), assets em `/admin/assets/*`.
