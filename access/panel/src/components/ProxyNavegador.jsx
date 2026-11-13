// Proxy do navegador (spec 31): IP residencial do Brasil para o navegador da VM do cliente, só nos sites que barram datacenter (Instagram, Facebook, TikTok...).
// A URL completa nunca volta do servidor: aqui aparece mascarada (esquema://***@host:porta).
import { useState } from 'react'
import { Globe, Save, Trash2 } from 'lucide-react'
import { api } from '../lib/api.js'
import { Button, Field, Input, Card, CardHeader, Notice, ErrorState, Skeleton, useToast, useLoad } from './ui.jsx'

export function ProxyNavegador({ chave = 'padrao' }) {
  const toast = useToast()
  const { data, loading, error, reload } = useLoad(() => api.proxyNav(), [chave])
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const salvar = async (valor) => {
    setBusy(true)
    try { await api.putProxyNav(chave, valor); toast.success(valor ? 'Proxy salvo.' : 'Proxy removido.'); setUrl(''); reload() } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const atual = data?.proxies?.[chave]
  const padrao = data?.proxies?.padrao
  return (
    <Card>
      <CardHeader icon={Globe} title={chave === 'padrao' ? 'Proxy do navegador (padrão)' : 'Proxy do navegador'} subtitle="IP do Brasil só para Instagram, Facebook, TikTok e afins, no navegador das VMs" />
      <div className="px-5 pb-5 flex flex-col gap-3">
        {error ? <ErrorState message={error} onRetry={reload} /> : loading ? <Skeleton className="h-16" /> : (
          <>
            {!data.secrets_key && <Notice variant="warning">SECRETS_KEY não configurada no servidor: o proxy não pode ser guardado.</Notice>}
            <div className="text-sm">Agora: <b>{atual || (chave !== 'padrao' && padrao ? `segue o padrão (${padrao})` : 'saída direta (sem proxy)')}</b></div>
            <Field label="Novo proxy" htmlFor={'px-' + chave} hint="http://usuario:senha@host:porta (ou socks5://...). Prefira residencial/ISP do Brasil com IP fixo.">
              <Input id={'px-' + chave} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://usuario:senha@br.fornecedor.com:8000" autoComplete="off" />
            </Field>
            <div className="flex gap-2 justify-end">
              {atual && <Button variant="ghost" icon={Trash2} disabled={busy} onClick={() => salvar(null)}>Remover</Button>}
              <Button icon={Save} disabled={busy || !url} onClick={() => salvar(url)}>Salvar proxy</Button>
            </div>
            <div className="text-xs text-muted-foreground">Sites que passam pelo proxy: {(data.dominios || []).join(', ')}</div>
          </>
        )}
      </div>
    </Card>
  )
}
