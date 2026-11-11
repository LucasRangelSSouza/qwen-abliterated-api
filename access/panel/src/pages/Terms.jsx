import { useState } from 'react'
import { ScrollText, ShieldCheck, Search } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Input, Skeleton, EmptyState, ErrorState, Notice, useToast, useLoad } from '../components/ui.jsx'

const quando = (v) => (v ? new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', dateStyle: 'short', timeStyle: 'medium' }).format(new Date(v)) : '—')

export default function Terms() {
  const toast = useToast()
  const [email, setEmail] = useState('')
  const [busca, setBusca] = useState('')
  const list = useLoad(() => api.termsAcceptances(busca), [busca])
  const [integ, setInteg] = useState(null)
  const [busy, setBusy] = useState(false)
  const conferir = async () => { setBusy(true); try { setInteg(await api.termsIntegrity()) } catch (e) { toast.error(e.message) } finally { setBusy(false) } }
  const d = list.data
  return (
    <>
      <PageHeader title="Termos de uso" subtitle="Aceites registrados com data, hora, IP e código de verificação" />
      <Card className="overflow-hidden">
        <CardHeader icon={ScrollText} title="Aceites" subtitle="Cada aceite é guardado sem poder ser alterado, encadeado ao anterior e assinado"
          action={<Button variant="outline" icon={ShieldCheck} onClick={conferir} loading={busy}>Conferir integridade</Button>} />
        {integ && <div className="px-5 pb-3"><Notice variant={integ.ok ? 'info' : 'warning'}>{integ.ok ? `Tudo certo: ${integ.registros} registros, corrente e assinaturas conferem.` : `Problema em ${integ.problemas.length} registro(s): ${integ.problemas.map((p) => `#${p.id} ${p.erro}`).join('; ')}`}</Notice></div>}
        <form className="px-5 pb-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); setBusca(email.trim()) }}>
          <Input icon={Search} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Buscar por e-mail (vazio = os últimos)" aria-label="E-mail" />
          <Button type="submit" variant="secondary">Buscar</Button>
        </form>
        {list.error ? <ErrorState message={list.error} onRetry={list.reload} /> : list.loading && !d ? <div className="p-5 pt-0"><Skeleton className="h-32" /></div>
          : d.aceites.length === 0 ? <EmptyState icon={ScrollText} title="Nenhum aceite" text="Os aceites aparecem aqui quando alguém cria a senha, faz o teste grátis ou compra pelo checkout do site." />
          : (
            <div className="overflow-x-auto border-t border-border-subtle">
              <table className="w-full text-left text-sm">
                <thead><tr className="text-xs text-text-muted border-b border-border-subtle bg-bg-alt/50">
                  <th className="font-medium px-5 py-3">E-mail</th><th className="font-medium px-3 py-3">Versão</th><th className="font-medium px-3 py-3">Quando</th>
                  <th className="font-medium px-3 py-3">Canal</th><th className="font-medium px-3 py-3">IP</th><th className="font-medium px-3 py-3">Código</th>
                </tr></thead>
                <tbody>{d.aceites.map((a) => (
                  <tr key={a.id} className="border-b border-border-subtle last:border-b-0 hover:bg-surface-2/50">
                    <td className="px-5 py-3 font-medium text-text-main">{a.email}</td><td className="px-3 py-3 font-mono">{a.versao}</td><td className="px-3 py-3">{quando(a.aceito_iso)}</td>
                    <td className="px-3 py-3"><Badge>{a.canal}</Badge></td><td className="px-3 py-3 font-mono text-xs">{a.ip || '—'}</td><td className="px-3 py-3 font-mono text-xs">{a.codigo}</td>
                  </tr>))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </>
  )
}
