// Pecas usadas nas paginas das duas maquinas: saldo/faturamento, custo da maquina e graficos de uso e latencia.
import { useEffect } from 'react'
import { Wallet, DollarSign, Activity, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtDur, fmtFull, fmtInt, cn } from '../lib/format.js'
import { Card, CardHeader, Badge, Notice, Skeleton, useLoad } from './ui.jsx'
import { BarChart, LineChart, ChartCard } from './charts.jsx'

export function Stat({ label, value, sub, strong, tone }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className={cn('num font-semibold tracking-tight', strong ? 'text-2xl' : 'text-lg', tone === 'warn' ? 'text-amber-600 dark:text-amber-400' : tone === 'bad' ? 'text-red-600 dark:text-red-400' : 'text-text-main')}>{value}</div>
      {sub && <div className="text-xs text-text-muted">{sub}</div>}
    </div>
  )
}

const dia = (v) => (v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' }).format(new Date(v * 1000)) : '—')
const diaAno = (v) => (v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric' }).format(new Date(v * 1000)) : '—')
const hora = (v) => new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit' }).format(new Date(v * 1000)).replace(/\D/g, '') + 'h'

/** Carrega e atualiza o faturamento (saldo, consumo, maquinas) a cada 30 s com a aba visivel. */
export function useBilling() {
  const b = useLoad(() => api.billing(), [])
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') b.refresh() }, 30000)
    return () => clearInterval(t)
  }, [b.refresh])
  return b
}

/** Saldo da Vast e quando recarregar: o numero que mais importa para o administrador. */
export function BalanceCard({ billing }) {
  const b = billing.data
  const d = b?.days_of_balance
  const tone = d == null ? undefined : d < 3 ? 'bad' : d < 8 ? 'warn' : undefined
  return (
    <Card>
      <CardHeader icon={Wallet} title="Saldo e faturamento" subtitle="Conta da Vast: o que sobra, quanto as máquinas consomem e quando recarregar"
        action={d != null && d < 8 ? <Badge variant={d < 3 ? 'error' : 'warning'} dot pulse>{d < 3 ? 'Recarregar já' : 'Recarregar em breve'}</Badge> : null} />
      <div className="px-5 pb-5">
        {billing.loading && !b ? <Skeleton className="h-20" /> : b?.error ? <Notice>{b.error}</Notice> : (
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-x-4 gap-y-5">
            <Stat label="Saldo" value={fmtUsd(b.credit_usd)} strong />
            <Stat label="Consumo por dia" value={fmtUsd(b.burn_per_day_usd)} sub={`≈ ${fmtUsd(b.burn_per_month_usd)}/mês com as máquinas ligadas`} />
            <Stat label="O saldo cobre" value={d == null ? '—' : `${d} dias`} tone={tone} sub={b.recharge_by ? `até ${diaAno(b.recharge_by)}` : null} />
            <Stat label="Recarregar até" value={b.recharge_by ? dia(b.recharge_by) : '—'} tone={tone} />
            <Stat label="Para fechar 30 dias" value={b.recharge_needed_for_30d_usd > 0 ? fmtUsd(b.recharge_needed_for_30d_usd) : 'coberto'} sub="recarga necessária" />
          </div>
        )}
      </div>
    </Card>
  )
}

const mesInicio = () => { const n = new Date(); return Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), 1) / 1000 }

/** Custo de uma maquina: preco por hora (GPU + disco), gasto, custo programado por mes, localizacao e vencimento da oferta. */
export function MachineCostCard({ m, loading, ledger }) {
  if (loading && !m) return <Card><CardHeader icon={DollarSign} title="Custo e localização" /><div className="px-5 pb-5"><Skeleton className="h-24" /></div></Card>
  if (!m) return <Card><CardHeader icon={DollarSign} title="Custo e localização" /><p className="px-5 pb-5 text-sm text-text-muted">Máquina não encontrada na Vast.</p></Card>
  const now = Date.now() / 1000
  const desde = Math.max(m.started || 0, mesInicio())
  const gastoMes = ledger?.month_usd ?? (m.running ? ((now - desde) / 3600) * m.dph : null)
  const gastoTotal = m.started ? ((now - m.started) / 3600) * m.dph : null
  const diasOferta = m.offer_end ? Math.max(0, Math.round((m.offer_end - now) / 86400)) : null
  return (
    <Card>
      <CardHeader icon={DollarSign} title="Custo e localização" subtitle="Preço real da máquina (GPU + disco) e o que ela vai custar" />
      <div className="px-5 pb-5 grid grid-cols-2 lg:grid-cols-4 gap-x-4 gap-y-5">
        <Stat label="Preço por hora" value={`${fmtUsd(m.dph)}/h`} sub={`GPU ${fmtUsd(m.dph_gpu)} + disco ${fmtUsd(m.storage_dph)}`} strong />
        <Stat label="Custo programado por mês" value={fmtUsd(m.monthly_usd)} sub={m.running ? 'ligada 24 h por dia' : 'desligada agora'} strong />
        <Stat label="Gasto no mês" value={gastoMes == null ? '—' : fmtUsd(gastoMes)} sub={ledger ? 'medido pela fila' : 'estimado pelo tempo ligada'} />
        <Stat label="Gasto desde o aluguel" value={gastoTotal == null ? '—' : fmtUsd(gastoTotal)} sub={m.started ? `desde ${diaAno(m.started)}` : null} />
        <Stat label="Localização" value={m.location || '—'} sub={`${m.gpu || 'GPU'}${m.num_gpus > 1 ? ` × ${m.num_gpus}` : ''}`} />
        <Stat label="Recursos" value={`${m.cpu_ram_gb || '—'} GB RAM`} sub={m.disk_gb ? `${Math.round(m.disk_gb)} GB de disco` : null} />
        <Stat label="Oferta vale até" value={m.offer_end ? diaAno(m.offer_end) : '—'} sub={diasOferta != null ? (diasOferta > 400 ? 'sem prazo curto' : `${fmtInt(diasOferta)} dias`) : 'a Vast não informa'} />
        <Stat label="Instância" value={<span className="font-mono text-base">#{m.id}</span>} sub={m.label || null} />
      </div>
    </Card>
  )
}

/** Pico de uso, volume e latencia (p50/p95) de uma maquina. `machine` = 'llm' ou 'image'. */
export function UsageSection({ machine, unit }) {
  const mt = useLoad(() => api.metrics(machine, 24, 14), [machine])
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') mt.refresh() }, 30000)
    return () => clearInterval(t)
  }, [mt.refresh])
  const d = mt.data
  const t = d?.totals || {}
  return (
    <Card>
      <CardHeader icon={Activity} title="Uso, picos e latência" subtitle="Últimas 24 h por hora e últimos 14 dias por dia; só quantidades e tempos, nunca o conteúdo" />
      <div className="px-5 pb-5">
        {mt.loading && !d ? <Skeleton className="h-48" /> : d?.error ? <Notice>{d.error}</Notice> : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-x-4 gap-y-5 mb-5">
              <Stat label={`${unit} (14 dias)`} value={fmtInt(t.requests ?? 0)} sub={t.errors ? `${t.errors} com erro` : 'sem erros'} strong />
              <Stat label="Pico de uso" value={d?.peak_hour ? `${d.peak_hour.requests} / h` : '—'} sub={d?.peak_hour ? `às ${hora(d.peak_hour.t)} de ${dia(d.peak_hour.t)}` : 'nas últimas 24 h'} />
              <Stat label="Espera na fila" value={t.wait_p50 != null ? fmtDur(t.wait_p50) : '—'} sub={t.wait_p95 != null ? `95% até ${fmtDur(t.wait_p95)}` : null} />
              <Stat label="Tempo de execução" value={t.run_p50 != null ? fmtDur(t.run_p50) : '—'} sub={t.run_p95 != null ? `95% até ${fmtDur(t.run_p95)}` : null} />
              <Stat label="Mais ativos" value={d?.top_users?.[0]?.who || '—'} sub={d?.top_users?.[0] ? `${d.top_users[0].requests} pedidos` : null} />
            </div>
            <div className="grid gap-4 lg:grid-cols-2">
              <ChartCard title="Pedidos por hora" subtitle="Últimas 24 horas">
                <BarChart data={(d?.hourly || []).map((h) => ({ x: h.t, y: h.requests }))} fmtX={(x) => hora(x)} />
              </ChartCard>
              <ChartCard title="Pedidos por dia" subtitle="Últimos 14 dias">
                <BarChart data={(d?.daily || []).map((h) => ({ x: h.t, y: h.requests }))} fmtX={(x) => dia(x)} />
              </ChartCard>
              <ChartCard title="Latência por hora" subtitle="Tempo de execução e espera, em segundos">
                <LineChart xs={(d?.hourly || []).map((h) => h.t)} fmtX={(x) => hora(x)} fmtY={(v) => (v < 10 ? String(Math.round(v * 10) / 10).replace('.', ',') : Math.round(v)) + ' s'}
                  series={[{ name: 'Execução (mediana)', color: 'var(--color-brand-500)', points: (d?.hourly || []).map((h) => h.run_p50) },
                           { name: 'Execução (95%)', color: '#d97706', points: (d?.hourly || []).map((h) => h.run_p95) },
                           { name: 'Espera na fila (95%)', color: '#2563eb', points: (d?.hourly || []).map((h) => h.wait_p95) }]} />
              </ChartCard>
              <ChartCard title="Ocupação da GPU por hora" subtitle="Quanto da hora a GPU ficou trabalhando">
                <BarChart data={(d?.hourly || []).map((h) => ({ x: h.t, y: h.busy_pct || 0 }))} fmtX={(x) => hora(x)} fmtY={(v) => Math.round(v) + '%'} color="#16a34a" unit="%" />
              </ChartCard>
            </div>
          </>
        )}
      </div>
    </Card>
  )
}

export { AlertTriangle, fmtFull }
