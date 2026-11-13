import { useEffect, useState } from 'react'
import { Wallet, KeyRound, RefreshCw, Save, ExternalLink, Sparkles, Clapperboard, Mic, Image as ImageIcon, Cpu, MessageSquare, CircleDollarSign } from 'lucide-react'
import { api } from '../lib/api.js'
import { cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Notice, Skeleton, ErrorState, useToast, useLoad } from '../components/ui.jsx'

const usd = (v, exato = false) => {
  if (v == null) return '—'
  const n = Number(v)
  const casas = n !== 0 && Math.abs(n) < 0.01 ? Math.min(6, 2 - Math.floor(Math.log10(Math.abs(n)))) : 2
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: exato ? 2 : casas, maximumFractionDigits: exato ? Math.max(casas, 4) : casas })
}
const QUAL = (q) => ({ padrao: '', turbo: 'turbo', hd: 'HD', '720p-audio': '720p com som' }[q] ?? q)
const brl = (v) => (v == null ? '—' : Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }))
const ICON = { runware: Sparkles, vast: Cpu, chatgpt: MessageSquare }
const KIND = { video: Clapperboard, audio: Mic, image: ImageIcon }
const UNID = { segundo: 's', caractere: 'caractere', imagem: 'imagem' }
const EXEMPLO = { segundo: '10 s', caractere: '1.000 caracteres', imagem: '1 imagem' }

function Saldo({ f }) {
  const s = f.saldo || {}
  if (f.id === 'chatgpt') return <div className="text-sm text-text-muted">Assinatura (sem saldo por uso)</div>
  if (!s.ok) return <Badge variant="warning" dot>{s.erro || 'sem leitura'}</Badge>
  const baixo = s.saldo_usd < 5 || (s.dias != null && Number(s.dias) < 3) // Vast: poucos dias de saldo tambem e alerta
  return (
    <div>
      <div className={cn('num text-2xl font-semibold tracking-tight', baixo ? 'text-red-600 dark:text-red-400' : 'text-text-main')}>{usd(s.saldo_usd)}</div>
      <div className="text-xs text-text-muted">
        {s.simulado ? 'simulado (modo de teste)' : f.id === 'runware'
          ? `hoje ${usd(s.gasto_hoje_usd)} · 30 dias ${usd(s.gasto_30d_usd)} · ${s.pedidos_30d ?? 0} pedidos`
          : s.dias != null ? `gasto ${usd(s.gasto_dia_usd)}/dia · dura ${Number(s.dias).toFixed(1)} dias${Number(s.dias) < 3 ? ' · recarregue' : ''}` : ''}
      </div>
    </div>
  )
}

function ProviderCard({ f, secretsOk, onSaved }) {
  const toast = useToast()
  const [k, setK] = useState('')
  const [busy, setBusy] = useState(false)
  const Icon = ICON[f.id] || Wallet
  const editavel = f.id === 'runware'
  const salvar = async (valor) => {
    setBusy(true)
    try { onSaved(await api.saveProviderKey({ fornecedor: f.id, chave: valor })); setK(''); toast.success(valor ? 'Chave testada e guardada.' : 'Chave removida. O Estúdio volta a "em breve".') }
    catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  return (
    <Card className="flex flex-col">
      <div className="p-5 flex items-start gap-3">
        <div className="grid place-items-center size-10 shrink-0 rounded-[12px] bg-brand-500/10 text-brand-text"><Icon className="size-5" /></div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-text-main">{f.nome}</div>
          <div className="text-xs text-text-muted mt-0.5">{f.uso}</div>
          <div className="mt-2">
            {f.chave
              ? <Badge variant="success" dot>{f.origem === 'painel' ? `chave ${f.chave}` : f.origem === 'ambiente' ? `chave ${f.chave} (servidor)` : f.chave}</Badge>
              : <Badge variant="warning" dot>sem chave</Badge>}
          </div>
        </div>
        {f.site && <a href={f.site} target="_blank" rel="noreferrer" className="text-text-muted hover:text-text-main" aria-label={`Abrir ${f.nome}`}><ExternalLink className="size-4" /></a>}
      </div>
      <div className="px-5 pb-5 mt-auto flex flex-col gap-3">
        <Saldo f={f} />
        {editavel && (
          <div className="flex gap-2 items-end">
            <Field label={f.chave ? 'Trocar chave' : 'Colar a chave'} htmlFor={`k-${f.id}`} className="flex-1">
              <Input id={`k-${f.id}`} type="password" autoComplete="off" value={k} onChange={(e) => setK(e.target.value)} placeholder="Testada antes de guardar" disabled={!secretsOk} />
            </Field>
            <Button icon={KeyRound} onClick={() => salvar(k.trim())} loading={busy} disabled={!k.trim() || !secretsOk}>Salvar</Button>
          </div>
        )}
        {editavel && f.origem === 'painel' && <button className="self-start text-xs text-text-muted hover:text-red-600" onClick={() => salvar('')} disabled={busy}>Remover a chave do painel</button>}
      </div>
    </Card>
  )
}

function PricingCard({ data, onSaved }) {
  const toast = useToast()
  const [f, setF] = useState(null)
  const [pe, setPe] = useState({})
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setF({ markup: String(data.precos.markup), markup_minimo: String(data.precos.markup_minimo), cap_brl: String(data.precos.cap_brl) })
    setPe(Object.fromEntries(Object.entries(data.precos.por_efeito || {}).map(([k, v]) => [k, String(v)])))
  }, [data])
  if (!f) return null
  const salvar = async () => {
    setBusy(true)
    try { onSaved(await api.saveProviderPrices({ markup: +f.markup, markup_minimo: +f.markup_minimo, cap_brl: +f.cap_brl, por_efeito: pe })); toast.success('Preços salvos. Valem no próximo pedido.') }
    catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  return (
    <Card>
      <CardHeader icon={CircleDollarSign} title="Preço do Estúdio" subtitle="Preço = custo do fornecedor × markup, sem passar do teto por pedido. O piso garante margem quando o pedido é grande." />
      <div className="px-5 pb-5 flex flex-col gap-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Markup" htmlFor="p-mk" hint="Ex.: 5 = cinco vezes o custo."><Input id="p-mk" type="number" min="1" max="50" step="0.1" value={f.markup} onChange={(e) => setF((s) => ({ ...s, markup: e.target.value }))} /></Field>
          <Field label="Teto por pedido (R$)" htmlFor="p-cap" hint="O preço para aqui, se a margem mínima permitir."><Input id="p-cap" type="number" min="1" step="1" value={f.cap_brl} onChange={(e) => setF((s) => ({ ...s, cap_brl: e.target.value }))} /></Field>
          <Field label="Markup mínimo" htmlFor="p-min" hint="Piso: nunca abaixo disso, mesmo acima do teto."><Input id="p-min" type="number" min="1" max="10" step="0.1" value={f.markup_minimo} onChange={(e) => setF((s) => ({ ...s, markup_minimo: e.target.value }))} /></Field>
        </div>
        <ul className="md:hidden -mx-5 border-t border-border-subtle">
          {data.tabela.map((t) => {
            const K = KIND[t.kind] || Sparkles
            const ex = t.exemplo
            return (
              <li key={t.efeito} className="px-5 py-4 border-b border-border-subtle last:border-0">
                <div className="flex items-start gap-3">
                  <K className="size-4 mt-0.5 text-text-muted shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-text-main">{t.rotulo}</div>
                    <div className="text-xs text-text-subtle font-mono truncate">{t.modelo}</div>
                    <div className="mt-2 text-xs text-text-muted num">{usd(t.taxas_usd[t.qualidade], true)}/{UNID[t.unidade]}{QUAL(t.qualidade) && ' · ' + QUAL(t.qualidade)} · {EXEMPLO[t.unidade]} custa {usd(ex.custo_usd)}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="num text-sm font-semibold text-text-main">{brl(ex.preco_brl)}</div>
                    <div className="num text-xs text-text-muted">{ex.markup_aplicado}×{ex.acima_do_teto && ' · acima do teto'}</div>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <label htmlFor={`m-${t.efeito}`} className="text-xs text-text-muted">Markup próprio</label>
                  <Input id={`m-${t.efeito}`} type="number" min="1" max="50" step="0.1" placeholder={String(data.precos.markup)} value={pe[t.efeito] ?? ''}
                    onChange={(e) => setPe((s) => ({ ...s, [t.efeito]: e.target.value }))} className="w-24" />
                </div>
              </li>
            )
          })}
        </ul>
        <div className="hidden md:block overflow-x-auto -mx-5">
          <table className="w-full text-sm min-w-[720px]">
            <thead>
              <tr className="text-left text-xs text-text-muted border-y border-border-subtle">
                <th className="font-medium px-5 py-2.5">Efeito</th>
                <th className="font-medium px-3 py-2.5">Custo por unidade</th>
                <th className="font-medium px-3 py-2.5">Exemplo</th>
                <th className="font-medium px-3 py-2.5 text-right">Custo</th>
                <th className="font-medium px-3 py-2.5 text-right">Cliente paga</th>
                <th className="font-medium px-5 py-2.5 w-28">Markup próprio</th>
              </tr>
            </thead>
            <tbody>
              {data.tabela.map((t) => {
                const K = KIND[t.kind] || Sparkles
                const ex = t.exemplo
                return (
                  <tr key={t.efeito} className="border-b border-border-subtle last:border-0">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2.5">
                        <K className="size-4 text-text-muted shrink-0" />
                        <div className="min-w-0">
                          <div className="text-text-main font-medium truncate">{t.rotulo}</div>
                          <div className="text-xs text-text-subtle font-mono truncate">{t.modelo}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3 num text-text-muted">{usd(t.taxas_usd[t.qualidade], true)}/{UNID[t.unidade]}{QUAL(t.qualidade) && <span className="text-text-subtle"> · {QUAL(t.qualidade)}</span>}</td>
                    <td className="px-3 py-3 text-text-muted">{EXEMPLO[t.unidade]}</td>
                    <td className="px-3 py-3 num text-right text-text-muted">{usd(ex.custo_usd)}</td>
                    <td className="px-3 py-3 num text-right">
                      <div className="font-medium text-text-main">{brl(ex.preco_brl)}</div>
                      <div className="text-xs text-text-muted">{usd(ex.preco_usd)} · {ex.markup_aplicado}×{ex.acima_do_teto && <span className="text-amber-600 dark:text-amber-400"> · acima do teto</span>}</div>
                    </td>
                    <td className="px-5 py-3">
                      <Input aria-label={`Markup de ${t.rotulo}`} type="number" min="1" max="50" step="0.1" placeholder={String(data.precos.markup)} value={pe[t.efeito] ?? ''}
                        onChange={(e) => setPe((s) => ({ ...s, [t.efeito]: e.target.value }))} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <p className="text-xs text-text-muted">Custos lidos da Runware em {data.tabela_de}. Cotação do dólar: a mesma dos créditos em Assinantes.</p>
          <Button icon={Save} onClick={salvar} loading={busy}>Salvar preços</Button>
        </div>
      </div>
    </Card>
  )
}

export default function Providers() {
  const { data: loaded, loading, error, reload, refresh } = useLoad(() => api.providers(), [])
  const [data, setData] = useState(null)
  useEffect(() => { if (loaded) setData(loaded) }, [loaded])
  return (
    <>
      <PageHeader title="Fornecedores e preços" subtitle="Chaves de API, saldo em cada fornecedor e quanto o cliente paga no Estúdio"
        actions={<Button variant="secondary" icon={RefreshCw} onClick={refresh}>Atualizar saldos</Button>} />
      {error && !data ? <ErrorState message={error} onRetry={reload} /> : loading && !data ? (
        <div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-44" /><Skeleton className="h-44" /><Skeleton className="h-44" /></div>
      ) : data && (
        <div className="flex flex-col gap-5">
          {!data.secrets_key_set && <Notice variant="warning">O servidor está sem SECRETS_KEY: as chaves não podem ser guardadas pelo painel. A variável de ambiente continua valendo.</Notice>}
          {data.mock && <Notice variant="info">Modo de teste ligado (RUNWARE_MOCK): o Estúdio responde sem chamar a Runware e não gasta nada.</Notice>}
          <div className="grid gap-4 md:grid-cols-3">
            {data.fornecedores.map((f) => <ProviderCard key={f.id} f={f} secretsOk={data.secrets_key_set} onSaved={setData} />)}
          </div>
          <PricingCard data={data} onSaved={setData} />
        </div>
      )}
    </>
  )
}
