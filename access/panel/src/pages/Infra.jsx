import { useMemo, useState } from 'react'
import { Server, Activity, ExternalLink, Search } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Input, Select, Skeleton, ErrorState, useLoad } from '../components/ui.jsx'

const corSaude = { 'saudável': 'success', 'com problema': 'error', 'iniciando': 'warning', 'sem checagem': 'default' }
const corMonitor = { ok: 'success', fora: 'error', pendente: 'warning' }

function Kpi({ rotulo, valor, sub }) {
  return <Card className="p-4"><div className="text-xs text-text-muted">{rotulo}</div><div className="text-2xl font-semibold num mt-1 text-text-main">{valor}</div>{sub && <div className="text-xs text-text-subtle mt-1">{sub}</div>}</Card>
}

// Infraestrutura (spec 57 §6.4): contêineres da VPS principal (proxy do Docker só leitura) com a saúde do Uptime Kuma.
export default function Infra() {
  const r = useLoad(() => api.painelInfra(), [])
  const [q, setQ] = useState('')
  const [grupo, setGrupo] = useState('todos')
  const linhas = useMemo(() => (r.data?.conteineres || []).filter((c) =>
    (grupo === 'todos' || c.grupo === grupo) && (!q || (c.nome + ' ' + (c.produto || '')).toLowerCase().includes(q.toLowerCase()))), [r.data, q, grupo])
  if (r.loading) return <Skeleton className="h-40" />
  if (r.error || r.data?.erro) return <ErrorState message={r.error || r.data.erro} onRetry={r.reload} />
  const d = r.data
  return (
    <div>
      <PageHeader title="Infraestrutura" subtitle="VPS principal (Contabo): contêineres e monitores"
        actions={<a href={d.kuma.url} target="_blank" rel="noopener"><Button variant="secondary" icon={Activity}>Abrir no Uptime Kuma</Button></a>} />
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4" data-aba="infra">
        <Kpi rotulo="Contêineres" valor={d.total} sub={`${d.rodando} rodando`} />
        <Kpi rotulo="Com problema ou parados" valor={d.com_problema} />
        <Kpi rotulo="Monitores ok" valor={d.kuma.ok} sub={d.kuma.fora.length ? 'fora: ' + d.kuma.fora.join(', ') : 'nenhum fora'} />
        <Kpi rotulo="Máquinas de cliente" valor={<a href="#/vms" className="text-brand-text underline">ver VMs</a>} sub="Contabo, na aba Máquinas virtuais" />
      </div>
      <Card className="mt-4">
        <CardHeader title="Contêineres" subtitle="Leitura do Docker (somente leitura); a saúde do produto vem do Uptime Kuma" icon={Server} />
        <div className="flex flex-wrap gap-2 px-5 pb-3">
          <div className="flex-1 min-w-[220px]"><Input icon={Search} placeholder="Buscar contêiner ou produto" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <Select value={grupo} onChange={(e) => setGrupo(e.target.value)} className="w-44">
            {['todos', 'Produto', 'Plataforma', 'LUAR', 'Infraestrutura'].map((g) => <option key={g} value={g}>{g === 'todos' ? 'Todos' : g}</option>)}
          </Select>
        </div>
        <div className="divide-y divide-border-subtle" data-lista="conteineres">
          {linhas.map((c) => (
            <div key={c.nome} className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm">
              <span className="w-52 truncate font-medium text-text-main">{c.nome}</span>
              <span className="w-28 text-text-muted">{c.produto || c.grupo}</span>
              <span className="flex-1 min-w-[140px] text-text-subtle text-xs">{c.status}</span>
              <Badge variant={c.estado === 'running' ? corSaude[c.saude] : 'error'}>{c.estado === 'running' ? c.saude : c.estado}</Badge>
              {c.monitor && <Badge variant={corMonitor[c.monitor] || 'default'}>monitor: {c.monitor}</Badge>}
              {c.link && <a href={c.link} target="_blank" rel="noopener" className="text-text-muted hover:text-text-main"><ExternalLink className="size-4" /></a>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
