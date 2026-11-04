import { useEffect } from 'react'
import { Image as ImageIcon, Server, ListOrdered, RefreshCw, Cpu } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtDur, fmtFull } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Notice, Skeleton, EmptyState, Button, useLoad } from '../components/ui.jsx'
import { BalanceCard, MachineCostCard, UsageSection, Stat, useBilling } from '../components/machine-shared.jsx'

const STATES = {
  ready: { label: 'Pronta', variant: 'success', text: 'A 3090 está ligada e o gerador de imagens responde.' },
  starting: { label: 'Ligando', variant: 'warning', text: 'A máquina está subindo; os pedidos esperam na fila.' },
  degraded: { label: 'Com problema', variant: 'error', text: 'A máquina está ligada, mas o gerador não responde. Os pedidos falham até voltar.' },
  off: { label: 'Desligada', variant: 'default', text: 'A máquina de imagem não está de pé.' },
}
const stateInfo = (s) => STATES[s] || { label: s || 'Desconhecido', variant: 'default', text: '' }
const JOB = { na_fila: ['Na fila', 'info'], executando: ['Gerando', 'success'], done: ['Concluído', 'success'], failed: ['Falhou', 'error'] }
const WHO = (j) => j.end_user || j.consumer || '—'

/** Pagina da maquina de imagem: mesma organizacao da LLM (saldo, estado, custo, fila, uso e latencia). Sem galeria de imagens. */
export default function MachineImage() {
  const st = useLoad(() => api.mediaStatus(), [])
  const jb = useLoad(() => api.mediaJobs(40), [])
  const billing = useBilling()
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') { st.refresh(); jb.refresh() } }, 5000)
    return () => clearInterval(t)
  }, [st.refresh, jb.refresh])

  const s = st.data
  const info = stateInfo(s?.state)
  const active = [...(jb.data?.running || []), ...(jb.data?.waiting || [])]
  const recent = jb.data?.recent || []
  const lane = jb.data?.lane
  const vram = s?.comfy?.vram_total ? Math.round(((s.comfy.vram_total - s.comfy.vram_free) / s.comfy.vram_total) * 100) : null

  if (s && s.enabled === false) {
    return (<><PageHeader title="Máquina de imagem" subtitle="Geração de imagens." /><Card><EmptyState icon={ImageIcon} title="Geração de imagem desligada" text={s.error || 'Defina MEDIA_INSTANCE_ID no llm-queue para ligar a fila de imagem.'} /></Card></>)
  }
  return (
    <>
      <PageHeader title="Máquina de imagem" subtitle="A GPU que gera as imagens (ComfyUI): estado, custo, fila de pedidos, uso e latência."
        actions={<Button variant="outline" icon={RefreshCw} onClick={() => { st.refresh(); jb.refresh(); billing.refresh() }}>Atualizar</Button>} />
      <div className="space-y-4">
        {(st.error || jb.error) && <Notice>Dados incompletos: {st.error || jb.error}</Notice>}
        <BalanceCard billing={billing} />

        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader icon={ImageIcon} title="Máquina agora" subtitle="3090 fixa 24 h por dia rodando o ComfyUI" />
            <div className="px-5 pb-5">
              {st.loading && !s ? <Skeleton className="h-20" /> : (
                <>
                  <div className="flex items-center gap-3 flex-wrap">
                    <Badge variant={info.variant} dot pulse={s?.state === 'starting'} className="text-sm px-3 py-1">{info.label}</Badge>
                    <Badge variant={s?.tunnel ? 'success' : 'default'}>{s?.tunnel ? 'Túnel aberto' : 'Túnel fechado'}</Badge>
                  </div>
                  <p className="text-sm text-text-muted mt-3">{info.text}</p>
                  <div className="grid grid-cols-2 gap-4 mt-4">
                    <Stat label="ComfyUI" value={s?.comfy?.version ? 'v' + s.comfy.version : '—'} sub={vram != null ? `VRAM em uso: ${vram}%` : 'sem resposta'} />
                    <Stat label="Receitas" value={s?.recipes?.length ?? 0} sub={(s?.recipes || []).map((r) => r.title).join(', ') || null} />
                  </div>
                </>
              )}
            </div>
          </Card>
          <Card>
            <CardHeader icon={Cpu} title="Produção de hoje" subtitle="Últimas 24 horas" />
            <div className="px-5 pb-5">
              {st.loading && !s ? <Skeleton className="h-20" /> : (
                <div className="grid grid-cols-2 gap-x-4 gap-y-5">
                  <Stat label="Imagens geradas" value={s?.today?.done ?? 0} sub={s?.today?.failed ? `${s.today.failed} falharam` : 'nenhuma falha'} strong />
                  <Stat label="Tempo médio" value={s?.today?.avg_seconds != null ? fmtDur(s.today.avg_seconds) : '—'} sub="por imagem" />
                  <Stat label="Na fila" value={jb.data?.waiting?.length ?? 0} sub={lane ? `${lane.inflight} gerando · limite ${lane.limit} por vez` : null} />
                  <Stat label="Atendidas desde a última subida" value={lane?.served ?? 0} />
                </div>
              )}
            </div>
          </Card>
        </div>

        <MachineCostCard m={billing.data?.machines?.image} loading={billing.loading} />

        <Card className="overflow-hidden">
          <CardHeader icon={ListOrdered} title="Fila de pedidos" subtitle="Quem está esperando, quem está gerando e os últimos que terminaram. O texto do pedido fica guardado por 48 h" />
          {jb.loading && !jb.data ? <div className="p-5 pt-2"><Skeleton className="h-16" /></div> : (
            <div className="border-t border-border-subtle">
              {active.length === 0 ? <p className="px-5 py-4 text-sm text-text-muted">Nenhum pedido na fila agora.</p> : (
                <ul className="divide-y divide-border-subtle">
                  {active.map((j) => {
                    const [label, variant] = JOB[j.state] || [j.state, 'default']
                    return (
                      <li key={j.id} className="px-5 py-3 flex items-center justify-between gap-3 flex-wrap">
                        <div className="flex items-center gap-3 min-w-0">
                          <Badge variant={variant} dot pulse={j.state === 'executando'}>{label}</Badge>
                          <span className="text-sm text-text-main shrink-0">{WHO(j)}</span>
                          <span className="text-xs text-text-muted truncate max-w-[40ch]" title={j.prompt}>{j.prompt}</span>
                        </div>
                        <span className="num text-sm text-text-muted">{j.state === 'executando' ? 'gerando há ' + fmtDur(j.running_s) : 'esperando há ' + fmtDur(j.waited_s)}</span>
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
                        <th className="font-medium px-3 py-2.5">Pedido</th>
                        <th className="font-medium px-3 py-2.5 text-right">Esperou</th>
                        <th className="font-medium px-3 py-2.5 text-right">Gerou em</th>
                        <th className="font-medium px-5 py-2.5">Resultado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.slice(0, 15).map((j) => {
                        const [label, variant] = JOB[j.state] || [j.state, 'default']
                        return (
                          <tr key={j.id} className="border-b border-border-subtle last:border-b-0 align-top">
                            <td className="px-5 py-2.5 num whitespace-nowrap">{fmtFull(j.finished)}</td>
                            <td className="px-3 py-2.5">{WHO(j)}</td>
                            <td className="px-3 py-2.5 text-text-muted max-w-[34ch] truncate" title={j.prompt}>{j.prompt}</td>
                            <td className="px-3 py-2.5 num text-right">{fmtDur((j.started ?? j.finished) - j.enq)}</td>
                            <td className="px-3 py-2.5 num text-right">{j.seconds != null ? fmtDur(j.seconds) : '—'}</td>
                            <td className="px-5 py-2.5"><Badge variant={variant}>{label}</Badge>{j.error && <div className="text-xs text-text-muted mt-1 max-w-[30ch]">{j.error}</div>}</td>
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

        <UsageSection machine="image" unit="Imagens" />
      </div>
    </>
  )
}
