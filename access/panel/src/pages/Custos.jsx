import { useEffect, useState } from 'react'
import { Wallet, Plus, Save, Trash2, CreditCard, TrendingUp } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Input, Select, Skeleton, ErrorState, Notice, useToast, useLoad } from '../components/ui.jsx'

const brl = (n) => (n == null ? '—' : Number(n).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const moeda = (v, m) => (m === 'USD' ? `US$ ${Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : brl(v))

// Quanto o ecossistema custa por mês e quanto carregar no cartão pré-pago. Fixos editáveis; Vast e Runware medidos.
export default function Custos() {
  const toast = useToast()
  const d = useLoad(() => api.custos(), [])
  const [fixos, setFixos] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (d.data) setFixos(d.data.itens.filter((i) => i.tipo === 'fixo').map(({ nome, para_que, valor, moeda: m }) => ({ nome, para_que, valor: String(valor).replace('.', ','), moeda: m }))) }, [d.data])
  const salvar = async () => {
    setBusy(true)
    try { await api.salvarCustos(fixos); toast?.('Custos salvos'); d.reload() } catch (e) { toast?.(e.message) } finally { setBusy(false) }
  }
  const j = d.data
  const maior = j ? Math.max(1, ...j.itens.map((i) => i.brl_mes)) : 1
  return (
    <div>
      <PageHeader title="Custos" subtitle="Quanto custa manter tudo no ar por mês" />
      {d.loading ? <Skeleton className="h-64" /> : d.error ? <ErrorState message={d.error} onRetry={d.reload} /> : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="p-5 sm:col-span-1 border border-emerald-500/30">
              <div className="flex items-center gap-2 text-sm text-text-muted"><CreditCard className="size-4" />Carregue no cartão este mês</div>
              <div className="text-3xl font-semibold num mt-2 text-emerald-600 dark:text-emerald-400">{brl(j.carregar_brl)}</div>
              <div className="text-xs text-text-subtle mt-1">custo do mês + {Math.round(j.folga * 100)}% de folga · dólar a {brl(j.cotacao_usd)}</div>
            </Card>
            <Card className="p-5">
              <div className="flex items-center gap-2 text-sm text-text-muted"><Wallet className="size-4" />Custo previsto no mês</div>
              <div className="text-3xl font-semibold num mt-2 text-text-main">{brl(j.total_brl_mes)}</div>
              <div className="text-xs text-text-subtle mt-1">fixos + máquinas ligadas agora + uso dos últimos 30 dias</div>
            </Card>
            <Card className="p-5">
              <div className="flex items-center gap-2 text-sm text-text-muted"><TrendingUp className="size-4" />Receita dos produtos (planos ativos)</div>
              <div className="text-3xl font-semibold num mt-2 text-text-main">{brl(j.receita_produtos_brl)}</div>
              <div className="text-xs text-text-subtle mt-1">Cortaí e Rendaí; o Cérebro Pro está na aba Assinantes</div>
            </Card>
          </div>
          <Card className="mt-4">
            <CardHeader title="Para onde vai o dinheiro" subtitle="Cada serviço em reais por mês" icon={Wallet} />
            <div className="px-5 pb-5 space-y-3">
              {j.itens.slice().sort((a, b) => b.brl_mes - a.brl_mes).map((i) => (
                <div key={i.nome}>
                  <div className="flex justify-between gap-3 text-sm">
                    <span className="text-text-main">{i.nome} {i.tipo === 'medido' && <Badge variant={i.ok ? 'info' : 'warning'}>{i.ok ? 'medido' : 'sem leitura'}</Badge>}</span>
                    <span className="num font-medium">{brl(i.brl_mes)}{i.moeda === 'USD' && <span className="text-text-subtle font-normal"> ({moeda(i.valor, 'USD')})</span>}</span>
                  </div>
                  <div className="text-xs text-text-subtle">{i.para_que}{i.detalhe ? ` · ${i.detalhe}` : ''}</div>
                  <div className="h-2 rounded-full bg-surface-2 mt-1.5 overflow-hidden"><div className="h-full rounded-full bg-emerald-500/70" style={{ width: `${Math.max(2, (i.brl_mes / maior) * 100)}%` }} /></div>
                </div>
              ))}
            </div>
          </Card>
          <Card className="mt-4">
            <CardHeader title="Custos fixos" subtitle="Preencha o valor mensal de cada serviço (em reais ou dólares)" icon={CreditCard}
              action={<div className="flex gap-2"><Button size="sm" variant="secondary" icon={Plus} onClick={() => setFixos([...(fixos || []), { nome: '', para_que: '', valor: '0', moeda: 'BRL' }])}>Adicionar</Button>
                <Button size="sm" icon={Save} loading={busy} onClick={salvar}>Salvar</Button></div>} />
            <div className="px-5 pb-5 space-y-2">
              {(fixos || []).map((f, k) => (
                <div key={k} className="grid gap-2 sm:grid-cols-[1.2fr_1.6fr_110px_90px_40px] items-center">
                  <Input value={f.nome} onChange={(e) => setFixos(fixos.map((x, i) => (i === k ? { ...x, nome: e.target.value } : x)))} placeholder="Serviço" aria-label="Serviço" />
                  <Input value={f.para_que} onChange={(e) => setFixos(fixos.map((x, i) => (i === k ? { ...x, para_que: e.target.value } : x)))} placeholder="Para que serve" aria-label="Para que serve" />
                  <Input value={f.valor} inputMode="decimal" onChange={(e) => setFixos(fixos.map((x, i) => (i === k ? { ...x, valor: e.target.value } : x)))} aria-label="Valor mensal" />
                  <Select value={f.moeda} onChange={(e) => setFixos(fixos.map((x, i) => (i === k ? { ...x, moeda: e.target.value } : x)))} aria-label="Moeda"><option value="BRL">R$</option><option value="USD">US$</option></Select>
                  <Button size="sm" variant="ghost" icon={Trash2} aria-label="Remover" onClick={() => setFixos(fixos.filter((_, i) => i !== k))} />
                </div>
              ))}
              <Notice variant="info">A GPU da Vast e a Runware entram sozinhas (medidas). Os fixos ficam salvos aqui e somam no total.</Notice>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
