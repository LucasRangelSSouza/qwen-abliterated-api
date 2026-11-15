import { useEffect, useState } from 'react'
import { Brain, Save, Plus, Trash2, ArrowUp } from 'lucide-react'
import { api } from '../lib/api.js'
import PageHeader from '../components/PageHeader.jsx'
import { Card, CardHeader, Button, Input, Select, Skeleton, ErrorState, Notice, useToast, useLoad } from '../components/ui.jsx'

const DESCR = {
  'rt-planejar': 'Planejamento: escreve o plano de cada tarefa do agente antes da execução.',
  'rt-geral': 'Dia a dia: Cortaí, Rendaí e tarefas que não precisam da IA sem recusa.',
}

// Combos de IA (spec 40): a ordem dos destinos de cada apelido. O primeiro que responder vence; erro passa para o próximo.
export default function Combos() {
  const toast = useToast()
  const d = useLoad(() => api.combos(), [])
  const [c, setC] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (d.data) setC(d.data.combos) }, [d.data])
  const muda = (ap, lista) => setC({ ...c, [ap]: lista })
  const salvar = async () => {
    setBusy(true)
    try { await api.salvarCombos(c); toast?.('Combos salvos'); d.reload() } catch (e) { toast?.(e.message) } finally { setBusy(false) }
  }
  return (
    <div>
      <PageHeader title="IA e combos" subtitle="Qual IA atende cada tipo de pedido, em ordem de preferência" actions={<Button icon={Save} loading={busy} onClick={salvar}>Salvar</Button>} />
      <Notice variant="info">Destino "9Router" usa as contas OAuth do roteador global (cx/ = ChatGPT, cc/ = Claude Code, mmf/ = MiniMax). "Casa" é a nossa IA (qwen-abliterated). Se o primeiro falhar, o próximo assume.</Notice>
      {d.loading || !c ? <Skeleton className="h-48 mt-4" /> : d.error ? <ErrorState message={d.error} onRetry={d.reload} /> : Object.entries(c).map(([ap, lista]) => (
        <Card key={ap} className="mt-4">
          <CardHeader title={ap} subtitle={DESCR[ap] || 'Combo personalizado'} icon={Brain}
            action={<Button size="sm" variant="secondary" icon={Plus} onClick={() => muda(ap, [...lista, { router: 'cx/gpt-5.6-sol' }])}>Destino</Button>} />
          <div className="px-5 pb-5 space-y-2">
            {lista.map((dest, i) => {
              const tipo = 'router' in dest ? 'router' : 'casa'
              return (
                <div key={i} className="grid gap-2 grid-cols-[28px_120px_1fr_40px_40px] items-center">
                  <span className="text-sm text-text-muted num">{i + 1}º</span>
                  <Select value={tipo} aria-label="Tipo" onChange={(e) => muda(ap, lista.map((x, k) => (k === i ? { [e.target.value]: e.target.value === 'casa' ? 'qwen-abliterated' : 'cx/gpt-5.6-sol' } : x)))}>
                    <option value="router">9Router</option><option value="casa">Casa</option>
                  </Select>
                  <Input value={dest[tipo]} aria-label="Modelo" onChange={(e) => muda(ap, lista.map((x, k) => (k === i ? { [tipo]: e.target.value } : x)))} />
                  <Button size="sm" variant="ghost" icon={ArrowUp} aria-label="Subir" disabled={i === 0} onClick={() => { const n = [...lista]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; muda(ap, n) }} />
                  <Button size="sm" variant="ghost" icon={Trash2} aria-label="Remover" disabled={lista.length === 1} onClick={() => muda(ap, lista.filter((_, k) => k !== i))} />
                </div>
              )
            })}
          </div>
        </Card>
      ))}
    </div>
  )
}
