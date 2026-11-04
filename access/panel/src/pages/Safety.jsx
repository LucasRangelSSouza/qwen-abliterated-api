import { useEffect, useState } from 'react'
import { ShieldCheck, ShieldOff, ScanSearch, ScanEye, Save, RefreshCw } from 'lucide-react'
import { api } from '../lib/api.js'
import { fmtFull, cn } from '../lib/format.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Badge, Button, Field, Input, Textarea, Select, Toggle, Notice, Skeleton, ErrorState, useToast, useLoad } from '../components/ui.jsx'

const LABEL = {
  csam: 'Conteúdo sexual envolvendo menores', cbrn: 'Armas químicas, biológicas e nucleares', malware: 'Criação de malware e ransomware',
  fraud: 'Fraude e golpes (phishing, clonagem, lavagem)', violence: 'Violência e ameaça direcionada, bombas caseiras', selfharm: 'Instruções de autolesão e suicídio',
  destructive_action: 'Ação destrutiva do agente (apagar tudo, formatar disco)', credential_exfil: 'Roubo de credenciais e baixar-e-executar',
}
const BACKEND = { rules: 'Regras (rápido, cobertura básica)', model: 'Modelo-guarda (cobertura ampla)', both: 'Regras + modelo-guarda' }

/** Um classificador. `which` = 'input' ou 'output'; cada um tem a sua rota, a sua configuração e o seu log (nunca misturados). */
function ClassifierCard({ which, icon, title, subtitle, data, onChanged }) {
  const toast = useToast()
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const log = useLoad(() => api.safetyLog(which, 8), [which])
  useEffect(() => { if (data) setF({ enabled: data.enabled, backend: data.backend, categories: data.categories, model_url: data.model_url || '', model_name: data.model_name || '', model_key: '', refusal: data.refusal || '' }) }, [data])
  if (!f) return <Card><CardHeader icon={icon} title={title} /><div className="p-5"><Skeleton className="h-40" /></div></Card>
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }))
  const toggleCat = (c) => set('categories', f.categories.includes(c) ? f.categories.filter((x) => x !== c) : [...f.categories, c])
  const save = async () => {
    setBusy(true)
    try {
      const body = { enabled: f.enabled, backend: f.backend, categories: f.categories, model_url: f.model_url, model_name: f.model_name, refusal: f.refusal }
      if (f.model_key) body.model_key = f.model_key
      await (which === 'input' ? api.setSafetyInput(body) : api.setSafetyOutput(body))
      toast.success('Classificador salvo.')
      onChanged()
      log.refresh()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }
  const blocked = Object.entries(data.blocked_24h || {})
  const usaModelo = f.backend !== 'rules'
  return (
    <Card className="overflow-hidden">
      <CardHeader icon={icon} title={title} subtitle={subtitle}
        action={<Badge variant={f.enabled ? 'success' : 'default'} dot>{f.enabled ? 'Ligado' : 'Desligado'}</Badge>} />
      <div className="px-5 pb-5 flex flex-col gap-5">
        <Toggle id={`sf-${which}`} checked={f.enabled} onChange={(v) => set('enabled', v)} label="Ligado" description="Vale só para quem está no nível 2. O nível 1 nunca passa por ele." />
        <Field label="Como classificar" htmlFor={`bk-${which}`}>
          <Select id={`bk-${which}`} value={f.backend} onChange={(e) => set('backend', e.target.value)}>
            {Object.entries(BACKEND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        {usaModelo && (
          <div className="rounded-[12px] border border-border-subtle bg-bg p-4 grid gap-3 sm:grid-cols-2">
            <Field label="Endereço do modelo-guarda" htmlFor={`mu-${which}`} hint="API compatível com OpenAI, ex.: http://guard.rt:8000/v1" className="sm:col-span-2"><Input id={`mu-${which}`} value={f.model_url} onChange={(e) => set('model_url', e.target.value)} /></Field>
            <Field label="Nome do modelo" htmlFor={`mn-${which}`}><Input id={`mn-${which}`} value={f.model_name} onChange={(e) => set('model_name', e.target.value)} placeholder="Qwen3Guard-Gen-4B" /></Field>
            <Field label="Chave (só escrita)" htmlFor={`mk-${which}`} hint={data.model_key_set ? 'Já existe uma chave salva. Deixe vazio para manter.' : null}><Input id={`mk-${which}`} type="password" value={f.model_key} onChange={(e) => set('model_key', e.target.value)} autoComplete="off" /></Field>
          </div>
        )}
        <div>
          <div className="text-sm font-medium text-text-main mb-2">O que este classificador barra</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {data.all_categories.map((c) => (
              <label key={c} className={cn('flex items-start gap-2.5 rounded-[10px] border px-3 py-2.5 cursor-pointer text-sm transition-colors', f.categories.includes(c) ? 'border-brand-500/50 bg-brand-500/5' : 'border-border-subtle hover:bg-surface-2/50')}>
                <input type="checkbox" className="mt-0.5 accent-[var(--color-brand-500)]" checked={f.categories.includes(c)} onChange={() => toggleCat(c)} />
                <span className="text-text-main">{LABEL[c] || c}</span>
              </label>
            ))}
          </div>
        </div>
        <Field label="Mensagem de recusa" htmlFor={`rf-${which}`} hint="Deixe vazio para usar o texto padrão. Para autolesão, o aviso do CVV (188) sempre é usado.">
          <Textarea id={`rf-${which}`} value={f.refusal} onChange={(e) => set('refusal', e.target.value)} rows={3} />
        </Field>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-xs text-text-muted">
            {blocked.length ? <>Barrados nas últimas 24 h: {blocked.map(([c, n]) => <Badge key={c} variant="warning" className="ml-1">{LABEL[c]?.split(' (')[0] || c} · {n}</Badge>)}</> : 'Nenhum bloqueio nas últimas 24 h.'}
          </div>
          <Button icon={Save} onClick={save} loading={busy}>Salvar este classificador</Button>
        </div>
      </div>
      <div className="border-t border-border-subtle px-5 py-3">
        <div className="text-xs font-medium text-text-muted mb-2">Últimos bloqueios (só categoria e quem; nunca o conteúdo)</div>
        {log.loading && !log.data ? <Skeleton className="h-10" /> : (log.data?.rows || []).length === 0 ? <p className="text-sm text-text-muted">Nada barrado ainda.</p> : (
          <ul className="divide-y divide-border-subtle">
            {log.data.rows.map((r, i) => (
              <li key={i} className="py-2 flex items-center justify-between gap-3 text-sm">
                <span className="text-text-main">{r.person || '—'} <Badge variant="warning" className="ml-1">{LABEL[r.category]?.split(' (')[0] || r.category}</Badge></span>
                <span className="num text-xs text-text-muted">{r.route} · {r.source} · {fmtFull(r.ts)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}

export default function Safety() {
  const { data, loading, error, reload, refresh } = useLoad(() => api.safety(), [])
  return (
    <>
      <PageHeader title="Segurança" subtitle="Nível de cada pessoa e os dois classificadores, separados: um olha o que a pessoa pede, o outro olha o que o modelo responde e faz."
        actions={<Button variant="outline" icon={RefreshCw} onClick={refresh}>Atualizar</Button>} />
      {error ? <Card><ErrorState message={error} onRetry={reload} /></Card> : (
        <div className="space-y-4">
          <Card>
            <CardHeader icon={ShieldOff} title="Dois níveis" subtitle="O nível é escolhido em cada pessoa (tela Pessoas)" />
            <div className="px-5 pb-5 grid gap-4 md:grid-cols-2">
              <div className="rounded-[12px] border border-border-subtle p-4">
                <div className="flex items-center gap-2"><Badge variant="primary">Nível 1</Badge><span className="font-semibold text-text-main">Sem trava</span></div>
                <p className="text-sm text-text-muted mt-2">A IA sem filtro, do jeito que ela é. É o padrão. O pedido não passa por nenhum classificador, em nenhum caminho.</p>
                <div className="num text-2xl font-semibold mt-3 text-text-main">{loading && !data ? '…' : data?.people?.level1 ?? 0} <span className="text-sm font-normal text-text-muted">pessoas</span></div>
              </div>
              <div className="rounded-[12px] border border-border-subtle p-4">
                <div className="flex items-center gap-2"><Badge variant="warning">Nível 2</Badge><span className="font-semibold text-text-main">Com classificadores</span></div>
                <p className="text-sm text-text-muted mt-2">Passa pelos dois classificadores. A resposta só aparece quando termina de ser conferida, então é um pouco mais lenta.</p>
                <div className="num text-2xl font-semibold mt-3 text-text-main">{loading && !data ? '…' : data?.people?.level2 ?? 0} <span className="text-sm font-normal text-text-muted">pessoas</span></div>
              </div>
            </div>
          </Card>
          <Notice variant="info">As regras são uma base conservadora (barram pouco, erram pouco). A cobertura ampla vem de um modelo-guarda; enquanto ele não estiver ligado, vale só o que está nas regras.</Notice>
          <div className="grid gap-4 xl:grid-cols-2">
            <ClassifierCard which="input" icon={ScanSearch} title="Classificador 1 · Entrada" subtitle="Lê o que a pessoa pede, antes de chegar ao modelo" data={data?.input} onChanged={refresh} />
            <ClassifierCard which="output" icon={ScanEye} title="Classificador 2 · Saída e ações" subtitle="Lê a resposta e as ações do agente, antes de entregar ou executar" data={data?.output} onChanged={refresh} />
          </div>
        </div>
      )}
    </>
  )
}
