// Créditos avulsos (Spec 24/25 §3.6): saldo e pacotes de uma pessoa (conceder e revogar) e os ajustes de preço em dólar.
// As rotas já existiam no servidor; esta tela é o que faltava para operar sem SQL.
import { useEffect, useState } from 'react'
import { Coins, Plus, Trash2, Save } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtInt, fmtFull } from '../lib/format.js'
import { Button, Badge, Field, Input, Select, Skeleton, ErrorState, Card, CardHeader, Notice, useToast, useLoad } from './ui.jsx'

const TIPOS = { tokens: 'Uso (tokens)', image: 'Imagens', video: 'Vídeos', audio: 'Áudios' }

export function CreditosDaPessoa({ pid }) {
  const toast = useToast()
  const { data, loading, error, reload } = useLoad(() => api.personCredits(pid), [pid])
  const [kind, setKind] = useState('tokens')
  const [units, setUnits] = useState('')
  const [busy, setBusy] = useState(false)
  const conceder = async () => {
    setBusy(true)
    try {
      const body = { kind }
      if (units) body.units = +units
      const r = await api.grantCredit(pid, body)
      toast.success(`Pacote concedido: ${fmtInt(r.units)} ${TIPOS[r.kind].toLowerCase()}.`)
      setUnits('')
      reload()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const revogar = async (cid) => {
    try { await api.revokeCredit(cid); toast.success('Pacote revogado.'); reload() } catch (e) { toast.error(e.message) }
  }
  return (
    <div className="rounded-[12px] border border-border-subtle bg-bg p-4 flex flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold text-text-main flex items-center gap-2"><Coins className="size-4" />Créditos avulsos</h3>
        <p className="text-xs text-text-muted">Pacotes comprados ou concedidos. Só são usados depois que o plano acaba e não contam nas janelas. Valem 30 dias (ajuste em Assinantes).</p>
      </div>
      {error ? <ErrorState message={error} onRetry={reload} /> : loading || !data ? <Skeleton className="h-20" /> : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {Object.entries(TIPOS).map(([k, label]) => (
              <div key={k} className="rounded-[10px] border border-border-subtle px-3 py-2">
                <div className="text-xs text-text-muted">{label}</div>
                <div className="num text-sm font-semibold text-text-main">{fmtInt(data.balances?.[k] || 0)}</div>
              </div>
            ))}
          </div>
          {(data.rows || []).length > 0 && (
            <ul className="divide-y divide-border-subtle">
              {data.rows.map((c) => (
                <li key={c.id} className="py-2 flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0">
                    <span className="text-text-main">{TIPOS[c.kind] || c.kind}</span>{' '}
                    <span className="num text-text-muted">{fmtInt(c.remaining)} de {fmtInt(c.units)}</span>{' '}
                    <span className="text-xs text-text-muted">· vence {fmtFull(c.expires_at)} · {c.source === 'admin' ? 'concedido' : 'compra'}</span>
                  </span>
                  <span className="flex items-center gap-2 shrink-0">
                    {c.revoked ? <Badge>revogado</Badge> : c.expired ? <Badge>vencido</Badge> : (
                      <Button variant="ghost" icon={Trash2} onClick={() => revogar(c.id)} aria-label="Revogar pacote">Revogar</Button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Tipo" htmlFor={`cr-k-${pid}`}>
              <Select id={`cr-k-${pid}`} value={kind} onChange={(e) => setKind(e.target.value)}>
                {Object.entries(TIPOS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </Select>
            </Field>
            <Field label="Quantidade" htmlFor={`cr-u-${pid}`} hint="Vazio = o tamanho do pacote">
              <Input id={`cr-u-${pid}`} inputMode="numeric" value={units} onChange={(e) => setUnits(e.target.value.replace(/\D/g, ''))} placeholder="pacote" />
            </Field>
            <Button icon={Plus} onClick={conceder} loading={busy}>Conceder pacote</Button>
          </div>
        </>
      )}
    </div>
  )
}

export function CreditosAjustes() {
  const toast = useToast()
  const st = useLoad(() => api.billingSettings(), [])
  const [f, setF] = useState(null)
  useEffect(() => {
    if (!st.data) return
    const d = st.data
    setF({
      usd_brl_rate: String(d.usd_brl_rate ?? ''), credit_price_usd: d.credit_price_usd == null ? '' : String(d.credit_price_usd), credit_expiry_days: String(d.credit_expiry_days ?? 30),
      units: JSON.stringify(d.credit_pack_units || {}, null, 0), offers: JSON.stringify(d.credit_offers || {}, null, 0), products: JSON.stringify(d.credit_products || {}, null, 0),
    })
  }, [st.data])
  const save = async () => {
    let units, offers, products
    try { units = JSON.parse(f.units || '{}'); offers = JSON.parse(f.offers || '{}'); products = JSON.parse(f.products || '{}') } catch { return toast.error('Os campos de pacote, ofertas e produtos precisam ser JSON válido.') }
    const body = { usd_brl_rate: +f.usd_brl_rate, credit_expiry_days: +f.credit_expiry_days, credit_pack_units: units, credit_offers: offers, credit_products: products, credit_price_usd: f.credit_price_usd === '' ? null : +f.credit_price_usd }
    try { await api.putBillingSettings(body); toast.success('Créditos salvos.'); st.refresh() } catch (e) { toast.error(e.message) }
  }
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }))
  return (
    <Card>
      <CardHeader icon={Coins} title="Créditos avulsos (dólar)" subtitle="Preço do pacote, validade, tamanho e os produtos da Cakto" />
      <div className="px-5 pb-5 flex flex-col gap-4">
        {st.error ? <ErrorState message={st.error} onRetry={st.refresh} /> : !f ? <Skeleton className="h-40" /> : (
          <>
            <div className="grid sm:grid-cols-3 gap-3">
              <Field label="Cotação (R$ por US$)" htmlFor="cr-rate"><Input id="cr-rate" inputMode="decimal" value={f.usd_brl_rate} onChange={set('usd_brl_rate')} /></Field>
              <Field label="Preço do pacote (US$)" htmlFor="cr-price" hint={`Vazio = pela cotação (hoje US$ ${st.data.credit_price_usd ?? '—'})`}><Input id="cr-price" inputMode="decimal" value={f.credit_price_usd} onChange={set('credit_price_usd')} /></Field>
              <Field label="Validade (dias)" htmlFor="cr-exp"><Input id="cr-exp" inputMode="numeric" value={f.credit_expiry_days} onChange={set('credit_expiry_days')} /></Field>
            </div>
            <Field label="Tamanho dos pacotes" htmlFor="cr-units" hint='Ex.: {"tokens_mult": 2, "image": 500, "video": 30, "audio": 150} (uso = vezes a sessão do plano)'><Input id="cr-units" value={f.units} onChange={set('units')} /></Field>
            <Field label="Ofertas da Cakto (checkout)" htmlFor="cr-off" hint='{"tokens": "id da oferta", "image": "...", ...}'><Input id="cr-off" value={f.offers} onChange={set('offers')} /></Field>
            <Field label="Produtos da Cakto (webhook)" htmlFor="cr-prod" hint='{"tokens": "id do produto", ...}'><Input id="cr-prod" value={f.products} onChange={set('products')} /></Field>
            <div className="flex justify-end"><Button icon={Save} onClick={save}>Salvar créditos</Button></div>
            <Notice variant="info">Os produtos são criados com o script <code>access/scripts/criar_produtos_credito.py --criar</code>; depois cole aqui os ids das ofertas e dos produtos.</Notice>
          </>
        )}
      </div>
    </Card>
  )
}
