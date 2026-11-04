import { useEffect, useState } from 'react'
import { Server, Plus, Save, RefreshCw, Power, PlayCircle, Rocket, Trash2, KeyRound, Cpu, MemoryStick, HardDrive } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtInt, cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Select, Toggle, Notice, Skeleton, EmptyState, ErrorState, Drawer, ConfirmModal, useToast, useLoad } from '../components/ui.jsx'

const STATUS = {
  requested: ['Aguardando criação', 'info'], provisioning: ['Criando', 'warning'], running: ['Rodando', 'success'],
  suspended: ['Desligada (pagamento)', 'warning'], canceled: ['Cancelada', 'default'], error: ['Erro', 'error'],
}
const brl = (cents) => (cents == null ? '—' : (cents / 100).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const dia = (v) => (v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: '2-digit' }).format(new Date(v)) : '—')
const gb = (mb) => (mb ? `${Math.round(mb / 1024)} GB` : '—')

function Stat({ label, value, sub, tone }) {
  return (
    <div className="rounded-[14px] border border-border-subtle bg-surface p-4">
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className={cn('num text-2xl font-semibold tracking-tight', tone === 'good' ? 'text-emerald-600 dark:text-emerald-400' : tone === 'bad' ? 'text-red-600 dark:text-red-400' : 'text-text-main')}>{value}</div>
      {sub && <div className="text-xs text-text-muted">{sub}</div>}
    </div>
  )
}

function NewDrawer({ open, onClose, onSaved }) {
  const toast = useToast()
  const subs = useLoad(() => (open ? api.subscribers('', '') : Promise.resolve({ rows: [] })), [open])
  const [acc, setAcc] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) setAcc('') }, [open])
  const save = async () => {
    setBusy(true)
    try { await api.createVm({ account_id: +acc }); toast.success('Pedido de VM registrado. Crie na Contabo quando quiser.'); onSaved(); onClose() } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Nova máquina virtual" subtitle="Registra o pedido para o assinante. A VPS só é criada na Contabo quando você clicar em Criar."
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button><Button onClick={save} loading={busy} disabled={!acc}>Registrar pedido</Button></>}>
      <Field label="Assinante" htmlFor="vm-acc" hint="A VM usa a conta (e a chave dedicada) deste assinante.">
        <Select id="vm-acc" value={acc} onChange={(e) => setAcc(e.target.value)}>
          <option value="">Escolha…</option>
          {(subs.data?.rows || []).map((s) => <option key={s.id} value={s.id}>{s.name || s.email} · {s.email}</option>)}
        </Select>
      </Field>
    </Drawer>
  )
}

function ConfigCard() {
  const toast = useToast()
  const { data, loading, refresh } = useLoad(() => api.vmConfig(), [])
  const [f, setF] = useState(null)
  const [cred, setCred] = useState({ client_id: '', client_secret: '', api_user: '', api_password: '' })
  useEffect(() => {
    if (data) setF({ product_id: data.product_id, region: data.region, cost: String(data.cost_cents / 100), price: String(data.price_cents / 100), grace_days: String(data.grace_days), auto_provision: data.auto_provision })
  }, [data])
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }))
  const save = async () => {
    const body = { product_id: f.product_id.trim(), region: f.region.trim(), cost_cents: Math.round(+f.cost * 100), price_cents: Math.round(+f.price * 100), grace_days: +f.grace_days, auto_provision: f.auto_provision }
    if (Object.values(cred).some((v) => v.trim())) body.credentials = cred
    try { await api.putVmConfig(body); toast.success('Configuração salva.'); setCred({ client_id: '', client_secret: '', api_user: '', api_password: '' }); refresh() } catch (e) { toast.error(e.message) }
  }
  return (
    <Card>
      <CardHeader icon={KeyRound} title="Contabo e preços" subtitle="Plano da VPS, custo que você paga, preço cobrado do cliente e carência do atraso" />
      <div className="px-5 pb-5 flex flex-col gap-4">
        {loading && !f ? <Skeleton className="h-40" /> : f && (
          <>
            <div className="flex gap-2 flex-wrap">
              <Badge variant={data.credentials_set ? 'success' : 'warning'} dot>{data.credentials_set ? 'Credenciais da Contabo guardadas (cifradas)' : 'Sem credenciais da Contabo'}</Badge>
              <Badge variant={data.secrets_key_set ? 'success' : 'error'} dot>{data.secrets_key_set ? 'Chave mestra ok' : 'SECRETS_KEY ausente no servidor'}</Badge>
              <Badge variant={f.product_id ? 'success' : 'warning'} dot>{f.product_id ? 'Plano definido' : 'Falta o productId do plano'}</Badge>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="productId do plano (Contabo)" htmlFor="v-prod" hint="Ex.: o código do Cloud VPS de 8 GB. Sem ele nenhuma VM é criada."><Input id="v-prod" value={f.product_id} onChange={(e) => set('product_id', e.target.value)} className="font-mono" /></Field>
              <Field label="Região" htmlFor="v-reg"><Input id="v-reg" value={f.region} onChange={(e) => set('region', e.target.value)} /></Field>
              <Field label="Custo por VM (R$/mês)" htmlFor="v-cost" hint="O que a Contabo cobra de você."><Input id="v-cost" type="number" min="0" step="0.01" value={f.cost} onChange={(e) => set('cost', e.target.value)} /></Field>
              <Field label="Preço ao cliente (R$/mês)" htmlFor="v-price"><Input id="v-price" type="number" min="0" step="0.01" value={f.price} onChange={(e) => set('price', e.target.value)} /></Field>
              <Field label="Carência do atraso (dias)" htmlFor="v-grace" hint="Passou disso sem pagar: a VM é desligada. Pagou: religa sozinha."><Input id="v-grace" type="number" min="0" max="60" value={f.grace_days} onChange={(e) => set('grace_days', e.target.value)} /></Field>
            </div>
            <Toggle id="v-auto" checked={f.auto_provision} onChange={(v) => set('auto_provision', v)} label="Criar a VPS sozinho quando o pagamento da VM for confirmado" description="Desligado (recomendado no começo): você clica em Criar. Criar VPS gera cobrança na Contabo." />
            <details className="rounded-[12px] border border-border-subtle p-4">
              <summary className="cursor-pointer text-sm font-medium text-text-main">Trocar credenciais da API da Contabo</summary>
              <div className="grid gap-4 sm:grid-cols-2 mt-4">
                <Field label="Client ID" htmlFor="c-id"><Input id="c-id" value={cred.client_id} onChange={(e) => setCred((s) => ({ ...s, client_id: e.target.value }))} autoComplete="off" /></Field>
                <Field label="Client Secret" htmlFor="c-sec"><Input id="c-sec" type="password" value={cred.client_secret} onChange={(e) => setCred((s) => ({ ...s, client_secret: e.target.value }))} autoComplete="off" /></Field>
                <Field label="Usuário da API" htmlFor="c-usr"><Input id="c-usr" value={cred.api_user} onChange={(e) => setCred((s) => ({ ...s, api_user: e.target.value }))} autoComplete="off" /></Field>
                <Field label="Senha da API" htmlFor="c-pw"><Input id="c-pw" type="password" value={cred.api_password} onChange={(e) => setCred((s) => ({ ...s, api_password: e.target.value }))} autoComplete="off" /></Field>
              </div>
              <p className="text-xs text-text-muted mt-3">Só de escrita: depois de salvas, as credenciais nunca voltam para a tela.</p>
            </details>
            <div className="flex justify-end"><Button icon={Save} onClick={save}>Salvar configuração</Button></div>
          </>
        )}
      </div>
    </Card>
  )
}

function Machines() {
  const toast = useToast()
  const list = useLoad(() => api.vms(), [])
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(null)
  const [busy, setBusy] = useState(false)
  const d = list.data
  const t = d?.total
  const run = async (fn, okMsg) => { setBusy(true); try { await fn(); toast.success(okMsg); list.refresh() } catch (e) { toast.error(e.message) } finally { setBusy(false); setConfirm(null) } }
  const margin = t ? t.price_cents - t.cost_cents : 0

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Stat label="VMs ativas" value={t ? fmtInt(t.vms) : '…'} sub="rodando, criando ou desligadas" />
        <Stat label="Custo na Contabo" value={t ? brl(t.cost_cents) : '…'} sub="por mês" />
        <Stat label="Receita das VMs" value={t ? brl(t.price_cents) : '…'} sub="por mês" />
        <Stat label="Margem" value={t ? brl(margin) : '…'} sub="receita − custo" tone={t ? (margin >= 0 ? 'good' : 'bad') : undefined} />
      </div>
      <Card className="overflow-hidden">
        <CardHeader icon={Server} title="Máquinas virtuais dos clientes" subtitle="Uma VPS por cliente, com o Segundo Cérebro em modo servidor. Pagamento em dia: ligada. Passou da carência: desligada sozinha"
          action={<div className="flex gap-2"><Button variant="outline" icon={RefreshCw} onClick={() => run(() => api.syncVms(), 'Sincronizado com a Contabo.')} loading={busy}>Sincronizar</Button><Button icon={Plus} onClick={() => setOpen(true)}>Nova VM</Button></div>} />
        {list.error ? <ErrorState message={list.error} onRetry={list.reload} /> : list.loading && !d ? <div className="p-5 pt-0"><Skeleton className="h-40" /></div>
          : d.rows.length === 0 ? <EmptyState icon={Server} title="Nenhuma máquina virtual ainda" text="Quando um cliente contratar a VM (ou você registrar um pedido), ela aparece aqui com custo, recursos e situação." action={<Button icon={Plus} onClick={() => setOpen(true)}>Registrar pedido</Button>} />
          : (
            <div className="overflow-x-auto border-t border-border-subtle">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                    <th className="font-medium px-5 py-3">Cliente</th><th className="font-medium px-3 py-3">Situação</th><th className="font-medium px-3 py-3">Recursos</th>
                    <th className="font-medium px-3 py-3">Rede</th><th className="font-medium px-3 py-3 text-right">Custo</th><th className="font-medium px-3 py-3 text-right">Preço</th>
                    <th className="font-medium px-3 py-3 text-right">Margem</th><th className="px-5 py-3"><span className="sr-only">Ações</span></th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((v) => {
                    const [label, variant] = STATUS[v.status] || [v.status, 'default']
                    return (
                      <tr key={v.id} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50 align-top">
                        <td className="px-5 py-3.5 max-w-[210px]"><div className="font-semibold text-text-main truncate">{v.name_client || v.email}</div><div className="text-xs text-text-muted truncate">{v.email}</div><div className="text-xs font-mono text-text-subtle truncate">{v.name}</div><div className="text-xs text-text-muted">criada em {dia(v.created_at)}</div></td>
                        <td className="px-3 py-3.5"><Badge variant={variant} dot pulse={v.status === 'provisioning'}>{label}</Badge>{v.last_error && <div className="text-xs text-red-600 dark:text-red-400 mt-1 max-w-[220px]">{v.last_error}</div>}{v.suspended_at && <div className="text-xs text-text-muted mt-1">desde {dia(v.suspended_at)}</div>}</td>
                        <td className="px-3 py-3.5 text-xs text-text-muted whitespace-nowrap"><div className="flex items-center gap-1.5"><Cpu className="size-3.5" />{v.cores ? `${v.cores} vCPU` : '—'}</div><div className="flex items-center gap-1.5"><MemoryStick className="size-3.5" />{gb(v.ram_mb)}</div><div className="flex items-center gap-1.5"><HardDrive className="size-3.5" />{gb(v.disk_mb)}</div></td>
                        <td className="px-3 py-3.5 text-xs"><div className="font-mono text-text-main">{v.ip || '—'}</div><div className="text-text-muted">{v.region || '—'}</div></td>
                        <td className="px-3 py-3.5 num text-right">{brl(v.cost_cents)}</td>
                        <td className="px-3 py-3.5 num text-right">{brl(v.price_cents)}</td>
                        <td className={cn('px-3 py-3.5 num text-right font-medium', v.margin_cents >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400')}>{brl(v.margin_cents)}</td>
                        <td className="px-5 py-3 whitespace-nowrap text-right">
                          <div className="inline-flex gap-1.5">
                            {(v.status === 'requested' || v.status === 'error') && <Button size="sm" icon={Rocket} onClick={() => setConfirm({ v, tipo: 'provision' })}>Criar</Button>}
                            {v.status === 'running' && <Button size="sm" variant="secondary" icon={Power} onClick={() => setConfirm({ v, tipo: 'suspend' })}>Desligar</Button>}
                            {v.status === 'suspended' && <Button size="sm" icon={PlayCircle} onClick={() => run(() => api.resumeVm(v.id), 'VM religada.')}>Religar</Button>}
                            {v.contabo_id && v.status !== 'canceled' && <Button size="sm" variant="outline" icon={Trash2} onClick={() => setConfirm({ v, tipo: 'cancel' })} aria-label={`Cancelar ${v.name}`}>Cancelar</Button>}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        <p className="px-5 py-3 text-xs text-text-muted border-t border-border-subtle">A automação só mexe em VPS com nome começando por <code>sc-vm-</code>; a VPS de produção é protegida e nunca é alterada.</p>
      </Card>
      <NewDrawer open={open} onClose={() => setOpen(false)} onSaved={() => list.refresh()} />
      <ConfirmModal open={!!confirm} onClose={() => setConfirm(null)} loading={busy} danger={confirm?.tipo !== 'provision'}
        title={confirm?.tipo === 'provision' ? 'Criar a VPS na Contabo?' : confirm?.tipo === 'suspend' ? 'Desligar a VM?' : 'Cancelar a VPS na Contabo?'}
        confirmLabel={confirm?.tipo === 'provision' ? 'Criar (gera cobrança)' : confirm?.tipo === 'suspend' ? 'Desligar' : 'Cancelar VPS'}
        onConfirm={() => run(() => (confirm.tipo === 'provision' ? api.provisionVm(confirm.v.id) : confirm.tipo === 'suspend' ? api.suspendVm(confirm.v.id) : api.cancelVm(confirm.v.id)),
          confirm.tipo === 'provision' ? 'VPS pedida à Contabo. Leva alguns minutos para ficar pronta.' : confirm.tipo === 'suspend' ? 'VM desligada.' : 'Cancelamento enviado à Contabo.')}>
        {confirm?.tipo === 'provision' && <p className="text-sm text-text-muted">Isso contrata uma VPS nova na Contabo para <b>{confirm.v.email}</b> e começa a cobrança mensal. O Segundo Cérebro é instalado sozinho.</p>}
        {confirm?.tipo === 'suspend' && <p className="text-sm text-text-muted">O cliente <b>{confirm.v.email}</b> perde o acesso à máquina até você religar. Os dados ficam guardados.</p>}
        {confirm?.tipo === 'cancel' && <p className="text-sm text-text-muted">A Contabo encerra a VPS ao fim do período contratado e <b>os dados dela são apagados</b>. Não dá para desfazer.</p>}
      </ConfirmModal>
    </>
  )
}

export default function VMsPage() {
  return (
    <>
      <PageHeader title="Máquinas virtuais" subtitle="Monitoramento das VPS dedicadas dos clientes: de quem é, quanto custa, quanto rende e em que estado está." />
      <div className="space-y-4">
        <Machines />
        <ConfigCard />
      </div>
    </>
  )
}
