import { useEffect } from 'react'
import { Cpu, Wallet, Server, RefreshCw, ListOrdered } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtDur, fmtFull, fmtIn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Progress, Button, Notice, Skeleton, EmptyState, ErrorState, useLoad } from '../components/ui.jsx'

const STATES = {
  ready: { label: 'Pronta', variant: 'success', text: 'A GPU está ligada e respondendo.' },
  starting: { label: 'Ligando', variant: 'warning', text: 'A GPU está sendo preparada. Quem chamar agora recebe o aviso de espera.' },
  fallback: { label: 'Alugando máquina', variant: 'warning', text: 'A fila está procurando e alugando uma máquina (veja as tentativas abaixo).' },
  loading: { label: 'Carregando o modelo', variant: 'warning', text: 'A máquina já foi alugada; baixando os pesos e subindo o vLLM.' },
  stopping: { label: 'Desligando', variant: 'default', text: 'Parando a máquina (o disco continua guardado por algumas horas).' },
  off: { label: 'Desligada', variant: 'default', text: 'Sem custo de GPU. Liga sozinha no próximo pedido.' },
}
const stateInfo = (s) => {
  const k = String(s || '').toLowerCase()
  return STATES[k] || (k === 'stopped' || k === 'down' || k === 'sleeping' ? STATES.off : k === 'booting' || k === 'warming' ? STATES.starting : { label: s || 'Desconhecido', variant: 'default', text: '' })
}
const isBooting = (s) => ['starting', 'fallback', 'loading'].includes(String(s || '').toLowerCase())
const OUTCOME = {
  running: ['Alugada', 'success'], oferta_sumiu: ['Oferta sumiu', 'warning'], criar_falhou: ['Recusada pela Vast', 'error'],
  erro_gpu: ['Erro de GPU no host', 'error'], erro_registry: ['Host não baixa a imagem', 'error'], erro_disk: ['Disco cheio', 'error'],
  nao_chegou_a_running: ['Não ficou pronta a tempo', 'error'], sumiu_da_lista: ['Sumiu da lista da Vast', 'warning'],
}

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

const JOB_STATE = { na_fila: ['Na fila', 'info'], aguardando_gpu: ['Aguardando a GPU', 'warning'], executando: ['Executando', 'success'] }
const CODE = (c) => (String(c) === '200' ? ['Concluído', 'success'] : String(c) === '499' ? ['Cliente desistiu', 'default'] : String(c) === '429' ? ['Fila cheia', 'warning'] : ['Erro ' + c, 'error'])
const WHO = (j) => j.end_user || j.consumer
const KIND = { chat: 'Chat', embed: 'Embedding' }

/** Fila de pedidos: so metadados (quem, tipo, tempo). Atualiza a cada 5 s apenas com a aba visivel, para nao pesar na producao. */
function Jobs() {
  const { data, loading, refresh } = useLoad(() => api.queue(), [])
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 5000)
    return () => clearInterval(t)
  }, [refresh])
  const active = [...(data?.running || []), ...(data?.waiting || [])]
  const recent = data?.recent || []
  const l = data?.lanes?.chat
  return (
    <Card className="overflow-hidden">
      <CardHeader icon={ListOrdered} title="Fila de pedidos" subtitle="Quem está esperando, quem está rodando e os últimos que terminaram (sem o conteúdo das mensagens)" />
      {loading && !data ? <div className="p-5 pt-2"><Skeleton className="h-16" /></div> : (
        <div className="border-t border-border-subtle">
          {data?.error && <div className="px-5 pt-4"><Notice>{data.error}</Notice></div>}
          <div className="px-5 py-4 grid gap-4 grid-cols-3">
            <Stat label="Na fila" value={data?.waiting?.length ?? 0} sub={l ? 'limite de ' + l.limit + ' em paralelo' : null} strong />
            <Stat label="Executando" value={data?.running?.length ?? 0} />
            <Stat label="Atendidos desde a última subida" value={l?.served ?? 0} />
          </div>
          {active.length === 0 ? <p className="px-5 pb-4 text-sm text-text-muted">Nenhum pedido na fila agora.</p> : (
            <ul className="divide-y divide-border-subtle border-t border-border-subtle">
              {active.map((j) => {
                const [label, variant] = JOB_STATE[j.state] || [j.state, 'default']
                return (
                  <li key={j.id} className="px-5 py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-3 min-w-0">
                      <Badge variant={variant} dot pulse={j.state === 'executando'}>{label}</Badge>
                      <span className="text-sm text-text-main truncate">{WHO(j)}</span>
                      <span className="text-xs text-text-muted">{KIND[j.kind] || j.kind}{j.cls === 'batch' ? ' · lote' : ''}</span>
                    </div>
                    <span className="num text-sm text-text-muted">{j.state === 'executando' ? 'rodando há ' + fmtDur(j.running_s) : 'esperando há ' + fmtDur(j.waited_s)}</span>
                  </li>
                )
              })}
            </ul>
          )}
          {recent.length > 0 && (
            <div className="overflow-x-auto border-t border-border-subtle">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                    <th className="font-medium px-5 py-2.5">Terminou</th>
                    <th className="font-medium px-3 py-2.5">Quem</th>
                    <th className="font-medium px-3 py-2.5">Tipo</th>
                    <th className="font-medium px-3 py-2.5 text-right">Esperou</th>
                    <th className="font-medium px-3 py-2.5 text-right">Rodou</th>
                    <th className="font-medium px-5 py-2.5">Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.slice(0, 15).map((j) => {
                    const [label, variant] = CODE(j.code)
                    return (
                      <tr key={j.id} className="border-b border-border-subtle last:border-b-0">
                        <td className="px-5 py-2.5 num whitespace-nowrap">{fmtFull(j.end)}</td>
                        <td className="px-3 py-2.5">{WHO(j)}</td>
                        <td className="px-3 py-2.5 text-text-muted">{KIND[j.kind] || j.kind}</td>
                        <td className="px-3 py-2.5 num text-right">{fmtDur((j.start ?? j.end) - j.enq)}</td>
                        <td className="px-3 py-2.5 num text-right">{j.start ? fmtDur(j.end - j.start) : '—'}</td>
                        <td className="px-5 py-2.5"><Badge variant={variant}>{label}</Badge></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

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
                      <Badge variant={info.variant} dot pulse={isBooting(st?.state)} className="text-sm px-3 py-1">{info.label}</Badge>
                      {st?.eta_s != null && !['ready', 'off'].includes(String(st.state).toLowerCase()) && <span className="text-sm text-text-muted num">pronta em cerca de {fmtDur(st.eta_s)}</span>}
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

          <Jobs />

          <Card className="overflow-hidden">
            <CardHeader icon={Server} title="Máquina e tentativas de aluguel"
              subtitle="A fila procura a oferta mais barata que passa nos filtros; se uma falha, tenta a próxima" />
            {loading && !data ? <div className="p-5 pt-2"><Skeleton className="h-16" /></div> : (
              <div className="border-t border-border-subtle">
                <div className="px-5 py-4 grid gap-4 sm:grid-cols-4">
                  <Stat label="Máquina atual" value={st?.instance ? <span className="font-mono text-base">{st.instance.machine ?? '—'}</span> : 'nenhuma'}
                    sub={st?.instance ? (st.instance.stopped ? 'parada (disco guardado)' : 'alugada') : 'a fila aluga quando houver pedido'} strong />
                  <Stat label="Região" value={st?.instance?.geo || '—'} />
                  <Stat label="Preço" value={st?.instance?.dph != null ? `${fmtUsd(st.instance.dph)}/h` : '—'} sub={st?.limits?.max_allin_dph != null ? `teto ${fmtUsd(st.limits.max_allin_dph)}/h` : null} />
                  <Stat label="Subida esperada" value={st?.path === 'restart' ? 'religar (~2 min)' : st?.path ? 'máquina nova (8–20 min)' : '—'}
                    sub={st?.limits?.idle_min != null ? `desliga após ${st.limits.idle_min} min sem pedidos` : null} />
                </div>
                {(st?.attempts || []).length === 0 ? <p className="px-5 pb-5 text-sm text-text-muted">Nenhuma tentativa de aluguel registrada desde que a fila subiu.</p> : (
                  <div className="overflow-x-auto border-t border-border-subtle">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                          <th className="font-medium px-5 py-3">Hora</th>
                          <th className="font-medium px-3 py-3">Host / região</th>
                          <th className="font-medium px-3 py-3 text-right">Preço/h</th>
                          <th className="font-medium px-3 py-3 text-right">Download</th>
                          <th className="font-medium px-3 py-3">Resultado</th>
                          <th className="font-medium px-5 py-3">Detalhe</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...st.attempts].reverse().map((a, i) => {
                          const [label, variant] = OUTCOME[a.outcome] || [a.outcome, 'default']
                          return (
                            <tr key={i} className="border-b border-border-subtle last:border-b-0 align-top">
                              <td className="px-5 py-3 num whitespace-nowrap">{fmtFull(a.t)}</td>
                              <td className="px-3 py-3 whitespace-nowrap"><div className="font-mono text-[12px]">{a.machine}{a.known ? ' ★' : ''}</div><div className="text-xs text-text-muted">{a.geo}</div></td>
                              <td className="px-3 py-3 num text-right whitespace-nowrap">{a.dph != null ? fmtUsd(a.dph) : '—'}</td>
                              <td className="px-3 py-3 num text-right whitespace-nowrap">{a.down_mbps != null ? `${a.down_mbps} Mbps` : '—'}</td>
                              <td className="px-3 py-3"><Badge variant={variant}>{label}</Badge></td>
                              <td className="px-5 py-3 text-xs text-text-muted max-w-[320px] break-words">{a.detail || '—'}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="px-5 py-3 text-xs text-text-muted border-t border-border-subtle">★ = host que já subiu antes (imagem em cache). O preço mostrado é máquina + disco, por hora.</p>
              </div>
            )}
          </Card>

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
