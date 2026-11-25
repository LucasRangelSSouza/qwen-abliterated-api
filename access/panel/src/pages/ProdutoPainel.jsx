import { useMemo, useState } from 'react'
import { ExternalLink, Github, Activity, BookOpen, Rocket, Shield, Search, Users, AlertTriangle, Link2 } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Input, Select, Skeleton, EmptyState, ErrorState, useLoad } from '../components/ui.jsx'

const data = (t) => (t ? new Date(t * 1000).toLocaleDateString('pt-BR') : '—')

function Kpi({ rotulo, valor, sub }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-text-muted">{rotulo}</div>
      <div className="text-2xl font-semibold num mt-1 text-text-main">{valor ?? '—'}</div>
      {sub && <div className="text-xs text-text-subtle mt-1">{sub}</div>}
    </Card>
  )
}

const LINKS = [
  ['produto', 'Abrir o produto', ExternalLink],
  ['admin', 'Abrir o /admin', Shield],
  ['github', 'Repositório no GitHub', Github],
  ['readme', 'README', BookOpen],
  ['release', 'Onde sai a release', Rocket],
  ['deploy', 'Onde faz o deploy', Rocket],
  ['monitor', 'Monitor (Uptime Kuma)', Activity],
]

// Aba de produto do painel único (spec 57 §6.2): dados do banco `produtos` e links do registro único.
export default function ProdutoPainel({ id }) {
  const p = useLoad(() => api.painelProduto(id), [id])
  const [q, setQ] = useState('')
  const [situacao, setSituacao] = useState('todos')
  const lista = useLoad(() => (p.data?.schema ? api.painelUsuarios(id, { q, situacao }) : Promise.resolve(null)), [id, q, situacao, p.data?.schema])
  const r = p.data?.resumo
  const linhas = useMemo(() => lista.data?.usuarios || [], [lista.data])
  const emBreve = p.data && !p.data.schema

  return (
    <div>
      <PageHeader title={p.data?.nome || id} subtitle={(p.data?.hosts || []).join(' · ') || 'Produto'}
        actions={p.data?.links?.produto && <a href={p.data.links.produto} target="_blank" rel="noopener"><Button variant="secondary" icon={ExternalLink}>Abrir o produto</Button></a>} />
      {p.loading ? <div className="grid gap-3 sm:grid-cols-4">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24" />)}</div>
        : p.error ? <ErrorState message={p.error} onRetry={p.reload} />
        : (
        <>
          {p.data.aviso && <Card className="p-3 mb-3 text-sm text-text-muted">{p.data.aviso}</Card>}
          {p.data.erro && <Card className="p-4 mb-3 flex items-center gap-3 text-sm"><AlertTriangle className="size-5 text-amber-500" />{p.data.erro}</Card>}
          {r && (
            <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
              <Kpi rotulo="Usuários" valor={r.usuarios} sub="sem contar contas de QA" />
              <Kpi rotulo="Assinantes ativos" valor={r.assinantes} sub={Object.entries(r.por_plano || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'nenhum ainda'} />
              <Kpi rotulo="Novos em 7 dias" valor={r.novos_7d} />
              <Kpi rotulo="Planos vencidos" valor={r.vencidos} />
            </div>
          )}
          <Card className="mt-4">
            <CardHeader title="Acesso rápido" subtitle="Links do registro único de produtos" icon={Link2} />
            <div className="flex flex-wrap gap-2 px-5 pb-5">
              {LINKS.filter(([k]) => p.data.links?.[k]).map(([k, rotulo, Ic]) => (
                <a key={k} href={p.data.links[k]} target="_blank" rel="noopener" data-link={k}>
                  <Button size="sm" variant="secondary" icon={Ic}>{rotulo}</Button>
                </a>
              ))}
            </div>
          </Card>
          {emBreve ? <Card className="mt-4"><EmptyState title="Em breve" text="Este produto ainda não tem base de usuários no banco." /></Card> : (
          <Card className="mt-4">
            <CardHeader title="Usuários" subtitle="Do banco do produto (contas de QA marcadas)" icon={Users} />
            <div className="flex flex-wrap gap-2 px-5 pb-3">
              <div className="flex-1 min-w-[220px]"><Input icon={Search} placeholder="Buscar por e-mail" value={q} onChange={(e) => setQ(e.target.value)} /></div>
              <Select value={situacao} onChange={(e) => setSituacao(e.target.value)} className="w-44">
                <option value="todos">Todos</option><option value="ativos">Com plano ativo</option><option value="inativos">Sem plano ativo</option>
              </Select>
            </div>
            {lista.loading ? <div className="p-5"><Skeleton className="h-24" /></div>
              : lista.error ? <ErrorState message={lista.error} onRetry={lista.reload} />
              : !linhas.length ? <EmptyState title="Nenhum usuário" text={q ? 'Nada com esse e-mail.' : 'Ainda não há cadastros.'} />
              : (
              <div className="divide-y divide-border-subtle" data-lista="usuarios">
                {linhas.map((u) => (
                  <div key={u.id} className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-sm">
                    <span className="flex-1 min-w-[200px] truncate text-text-main">{u.email}</span>
                    {u.qa && <Badge>QA</Badge>}
                    <span className="w-24 text-text-muted">{u.plano || 'sem plano'}</span>
                    <Badge variant={u.assinante ? 'success' : 'default'}>{u.assinante ? `ativo até ${data(u.ativo_ate)}` : 'sem plano ativo'}</Badge>
                    <span className="w-28 text-text-subtle text-xs">desde {data(u.criado)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>)}
        </>
      )}
    </div>
  )
}
