import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Activity, X } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtInt, fmtFull } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, Badge, Field, Select, Input, Button, Skeleton, EmptyState, ErrorState, useLoad } from '../components/ui.jsx'

const PAGE = 50

export function limitsLabel(l) {
  const parts = []
  if (l?.session_limit_tokens != null) parts.push(`sessão ${fmtInt(l.session_limit_tokens)}`)
  if (l?.week_limit_tokens != null) parts.push(`semanal ${fmtInt(l.week_limit_tokens)}`)
  return parts.length ? parts.join(' / ') : 'sem limite'
}

const StatusBadge = ({ s }) => {
  const v = s >= 200 && s < 300 ? 'success' : s === 429 ? 'warning' : s >= 400 ? 'error' : 'default'
  const t = s === 429 ? 'Limite' : s === 403 ? 'Suspensa' : s >= 500 ? 'Falha' : s >= 400 ? 'Recusado' : 'OK'
  return <Badge variant={v} dot>{t} <span className="num font-normal opacity-70">{s}</span></Badge>
}

export default function Usage() {
  const [person, setPerson] = useState('')
  const [day, setDay] = useState('')
  const [page, setPage] = useState(0)
  const people = useLoad(() => api.people(''), [])
  useEffect(() => setPage(0), [person, day])
  const { data, loading, error, reload } = useLoad(
    () => api.usage({ person_id: person, day, limit: PAGE, offset: page * PAGE }), [person, day, page])

  const rows = data?.rows || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PAGE))
  const filtered = person || day
  const plist = Array.isArray(people.data) ? people.data : []

  return (
    <>
      <PageHeader title="Uso" subtitle="Registro das chamadas. Guarda só números, nunca o texto das mensagens, por 90 dias." />
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <Field label="Pessoa" htmlFor="f-person" className="w-full sm:w-64">
          <Select id="f-person" value={person} onChange={(e) => setPerson(e.target.value)}>
            <option value="">Todas as pessoas</option>
            {plist.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label="Dia" htmlFor="f-day" className="w-full sm:w-48">
          <Input id="f-day" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </Field>
        {filtered && <Button variant="ghost" icon={X} onClick={() => { setPerson(''); setDay('') }}>Limpar filtros</Button>}
      </div>

      <Card className="overflow-hidden">
        {error ? <ErrorState message={error} onRetry={reload} />
          : loading && !data ? <div className="p-5 space-y-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-10" />)}</div>
          : rows.length === 0 ? (
            <EmptyState icon={Activity} title={filtered ? 'Nenhuma chamada com esses filtros' : 'Ainda não há uso registrado'}
              text={filtered ? 'Tente outra pessoa ou outro dia.' : 'Assim que alguém usar a API com uma chave, as chamadas aparecem aqui.'} />
          ) : (
            <>
              <div className={'hidden md:block overflow-x-auto transition-opacity ' + (loading ? 'opacity-60' : '')}>
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                      <th className="font-medium px-5 py-3">Hora</th>
                      <th className="font-medium px-3 py-3">Pessoa</th>
                      <th className="font-medium px-3 py-3">Rota</th>
                      <th className="font-medium px-3 py-3 text-right">Entrada</th>
                      <th className="font-medium px-3 py-3 text-right">Saída</th>
                      <th className="font-medium px-3 py-3 text-right">Total</th>
                      <th className="font-medium px-3 py-3">Status</th>
                      <th className="font-medium px-5 py-3">Limites na hora</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50">
                        <td className="px-5 py-3 num whitespace-nowrap text-text-muted">{fmtFull(r.ts)}</td>
                        <td className="px-3 py-3 font-medium text-text-main whitespace-nowrap">{r.name}</td>
                        <td className="px-3 py-3"><code className="text-[12px] font-mono text-text-muted">{r.route}</code></td>
                        <td className="px-3 py-3 num text-right">{fmtInt(r.in_tokens)}</td>
                        <td className="px-3 py-3 num text-right">{fmtInt(r.out_tokens)}</td>
                        <td className="px-3 py-3 num text-right font-semibold text-text-main">{fmtInt(r.total_tokens)}</td>
                        <td className="px-3 py-3"><StatusBadge s={r.status} /></td>
                        <td className="px-5 py-3 text-xs text-text-muted whitespace-nowrap">{limitsLabel(r.limits)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="md:hidden divide-y divide-border-subtle">
                {rows.map((r, i) => (
                  <li key={i} className="p-4 flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-text-main">{r.name}</span>
                      <StatusBadge s={r.status} />
                    </div>
                    <div className="text-xs text-text-muted num">{fmtFull(r.ts)} · <code className="font-mono">{r.route}</code></div>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div><div className="text-text-muted">Entrada</div><div className="num text-sm">{fmtInt(r.in_tokens)}</div></div>
                      <div><div className="text-text-muted">Saída</div><div className="num text-sm">{fmtInt(r.out_tokens)}</div></div>
                      <div><div className="text-text-muted">Total</div><div className="num text-sm font-semibold text-text-main">{fmtInt(r.total_tokens)}</div></div>
                    </div>
                    <div className="text-xs text-text-muted">Limites: {limitsLabel(r.limits)}</div>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-border-subtle text-sm text-text-muted">
                <span className="num">{fmtInt(page * PAGE + 1)}–{fmtInt(Math.min(total, (page + 1) * PAGE))} de {fmtInt(total)}</span>
                <div className="flex items-center gap-2">
                  <Button variant="secondary" size="sm" icon={ChevronLeft} disabled={page === 0} onClick={() => setPage((p) => p - 1)} aria-label="Página anterior">Anterior</Button>
                  <span className="num hidden sm:inline">Página {page + 1} de {pages}</span>
                  <Button variant="secondary" size="sm" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)} aria-label="Próxima página">Próxima<ChevronRight className="size-4" /></Button>
                </div>
              </div>
            </>
          )}
      </Card>
    </>
  )
}
