import { Cpu, Wallet, Server, RefreshCw } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtDur, fmtFull, fmtIn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Progress, Button, Notice, Skeleton, EmptyState, ErrorState, useLoad } from '../components/ui.jsx'

const STATES = {
  ready: { label: 'Pronta', variant: 'success', text: 'A GPU está ligada e respondendo.' },
  starting: { label: 'Ligando', variant: 'warning', text: 'A GPU está sendo preparada. Quem chamar agora recebe o aviso de espera.' },
  off: { label: 'Desligada', variant: 'default', text: 'Sem custo. Liga sozinha no próximo pedido.' },
}
const stateInfo = (s) => STATES[s] || (s === 'stopped' || s === 'down' || s === 'sleeping' ? STATES.off : s === 'booting' || s === 'warming' ? STATES.starting : { label: s || 'Desconhecido', variant: 'default', text: '' })

const REASON = { ociosidade: 'warning', erro: 'error', 'comando do admin': 'info' }
const STAGES = [['image_s', 'Imagem'], ['download_s', 'Download'], ['vllm_s', 'vLLM'], ['warmup_s', 'Aquecimento']]

function Stat({ label, value, sub, strong }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className={'num font-semibold tracking-tight ' + (strong ? 'text-2xl text-text-main' : 'text-lg text-text-main')}>{value}</div>
      {sub && <div className="text-xs text-text-muted">{sub}</div>}
    </div>
  )
}

function Stages({ c }) {
  const vals = STAGES.map(([k]) => c.stages?.[k])
  if (vals.every((v) => v == null)) return <span className="text-text-subtle">—</span>
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-text-muted num">
      {STAGES.map(([k, l]) => <span key={k}>{l} <span className="text-text-main">{c.stages?.[k] == null ? '—' : fmtDur(c.stages[k])}</span></span>)}
    </div>
  )
}
const Kind = ({ k }) => <Badge variant={k === 'cold' ? 'primary' : 'info'}>{k === 'cold' ? 'Do zero' : 'Religar'}</Badge>
const Reason = ({ r, running }) => running ? <Badge variant="success" dot pulse>Em execução</Badge>
  : r ? <Badge variant={REASON[r] || 'default'}>{r.charAt(0).toUpperCase() + r.slice(1)}</Badge> : <span className="text-text-subtle">—</span>

export default function Machines() {
  const { data, loading, error, reload, refresh } = useLoad(() => api.machines(50), [])
  const st = data?.status
  const sp = st?.spend
  const info = stateInfo(st?.state)
  const cycles = data?.cycles || []
  const warn = data?.error || st?.error
  const capPct = sp?.month_cap_usd ? Math.min(100, (sp.month_usd / sp.month_cap_usd) * 100) : 0

  return (
    <>
      <PageHeader title="Máquinas" subtitle="Estado da GPU, gasto do mês e o ciclo de vida de cada máquina ligada."
        actions={<Button variant="outline" icon={RefreshCw} onClick={refresh}>Atualizar</Button>} />

      {error ? <Card><ErrorState message={error} onRetry={reload} /></Card> : (
        <div className="space-y-4">
          {warn && <Notice>Dados incompletos: {String(warn)}</Notice>}
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader icon={Cpu} title="GPU agora" />
              <div className="px-5 pb-5">
                {loading && !data ? <Skeleton className="h-20" /> : (
                  <>
                    <div className="flex items-center gap-3 flex-wrap">
                      <Badge variant={info.variant} dot pulse={st?.state === 'starting'} className="text-sm px-3 py-1">{info.label}</Badge>
                      {st?.eta_s != null && st.state !== 'ready' && <span className="text-sm text-text-muted num">pronta em cerca de {fmtDur(st.eta_s)}</span>}
                    </div>
                    <p className="text-sm text-text-muted mt-3">{info.text}</p>
                  </>
                )}
              </div>
            </Card>
            <Card>
              <CardHeader icon={Wallet} title="Gasto" subtitle="Dólares, estimativa da fila" />
              <div className="px-5 pb-5">
                {loading && !data ? <Skeleton className="h-20" /> : !sp ? <p className="text-sm text-text-muted">Sem dados de gasto.</p> : (
                  <>
                    <div className="grid grid-cols-3 gap-4">
                      <Stat label="Mês" value={fmtUsd(sp.month_usd)} sub={sp.month_cap_usd != null ? `teto ${fmtUsd(sp.month_cap_usd)}` : null} strong />
                      <Stat label="Hoje" value={fmtUsd(sp.day_usd)} />
                      <Stat label="Saldo do mês" value={fmtUsd(sp.remaining_usd)} />
                    </div>
                    {sp.month_cap_usd != null && <div className="mt-4"><Progress used={sp.month_usd} limit={sp.month_cap_usd} /><div className="text-xs text-text-muted mt-1.5 num">{Math.round(capPct)}% do teto mensal</div></div>}
                  </>
                )}
              </div>
            </Card>
          </div>

          <Card className="overflow-hidden">
            <CardHeader icon={Server} title="Ciclos de vida" subtitle="Uma linha por vez que a GPU foi ligada" />
            {loading && !data ? <div className="p-5 pt-2 space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>
              : cycles.length === 0 ? <EmptyState icon={Server} title="Nenhum ciclo registrado" text="Quando a GPU ligar pela primeira vez, o ciclo aparece aqui." />
              : (
                <>
                  <div className="hidden lg:block overflow-x-auto border-t border-border-subtle">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                          <th className="font-medium px-5 py-3">Ligou em</th>
                          <th className="font-medium px-3 py-3">Tipo</th>
                          <th className="font-medium px-3 py-3">Cold start</th>
                          <th className="font-medium px-3 py-3">Host / região</th>
                          <th className="font-medium px-3 py-3 text-right">Preço/h</th>
                          <th className="font-medium px-3 py-3">Desligou em</th>
                          <th className="font-medium px-3 py-3">Motivo</th>
                          <th className="font-medium px-5 py-3 text-right">Custo</th>
                        </tr>
                      </thead>
                      <tbody>
                        {cycles.map((c) => (
                          <tr key={c.id} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50 align-top">
                            <td className="px-5 py-3 num whitespace-nowrap">{fmtFull(c.started_at)}</td>
                            <td className="px-3 py-3"><Kind k={c.kind} /></td>
                            <td className="px-3 py-3 min-w-[220px]">
                              <div className="num font-semibold text-text-main">{c.cold_start_s == null ? 'não ficou pronta' : fmtDur(c.cold_start_s)}</div>
                              <Stages c={c} />
                            </td>
                            <td className="px-3 py-3 whitespace-nowrap"><div className="font-mono text-[12px]">{c.host}</div><div className="text-xs text-text-muted">{c.geo}</div></td>
                            <td className="px-3 py-3 num text-right whitespace-nowrap">{fmtUsd(c.price_dph)}</td>
                            <td className="px-3 py-3 num whitespace-nowrap">{c.stopped_at ? fmtFull(c.stopped_at) : <span className="text-text-subtle">—</span>}</td>
                            <td className="px-3 py-3"><Reason r={c.stop_reason} running={!c.stopped_at} /></td>
                            <td className="px-5 py-3 num text-right font-semibold text-text-main whitespace-nowrap">{fmtUsd(c.cost_usd)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <ul className="lg:hidden divide-y divide-border-subtle border-t border-border-subtle">
                    {cycles.map((c) => (
                      <li key={c.id} className="p-4 flex flex-col gap-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2"><Kind k={c.kind} /><span className="num text-sm text-text-main">{fmtFull(c.started_at)}</span></div>
                          <span className="num font-semibold text-text-main">{fmtUsd(c.cost_usd)}</span>
                        </div>
                        <div className="text-sm"><span className="text-text-muted">Cold start </span><span className="num font-semibold text-text-main">{c.cold_start_s == null ? 'não ficou pronta' : fmtDur(c.cold_start_s)}</span></div>
                        <Stages c={c} />
                        <div className="flex items-center justify-between gap-2 text-xs text-text-muted">
                          <span><span className="font-mono">{c.host}</span> · {c.geo} · <span className="num">{fmtUsd(c.price_dph)}/h</span></span>
                          <Reason r={c.stop_reason} running={!c.stopped_at} />
                        </div>
                        {c.stopped_at && <div className="text-xs text-text-muted num">Desligou em {fmtFull(c.stopped_at)}</div>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
          </Card>
        </div>
      )}
    </>
  )
}
