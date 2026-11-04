// Graficos leves em SVG (sem biblioteca): barras e linhas, claro/escuro pelas variaveis do tema, dica ao passar o mouse.
import { cn } from '../lib/format.js'

const W = 420
const PAD = { l: 34, r: 8, t: 8, b: 22 }

const nice = (max) => {
  if (max <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(max))
  const f = max / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p
}
const compact = (v) => (v >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0).replace('.', ',') + ' mil' : String(Math.round(v * 10) / 10).replace('.', ','))

function Frame({ height, top, ticks, fmtY, labels, children, empty }) {
  const h = height - PAD.t - PAD.b
  const w = W - PAD.l - PAD.r
  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="w-full h-auto select-none" role="img">
      {ticks.map((t) => {
        const y = PAD.t + h - (t / top) * h
        return (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} stroke="var(--color-border-subtle)" strokeWidth="1" />
            <text x={PAD.l - 6} y={y + 3.5} textAnchor="end" className="fill-text-muted" fontSize="11">{fmtY(t)}</text>
          </g>
        )
      })}
      {children(w, h)}
      {labels.map(([i, text]) => <text key={i} x={PAD.l + (w * (i + 0.5)) / Math.max(1, labels.n)} y={height - 6} textAnchor="middle" className="fill-text-muted" fontSize="11">{text}</text>)}
      {empty && <text x={W / 2} y={height / 2} textAnchor="middle" className="fill-text-muted" fontSize="12">Sem dados neste período</text>}
    </svg>
  )
}

function axis(values, fmtX, every) {
  const n = values.length
  const out = []
  values.forEach((_, i) => { if (i % every === 0 || i === n - 1) out.push([i, fmtX(values[i], i)]) })
  out.n = n
  return out
}

/** Barras. data: [{x, y, tip}]; y pode ser 0. */
export function BarChart({ data, height = 150, fmtX = (x) => x, fmtY = compact, color = 'var(--color-brand-500)', unit = '' }) {
  const max = Math.max(0, ...data.map((d) => d.y || 0))
  const top = nice(max)
  const ticks = [0, top / 2, top]
  const every = Math.max(1, Math.ceil(data.length / 8))
  return (
    <Frame height={height} top={top} ticks={ticks} fmtY={fmtY} labels={axis(data.map((d) => d.x), fmtX, every)} empty={max === 0}>
      {(w, h) => data.map((d, i) => {
        const bw = (w / data.length) * 0.72
        const bh = ((d.y || 0) / top) * h
        const x = PAD.l + (w * (i + 0.5)) / data.length - bw / 2
        return (
          <rect key={i} x={x} y={PAD.t + h - bh} width={bw} height={Math.max(bh, d.y ? 1.5 : 0)} rx="2" fill={color} opacity={d.y ? 0.9 : 0.25}>
            <title>{d.tip ?? `${fmtX(d.x, i)}: ${fmtY(d.y || 0)}${unit}`}</title>
          </rect>
        )
      })}
    </Frame>
  )
}

/** Linhas. series: [{name, color, points:[numero|null]}]; xs: rotulos. */
export function LineChart({ series, xs, height = 150, fmtX = (x) => x, fmtY = compact }) {
  const max = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p || 0)))
  const top = nice(max)
  const n = xs.length
  const every = Math.max(1, Math.ceil(n / 8))
  return (
    <div>
      <Frame height={height} top={top} ticks={[0, top / 2, top]} fmtY={fmtY} labels={axis(xs, fmtX, every)} empty={max === 0}>
        {(w, h) => series.map((s) => {
          const pts = s.points.map((p, i) => (p == null ? null : [PAD.l + (w * (i + 0.5)) / n, PAD.t + h - (p / top) * h, p, i]))
          let d = ''
          let pen = false
          pts.forEach((p) => { if (!p) { pen = false; return } d += `${pen ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)} `; pen = true })
          return (
            <g key={s.name}>
              <path d={d} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              {pts.map((p) => p && <circle key={p[3]} cx={p[0]} cy={p[1]} r="2.6" fill={s.color}><title>{`${s.name} · ${fmtX(xs[p[3]], p[3])}: ${fmtY(p[2])}`}</title></circle>)}
            </g>
          )
        })}
      </Frame>
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-1 pt-1">
        {series.map((s) => <span key={s.name} className="inline-flex items-center gap-1.5 text-xs text-text-muted"><span className="size-2 rounded-full" style={{ background: s.color }} />{s.name}</span>)}
      </div>
    </div>
  )
}

export function ChartCard({ title, subtitle, children, className }) {
  return (
    <div className={cn('rounded-[14px] border border-border-subtle bg-surface p-4', className)}>
      <div className="mb-2"><div className="text-sm font-semibold text-text-main">{title}</div>{subtitle && <div className="text-xs text-text-muted">{subtitle}</div>}</div>
      {children}
    </div>
  )
}
