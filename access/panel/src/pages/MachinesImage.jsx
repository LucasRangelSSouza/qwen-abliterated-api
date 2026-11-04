import { useEffect, useState } from 'react'
import { Image as ImageIcon, Server, ListOrdered, Images, Wallet, Cpu, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtUsd, fmtDur, fmtFull } from '../lib/format.js'
import { Card, CardHeader, Badge, Notice, Skeleton, EmptyState, Modal, useLoad } from '../components/ui.jsx'

export const IMG_STATES = {
  ready: { label: 'Pronta', variant: 'success', text: 'A 3090 está ligada e o gerador de imagens responde.' },
  starting: { label: 'Ligando', variant: 'warning', text: 'A máquina está subindo; os pedidos esperam na fila.' },
  degraded: { label: 'Com problema', variant: 'error', text: 'A máquina está ligada, mas o gerador não responde. Os pedidos falham até voltar.' },
  off: { label: 'Desligada', variant: 'default', text: 'A máquina de imagem não está de pé.' },
}
export const imgState = (s) => IMG_STATES[s] || { label: s || 'Desconhecido', variant: 'default', text: '' }

const JOB = { na_fila: ['Na fila', 'info'], executando: ['Gerando', 'success'], done: ['Pronta', 'success'], failed: ['Falhou', 'error'] }
const RECIPE = { 'flux-schnell': 'Flux schnell' }
const WHO = (j) => j.end_user || j.consumer || '—'

/** Imagem do pedido (so o painel, com login). No mock nao ha servidor de imagens: desenha um degrade com a semente do id. */
export function jobImg(id) {
  if (import.meta.env.VITE_MOCK) {
    let h = 0
    for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) % 360
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='hsl(${h},70%,55%)'/><stop offset='1' stop-color='hsl(${(h + 70) % 360},75%,35%)'/></linearGradient></defs><rect width='256' height='256' fill='url(#g)'/><circle cx='128' cy='116' r='56' fill='rgba(255,255,255,.18)'/></svg>`
    return 'data:image/svg+xml,' + encodeURIComponent(svg)
  }
  return `/admin/api/media/image/${id}`
}

function Stat({ label, value, sub, strong }) {
  return (
    <div>
      <div className="text-xs font-medium text-text-muted">{label}</div>
      <div className={'num font-semibold tracking-tight ' + (strong ? 'text-2xl text-text-main' : 'text-lg text-text-main')}>{value}</div>
      {sub && <div className="text-xs text-text-muted">{sub}</div>}
    </div>
  )
}

function Thumb({ j, onOpen }) {
  const ok = j.state === 'done'
  return (
    <button type="button" onClick={() => ok && onOpen(j)} disabled={!ok}
      className="group text-left rounded-[12px] border border-border-subtle bg-surface overflow-hidden focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-default">
      <div className="aspect-square bg-surface-2 relative overflow-hidden">
        {ok ? <img src={jobImg(j.id)} alt={'Imagem gerada para ' + WHO(j)} loading="lazy" className="size-full object-cover transition-transform duration-300 group-hover:scale-105" />
          : <div className="size-full grid place-items-center p-3 text-center text-xs text-text-muted"><AlertTriangle className="size-5 mb-1 mx-auto text-red-500" />{j.error || 'Falhou'}</div>}
      </div>
      <div className="px-2.5 py-2">
        <div className="text-xs font-medium text-text-main truncate">{WHO(j)}</div>
        <div className="text-[11px] text-text-muted truncate" title={j.prompt}>{j.prompt}</div>
        <div className="text-[11px] text-text-subtle num mt-0.5">{ok ? fmtDur(j.seconds) + ' · ' + (j.size || '') : fmtFull(j.finished)}</div>
      </div>
    </button>
  )
}

export default function MachinesImage({ onStatus }) {
  const st = useLoad(() => api.mediaStatus(), [])
  const jb = useLoad(() => api.mediaJobs(40), [])
  const [open, setOpen] = useState(null)
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === 'visible') { st.refresh(); jb.refresh() } }, 4000)
    return () => clearInterval(t)
  }, [st.refresh, jb.refresh])
  useEffect(() => { onStatus?.(st.data, jb.data) }, [st.data, jb.data])

  const s = st.data
  const info = imgState(s?.state)
  const inst = s?.instance
  const active = [...(jb.data?.running || []), ...(jb.data?.waiting || [])]
  const recent = jb.data?.recent || []
  const gallery = recent.filter((j) => j.state === 'done' || j.state === 'failed')
  const vram = s?.comfy?.vram_total ? Math.round(((s.comfy.vram_total - s.comfy.vram_free) / s.comfy.vram_total) * 100) : null

  if (s && s.enabled === false) {
    return <Card><EmptyState icon={ImageIcon} title="Geração de imagem desligada" text={s.error || 'Defina MEDIA_INSTANCE_ID no llm-queue para ligar a fila de imagem.'} /></Card>
  }
  return (
    <div className="space-y-4">
      {(st.error || jb.error) && <Notice>Dados incompletos: {st.error || jb.error}</Notice>}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader icon={ImageIcon} title="Máquina de imagem" subtitle="3090 fixa 24/7 rodando o ComfyUI" />
          <div className="px-5 pb-5">
            {st.loading && !s ? <Skeleton className="h-20" /> : (
              <>
                <div className="flex items-center gap-3 flex-wrap">
                  <Badge variant={info.variant} dot pulse={s?.state === 'starting'} className="text-sm px-3 py-1">{info.label}</Badge>
                  <Badge variant={s?.tunnel ? 'success' : 'default'}>{s?.tunnel ? 'Túnel aberto' : 'Túnel fechado'}</Badge>
                </div>
                <p className="text-sm text-text-muted mt-3">{info.text}</p>
              </>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader icon={Wallet} title="Custo e produção" subtitle="Preço da máquina e o que ela entregou nas últimas 24 h" />
          <div className="px-5 pb-5">
            {st.loading && !s ? <Skeleton className="h-20" /> : (
              <div className="grid grid-cols-2 gap-x-4 gap-y-5">
                <Stat label="Preço" value={inst?.dph != null ? `${fmtUsd(inst.dph)}/h` : '—'} sub={s?.monthly_cost_usd ? `≈ ${fmtUsd(s.monthly_cost_usd)}/mês ligada` : null} strong />
                <Stat label="Imagens (24 h)" value={s?.today?.done ?? 0} sub={s?.today?.failed ? `${s.today.failed} falharam` : 'nenhuma falha'} />
                <Stat label="Tempo médio" value={s?.today?.avg_seconds != null ? fmtDur(s.today.avg_seconds) : '—'} sub="por imagem" />
                <Stat label="Fila" value={(jb.data?.waiting?.length ?? 0)} sub={`${jb.data?.running?.length ?? 0} gerando`} />
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader icon={Server} title="Máquina virtual" subtitle="Onde o gerador de imagens está rodando" />
        <div className="border-t border-border-subtle px-5 py-4 grid gap-4 grid-cols-2 sm:grid-cols-4">
          <Stat label="Instância" value={inst ? <span className="font-mono text-base">#{inst.id}</span> : '—'} sub={inst?.label || null} strong />
          <Stat label="GPU" value={s?.comfy?.gpu || inst?.gpu || '—'} sub={inst?.num_gpus ? `${inst.num_gpus} placa` : null} />
          <Stat label="Região" value={inst?.location || '—'} sub={inst?.cpu_ram_gb ? `${inst.cpu_ram_gb} GB de RAM · ${Math.round(inst.disk_gb || 0)} GB de disco` : null} />
          <Stat label="ComfyUI" value={s?.comfy?.version ? 'v' + s.comfy.version : '—'} sub={vram != null ? `VRAM em uso: ${vram}%` : 'sem resposta'} />
        </div>
        {s?.recipes?.length > 0 && (
          <div className="border-t border-border-subtle px-5 py-3 flex items-center gap-2 flex-wrap text-xs text-text-muted">
            <Cpu className="size-3.5" /> Receitas disponíveis:
            {s.recipes.map((r) => <Badge key={r.id} variant="primary">{r.title}</Badge>)}
          </div>
        )}
      </Card>

      <Card className="overflow-hidden">
        <CardHeader icon={ListOrdered} title="Fila de imagens" subtitle="Uma imagem por vez; quem pediu e o que pediu (o texto do pedido fica guardado por 48 h)" />
        {jb.loading && !jb.data ? <div className="p-5 pt-2"><Skeleton className="h-16" /></div> : (
          active.length === 0 ? <p className="px-5 pb-5 text-sm text-text-muted border-t border-border-subtle pt-4">Nenhum pedido na fila agora.</p> : (
            <ul className="divide-y divide-border-subtle border-t border-border-subtle">
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
          )
        )}
      </Card>

      <Card className="overflow-hidden">
        <CardHeader icon={Images} title="Galeria" subtitle="Últimas imagens geradas; clique para ver em tamanho grande" />
        {jb.loading && !jb.data ? <div className="p-5 pt-2"><Skeleton className="h-32" /></div>
          : gallery.length === 0 ? <EmptyState icon={Images} title="Nenhuma imagem ainda" text="Quando alguém pedir uma imagem, ela aparece aqui." />
          : (
            <div className="border-t border-border-subtle p-4 grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
              {gallery.slice(0, 20).map((j) => <Thumb key={j.id} j={j} onOpen={setOpen} />)}
            </div>
          )}
      </Card>

      <Modal open={!!open} onClose={() => setOpen(null)} title={open ? `${WHO(open)} · ${RECIPE[open.recipe] || open.recipe}` : ''} wide>
        {open && (
          <div className="space-y-3">
            <img src={jobImg(open.id)} alt={'Imagem gerada para ' + WHO(open)} className="w-full max-h-[70vh] object-contain rounded-[12px] bg-surface-2" />
            <p className="text-sm text-text-main break-words">{open.prompt}</p>
            <p className="text-xs text-text-muted num">{open.size} · gerada em {fmtDur(open.seconds)} · {fmtFull(open.finished)}</p>
          </div>
        )}
      </Modal>
    </div>
  )
}
