import { useMemo, useState } from 'react'
import { ExternalLink, Copy, Search, Users, TrendingUp, Inbox, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtAgo, cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Input, Select, Skeleton, EmptyState, ErrorState, useToast, useLoad } from '../components/ui.jsx'

const brl = (n) => (n == null ? '—' : Number(n).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const data = (t) => (t ? new Date(t * 1000).toLocaleDateString('pt-BR') : '—')

function Kpi({ rotulo, valor, sub }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-text-muted">{rotulo}</div>
      <div className="text-2xl font-semibold num mt-1 text-text-main">{valor}</div>
      {sub && <div className="text-xs text-text-subtle mt-1">{sub}</div>}
    </Card>
  )
}

// Uma página por produto do ecossistema (Cortaí, Rendaí...): resumo, planos com link de checkout e assinantes.
export default function Produto({ id }) {
  const toast = useToast()
  const resumo = useLoad(async () => (await api.produtos()).produtos.find((p) => p.id === id), [id])
  const lista = useLoad(() => api.produtoAssinantes(id), [id])
  const [q, setQ] = useState('')
  const [filtro, setFiltro] = useState('ativos')
  const r = resumo.data
  const linhas = useMemo(() => (lista.data?.assinantes || []).filter((a) =>
    (filtro === 'todos' || (filtro === 'ativos' ? a.ativo : !a.ativo)) && (!q || a.email.toLowerCase().includes(q.toLowerCase()))), [lista.data, q, filtro])
  const copiar = async (t) => { try { await navigator.clipboard.writeText(t); toast?.('Link copiado') } catch {} }

  return (
    <div>
      <PageHeader title={r?.nome || id} subtitle={r?.url ? r.url.replace('https://', '') : 'Produto do ecossistema'}
        actions={r?.url && <a href={r.url} target="_blank" rel="noopener"><Button variant="secondary" icon={ExternalLink}>Abrir o site</Button></a>} />
      {resumo.loading ? <div className="grid gap-3 sm:grid-cols-4">{[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24" />)}</div>
        : resumo.error ? <ErrorState message={resumo.error} onRetry={resumo.reload} />
        : r?.erro ? <Card className="p-4 flex items-center gap-3 text-sm"><AlertTriangle className="size-5 text-amber-500" />O produto não respondeu: {r.erro}</Card>
        : r && (
        <>
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <Kpi rotulo="Assinantes ativos" valor={r.assinantes} sub={Object.entries(r.por_plano || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'nenhum ainda'} />
            <Kpi rotulo="Receita mensal (planos ativos)" valor={brl(r.receita_mensal)} />
            <Kpi rotulo="Cadastros" valor={r.cadastros} />
            {'pedidos_30d' in r ? <Kpi rotulo="Pedidos em 30 dias" valor={r.pedidos_30d} sub={`${r.erros_30d} com erro · ${r.na_fila} na fila agora`} />
              : <Kpi rotulo="Produtos na vitrine" valor={r.produtos_vitrine} sub={`${r.acoes_7d} ações e ${r.vendas_registradas_7d} vendas registradas em 7 dias`} />}
          </div>
          <Card className="mt-4">
            <CardHeader title="Planos e links de checkout" subtitle={`Preço de lançamento até ${(r.planos?.[0]?.lancamento_ate || '').split('-').reverse().join('/')}`} icon={TrendingUp} />
            <div className="divide-y divide-border-subtle">
              {(r.planos || []).map((p) => (
                <div key={p.codigo} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                  <b className="w-20 text-text-main">{p.nome}</b>
                  <span className="num w-24">{brl(p.preco)}</span>
                  <span className="text-text-subtle w-40">depois: {brl(p.depois)}</span>
                  <span className="flex-1 min-w-0 truncate text-text-muted">{p.checkout || 'sem link'}</span>
                  {p.checkout && <Button size="sm" variant="secondary" icon={Copy} onClick={() => copiar(p.checkout)}>Copiar</Button>}
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
      <Card className="mt-4">
        <CardHeader title="Assinantes" subtitle={lista.data ? `${linhas.length} de ${(lista.data.assinantes || []).length}` : ''} icon={Users}
          action={<div className="flex gap-2"><Input icon={Search} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar e-mail" aria-label="Buscar e-mail" className="w-48" />
            <Select value={filtro} onChange={(e) => setFiltro(e.target.value)} aria-label="Filtro"><option value="ativos">Ativos</option><option value="inativos">Sem plano</option><option value="todos">Todos</option></Select></div>} />
        {lista.loading ? <div className="p-5"><Skeleton className="h-32" /></div>
          : lista.error || lista.data?.erro ? <ErrorState message={lista.error || lista.data.erro} onRetry={lista.reload} />
          : !linhas.length ? <EmptyState icon={Inbox} title="Ninguém por aqui" text="Os assinantes aparecem quando a primeira compra cair pela Cakto." />
          : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-text-muted border-b border-border-subtle">
                <th className="px-5 py-2 font-medium">E-mail</th><th className="px-3 py-2 font-medium">Plano</th><th className="px-3 py-2 font-medium">Válido até</th>
                <th className="px-3 py-2 font-medium">Uso</th><th className="px-3 py-2 font-medium">Último acesso</th><th className="px-3 py-2 font-medium">Cadastro</th></tr></thead>
              <tbody>{linhas.map((a) => (
                <tr key={a.id} className="border-b border-border-subtle last:border-b-0">
                  <td className="px-5 py-2.5 text-text-main">{a.email}</td>
                  <td className="px-3 py-2.5">{a.ativo ? <Badge variant="success" dot>{a.plano}</Badge> : <Badge>sem plano</Badge>}</td>
                  <td className="px-3 py-2.5 num">{a.ativo ? data(a.ativo_ate) : '—'}</td>
                  <td className="px-3 py-2.5 num">{a.pedidos_30d != null ? `${a.pedidos_30d} pedidos (30d)` : `${a.xp ?? 0} XP`}</td>
                  <td className={cn('px-3 py-2.5', !a.ultimo && 'text-text-subtle')}>{a.ultimo ? fmtAgo(a.ultimo * 1000) : 'nunca'}</td>
                  <td className="px-3 py-2.5 num">{data(a.criado)}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
