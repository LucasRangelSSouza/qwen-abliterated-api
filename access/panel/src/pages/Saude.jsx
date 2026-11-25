import { Database, HardDrive, AlertTriangle, RefreshCw } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Skeleton, ErrorState, useLoad } from '../components/ui.jsx'

const gb = (b) => (b == null ? '—' : (b / 1e9).toFixed(1) + ' GB')
const min = (s) => (s < 120 ? `${s} s` : `${Math.round(s / 60)} min`)

function Kpi({ rotulo, valor, sub, nivel }) {
  const cor = nivel === 'critico' ? 'text-red-600' : nivel === 'aviso' ? 'text-amber-600' : 'text-text-main'
  return (
    <Card className="p-4">
      <div className="text-xs text-text-muted">{rotulo}</div>
      <div className={'text-2xl font-semibold num mt-1 ' + cor}>{valor ?? '—'}</div>
      {sub && <div className="text-xs text-text-subtle mt-1">{sub}</div>}
    </Card>
  )
}

// nível pelo limite (aviso, crítico); "menor é pior" quando o crítico é menor que o aviso (ex.: acerto de cache)
function nivel(v, lim) {
  if (v == null || !lim) return ''
  const [a, c] = lim
  if (c < a) return v <= c ? 'critico' : v <= a ? 'aviso' : ''
  return v >= c ? 'critico' : v >= a ? 'aviso' : ''
}

function Linha({ serie, campo, rotulo }) {
  const pts = serie.filter((p) => p[campo] != null)
  if (pts.length < 2) return <div className="text-xs text-text-subtle px-5 pb-4">{rotulo}: juntando amostras…</div>
  const vs = pts.map((p) => p[campo]); const mx = Math.max(...vs, 1e-9); const mn = Math.min(...vs, 0)
  const w = 600, h = 60
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${(i / (pts.length - 1)) * w},${h - ((p[campo] - mn) / (mx - mn || 1)) * h}`).join(' ')
  return (
    <div className="px-5 pb-4">
      <div className="text-xs text-text-muted mb-1">{rotulo} (último {vs[vs.length - 1]}, máx {mx})</div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16"><path d={d} fill="none" stroke="currentColor" strokeWidth="2" className="text-brand-500" /></svg>
    </div>
  )
}

function Cabecalho({ r, titulo, onAtualizar }) {
  return (
    <>
      <PageHeader title={titulo} subtitle={r.data && !r.data.erro ? `Amostra de há ${min(r.data.idade_s)} · ${r.data.amostras} amostras em 24 h · coletor a cada 5 min` : 'Saúde'}
        actions={<Button variant="secondary" icon={RefreshCw} onClick={onAtualizar}>Atualizar</Button>} />
      {r.data?.sem_dados && <Card className="p-4 mb-3 flex items-center gap-3 text-sm" data-aviso="sem-dados"><AlertTriangle className="size-5 text-amber-500" />Sem dados há {min(r.data.idade_s)}: o coletor parou. Os números abaixo são da última amostra.</Card>}
    </>
  )
}

export function Postgres() {
  const r = useLoad(() => api.painelSaude(), [])
  if (r.loading) return <Skeleton className="h-40" />
  if (r.error || r.data?.erro) return <ErrorState message={r.error || r.data.erro} onRetry={r.reload} />
  const { ultima: u, limites: L, serie } = r.data
  const pg = u.postgres || {}, dk = u.disco || {}
  return (
    <div>
      <Cabecalho r={r} titulo="Postgres" onAtualizar={r.reload} />
      {pg.erro ? <Card className="p-4 text-sm">Postgres não respondeu ao coletor: {pg.erro}</Card> : (
      <>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4" data-aba="postgres">
          <Kpi rotulo="Conexões" valor={`${pg.conexoes}/${pg.max_conexoes}`} sub={`${pg.ativas} ativas agora`} nivel={nivel(pg.conexoes_pct, L.pg_conexoes_pct)} />
          <Kpi rotulo="Acerto de cache" valor={`${pg.cache_pct}%`} nivel={nivel(pg.cache_pct, L.pg_cache_pct)} />
          <Kpi rotulo="Transações por segundo" valor={pg.tps ?? '—'} sub={`${pg.deadlocks} deadlocks na vida`} />
          <Kpi rotulo="Transação mais longa" valor={min(pg.tx_mais_longa_s || 0)} nivel={nivel(pg.tx_mais_longa_s, L.pg_tx_longa_s)} />
        </div>
        <Card className="mt-4"><CardHeader title="Últimas 24 horas" icon={Database} />
          <Linha serie={serie} campo="pg_conexoes" rotulo="Conexões" /><Linha serie={serie} campo="pg_tps" rotulo="Transações por segundo" />
        </Card>
        <Card className="mt-4"><CardHeader title="Espaço por banco" subtitle={`${gb(pg.total_bytes)} no total · ${pg.versao} · no ar há ${Math.round((pg.no_ar_s || 0) / 86400)} dias`} icon={Database} />
          <div className="divide-y divide-border-subtle">{(pg.bancos || []).slice(0, 12).map((b) => (
            <div key={b.banco} className="flex px-5 py-2 text-sm"><span className="flex-1">{b.banco}</span><span className="num">{gb(b.bytes)}</span></div>))}</div>
        </Card>
      </>)}
      <Disco dk={dk} L={L} r={r} u={u} />
    </div>
  )
}

export function Minio() {
  const r = useLoad(() => api.painelSaude(), [])
  if (r.loading) return <Skeleton className="h-40" />
  if (r.error || r.data?.erro) return <ErrorState message={r.error || r.data.erro} onRetry={r.reload} />
  const { ultima: u, limites: L, serie } = r.data
  const mn = u.minio || {}
  return (
    <div>
      <Cabecalho r={r} titulo="MinIO" onAtualizar={r.reload} />
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4" data-aba="minio">
        <Kpi rotulo="Estado" valor={mn.pronto ? 'No ar' : 'Fora do ar'} nivel={mn.pronto ? '' : 'critico'} sub={`${mn.discos_online ?? 0} disco(s) online`} />
        <Kpi rotulo="Requisições por segundo" valor={mn.req_s ?? '—'} sub={`${mn.requisicoes ?? 0} desde que ligou`} />
        <Kpi rotulo="Erros 5xx" valor={`${mn.erros_5xx_pct ?? 0}%`} nivel={nivel(mn.erros_5xx_pct, L.minio_5xx_pct)} sub={`${mn.erros_4xx ?? 0} respostas 4xx (acesso negado, não existe)`} />
        <Kpi rotulo="Tráfego" valor={gb(mn.tx_bytes)} sub={`enviado · ${gb(mn.rx_bytes)} recebido`} />
      </div>
      <Card className="mt-4"><CardHeader title="Por bucket (produto)" icon={HardDrive} />
        <div className="divide-y divide-border-subtle" data-lista="buckets">{(mn.buckets || []).map((b) => (
          <div key={b.bucket} className="flex px-5 py-2 text-sm"><span className="flex-1">{b.bucket}</span><span className="w-32 text-text-muted">{b.objetos} objetos</span><span className="num w-24 text-right">{b.tamanho}</span></div>))}</div>
      </Card>
      <Card className="mt-4"><CardHeader title="Últimas 24 horas" icon={HardDrive} /><Linha serie={serie} campo="minio_req_s" rotulo="Requisições por segundo" /></Card>
      <Disco dk={u.disco || {}} L={L} r={r} u={u} />
    </div>
  )
}

function Disco({ dk, L, r, u }) {
  return (
    <Card className="mt-4"><CardHeader title="Disco da VPS" subtitle="Um disco só para Postgres, MinIO, volumes e imagens" icon={HardDrive} />
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4 px-5 pb-5">
        <Kpi rotulo="Usado" valor={`${dk.pct}%`} nivel={nivel(dk.pct, L.disco_pct)} sub={`${dk.usado_gb} de ${dk.total_gb} GB`} />
        <Kpi rotulo="Livre" valor={`${dk.livre_gb} GB`} />
        <Kpi rotulo="Dias até encher" valor={r.data.dias_ate_encher ?? 'estável'} sub="pelo crescimento recente" />
        <Kpi rotulo="Último backup" valor={`há ${u.backup?.horas ?? '—'} h`} nivel={nivel(u.backup?.horas, L.backup_horas)} sub={u.backup?.snapshot ? 'snapshot ' + u.backup.snapshot : ''} />
      </div>
    </Card>
  )
}
