import { Copy, Link2, ExternalLink } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Button, Skeleton, ErrorState, Notice, useToast, useLoad } from '../components/ui.jsx'

const brl = (n) => (n == null ? '—' : Number(n).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const CEREBRO = 'https://brain.rangeltech.net'

// Todos os links de venda do ecossistema num lugar só (cada produto e plano), para divulgar e conferir.
export default function Links() {
  const toast = useToast()
  const prods = useLoad(() => api.produtos(), [])
  const copiar = async (t) => { try { await navigator.clipboard.writeText(t); toast?.('Link copiado') } catch {} }
  return (
    <div>
      <PageHeader title="Links e cupons" subtitle="Links de checkout da Cakto de todos os produtos e planos" />
      <Notice variant="info">Cupom de desconto: crie na Cakto e escolha os produtos em que ele vale (todos ou um). Afiliados e cupons do Cérebro Pro continuam na aba Afiliados.</Notice>
      <Card className="mt-4">
        <CardHeader title="Cérebro Pro" subtitle="Planos e página de venda" icon={Link2}
          action={<a href={CEREBRO} target="_blank" rel="noopener"><Button size="sm" variant="secondary" icon={ExternalLink}>Site</Button></a>} />
        <div className="px-5 pb-4 text-sm text-text-muted">Links dos planos na aba Assinantes / Configurações (checkout embutido no site {CEREBRO.replace('https://', '')}).</div>
      </Card>
      {prods.loading ? <Skeleton className="h-40 mt-4" /> : prods.error ? <ErrorState message={prods.error} onRetry={prods.reload} /> : (prods.data?.produtos || []).map((p) => (
        <Card key={p.id} className="mt-4">
          <CardHeader title={p.nome} subtitle={p.erro ? `Não respondeu: ${p.erro}` : `${p.assinantes ?? 0} assinantes ativos`} icon={Link2}
            action={<a href={p.url} target="_blank" rel="noopener"><Button size="sm" variant="secondary" icon={ExternalLink}>Site</Button></a>} />
          <div className="divide-y divide-border-subtle">
            {(p.planos || []).map((pl) => (
              <div key={pl.codigo} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <b className="w-20 text-text-main">{pl.nome}</b><span className="num w-24">{brl(pl.preco)}</span>
                <span className="flex-1 min-w-0 truncate text-text-muted">{pl.checkout || 'sem link'}</span>
                {pl.checkout && <Button size="sm" variant="secondary" icon={Copy} onClick={() => copiar(pl.checkout)}>Copiar</Button>}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  )
}
