// Mock em memoria do contrato /admin/api/* (so para `VITE_MOCK=1 npm run dev`).
// Senha de demonstracao: admin
const H = 3600e3
const now = () => Date.now()
const iso = (t) => new Date(t).toISOString()

let auth = (() => { try { return sessionStorage.getItem('mock_auth') === '1' } catch { return false } })()
let password = 'admin'
let seq = 10

const mkLimits = (s, w, ss = 18000, ws = 604800) => ({ session_limit_tokens: s, week_limit_tokens: w, session_seconds: ss, week_seconds: ws })

const people = [
  { id: 1, name: 'Lucas Rangel', note: 'Dono, chave sem limite (Claude Code e LUAR)', status: 'active', key_last4: 'a91f',
    limits: mkLimits(null, null), sess: { used: 182340, resets: null }, week: { used: 1204500, resets: null }, last: now() - 4 * 60e3 },
  { id: 2, name: 'Marina Duarte', note: 'Agente de atendimento do LUAR (loja Sol)', status: 'active', key_last4: '7c2e',
    limits: mkLimits(500000, 2500000), sess: { used: 342100, resets: now() + 2.7 * H }, week: { used: 1180000, resets: now() + 4.1 * 24 * H }, last: now() - 12 * 60e3 },
  { id: 3, name: 'Paulo Henrique', note: 'Testes do RAgent 2.0', status: 'active', key_last4: 'b04d',
    limits: mkLimits(100000, null), sess: { used: 96800, resets: now() + 0.8 * H }, week: { used: 0, resets: null }, last: now() - 26 * 60e3 },
  { id: 4, name: 'Cliente Demo Padaria', note: 'Somente limite semanal', status: 'active', key_last4: '31e8',
    limits: mkLimits(null, 400000), sess: { used: 0, resets: null }, week: { used: 148000, resets: now() + 5.6 * 24 * H }, last: now() - 3 * 24 * H },
  { id: 5, name: 'Juliana Prado', note: 'Plataforma Senior, piloto', status: 'suspended', key_last4: '0d55',
    limits: mkLimits(200000, 800000), sess: { used: 0, resets: null }, week: { used: 612000, resets: now() + 1.2 * 24 * H }, last: now() - 6 * 24 * H },
  { id: 6, name: 'QA janela curta', note: 'Modo QA: sessao 2 min, semana 5 min', status: 'active', key_last4: 'f6a2',
    limits: mkLimits(100, 200, 120, 300), sess: { used: 100, resets: now() + 70e3 }, week: { used: 100, resets: now() + 4 * 60e3 }, last: now() - 50e3 },
  { id: 7, name: 'Beatriz Nogueira', note: '', status: 'active', key_last4: '9bb3',
    limits: mkLimits(150000, 600000), sess: { used: 0, resets: null }, week: { used: 0, resets: null }, last: null },
]

const routes = ['/v1/chat/completions', '/v1/chat/completions', '/v1/chat/completions', '/v1/embeddings', '/v1/models']
const statuses = [200, 200, 200, 200, 200, 200, 200, 429, 503, 200]
const usageRows = (() => {
  const rows = []
  let t = now() - 5 * 60e3
  for (let i = 0; i < 260; i++) {
    const p = people[(i * 7 + (i % 3)) % people.length]
    const route = routes[i % routes.length]
    const status = statuses[(i * 3) % statuses.length]
    const emb = route === '/v1/embeddings'
    const inT = route === '/v1/models' ? 0 : emb ? 40 + (i * 37) % 900 : 200 + (i * 211) % 7800
    const outT = emb || route === '/v1/models' || status !== 200 ? 0 : 30 + (i * 97) % 2400
    rows.push({
      ts: iso(t), person_id: p.id, name: p.name, route, in_tokens: status === 200 ? inT : 0, out_tokens: outT,
      total_tokens: status === 200 ? inT + outT : 0, status,
      limits: { session_limit_tokens: p.limits.session_limit_tokens, week_limit_tokens: p.limits.week_limit_tokens },
    })
    t -= (2 + (i * 13) % 55) * 60e3 * (i > 120 ? 3 : 1)
  }
  return rows
})()

const cycles = [
  { id: 41, kind: 'restart', started_at: iso(now() - 3.2 * H), ready_at: iso(now() - 3.2 * H + 188e3), cold_start_s: 188,
    stages: { image_s: 12, download_s: 31, vllm_s: 96, warmup_s: 49 }, host: 'host-8821', geo: 'US-TX', price_dph: 0.42, stopped_at: null, stop_reason: null, cost_usd: 1.34 },
  { id: 40, kind: 'cold', started_at: iso(now() - 1.1 * 24 * H), ready_at: iso(now() - 1.1 * 24 * H + 412e3), cold_start_s: 412,
    stages: { image_s: 188, download_s: 71, vllm_s: 98, warmup_s: 55 }, host: 'host-5503', geo: 'PL-Warsaw', price_dph: 0.39, stopped_at: iso(now() - 1.1 * 24 * H + 2.6 * H), stop_reason: 'ociosidade', cost_usd: 1.01 },
  { id: 39, kind: 'cold', started_at: iso(now() - 2.3 * 24 * H), ready_at: null, cold_start_s: null,
    stages: { image_s: 480, download_s: null, vllm_s: null, warmup_s: null }, host: 'host-1190', geo: 'SE-Stockholm', price_dph: 0.45, stopped_at: iso(now() - 2.3 * 24 * H + 0.2 * H), stop_reason: 'erro', cost_usd: 0.09 },
  { id: 38, kind: 'restart', started_at: iso(now() - 3.4 * 24 * H), ready_at: iso(now() - 3.4 * 24 * H + 171e3), cold_start_s: 171,
    stages: { image_s: 9, download_s: 28, vllm_s: 90, warmup_s: 44 }, host: 'host-5503', geo: 'PL-Warsaw', price_dph: 0.39, stopped_at: iso(now() - 3.4 * 24 * H + 1.4 * H), stop_reason: 'comando do admin', cost_usd: 0.55 },
  { id: 37, kind: 'cold', started_at: iso(now() - 4.6 * 24 * H), ready_at: iso(now() - 4.6 * 24 * H + 1090e3), cold_start_s: 1090,
    stages: { image_s: 640, download_s: 288, vllm_s: 102, warmup_s: 60 }, host: 'host-7712', geo: 'US-CA', price_dph: 0.47, stopped_at: iso(now() - 4.6 * 24 * H + 3.1 * H), stop_reason: 'ociosidade', cost_usd: 1.46 },
]

const nowS = () => Math.floor(Date.now() / 1000)
const status = () => ({ state: process_state, eta_s: process_state === 'starting' ? 142 : null, path: 'new',
  spend: { month_usd: 18.62, day_usd: 1.34, month_cap_usd: 60, remaining_usd: 41.38 },
  limits: { idle_min: 30, max_allin_dph: 0.45 },
  instance: process_state === 'off' ? null : { id: 58419432, machine: 150100, geo: 'Bulgaria, BG', dph: 0.4236, stopped: false },
  attempts: [
    { t: nowS() - 1900, offer: 1, machine: 137429, geo: 'Czechia, CZ', dph: 0.421, down_mbps: 3064, known: false, outcome: 'nao_chegou_a_running', detail: 'status=loading msg=Pull complete' },
    { t: nowS() - 1500, offer: 2, machine: 92165, geo: 'Quebec, CA', dph: 0.386, down_mbps: 656, known: false, outcome: 'erro_gpu', detail: 'failed to inject CDI devices: unresolvable CDI devices' },
    { t: nowS() - 900, offer: 3, machine: 55194, geo: 'Bulgaria, BG', dph: 0.4236, down_mbps: 1100, known: true, outcome: 'oferta_sumiu', detail: 'no_such_ask' },
    { t: nowS() - 600, offer: 4, machine: 150100, geo: 'Bulgaria, BG', dph: 0.4236, down_mbps: 870, known: false, outcome: 'running', detail: 'em 818s' },
  ] })
let safetyMock = null
let process_state = 'ready'

const settings = {
  month_cap_usd: 60, day_cap_usd: 8, idle_shutdown_s: 900, idle_shutdown_minutes: 15,
  qa_mode: true, default_session_seconds: 18000, default_week_seconds: 604800, usage_retention_days: 90,
}

let gpuConfig = { auto_shutdown: false, idle_min: 30, reserved: false, static: false, static_instance_id: null }

const sessionOf = (p) => ({ used: p.sess.used, limit: p.limits.session_limit_tokens, resets_at: p.sess.resets && p.sess.resets > now() ? iso(p.sess.resets) : null })
const weekOf = (p) => ({ used: p.week.used, limit: p.limits.week_limit_tokens, resets_at: p.week.resets && p.week.resets > now() ? iso(p.week.resets) : null })
const view = (p) => ({ id: p.id, name: p.name, note: p.note, status: p.status, key_last4: p.key_last4,
  limits: { ...p.limits }, session: sessionOf(p), week: weekOf(p), last_used_at: p.last ? iso(p.last) : null })
const newKey = () => 'sk-' + Array.from({ length: 40 }, () => 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(Math.random() * 36)]).join('')

const json = (data, status = 200) => new Response(data == null ? '' : JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const err = (status, detail) => json({ detail }, status)

function handle(method, url, body) {
  const path = url.pathname.replace(/^\/admin\/api/, '')
  const q = url.searchParams
  if (method === 'POST' && path === '/login') {
    if (body.password !== password) return err(401, 'Senha incorreta.')
    auth = true; try { sessionStorage.setItem('mock_auth', '1') } catch {}
    return json({ ok: true })
  }
  if (!auth) return err(401, 'Não autorizado.')
  if (method === 'POST' && path === '/logout') { auth = false; try { sessionStorage.removeItem('mock_auth') } catch {} return json({ ok: true }) }
  if (method === 'GET' && path === '/me') return json({ admin: true })
  if (method === 'GET' && path === '/people') {
    const s = (q.get('q') || '').toLowerCase()
    if (s === 'erro') return err(500, 'Falha simulada ao listar pessoas.')
    return json(people.filter((p) => !s || (p.name + ' ' + p.note).toLowerCase().includes(s)).map(view))
  }
  if (method === 'POST' && path === '/people') {
    if (!body.name?.trim()) return err(422, 'Informe o nome.')
    const p = { id: ++seq, name: body.name.trim(), note: body.note || '', status: 'active', key_last4: '', last: null,
      limits: mkLimits(body.session_limit_tokens ?? null, body.week_limit_tokens ?? null, body.session_seconds || 18000, body.week_seconds || 604800),
      sess: { used: 0, resets: null }, week: { used: 0, resets: null } }
    const key = newKey(); p.key_last4 = key.slice(-4); people.unshift(p)
    return json({ person: view(p), api_key: key })
  }
  let m = path.match(/^\/people\/(\d+)(?:\/(\w[\w-]*))?$/)
  if (m) {
    const p = people.find((x) => x.id === +m[1]); if (!p) return err(404, 'Pessoa não encontrada.')
    if (method === 'PATCH') {
      if (body.name !== undefined) p.name = body.name
      if (body.note !== undefined) p.note = body.note
      for (const k of ['session_limit_tokens', 'week_limit_tokens', 'session_seconds', 'week_seconds']) {
        const v = body[k] !== undefined ? body[k] : body.limits?.[k]
        if (v !== undefined) p.limits[k] = v
      }
      return json(view(p))
    }
    if (method === 'POST') {
      if (m[2] === 'suspend') { p.status = 'suspended'; return json(view(p)) }
      if (m[2] === 'resume') { p.status = 'active'; return json(view(p)) }
      if (m[2] === 'rotate-key') { const k = newKey(); p.key_last4 = k.slice(-4); return json({ api_key: k }) }
      if (m[2] === 'reset-windows') { p.sess = { used: 0, resets: null }; p.week = { used: 0, resets: null }; return json(view(p)) }
    }
  }
  if (method === 'POST' && path === '/reset-all-windows') { people.forEach((p) => { p.sess = { used: 0, resets: null }; p.week = { used: 0, resets: null } }); return json({ ok: true }) }
  if (method === 'GET' && path === '/usage') {
    const pid = q.get('person_id'), day = q.get('day'), limit = +(q.get('limit') || 100), offset = +(q.get('offset') || 0)
    const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' })
    let rows = usageRows.filter((r) => (!pid || String(r.person_id) === pid) && (!day || fmt.format(new Date(r.ts)) === day))
    if (pid === '7') rows = []
    return json({ total: rows.length, rows: rows.slice(offset, offset + limit) })
  }
  if (method === 'GET' && path === '/queue') {
    const n = nowS()
    const off = process_state === 'off'
    const waitState = process_state === 'ready' ? 'na_fila' : 'aguardando_gpu'
    return json({ gpu_state: process_state.toUpperCase(), lanes: { chat: { waiting: off ? 0 : 2, inflight: off ? 0 : 1, limit: 6, served: 412 }, embed: { waiting: 0, inflight: 0, limit: 8, served: 0 } },
      waiting: off ? [] : [
        { id: 131, state: waitState, kind: 'chat', consumer: 'llm-access', end_user: 'Marina Duarte', cls: 'interactive', enq: n - 42, waited_s: 42, running_s: null },
        { id: 132, state: waitState, kind: 'chat', consumer: 'llm-access', end_user: 'platform', cls: 'interactive', enq: n - 9, waited_s: 9, running_s: null }],
      running: off ? [] : [{ id: 130, state: 'executando', kind: 'chat', consumer: 'llm-access', end_user: 'dono', cls: 'interactive', enq: n - 80, waited_s: 3, running_s: 77 }],
      recent: [
        { id: 129, kind: 'chat', consumer: 'llm-access', end_user: 'Paulo Henrique', cls: 'interactive', enq: n - 400, start: n - 396, end: n - 351, code: '200' },
        { id: 128, kind: 'chat', consumer: 'llm-access', end_user: 'Marina Duarte', cls: 'interactive', enq: n - 700, start: n - 699, end: n - 640, code: '200' },
        { id: 127, kind: 'embed', consumer: 'llm-access', end_user: 'platform', cls: 'batch', enq: n - 900, start: null, end: n - 850, code: '503' }],
      error: null })
  }
  if (path === '/safety' || path.startsWith('/safety/')) {
    safetyMock = safetyMock || { input: { enabled: true, backend: 'rules', categories: ['csam', 'cbrn', 'malware', 'fraud', 'violence', 'selfharm'], model_url: '', model_name: '', refusal: '', model_key_set: false, all_categories: ['csam', 'cbrn', 'malware', 'fraud', 'violence', 'selfharm'], blocked_24h: { cbrn: 2, fraud: 1 } },
      output: { enabled: true, backend: 'both', categories: ['csam', 'cbrn', 'malware', 'fraud', 'violence', 'selfharm', 'destructive_action', 'credential_exfil'], model_url: 'http://guard.rt:8000/v1', model_name: 'Qwen3Guard-Gen-4B', refusal: '', model_key_set: true, all_categories: ['csam', 'cbrn', 'malware', 'fraud', 'violence', 'selfharm', 'destructive_action', 'credential_exfil'], blocked_24h: { destructive_action: 3 } } }
    if (method === 'GET' && path === '/safety') return json({ people: { level1: people.filter((p) => (p.safety_level || 1) === 1).length, level2: people.filter((p) => p.safety_level === 2).length }, input: safetyMock.input, output: safetyMock.output })
    if (method === 'PUT') { const w = path.endsWith('/input') ? 'input' : 'output'; const { model_key, ...rest } = body; safetyMock[w] = { ...safetyMock[w], ...rest, model_key_set: safetyMock[w].model_key_set || !!model_key }; return json(safetyMock[w]) }
    if (method === 'GET' && path === '/safety/log') { const n = nowS(); return json({ classifier: q.get('classifier'), rows: [{ ts: n - 600, person: 'Marina Duarte', category: q.get('classifier') === 'input' ? 'cbrn' : 'destructive_action', route: 'chat', source: 'rules' }, { ts: n - 7200, person: 'Paulo Henrique', category: q.get('classifier') === 'input' ? 'fraud' : 'destructive_action', route: 'chat', source: 'model' }] }) }
  }
  if (method === 'GET' && path === '/billing') {
    const n = nowS(), mk = (id, label, gpu, ng, dph, gpuDph, st, loc, ram, disk, start, end) => ({ id, label, status: 'running', gpu, num_gpus: ng, location: loc, dph, dph_gpu: gpuDph, storage_dph: st, disk_gb: disk, cpu_ram_gb: ram, started: start, offer_end: end, running: true, monthly_usd: +(dph * 720).toFixed(2) })
    return json({ credit_usd: 77.08, burn_per_hour_usd: 0.4166, burn_per_day_usd: 10.0, burn_per_month_usd: 300.0, days_of_balance: 7.7, recharge_by: n + 7.7 * 86400, recharge_needed_for_30d_usd: 222.92,
      machines: { llm: mk(58831117, 'hold-cz-25792', 'RTX 3090', 2, 0.2893, 0.28, 0.0093, 'Czechia, CZ', 94, 50, n - 2 * 86400, 1817059800), image: mk(59106171, 'midia-gpu-1', 'RTX 3090', 1, 0.1276, 0.1053, 0.0222, 'Brazil, BR', 16, 80, n - 4 * 3600, 1809098968), other: [] } })
  }
  if (method === 'GET' && path === '/metrics') {
    const n = nowS(), img = q.get('machine') === 'image', t0 = Math.floor(n / 3600) * 3600
    const hourly = Array.from({ length: 24 }, (_, i) => { const t = t0 - (23 - i) * 3600, h = new Date(t * 1000).getUTCHours(), peak = Math.exp(-((h - 15) ** 2) / 18), r = Math.round((img ? 14 : 30) * peak + (i % 3)); return { t, requests: r, errors: r > 20 ? 1 : 0, wait_p50: +(img ? 1.2 : 0.6 + peak * 2).toFixed(1), wait_p95: +(img ? 4 + peak * 20 : 3 + peak * 40).toFixed(1), run_p50: img ? 7.5 : 14 + peak * 8, run_p95: img ? 10 + peak * 3 : 38 + peak * 25, busy_pct: Math.min(100, Math.round(r * (img ? 6 : 2.4))) } })
    const d0 = Math.floor(n / 86400) * 86400
    const daily = Array.from({ length: 14 }, (_, i) => ({ t: d0 - (13 - i) * 86400, requests: Math.round((img ? 40 : 190) * (0.5 + 0.5 * Math.abs(Math.sin(i * 1.7)))), errors: i % 5 === 0 ? 2 : 0, run_p50: img ? 7.8 : 16, run_p95: img ? 11 : 52, wait_p50: 1, wait_p95: 9, busy_pct: 30 }))
    const pk = hourly.reduce((a, b) => (b.requests > a.requests ? b : a))
    return json({ machine: img ? 'image' : 'llm', hourly, daily, peak_hour: { t: pk.t, requests: pk.requests },
      totals: { requests: daily.reduce((a, b) => a + b.requests, 0), ok: 0, errors: 7, wait_p50: 1.1, wait_p95: 14.2, run_p50: img ? 7.8 : 16.4, run_p95: img ? 11.6 : 58 },
      top_users: [{ who: 'Marina Duarte', requests: 212 }, { who: 'dono', requests: 150 }, { who: 'platform', requests: 96 }] })
  }
  if (method === 'GET' && path === '/media/status') {
    return json({ enabled: true, state: process_state === 'off' ? 'off' : process_state === 'ready' ? 'ready' : 'starting', tunnel: process_state === 'ready',
      instance: { id: 59106171, status: 'running', dph: 0.1276, gpu: 'RTX 3090', num_gpus: 1, location: 'Brazil, BR', label: 'midia-gpu-1', disk_gb: 80, cpu_ram_gb: 16 },
      comfy: { version: '0.3.68', gpu: 'NVIDIA GeForce RTX 3090', vram_total: 25769803776, vram_free: 9437184000 }, lane: { waiting: 1, inflight: 1, limit: 1, served: 87 },
      recipes: [{ id: 'flux-schnell', kind: 'image', title: 'Flux schnell (rápido, boa qualidade)' }],
      today: { done: 54, failed: 2, avg_seconds: 7.8 }, monthly_cost_usd: 91.87, error: null })
  }
  if (method === 'GET' && path === '/media/jobs') {
    const n = nowS()
    const P = ['um hambúrguer suculento com queijo derretido, fundo escuro, fotografia de estúdio', 'capa de curso de Excel, mesa de escritório, tons de azul', 'caricatura de um chef sorrindo, estilo cartoon', 'avatar de uma professora, ilustração limpa', 'propaganda de café gelado em copo alto, luz de manhã', 'logo minimalista de uma padaria, fundo claro']
    const mk = (i, who, st, ago, sec) => ({ id: 'img' + i, state: st, recipe: 'flux-schnell', kind: 'image', consumer: 'llm-access', end_user: who, cls: 'interactive', prompt: P[i % P.length], size: '1024x1024', enq: n - ago, started: n - ago + 1, finished: st === 'done' || st === 'failed' ? n - ago + sec : null, seconds: sec, error: st === 'failed' ? 'o ComfyUI recusou o pedido: out of memory' : null })
    return json({ enabled: true, lane: { waiting: 1, inflight: 1, limit: 1, served: 87 },
      waiting: [{ ...mk(7, 'Paulo Henrique', 'na_fila', 6, null), waited_s: 6 }],
      running: [{ ...mk(8, 'Marina Duarte', 'executando', 5, null), running_s: 4.6 }],
      recent: [mk(1, 'dono', 'done', 40, 7.4), mk(2, 'Marina Duarte', 'done', 70, 8.1), mk(3, 'platform', 'done', 120, 6.9), mk(4, 'Paulo Henrique', 'failed', 180, 3.2), mk(5, 'dono', 'done', 260, 7.7), mk(6, 'Marina Duarte', 'done', 340, 8.5), mk(9, 'platform', 'done', 420, 7.2), mk(10, 'dono', 'done', 520, 7.9), mk(11, 'Paulo Henrique', 'done', 640, 8.8), mk(12, 'Marina Duarte', 'done', 800, 7.1)] })
  }
  if (method === 'GET' && path === '/machines') return json({ status: status(), cycles: cycles.slice(0, +(q.get('limit') || 50)) })
  if (method === 'GET' && path === '/settings') return json(settings)
  if (path === '/gpu-config') {
    if (method === 'GET') return json(gpuConfig)
    if (method === 'PUT') {
      if (body.idle_min !== undefined && !(body.idle_min >= 1 && body.idle_min <= 1440)) return err(422, 'idle_min deve ser um número entre 1 e 1440')
      gpuConfig = { ...gpuConfig, ...body }; return json(gpuConfig)
    }
  }
  if (method === 'POST' && path === '/password') {
    if (body.current !== password) return err(400, 'Senha atual incorreta.')
    if ((body.new || '').length < 8) return err(422, 'A nova senha precisa ter ao menos 8 caracteres.')
    password = body.new; return json({ ok: true })
  }
  return err(404, 'Rota não encontrada.')
}

export function installMock() {
  const real = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href)
    if (!url.pathname.startsWith('/admin/api/')) return real(input, init)
    await new Promise((r) => setTimeout(r, 180 + Math.random() * 220))
    let body = {}
    try { body = init.body ? JSON.parse(init.body) : {} } catch {}
    const params = new URLSearchParams(location.search)
    if (params.get('mock_state')) process_state = params.get('mock_state')
    if (params.get('mock_empty') && url.pathname.endsWith('/people')) return json([])
    if (params.get('mock_error') && url.pathname.endsWith('/machines')) return json({ ...handle('GET', url, {}) && JSON.parse(await handle('GET', url, {}).text()), error: 'queue indisponível: GET /admin/gpu/cycles respondeu 502' })
    return handle((init.method || 'GET').toUpperCase(), url, body)
  }
}
