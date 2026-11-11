import { useEffect, useState } from 'react'
import { BadgePercent, Plus, Save, Copy, ExternalLink } from 'lucide-react'
import { api } from '../lib/api.js'
import { cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Textarea, Toggle, Skeleton, EmptyState, ErrorState, Drawer, useToast, useLoad } from '../components/ui.jsx'

const brl = (n) => (n == null ? '—' : n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const SITE = 'https://brain.rangeltech.net'

function Linha({ p, v, onChange }) {
  const pct = v.preco && p.preco_normal ? Math.max(0, Math.round((1 - parseFloat(String(v.preco).replace(',', '.')) / p.preco_normal) * 100)) : null
  return (
    <div className="grid gap-2 sm:grid-cols-[170px_1fr_120px_56px] items-center py-2 border-b border-border-subtle last:border-b-0">
      <div className="text-sm font-medium text-text-main">{p.rotulo}<div className="text-xs text-text-muted">normal {brl(p.preco_normal)}</div></div>
      <Input value={v.url || ''} onChange={(e) => onChange({ ...v, url: e.target.value })} placeholder="https://pay.cakto.com.br/… (link do afiliado)" aria-label={`Link de ${p.rotulo}`} />
      <Input value={v.preco ?? ''} onChange={(e) => onChange({ ...v, preco: e.target.value })} placeholder="Preço R$" inputMode="decimal" aria-label={`Preço de ${p.rotulo}`} />
      <div className="text-sm num text-center">{pct != null && v.url ? <Badge variant="success">-{pct}%</Badge> : <span className="text-text-subtle">—</span>}</div>
    </div>
  )
}

function Editor({ open, onClose, afiliado, produtos, onSaved }) {
  const toast = useToast()
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [mostrarVm, setMostrarVm] = useState(false)
  useEffect(() => {
    if (!open) return
    const por = Object.fromEntries((afiliado?.links || []).map((l) => [l.produto, { url: l.url, preco: String(l.preco).replace('.', ',') }]))
    setF({ nome: afiliado?.nome || '', cupom: afiliado?.cupom || '', ativo: afiliado ? afiliado.ativo : true, observacoes: afiliado?.observacoes || '', links: por })
    setMostrarVm(Object.keys(por).some((k) => k.endsWith('_vm') || k.startsWith('vm_')))
  }, [open, afiliado])
  if (!f) return null
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }))
  const salvar = async () => {
    setBusy(true)
    try {
      const links = Object.entries(f.links).filter(([, v]) => (v.url || '').trim() || String(v.preco || '').trim()).map(([produto, v]) => ({ produto, url: v.url.trim(), preco: v.preco }))
      const corpo = { nome: f.nome, cupom: f.cupom, ativo: f.ativo, observacoes: f.observacoes, links }
      if (afiliado) await api.updateAffiliate(afiliado.id, corpo); else await api.createAffiliate(corpo)
      toast.success('Afiliado salvo.'); onSaved(); onClose()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const sem = produtos.filter((p) => !p.id.endsWith('_vm') && !p.id.startsWith('vm_'))
  const com = produtos.filter((p) => p.id.endsWith('_vm') || p.id.startsWith('vm_'))
  const row = (p) => <Linha key={p.id} p={p} v={f.links[p.id] || {}} onChange={(v) => setF((s) => ({ ...s, links: { ...s.links, [p.id]: v } }))} />
  return (
    <Drawer open={open} onClose={onClose} title={afiliado ? `Afiliado: ${afiliado.nome}` : 'Novo afiliado'} subtitle="O cupom só vale para os planos que tiverem link. O percentual é calculado pelo preço."
      footer={<><Button variant="secondary" onClick={onClose} disabled={busy}>Cancelar</Button><Button icon={Save} onClick={salvar} loading={busy} disabled={!f.nome.trim() || !f.cupom.trim()}>Salvar</Button></>}>
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Nome (aparece para o cliente)" htmlFor="af-nome"><Input id="af-nome" value={f.nome} onChange={(e) => set('nome', e.target.value)} placeholder="Ex.: Maria Silva" /></Field>
          <Field label="Cupom" htmlFor="af-cupom" hint="Sem validade. Letras, números, - ou _."><Input id="af-cupom" value={f.cupom} onChange={(e) => set('cupom', e.target.value.replace(/\s/g, ''))} className="font-mono uppercase" placeholder="MARIA10" /></Field>
        </div>
        <Toggle id="af-ativo" checked={f.ativo} onChange={(v) => set('ativo', v)} label="Cupom ligado" description="Desligue para pausar o afiliado sem apagar nada (links e vendas ficam). Religue quando quiser." />
        <div>
          <div className="text-sm font-medium text-text-main mb-1">Links do afiliado na Cakto e preço com desconto</div>
          <p className="text-xs text-text-muted mb-2">Cole o link de checkout do afiliado de cada plano. Deixe em branco o plano em que ele não dá desconto. Créditos avulsos nunca têm afiliado.</p>
          <div className="rounded-[12px] border border-border-subtle px-3">{sem.map(row)}</div>
          <button type="button" className="text-sm text-accent mt-3 underline" onClick={() => setMostrarVm((v) => !v)}>{mostrarVm ? 'Esconder' : 'Mostrar'} planos com máquina virtual e máquina avulsa</button>
          {mostrarVm && <div className="rounded-[12px] border border-border-subtle px-3 mt-2">{com.map(row)}</div>}
        </div>
        <Field label="Observações" htmlFor="af-obs"><Textarea id="af-obs" rows={2} value={f.observacoes} onChange={(e) => set('observacoes', e.target.value)} /></Field>
      </div>
    </Drawer>
  )
}

export default function Affiliates() {
  const toast = useToast()
  const list = useLoad(() => api.affiliates(), [])
  const [edit, setEdit] = useState(undefined) // undefined = fechado, null = novo
  const d = list.data
  const copiar = (a) => navigator.clipboard.writeText(`${SITE}/?cupom=${a.cupom}`).then(() => toast.success('Link copiado.'), () => toast.error('Não consegui copiar.'))
  const alternar = async (a) => { try { await api.updateAffiliate(a.id, { ativo: !a.ativo }); list.refresh() } catch (e) { toast.error(e.message) } }
  return (
    <>
      <PageHeader title="Afiliados" subtitle="Quem vende o Segundo Cérebro com cupom de desconto" />
      <Card className="overflow-hidden">
        <CardHeader icon={BadgePercent} title="Cupons de afiliados" subtitle="O cupom troca os botões de compra pelos links do afiliado na Cakto. Ganha só na primeira venda de plano" action={<Button icon={Plus} onClick={() => setEdit(null)}>Novo afiliado</Button>} />
        {list.error ? <ErrorState message={list.error} onRetry={list.reload} /> : list.loading && !d ? <div className="p-5 pt-0"><Skeleton className="h-32" /></div>
          : d.afiliados.length === 0 ? <EmptyState icon={BadgePercent} title="Nenhum afiliado ainda" text="Cadastre o nome, o cupom e os links da Cakto de cada plano." action={<Button icon={Plus} onClick={() => setEdit(null)}>Novo afiliado</Button>} />
          : (
            <div className="overflow-x-auto border-t border-border-subtle">
              <table className="w-full text-left text-sm">
                <thead><tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                  <th className="font-medium px-5 py-3">Afiliado</th><th className="font-medium px-3 py-3">Cupom</th><th className="font-medium px-3 py-3">Planos com desconto</th>
                  <th className="font-medium px-3 py-3 text-right">Vendas</th><th className="font-medium px-3 py-3 text-right">30 dias</th><th className="px-5 py-3"><span className="sr-only">Ações</span></th>
                </tr></thead>
                <tbody>
                  {d.afiliados.map((a) => (
                    <tr key={a.id} className={cn('border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50 align-top', !a.ativo && 'opacity-60')}>
                      <td className="px-5 py-3.5"><div className="font-semibold text-text-main">{a.nome}</div><Badge variant={a.ativo ? 'success' : 'default'} dot>{a.ativo ? 'Ligado' : 'Desligado'}</Badge></td>
                      <td className="px-3 py-3.5 font-mono">{a.cupom}</td>
                      <td className="px-3 py-3.5 max-w-[320px]"><div className="flex flex-wrap gap-1">{a.links.length ? a.links.map((l) => <Badge key={l.produto} variant="info">{d.produtos.find((p) => p.id === l.produto)?.rotulo} {l.percentual != null ? `-${l.percentual}%` : ''}</Badge>) : <span className="text-text-muted">nenhum link</span>}</div></td>
                      <td className="px-3 py-3.5 text-right num">{a.vendas}<div className="text-xs text-text-muted">{brl(a.vendas_valor)}</div></td>
                      <td className="px-3 py-3.5 text-right num">{a.vendas_30d}<div className="text-xs text-text-muted">{brl(a.vendas_30d_valor)}</div></td>
                      <td className="px-5 py-3.5 text-right whitespace-nowrap">
                        <Button variant="ghost" size="sm" icon={Copy} onClick={() => copiar(a)}>Link do site</Button>
                        <Button variant="ghost" size="sm" onClick={() => alternar(a)}>{a.ativo ? 'Desligar' : 'Ligar'}</Button>
                        <Button variant="outline" size="sm" onClick={() => setEdit(a)}>Editar</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
      <Editor open={edit !== undefined} onClose={() => setEdit(undefined)} afiliado={edit || null} produtos={d?.produtos || []} onSaved={() => list.refresh()} />
    </>
  )
}
