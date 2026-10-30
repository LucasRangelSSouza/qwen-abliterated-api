import { useState } from 'react'
import { SlidersHorizontal, Lock } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtInt, fmtDur } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Notice, Skeleton, ErrorState, useToast, useLoad } from '../components/ui.jsx'

// Rotulos conhecidos; qualquer chave extra devolvida pelo backend aparece mesmo assim.
const KNOWN = {
  month_cap_usd: ['Teto de gasto mensal', (v) => fmtUsd(v)],
  day_cap_usd: ['Teto de gasto diário', (v) => fmtUsd(v)],
  idle_shutdown_s: ['Desligar por ociosidade após', (v) => fmtDur(v)],
  idle_shutdown_minutes: ['Desligar por ociosidade após', (v) => `${fmtInt(v)} min`],
  idle_s: ['Desligar por ociosidade após', (v) => fmtDur(v)],
  idle_minutes: ['Desligar por ociosidade após', (v) => `${fmtInt(v)} min`],
  default_session_seconds: ['Janela de sessão padrão', (v) => fmtDur(v)],
  default_week_seconds: ['Janela semanal padrão', (v) => fmtDur(v)],
  usage_retention_days: ['Retenção do registro de uso', (v) => `${fmtInt(v)} dias`],
}
const fmtAny = (v) => typeof v === 'number' ? fmtInt(v) : typeof v === 'object' && v ? JSON.stringify(v) : String(v)
const pretty = (k) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

function flatten(obj, prefix = '') {
  return Object.entries(obj || {}).flatMap(([k, v]) =>
    v && typeof v === 'object' && !Array.isArray(v) ? flatten(v, prefix + k + '.') : [[prefix + k, v]])
}

function PasswordCard() {
  const toast = useToast()
  const [f, setF] = useState({ cur: '', next: '', rep: '' })
  const [err, setErr] = useState({})
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))
  const submit = async (e) => {
    e.preventDefault()
    const er = {}
    if (!f.cur) er.cur = 'Informe a senha atual.'
    if (f.next.length < 8) er.next = 'Use ao menos 8 caracteres.'
    if (f.rep !== f.next) er.rep = 'As senhas não conferem.'
    setErr(er)
    if (Object.keys(er).length) return
    setBusy(true)
    try { await api.password(f.cur, f.next); toast.success('Senha alterada.'); setF({ cur: '', next: '', rep: '' }) }
    catch (ex) { if (ex.status === 400 || ex.status === 403) setErr({ cur: ex.message }); else toast.error(ex.message) }
    finally { setBusy(false) }
  }
  return (
    <Card>
      <CardHeader icon={Lock} title="Senha do administrador" subtitle="Troque quando achar necessário." />
      <form onSubmit={submit} className="px-5 pb-5 flex flex-col gap-4 max-w-md">
        <Field label="Senha atual" htmlFor="pw-cur" error={err.cur}><Input id="pw-cur" type="password" autoComplete="current-password" value={f.cur} onChange={set('cur')} /></Field>
        <Field label="Nova senha" htmlFor="pw-new" error={err.next} hint="Mínimo de 8 caracteres."><Input id="pw-new" type="password" autoComplete="new-password" value={f.next} onChange={set('next')} /></Field>
        <Field label="Repetir nova senha" htmlFor="pw-rep" error={err.rep}><Input id="pw-rep" type="password" autoComplete="new-password" value={f.rep} onChange={set('rep')} /></Field>
        <div><Button type="submit" loading={busy}>Alterar senha</Button></div>
      </form>
    </Card>
  )
}

export default function Settings() {
  const { data, loading, error, reload } = useLoad(() => api.settings(), [])
  const entries = flatten(data).filter(([k]) => k !== 'qa_mode')
  const qa = data && (data.qa_mode ?? data.qa ?? data.access_qa_mode)
  const seen = new Set()
  return (
    <>
      <PageHeader title="Configurações" subtitle="Tetos e tempos em vigor, só leitura, e a senha do painel." />
      <div className="grid gap-4 lg:grid-cols-2 items-start">
        <Card>
          <CardHeader icon={SlidersHorizontal} title="Em vigor no servidor" subtitle="Para alterar, mude a variável de ambiente e reinicie." />
          <div className="px-5 pb-5">
            {error ? <ErrorState message={error} onRetry={reload} />
              : loading ? <div className="space-y-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-9" />)}</div> : (
                <dl className="divide-y divide-border-subtle">
                  <div className="flex items-center justify-between gap-4 py-3">
                    <dt className="text-sm text-text-muted">Modo QA (janelas curtas)</dt>
                    <dd>{qa ? <Badge variant="warning" dot>Ligado</Badge> : <Badge variant="default" dot>Desligado</Badge>}</dd>
                  </div>
                  {entries.map(([k, v]) => {
                    const known = KNOWN[k]
                    const label = known ? known[0] : pretty(k)
                    if (seen.has(label)) return null
                    seen.add(label)
                    return (
                      <div key={k} className="flex items-center justify-between gap-4 py-3">
                        <dt className="text-sm text-text-muted">{label}</dt>
                        <dd className="num text-sm font-semibold text-text-main text-right">{v == null ? '—' : known ? known[1](v) : fmtAny(v)}</dd>
                      </div>
                    )
                  })}
                </dl>
              )}
            {qa && <div className="mt-2"><Notice>Modo QA ligado: aceita janelas menores que 1 hora. Mantenha desligado em produção.</Notice></div>}
          </div>
        </Card>
        <PasswordCard />
      </div>
    </>
  )
}
