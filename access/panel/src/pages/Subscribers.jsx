import { useEffect, useState } from 'react'
import { Users, UserPlus, KeyRound, Link2, Save, RefreshCw, CreditCard, ListChecks, Check, Copy, Trash2 } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtFull, fmtInt, cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Select, Toggle, Notice, Skeleton, EmptyState, ErrorState, Drawer, ConfirmModal, useToast, useLoad } from '../components/ui.jsx'

const STATUS = { active: ['Assinando', 'success'], late: ['Em atraso', 'warning'], canceled: ['Cancelado', 'default'], refunded: ['Reembolsado', 'default'], chargeback: ['Chargeback', 'error'], pending: ['Pendente', 'info'] }
const brl = (cents) => (cents == null ? '—' : (cents / 100).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 }))
const dia = (v) => (v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(v)) : '—')

function Stat({ label, value, sub, tone }) {
  return (
    <div className="rounded-[14px] border border-border-subtle bg-surface p-4">
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className={cn('num text-2xl font-semibold tracking-tight', tone === 'warn' ? 'text-amber-600 dark:text-amber-400' : 'text-text-main')}>{value}</div>
      {sub && <div className="text-xs text-text-muted">{sub}</div>}
    </div>
  )
}

function CopyLink({ link, onClose }) {
  const [ok, setOk] = useState(false)
  return (
    <div className="rounded-[12px] border border-brand-500/40 bg-brand-500/5 p-4">
      <div className="text-sm font-semibold text-text-main mb-1">Link para a pessoa definir a senha</div>
      <p className="text-xs text-text-muted mb-2">Vale por 7 dias e só funciona uma vez. {onClose ? '' : 'Envie por e-mail ou WhatsApp.'}</p>
      <div className="flex gap-2">
        <input readOnly value={link} className="flex-1 h-10 px-3 text-xs font-mono rounded-[10px] bg-surface-2 border border-border-subtle text-text-main" onFocus={(e) => e.target.select()} />
        <Button variant="secondary" icon={ok ? Check : Copy} onClick={() => { navigator.clipboard?.writeText(link); setOk(true); setTimeout(() => setOk(false), 1500) }}>{ok ? 'Copiado' : 'Copiar'}</Button>
      </div>
    </div>
  )
}

function NewDrawer({ open, plans, onClose, onSaved }) {
  const toast = useToast()
  const [f, setF] = useState({ name: '', email: '', plan: 'pro', enable: false, safety_level: '1' })
  const [busy, setBusy] = useState(false)
  const [link, setLink] = useState(null)
  useEffect(() => { if (open) { setF({ name: '', email: '', plan: plans[0]?.id || 'pro', enable: false, safety_level: '1' }); setLink(null) } }, [open])
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }))
  const save = async () => {
    setBusy(true)
    try {
      const r = await api.createSubscriber({ name: f.name.trim(), email: f.email.trim(), plan: f.plan, enable: f.enable, safety_level: +f.safety_level })
      setLink(r.setup_link)
      toast.success('Assinante cadastrado.')
      onSaved()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Novo assinante" subtitle="Cadastro manual (cortesia, teste ou venda fora da Cakto)"
      footer={link ? <Button onClick={onClose}>Fechar</Button> : <><Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button><Button onClick={save} loading={busy}>Cadastrar</Button></>}>
      <div className="flex flex-col gap-5">
        {link ? <CopyLink link={link} /> : (
          <>
            <Field label="Nome" htmlFor="n-name"><Input id="n-name" value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="Ex.: Marina Duarte" /></Field>
            <Field label="E-mail" htmlFor="n-email" hint="É o login da pessoa no aplicativo."><Input id="n-email" type="email" value={f.email} onChange={(e) => set('email', e.target.value)} placeholder="pessoa@empresa.com" /></Field>
            <Field label="Plano" htmlFor="n-plan"><Select id="n-plan" value={f.plan} onChange={(e) => set('plan', e.target.value)}>{plans.map((p) => <option key={p.id} value={p.id}>{p.name} · {brl(p.price_cents)}/mês</option>)}</Select></Field>
            <Field label="Nível de segurança" htmlFor="n-sf"><Select id="n-sf" value={f.safety_level} onChange={(e) => set('safety_level', e.target.value)}><option value="1">Nível 1 · Sem trava</option><option value="2">Nível 2 · Classificador 1</option><option value="3">Nível 3 · Classificadores 1 e 2</option></Select></Field>
            <Toggle id="n-en" checked={f.enable} onChange={(v) => set('enable', v)} label="Já liberar a chave" description="Sem isso a pessoa fica aguardando a sua liberação." />
          </>
        )}
      </div>
    </Drawer>
  )
}

function Subscribers() {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [open, setOpen] = useState(false)
  const [link, setLink] = useState(null)
  const [apagar, setApagar] = useState(null)
  const list = useLoad(() => api.subscribers(q, status), [q, status])
  const plans = useLoad(() => api.plans(), [])
  const d = list.data
  const s = d?.summary
  const act = async (fn, okMsg) => { try { await fn(); toast.success(okMsg); list.refresh() } catch (e) { toast.error(e.message) } }

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <Stat label="Assinando" value={s ? fmtInt(s.active) : '…'} sub={s ? `${brl(s.mrr_cents)} por mês` : null} />
        <Stat label="Aguardando liberação" value={s ? fmtInt(s.awaiting) : '…'} sub="pagaram, falta você liberar" tone={s?.awaiting ? 'warn' : undefined} />
        <Stat label="Em atraso" value={s ? fmtInt(s.late) : '…'} tone={s?.late ? 'warn' : undefined} />
        <Stat label="Encerrados" value={s ? fmtInt(s.ended) : '…'} sub="cancelado, reembolso, chargeback" />
        <Stat label="Total de contas" value={s ? fmtInt(s.total) : '…'} />
      </div>
      <Card className="overflow-hidden">
        <CardHeader icon={Users} title="Assinantes" subtitle="Quem assina, quem está aguardando, em atraso ou bloqueado. A Cakto é a fonte do pagamento; aqui você libera e acompanha"
          action={<Button icon={UserPlus} onClick={() => setOpen(true)}>Novo assinante</Button>} />
        <div className="px-5 pb-4 flex flex-wrap gap-3">
          <div className="flex-1 min-w-[220px]"><Input placeholder="Buscar por nome ou e-mail" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar assinante" /></div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filtrar por situação" className="w-auto min-w-[200px]">
            <option value="">Todas as situações</option><option value="active">Assinando</option><option value="awaiting">Aguardando liberação</option><option value="late">Em atraso</option>
            <option value="canceled">Cancelado</option><option value="refunded">Reembolsado</option><option value="chargeback">Chargeback</option><option value="none">Sem assinatura</option>
          </Select>
        </div>
        {link && <div className="px-5 pb-4"><CopyLink link={link} onClose /></div>}
        {list.error ? <ErrorState message={list.error} onRetry={list.reload} /> : list.loading && !d ? <div className="p-5 pt-0"><Skeleton className="h-40" /></div>
          : d.rows.length === 0 ? <EmptyState icon={Users} title="Nenhum assinante ainda" text="Quando alguém comprar pela Cakto, a conta aparece aqui, aguardando a sua liberação." action={<Button icon={UserPlus} onClick={() => setOpen(true)}>Cadastrar manualmente</Button>} />
          : (
            <div className="overflow-x-auto border-t border-border-subtle">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                    <th className="font-medium px-5 py-3">Assinante</th><th className="font-medium px-3 py-3">Plano</th><th className="font-medium px-3 py-3">Situação</th>
                    <th className="font-medium px-3 py-3">Próximo pagamento</th><th className="font-medium px-3 py-3">Chave</th><th className="font-medium px-3 py-3">Segurança</th>
                    <th className="font-medium px-3 py-3 text-right">Computadores</th><th className="px-5 py-3"><span className="sr-only">Ações</span></th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => {
                    const [label, variant] = STATUS[r.status] || ['Sem assinatura', 'default']
                    return (
                      <tr key={r.id} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50 align-top">
                        <td className="px-5 py-3.5 max-w-[260px]"><div className="font-semibold text-text-main truncate">{r.name || '—'}</div><div className="text-xs text-text-muted truncate">{r.email}</div>
                          {!r.has_password && <div className="text-xs text-amber-600 dark:text-amber-400">ainda não definiu a senha</div>}</td>
                        <td className="px-3 py-3.5"><Badge variant="primary">{r.plan ? r.plan.toUpperCase() : '—'}</Badge></td>
                        <td className="px-3 py-3.5"><Badge variant={variant} dot>{label}</Badge>{r.blocked && <Badge variant="error" className="ml-1">Conta bloqueada</Badge>}</td>
                        <td className="px-3 py-3.5 num whitespace-nowrap text-text-muted">{dia(r.next_payment_at)}</td>
                        <td className="px-3 py-3.5">{r.enabled ? <Badge variant="success" dot>Habilitada</Badge> : <Badge variant={r.status === 'active' ? 'warning' : 'default'} dot pulse={r.status === 'active'}>{r.blocked_by_billing ? 'Bloqueada por pagamento' : 'Desabilitada'}</Badge>}</td>
                        <td className="px-3 py-3.5"><Badge variant={r.safety_level === 2 ? 'warning' : 'default'}>{r.safety_level === 2 ? 'Nível 2' : 'Nível 1'}</Badge></td>
                        <td className="px-3 py-3.5 num text-right">{r.devices}</td>
                        <td className="px-5 py-3 whitespace-nowrap text-right">
                          <div className="inline-flex gap-1.5">
                            {r.enabled ? <Button size="sm" variant="secondary" onClick={() => act(() => api.disableSubscriber(r.id), 'Chave desabilitada.')}>Bloquear</Button>
                              : <Button size="sm" icon={KeyRound} onClick={() => act(() => api.enableSubscriber(r.id), 'Chave habilitada.')}>Liberar</Button>}
                            <Button size="sm" variant="outline" icon={Link2} onClick={() => act(async () => setLink((await api.passwordLink(r.id)).setup_link), 'Link gerado, copie acima.')} aria-label={`Gerar link de senha de ${r.email}`}>Senha</Button>
                            <Button size="sm" variant="outline" icon={Trash2} onClick={() => setApagar(r)} aria-label={`Excluir ${r.email}`}>Excluir</Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        <p className="px-5 py-3 text-xs text-text-muted border-t border-border-subtle">Quem compra nasce <b>desabilitado</b> até você liberar. Atrasou além da carência ou cancelou: bloqueia sozinho; voltou a pagar: reabre sozinho. {d && !d.smtp && 'O envio de e-mail ainda não está configurado: use o botão Senha e mande o link.'}</p>
      </Card>
      <ConfirmModal open={!!apagar} onClose={() => setApagar(null)} danger title="Excluir este assinante?" confirmLabel="Excluir"
        onConfirm={() => act(async () => { await api.deleteSubscriber(apagar.id); setApagar(null) }, 'Assinante excluído.')}>
        <p className="text-sm text-text-muted">Remove a conta <b>{apagar?.email}</b>, as assinaturas e os pedidos de máquina virtual, e revoga as chaves. Se houver VM ativa na Contabo, cancele a VM antes. O histórico de uso fica guardado.</p>
      </ConfirmModal>
      <NewDrawer open={open} plans={plans.data?.plans || []} onClose={() => setOpen(false)} onSaved={() => list.refresh()} />
    </>
  )
}

function PlansCard() {
  const toast = useToast()
  const { data, loading, refresh } = useLoad(() => api.plans(), [])
  const [edit, setEdit] = useState({})
  const set = (id, k, v) => setEdit((e) => ({ ...e, [id]: { ...(e[id] || {}), [k]: v } }))
  const save = async (p) => {
    const c = edit[p.id] || {}
    const body = {}
    for (const [k, v] of Object.entries(c)) body[k] = k === 'name' ? v : v === '' && /limit_tokens/.test(k) ? null : Math.round(+v * (k === 'price_cents' ? 100 : 1))
    try { await api.putPlan(p.id, body); toast.success(`Plano ${p.name} salvo.`); setEdit((e) => ({ ...e, [p.id]: {} })); refresh() } catch (e) { toast.error(e.message) }
  }
  return (
    <Card>
      <CardHeader icon={ListChecks} title="Planos" subtitle="Limites de cada plano. Mudar vale para os próximos assinantes e quando a pessoa trocar de plano" />
      <div className="px-5 pb-5 grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        {loading && !data ? <Skeleton className="h-40 lg:col-span-4" /> : (data?.plans || []).map((p) => {
          const c = edit[p.id] || {}
          const val = (k, base) => c[k] ?? base
          return (
            <div key={p.id} className="rounded-[12px] border border-border-subtle p-4 flex flex-col gap-3">
              <div className="flex items-center justify-between"><span className="font-semibold text-text-main">{p.name}</span><Badge variant="primary">{p.id.toUpperCase()}</Badge></div>
              <Field label="Preço por mês (R$)" htmlFor={`pp-${p.id}`}><Input id={`pp-${p.id}`} type="number" min="0" value={val('price_cents', p.price_cents / 100)} onChange={(e) => set(p.id, 'price_cents', e.target.value)} /></Field>
              <Field label="Tokens por sessão (5 h)" htmlFor={`ps-${p.id}`}><Input id={`ps-${p.id}`} type="number" min="0" placeholder="vazio = sem limite" value={val('session_limit_tokens', p.session_limit_tokens ?? '')} onChange={(e) => set(p.id, 'session_limit_tokens', e.target.value)} /></Field>
              <Field label="Tokens por semana" htmlFor={`pw-${p.id}`}><Input id={`pw-${p.id}`} type="number" min="0" placeholder="vazio = sem limite" value={val('week_limit_tokens', p.week_limit_tokens ?? '')} onChange={(e) => set(p.id, 'week_limit_tokens', e.target.value)} /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Imagens por mês" htmlFor={`pi-${p.id}`}><Input id={`pi-${p.id}`} type="number" min="0" value={val('image_quota_month', p.image_quota_month)} onChange={(e) => set(p.id, 'image_quota_month', e.target.value)} /></Field>
                <Field label="Computadores (0 = ilimitado)" htmlFor={`pd-${p.id}`}><Input id={`pd-${p.id}`} type="number" min="0" value={val('max_devices', p.max_devices)} onChange={(e) => set(p.id, 'max_devices', e.target.value)} /></Field>
              </div>
              <Button icon={Save} variant="secondary" disabled={!Object.keys(c).length} onClick={() => save(p)}>Salvar plano</Button>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

function BillingCard() {
  const toast = useToast()
  const st = useLoad(() => api.billingSettings(), [])
  const ev = useLoad(() => api.billingEvents(15), [])
  const [f, setF] = useState(null)
  useEffect(() => { if (st.data) setF({ grace_days: String(st.data.grace_days), auto_enable: st.data.auto_enable, plan_map: JSON.stringify(st.data.plan_map || {}, null, 0) }) }, [st.data])
  const save = async () => {
    let map
    try { map = JSON.parse(f.plan_map || '{}') } catch { return toast.error('O mapa de planos precisa ser um JSON válido, ex.: {"meu produto": "max"}') }
    try { await api.putBillingSettings({ grace_days: +f.grace_days, auto_enable: f.auto_enable, plan_map: map }); toast.success('Cobrança salva.'); st.refresh() } catch (e) { toast.error(e.message) }
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader icon={CreditCard} title="Cobrança (Cakto)" subtitle="Como os pagamentos viram acesso" />
        <div className="px-5 pb-5 flex flex-col gap-4">
          {!f ? <Skeleton className="h-32" /> : (
            <>
              <div className="flex gap-2 flex-wrap">
                <Badge variant={st.data.webhook_configured ? 'success' : 'warning'} dot>{st.data.webhook_configured ? 'Webhook da Cakto configurado' : 'Webhook ainda sem segredo'}</Badge>
                <Badge variant={st.data.smtp_configured ? 'success' : 'default'} dot>{st.data.smtp_configured ? 'E-mail configurado' : 'E-mail desligado'}</Badge>
              </div>
              <Field label="Carência do atraso (dias)" htmlFor="b-grace" hint="Depois disso, quem está em atraso é bloqueado sozinho."><Input id="b-grace" type="number" min="0" max="60" value={f.grace_days} onChange={(e) => setF((s) => ({ ...s, grace_days: e.target.value }))} /></Field>
              <Toggle id="b-auto" checked={f.auto_enable} onChange={(v) => setF((s) => ({ ...s, auto_enable: v }))} label="Liberar a chave sozinho ao pagar" description="Desligado: toda primeira compra espera você liberar." />
              <Field label="Mapa de produtos da Cakto para planos" htmlFor="b-map" hint='Trecho do nome do produto → plano. Ex.: {"segundo cérebro max": "max"}. Sem mapa, o nome precisa conter pro, max ou ultra.'><Input id="b-map" value={f.plan_map} onChange={(e) => setF((s) => ({ ...s, plan_map: e.target.value }))} className="font-mono text-xs" /></Field>
              <div className="flex justify-end"><Button icon={Save} onClick={save}>Salvar cobrança</Button></div>
              <Notice variant="info">O endereço do webhook para colocar na Cakto é <code>/v1/billing/cakto</code> neste servidor, com o segredo no cabeçalho <code>x-webhook-secret</code>. Os nomes dos campos serão conferidos com um evento real.</Notice>
            </>
          )}
        </div>
      </Card>
      <Card>
        <CardHeader icon={ListChecks} title="Eventos recentes da Cakto" subtitle="Tudo que chegou, processado ou com erro" action={<Button variant="outline" icon={RefreshCw} onClick={ev.refresh}>Atualizar</Button>} />
        <div className="px-5 pb-5">
          {ev.loading && !ev.data ? <Skeleton className="h-24" /> : (ev.data?.rows || []).length === 0 ? <p className="text-sm text-text-muted">Nenhum evento recebido ainda.</p> : (
            <ul className="divide-y divide-border-subtle">
              {ev.data.rows.map((e) => (
                <li key={e.id} className="py-2.5 flex items-center justify-between gap-3 text-sm">
                  <span className="text-text-main truncate">{e.kind || 'evento'}</span>
                  <span className="flex items-center gap-2 shrink-0"><span className="num text-xs text-text-muted">{fmtFull(e.at)}</span>{e.processed ? <Badge variant="success">ok</Badge> : <Badge variant="error" title={e.error}>erro</Badge>}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </div>
  )
}

export default function SubscribersPage() {
  return (
    <>
      <PageHeader title="Assinantes" subtitle="Quem assina, o plano, o próximo pagamento e a liberação da chave. Cada assinante entra no aplicativo com e-mail e senha." />
      <div className="space-y-4">
        <Subscribers />
        <PlansCard />
        <BillingCard />
      </div>
    </>
  )
}
