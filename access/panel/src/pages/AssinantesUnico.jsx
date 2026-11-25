import { useState } from 'react'
import People from './People.jsx'
import Subscribers from './Subscribers.jsx'
import { cn } from '../lib/format.js'

// Segundo Cérebro > Assinantes (spec 57 §6.3): um item só no menu no lugar de "Pessoas" e "Assinantes", sem perder campo:
// "Contas e planos" (assinatura, Cakto, liberação) e "Chaves e limites" (chaves de API, limites por janela, dispositivos).
const VISOES = [['contas', 'Contas e planos', Subscribers], ['chaves', 'Chaves e limites', People]]

export default function AssinantesUnico() {
  const ler = () => (location.hash.includes('visao=chaves') ? 'chaves' : 'contas')
  const [v, setV] = useState(ler)
  const ir = (x) => { setV(x); history.replaceState(null, '', '#/assinantes?visao=' + x) }
  const Atual = VISOES.find((x) => x[0] === v)[2]
  return (
    <div>
      <div className="inline-flex rounded-[12px] bg-surface-2 p-1 mb-4" role="tablist" data-visoes="assinantes">
        {VISOES.map(([id, rotulo]) => (
          <button key={id} role="tab" aria-selected={v === id} onClick={() => ir(id)} data-visao={id}
            className={cn('px-4 h-9 rounded-[10px] text-sm font-medium transition-colors', v === id ? 'bg-surface text-text-main shadow-sm' : 'text-text-muted hover:text-text-main')}>
            {rotulo}
          </button>
        ))}
      </div>
      <Atual />
    </div>
  )
}
