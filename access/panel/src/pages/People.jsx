import { useEffect, useState } from 'react'
import { Plus, Search, Pencil, Pause, Play, KeyRound, RotateCcw, ChevronDown, Users as UsersIcon } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtInt, fmtIn, fmtAgo, cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import {
  Button, IconButton, Card, Badge, Field, Input, Textarea, Toggle, Progress, Drawer, ConfirmModal, KeyReveal,
  Skeleton, EmptyState, ErrorState, useToast, useLoad,
} from '../components/ui.jsx'

const DEF_S = 18000, DEF_W = 604800
const digits = (v) => v.replace(/\D/g, '')
const showNum = (v) => (v === '' ? '' : fmtInt(+v))

function WindowCell({ w }) {
  const limited = w.limit != null
  const reopens = w.resets_at ? fmtIn(w.resets_at) : null
  return (
    <div className="min-w-[150px]">
      <div className="num text-[13px] text-text-main mb-1.5 flex items-baseline gap-1">
        <span className="font-medium">{fmtInt(w.used)}</span>
        <span className="text-text-muted">{limited ? `/ ${fmtInt(w.limit)}` : '· sem limite'}</span>
      </div>
      <Progress used={w.used} limit={w.limit} />
      <div className="text-xs text-text-muted mt-1.5 h-4">{reopens ? `reabre ${reopens}` : limited ? 'janela fechada' : ''}</div>
    </div>
  )
}

/* ---------- Formulario (criar / editar) ---------- */
function PersonDrawer({ open, person, onClose, onSaved }) {
  const toast = useToast()
  const editing = !!person
  const [f, setF] = useState(null)
  const [errs, setErrs] = useState({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    const l = person?.limits
    setF({
      name: person?.name || '', note: person?.note || '',
      sOn: l ? l.session_limit_tokens != null : true, sTok: l?.session_limit_tokens != null ? String(l.session_limit_tokens) : '',
      wOn: l ? l.week_limit_tokens != null : false, wTok: l?.week_limit_tokens != null ? String(l.week_limit_tokens) : '',
      sSec: editing ? String(l.session_seconds) : '', wSec: editing ? String(l.week_seconds) : '',
    })
    setErrs({})
  }, [open, person, editing])
  if (!open || !f) return null
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }))

  const submit = async (e) => {
    e?.preventDefault()
    const er = {}
    if (!f.name.trim()) er.name = 'Informe o nome.'
    if (f.sOn && !(+f.sTok > 0)) er.sTok = 'Informe a quantidade de tokens.'
    if (f.wOn && !(+f.wTok > 0)) er.wTok = 'Informe a quantidade de tokens.'
    if (f.sSec && !(+f.sSec > 0)) er.sSec = 'Use segundos inteiros.'
    if (f.wSec && !(+f.wSec > 0)) er.wSec = 'Use segundos inteiros.'
    setErrs(er)
    if (Object.keys(er).length) return
    const body = {
      name: f.name.trim(), note: f.note.trim(),
      session_limit_tokens: f.sOn ? +f.sTok : null,
      week_limit_tokens: f.wOn ? +f.wTok : null,
    }
    if (f.sSec) body.session_seconds = +f.sSec
    if (f.wSec) body.week_seconds = +f.wSec
    setBusy(true)
    try {
      if (editing) { await api.updatePerson(person.id, body); toast.success('Pessoa atualizada.'); onSaved() }
      else { const r = await api.createPerson(body); toast.success('Pessoa cadastrada.'); onSaved(r.api_key) }
    } catch (ex) { toast.error(ex.message) }
    finally { setBusy(false) }
  }

  return (
    <Drawer open={open} onClose={onClose} title={editing ? 'Editar pessoa' : 'Nova pessoa'}
      subtitle={editing ? person.name : 'A chave de API é gerada ao salvar.'}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button>
        <Button onClick={submit} loading={busy}>{editing ? 'Salvar alterações' : 'Criar pessoa'}</Button>
      </>}>
      <form onSubmit={submit} className="flex flex-col gap-5">
        <Field label="Nome" htmlFor="p-name" error={errs.name}>
          <Input id="p-name" value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="Ex.: Marina Duarte" aria-invalid={!!errs.name} />
        </Field>
        <Field label="Observação" htmlFor="p-note" hint="Opcional. Por exemplo, a plataforma que a pessoa usa.">
          <Textarea id="p-note" value={f.note} onChange={(e) => set('note', e.target.value)} />
        </Field>

        <div className="rounded-[12px] border border-border-subtle bg-bg p-4 flex flex-col gap-4">
          <div>
            <h3 className="text-sm font-semibold text-text-main">Limites de uso</h3>
            <p className="text-xs text-text-muted">Cada limite liga e desliga sozinho. Sem nenhum, a pessoa não tem limite. Conta tokens de entrada e saída.</p>
          </div>
          <Toggle id="t-s" checked={f.sOn} onChange={(v) => set('sOn', v)} label="Limite de sessão" description="Janela de 5 horas" />
          {f.sOn && (
            <Field htmlFor="p-stok" error={errs.sTok}>
              <Input id="p-stok" inputMode="numeric" value={showNum(f.sTok)} onChange={(e) => set('sTok', digits(e.target.value))} placeholder="Ex.: 100.000 tokens" aria-label="Tokens por sessão" aria-invalid={!!errs.sTok} />
            </Field>
          )}
          <div className="h-px bg-border-subtle" />
          <Toggle id="t-w" checked={f.wOn} onChange={(v) => set('wOn', v)} label="Limite semanal" description="Janela de 7 dias" />
          {f.wOn && (
            <Field htmlFor="p-wtok" error={errs.wTok}>
              <Input id="p-wtok" inputMode="numeric" value={showNum(f.wTok)} onChange={(e) => set('wTok', digits(e.target.value))} placeholder="Ex.: 400.000 tokens" aria-label="Tokens por semana" aria-invalid={!!errs.wTok} />
            </Field>
          )}
        </div>

        <details className="group rounded-[12px] border border-border-subtle">
          <summary className="flex items-center justify-between px-4 h-11 cursor-pointer text-sm font-medium text-text-muted hover:text-text-main list-none rounded-[12px]">
            Avançado (QA)
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
          </summary>
          <div className="px-4 pb-4 pt-1 flex flex-col gap-4">
            <p className="text-xs text-text-muted">Duração das janelas em segundos, para testes. Em produção o mínimo é 1 hora (3.600 s); valores menores só com o modo QA ligado.</p>
            <Field label="Duração da janela de sessão (segundos)" htmlFor="p-ssec" error={errs.sSec}>
              <Input id="p-ssec" inputMode="numeric" value={f.sSec} onChange={(e) => set('sSec', digits(e.target.value))} placeholder={`Padrão ${DEF_S}`} />
            </Field>
            <Field label="Duração da janela semanal (segundos)" htmlFor="p-wsec" error={errs.wSec}>
              <Input id="p-wsec" inputMode="numeric" value={f.wSec} onChange={(e) => set('wSec', digits(e.target.value))} placeholder={`Padrão ${DEF_W}`} />
            </Field>
          </div>
        </details>
      </form>
    </Drawer>
  )
}

/* ---------- Acoes ---------- */
function RowActions({ p, onEdit, onToggle, onRotate, onReset, mobile }) {
  const susp = p.status === 'suspended'
  if (mobile) {
    return (
      <div className="grid grid-cols-2 gap-2">
        <Button variant="secondary" size="sm" icon={Pencil} onClick={onEdit}>Editar</Button>
        <Button variant="secondary" size="sm" icon={susp ? Play : Pause} onClick={onToggle}>{susp ? 'Reativar' : 'Suspender'}</Button>
        <Button variant="secondary" size="sm" icon={KeyRound} onClick={onRotate}>Nova chave</Button>
        <Button variant="secondary" size="sm" icon={RotateCcw} onClick={onReset}>Zerar janelas</Button>
      </div>
    )
  }
  return (
    <div className="flex items-center justify-end">
      <IconButton label="Editar" icon={Pencil} onClick={onEdit} />
      <IconButton label={susp ? 'Reativar' : 'Suspender'} icon={susp ? Play : Pause} onClick={onToggle} />
      <IconButton label="Gerar nova chave" icon={KeyRound} onClick={onRotate} />
      <IconButton label="Zerar janelas" icon={RotateCcw} onClick={onReset} />
    </div>
  )
}

const StatusBadge = ({ s }) => s === 'active'
  ? <Badge variant="success" dot>Ativa</Badge> : <Badge variant="warning" dot>Suspensa</Badge>

export default function People() {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  useEffect(() => { const t = setTimeout(() => setDq(q.trim()), 250); return () => clearTimeout(t) }, [q])
  const { data, loading, error, reload, refresh } = useLoad(() => api.people(dq), [dq])

  const [drawer, setDrawer] = useState({ open: false, person: null })
  const [newKey, setNewKey] = useState(null)
  const [confirm, setConfirm] = useState(null) // {kind, person?}
  const [busy, setBusy] = useState(false)

  const list = Array.isArray(data) ? data : data?.people || []

  const run = async (fn, okMsg) => {
    setBusy(true)
    try { const r = await fn(); if (okMsg) toast.success(okMsg); setConfirm(null); refresh(); return r }
    catch (ex) { toast.error(ex.message) }
    finally { setBusy(false) }
  }
  const doConfirm = async () => {
    const { kind, person } = confirm
    if (kind === 'rotate') {
      setBusy(true)
      try { const r = await api.rotateKey(person.id); setConfirm(null); setNewKey({ key: r.api_key, title: `Nova chave de ${person.name}` }); refresh() }
      catch (ex) { toast.error(ex.message) } finally { setBusy(false) }
    }
    if (kind === 'reset') run(() => api.resetWindows(person.id), `Janelas de ${person.name} zeradas.`)
    if (kind === 'resetAll') run(() => api.resetAll(), 'Janelas de todos zeradas.')
    if (kind === 'suspend') run(() => api.suspend(person.id), `${person.name} suspensa.`)
  }
  const toggle = (p) => p.status === 'active' ? setConfirm({ kind: 'suspend', person: p })
    : run(() => api.resume(p.id), `${p.name} reativada.`)

  const act = (p, mobile) => (
    <RowActions p={p} mobile={mobile}
      onEdit={() => setDrawer({ open: true, person: p })}
      onToggle={() => toggle(p)}
      onRotate={() => setConfirm({ kind: 'rotate', person: p })}
      onReset={() => setConfirm({ kind: 'reset', person: p })} />
  )

  const empty = !loading && !error && list.length === 0
  return (
    <>
      <PageHeader title="Pessoas" subtitle="Quem tem chave de acesso, quanto já usou e quando cada janela reabre."
        actions={<>
          <Button variant="outline" icon={RotateCcw} onClick={() => setConfirm({ kind: 'resetAll' })} disabled={!list.length}>Zerar janelas de todos</Button>
          <Button icon={Plus} onClick={() => setDrawer({ open: true, person: null })}>Nova pessoa</Button>
        </>} />

      <div className="mb-4 max-w-md">
        <Input icon={Search} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar por nome ou observação" aria-label="Buscar pessoas" />
      </div>

      <Card className="overflow-hidden">
        {error ? <ErrorState message={error} onRetry={reload} />
          : loading && !data ? (
            <div className="p-5 space-y-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : empty ? (
            dq ? <EmptyState icon={Search} title="Nenhum resultado" text={`Ninguém encontrado para "${dq}".`} />
              : <EmptyState icon={UsersIcon} title="Nenhuma pessoa cadastrada" text="Cadastre a primeira pessoa para gerar uma chave de API e definir limites."
                  action={<Button icon={Plus} onClick={() => setDrawer({ open: true, person: null })}>Nova pessoa</Button>} />
          ) : (
            <>
              {/* Tabela (>= md) */}
              <div className={cn('hidden md:block overflow-x-auto transition-opacity', loading && 'opacity-60')}>
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                      <th className="font-medium px-5 py-3">Pessoa</th>
                      <th className="font-medium px-3 py-3">Estado</th>
                      <th className="font-medium px-3 py-3">Chave</th>
                      <th className="font-medium px-3 py-3">Sessão</th>
                      <th className="font-medium px-3 py-3">Semana</th>
                      <th className="font-medium px-3 py-3">Último uso</th>
                      <th className="px-5 py-3"><span className="sr-only">Ações</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((p) => (
                      <tr key={p.id} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50 transition-colors align-top">
                        <td className="px-5 py-4 max-w-[240px]">
                          <div className="font-semibold text-text-main truncate">{p.name}</div>
                          <div className="text-xs text-text-muted line-clamp-2">{p.note || <span className="text-text-subtle">sem observação</span>}</div>
                        </td>
                        <td className="px-3 py-4"><StatusBadge s={p.status} /></td>
                        <td className="px-3 py-4"><code className="text-[12px] font-mono text-text-muted bg-surface-2 rounded-md px-1.5 py-0.5">...{p.key_last4}</code></td>
                        <td className="px-3 py-4"><WindowCell w={p.session} /></td>
                        <td className="px-3 py-4"><WindowCell w={p.week} /></td>
                        <td className="px-3 py-4 text-xs text-text-muted whitespace-nowrap">{fmtAgo(p.last_used_at)}</td>
                        <td className="px-3 py-3 whitespace-nowrap">{act(p)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* Cartoes (< md) */}
              <ul className="md:hidden divide-y divide-border-subtle">
                {list.map((p) => (
                  <li key={p.id} className="p-4 flex flex-col gap-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-semibold text-text-main">{p.name}</div>
                        {p.note && <div className="text-xs text-text-muted">{p.note}</div>}
                      </div>
                      <StatusBadge s={p.status} />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div><div className="text-xs font-medium text-text-muted mb-1">Sessão</div><WindowCell w={p.session} /></div>
                      <div><div className="text-xs font-medium text-text-muted mb-1">Semana</div><WindowCell w={p.week} /></div>
                    </div>
                    <div className="flex items-center justify-between text-xs text-text-muted">
                      <code className="font-mono bg-surface-2 rounded-md px-1.5 py-0.5">...{p.key_last4}</code>
                      <span>Último uso: {fmtAgo(p.last_used_at)}</span>
                    </div>
                    {act(p, true)}
                  </li>
                ))}
              </ul>
            </>
          )}
      </Card>

      <PersonDrawer open={drawer.open} person={drawer.person} onClose={() => setDrawer({ open: false, person: null })}
        onSaved={(key) => { setDrawer({ open: false, person: null }); refresh(); if (key) setNewKey({ key, title: 'Chave de API gerada' }) }} />
      <KeyReveal apiKey={newKey?.key} title={newKey?.title} onClose={() => setNewKey(null)} />

      <ConfirmModal open={!!confirm} onClose={() => setConfirm(null)} onConfirm={doConfirm} loading={busy}
        danger={confirm?.kind !== 'suspend'}
        title={{ rotate: 'Gerar nova chave?', reset: 'Zerar janelas?', resetAll: 'Zerar janelas de todos?', suspend: 'Suspender pessoa?' }[confirm?.kind] || ''}
        confirmLabel={{ rotate: 'Gerar nova chave', reset: 'Zerar janelas', resetAll: 'Zerar todas', suspend: 'Suspender' }[confirm?.kind]}>
        {confirm?.kind === 'rotate' && <p>A chave atual de <strong className="text-text-main">{confirm.person.name}</strong> para de funcionar na hora e uma nova será mostrada uma única vez. Quem usa a chave antiga precisa ser avisado.</p>}
        {confirm?.kind === 'reset' && <p>O consumo de sessão e da semana de <strong className="text-text-main">{confirm.person.name}</strong> volta a zero e as janelas fecham. A próxima chamada abre janelas novas.</p>}
        {confirm?.kind === 'resetAll' && <p>O consumo de sessão e da semana de <strong className="text-text-main">todas as pessoas</strong> volta a zero. Essa ação não pode ser desfeita.</p>}
        {confirm?.kind === 'suspend' && <p><strong className="text-text-main">{confirm.person.name}</strong> recebe erro 403 imediatamente, sem chegar à GPU. Dá para reativar a qualquer momento.</p>}
      </ConfirmModal>
    </>
  )
}
