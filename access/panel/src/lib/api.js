// Cliente HTTP do painel: caminhos relativos, cookie same-origin, 401 volta ao login.
let onUnauthorized = () => {}
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn }

export class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body }
}

async function request(method, path, body) {
  let res
  try {
    res = await fetch('/admin/api' + path, {
      method,
      credentials: 'same-origin',
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('Sem conexão com o servidor.', 0)
  }
  let data = null
  const text = await res.text()
  if (text) { try { data = JSON.parse(text) } catch { data = null } }
  if (res.status === 401 && path !== '/login' && path !== '/me') onUnauthorized()
  if (!res.ok) {
    const detail = data && (data.detail || data.error?.message || data.message)
    const msg = typeof detail === 'string' ? detail
      : res.status === 401 ? 'Não autorizado.' : `Erro ${res.status}`
    throw new ApiError(msg, res.status, data)
  }
  return data
}

const qs = (o) => {
  const p = new URLSearchParams()
  Object.entries(o).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v) })
  const s = p.toString()
  return s ? '?' + s : ''
}

export const api = {
  login: (password) => request('POST', '/login', { password }),
  logout: () => request('POST', '/logout'),
  me: () => request('GET', '/me'),
  people: (q) => request('GET', '/people' + qs({ q })),
  createPerson: (b) => request('POST', '/people', b),
  updatePerson: (id, b) => request('PATCH', `/people/${id}`, b),
  suspend: (id) => request('POST', `/people/${id}/suspend`),
  resume: (id) => request('POST', `/people/${id}/resume`),
  rotateKey: (id) => request('POST', `/people/${id}/rotate-key`),
  resetWindows: (id) => request('POST', `/people/${id}/reset-windows`),
  resetAll: () => request('POST', '/reset-all-windows'),
  usage: (o) => request('GET', '/usage' + qs(o)),
  machines: (limit = 50) => request('GET', '/machines' + qs({ limit })),
  queue: () => request('GET', '/queue'),
  subscribers: (q, status) => request('GET', '/subscribers' + qs({ q, status })),
  createSubscriber: (b) => request('POST', '/subscribers', b),
  enableSubscriber: (id) => request('POST', `/subscribers/${id}/enable`),
  disableSubscriber: (id) => request('POST', `/subscribers/${id}/disable`),
  deleteSubscriber: (id) => request('DELETE', `/subscribers/${id}`),
  passwordLink: (id) => request('POST', `/subscribers/${id}/password-link`),
  plans: () => request('GET', '/plans'),
  putPlan: (id, b) => request('PUT', `/plans/${id}`, b),
  billingSettings: () => request('GET', '/billing/settings'),
  putBillingSettings: (b) => request('PUT', '/billing/settings', b),
  billingEvents: (limit = 30) => request('GET', '/billing/events' + qs({ limit })),
  safety: () => request('GET', '/safety'),
  setSafetyInput: (b) => request('PUT', '/safety/input', b),
  setSafetyOutput: (b) => request('PUT', '/safety/output', b),
  safetyLog: (classifier, limit = 50) => request('GET', '/safety/log' + qs({ classifier, limit })),
  billing: () => request('GET', '/billing'),
  metrics: (machine, hours = 24, days = 14) => request('GET', '/metrics' + qs({ machine, hours, days })),
  mediaStatus: () => request('GET', '/media/status'),
  mediaJobs: (limit = 40) => request('GET', '/media/jobs' + qs({ limit })),
  settings: () => request('GET', '/settings'),
  gpuConfig: () => request('GET', '/gpu-config'),
  setGpuConfig: (b) => request('PUT', '/gpu-config', b),
  vms: () => request('GET', '/vms'),
  vmConfig: () => request('GET', '/vms/config'),
  putVmConfig: (b) => request('PUT', '/vms/config', b),
  createVm: (b) => request('POST', '/vms', b),
  provisionVm: (id) => request('POST', `/vms/${id}/provision`),
  suspendVm: (id) => request('POST', `/vms/${id}/suspend`),
  resumeVm: (id) => request('POST', `/vms/${id}/resume`),
  cancelVm: (id) => request('POST', `/vms/${id}/cancel`),
  syncVms: () => request('POST', '/vms/sync'),
  password: (current, next) => request('POST', '/password', { current, new: next }),
}
