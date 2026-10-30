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

const status = () => ({ state: process_state, eta_s: process_state === 'starting' ? 142 : null,
  spend: { month_usd: 18.62, day_usd: 1.34, month_cap_usd: 60, remaining_usd: 41.38 } })
let process_state = 'ready'

const settings = {
  month_cap_usd: 60, day_cap_usd: 8, idle_shutdown_s: 900, idle_shutdown_minutes: 15,
  qa_mode: true, default_session_seconds: 18000, default_week_seconds: 604800, usage_retention_days: 90,
}

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
  if (method === 'GET' && path === '/machines') return json({ status: status(), cycles: cycles.slice(0, +(q.get('limit') || 50)) })
  if (method === 'GET' && path === '/settings') return json(settings)
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
