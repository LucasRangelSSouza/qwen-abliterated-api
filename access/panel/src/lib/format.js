const TZ = 'America/Sao_Paulo'
const nf = new Intl.NumberFormat('pt-BR')
export const fmtInt = (n) => (n == null ? '—' : nf.format(Math.round(n)))
export const fmtUsd = (n) => (n == null ? '—' :
  new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: n > 0 && n < 1 ? 3 : 2 }).format(n))
// aceita ISO ou epoch (segundos ou milissegundos)
const toDate = (v) => (v == null ? null : new Date(typeof v === 'number' && v < 1e12 ? v * 1000 : v))
const dtf = (opts) => new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, ...opts })
const fmtWith = (f) => (v) => { const d = toDate(v); return !d || isNaN(d) ? '—' : f.format(d) }
export const fmtDateTime = fmtWith(dtf({ day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }))
export const fmtFull = fmtWith(dtf({ dateStyle: 'short', timeStyle: 'medium' }))
export const fmtDur = (s) => {
  if (s == null) return '—'
  s = Math.round(s)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60), r = s % 60
  if (m < 60) return r && m < 10 ? `${m} min ${r} s` : `${m} min`
  const h = Math.floor(m / 60), mm = m % 60
  if (h < 48) return mm ? `${h} h ${mm} min` : `${h} h`
  const d = Math.floor(h / 24), hh = h % 24
  return hh ? `${d} d ${hh} h` : `${d} d`
}
export const fmtIn = (v) => {
  const d = toDate(v); if (!d) return null
  const s = (d.getTime() - Date.now()) / 1000
  return s <= 0 ? 'agora' : 'em ' + fmtDur(s)
}
export const fmtAgo = (v) => {
  const d = toDate(v); if (!d) return 'nunca'
  const s = (Date.now() - d.getTime()) / 1000
  return s < 60 ? 'agora há pouco' : 'há ' + fmtDur(Math.round(s / 60) * 60)
}
export const todayBR = () => new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(new Date())
export const cn = (...a) => a.filter(Boolean).join(' ')
