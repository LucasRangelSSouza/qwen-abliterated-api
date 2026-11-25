import { useEffect, useState, useCallback } from 'react'
import { Users, UserCheck, Activity, Cpu, Server, Wallet, ShieldAlert, Settings as Cog, LogOut, Sun, Moon, ShieldCheck, Loader2, BadgePercent, ScrollText, Scissors, Coins, Link2, PiggyBank, Brain } from 'lucide-react'
import { api, setUnauthorizedHandler } from './lib/api.js'
import { cn } from './lib/format.js'
import { ToastProvider, Button, Card, Field, Input, IconButton } from './components/ui.jsx'
import People from './pages/People.jsx'
import Usage from './pages/Usage.jsx'
import MachineLlm from './pages/Machines.jsx'
import Providers from './pages/Providers.jsx'
import Subscribers from './pages/Subscribers.jsx'
import Settings from './pages/Settings.jsx'
import VMs from './pages/VMs.jsx'
import Affiliates from './pages/Affiliates.jsx'
import Terms from './pages/Terms.jsx'
import Produto from './pages/Produto.jsx'
import Links from './pages/Links.jsx'
import Custos from './pages/Custos.jsx'
import Combos from './pages/Combos.jsx'
import ProdutoPainel from './pages/ProdutoPainel.jsx'
import { Postgres, Minio } from './pages/Saude.jsx'
import Infra from './pages/Infra.jsx'
import AssinantesUnico from './pages/AssinantesUnico.jsx'
import { Database, HardDrive } from 'lucide-react'
import { Boxes } from 'lucide-react'

const Cortai = () => <Produto id="cortai" />
const Rendai = () => <Produto id="rendai" />

// Menu do painel único (spec 57 §6.1): Segundo Cérebro, Produtos (do registro único), Geral e Configurações.
const NAV = [
  { id: 'assinantes', grupo: 'Segundo Cérebro', label: 'Assinantes', icon: UserCheck, el: AssinantesUnico },
  { id: 'uso', grupo: 'Segundo Cérebro', label: 'Uso', icon: Activity, el: Usage },
  { id: 'llm', grupo: 'Geral', label: 'Qwen: LLM e GPU', short: 'LLM', icon: Cpu, el: MachineLlm },
  { id: 'fornecedores', grupo: 'Geral', label: 'Fornecedores', icon: Wallet, el: Providers },
  { id: 'vms', grupo: 'Segundo Cérebro', label: 'Máquinas virtuais', short: 'VMs', icon: Server, el: VMs },
  { id: 'produtos', grupo: 'Produtos', label: 'Todos os produtos', short: 'Produtos', icon: Boxes, el: () => <ListaProdutos /> },
  { id: 'links', grupo: 'Segundo Cérebro', label: 'Cupons e links', short: 'Links', icon: Link2, el: Links },
  { id: 'custos', grupo: 'Geral', label: 'Custos', icon: PiggyBank, el: Custos },
  { id: 'combos', grupo: 'Geral', label: 'IA e combos', short: 'IA', icon: Brain, el: Combos },
  { id: 'afiliados', grupo: 'Segundo Cérebro', label: 'Afiliados', icon: BadgePercent, el: Affiliates },
  { id: 'termos', grupo: 'Segundo Cérebro', label: 'Termos de uso', short: 'Termos', icon: ScrollText, el: Terms },
  { id: 'infra', grupo: 'Geral', label: 'Infraestrutura', icon: Server, el: Infra },
  { id: 'postgres', grupo: 'Geral', label: 'Postgres', icon: Database, el: Postgres },
  { id: 'minio', grupo: 'Geral', label: 'MinIO', icon: HardDrive, el: Minio },
  { id: 'config', grupo: 'Configurações', label: 'Configurações', short: 'Ajustes', icon: Cog, el: Settings },
]

const GRUPOS = ['Segundo Cérebro', 'Produtos', 'Geral', 'Configurações']
const TIPOS_PRODUTO = ['produto', 'variante', 'em_teste', 'em_preparo']

function useRegistro() {
  const [itens, setItens] = useState([])
  useEffect(() => { api.painelRegistro().then((r) => setItens((r.produtos || []).filter((p) => TIPOS_PRODUTO.includes(p.tipo)))).catch(() => {}) }, [])
  return itens
}

function ListaProdutos() {
  const itens = useRegistro()
  return (
    <div>
      <h1 className="text-xl font-semibold mb-4 text-text-main">Produtos</h1>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {itens.map((p) => (
          <a key={p.id} href={'#/p/' + p.id} className="rounded-[12px] border border-border-subtle bg-surface p-4 hover:bg-surface-2">
            <div className="font-medium text-text-main">{p.nome}</div>
            <div className="text-xs text-text-muted">{(p.hosts || [])[0] || ''}{p.tipo === 'variante' ? ' · variante de ' + p.base : p.tipo === 'em_preparo' ? ' · em breve' : p.tipo === 'em_teste' ? ' · em teste' : ''}</div>
          </a>
        ))}
      </div>
    </div>
  )
}

function useHashRoute() {
  const read = () => {
    let h = location.hash.replace(/^#\/?/, '') || 'assinantes'
    if (h.startsWith('pessoas')) { history.replaceState(null, '', '#/assinantes?visao=chaves'); h = 'assinantes' }
    if (h.startsWith('p/')) return h.split(/[?]/)[0]
    return h.split(/[/?]/)[0]
  }
  const [r, setR] = useState(read)
  useEffect(() => {
    const h = () => setR(read())
    addEventListener('hashchange', h)
    return () => removeEventListener('hashchange', h)
  }, [])
  return r.startsWith('p/') || NAV.some((n) => n.id === r) ? r : 'assinantes'
}

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  const toggle = () => {
    const next = !dark
    document.documentElement.classList.toggle('dark', next)
    try { localStorage.setItem('theme', next ? 'dark' : 'light') } catch {}
    setDark(next)
  }
  return [dark, toggle]
}

function Logo({ small }) {
  return (
    <div className="flex items-center gap-3">
      <div className={cn('grid place-items-center rounded-[10px] bg-gradient-to-br from-brand-500 to-brand-700 shadow-[var(--shadow-warm)]', small ? 'size-8' : 'size-9')}>
        <ShieldCheck className="size-5 text-white" />
      </div>
      <div className="leading-tight">
        <div className="font-semibold tracking-tight text-text-main">Painel Rangel Tech</div>
        <div className="text-xs text-text-muted">Painel do administrador</div>
      </div>
    </div>
  )
}

function Login({ onDone, dark, toggleTheme }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await api.login(pw); onDone() }
    catch (ex) { setErr(ex.status === 401 ? 'Senha incorreta.' : ex.message) }
    finally { setBusy(false) }
  }
  return (
    <main className="min-h-screen grid place-items-center p-4 relative">
      <div className="absolute top-4 right-4">
        <IconButton label={dark ? 'Modo claro' : 'Modo escuro'} icon={dark ? Sun : Moon} onClick={toggleTheme} />
      </div>
      <Card className="w-full max-w-sm p-7 shadow-[var(--shadow-elev)]">
        <Logo />
        <form onSubmit={submit} className="mt-7 flex flex-col gap-4">
          <Field label="Senha do administrador" htmlFor="pw" error={err}>
            <Input id="pw" type="password" autoComplete="current-password" autoFocus value={pw}
              onChange={(e) => setPw(e.target.value)} aria-invalid={!!err} />
          </Field>
          <Button type="submit" loading={busy} disabled={!pw}>Entrar</Button>
        </form>
      </Card>
    </main>
  )
}

function Shell({ onLogout, dark, toggleTheme }) {
  const route = useHashRoute()
  const registro = useRegistro()
  const Page = route.startsWith('p/') ? () => <ProdutoPainel id={route.slice(2)} /> : NAV.find((n) => n.id === route).el
  const go = (id) => { location.hash = '#/' + id }
  return (
    <div className="min-h-screen lg:flex">
      {/* Barra lateral (desktop) */}
      <aside className="hidden lg:flex w-64 shrink-0 flex-col border-r border-border-subtle bg-bg-alt/70 sticky top-0 h-screen">
        <div className="px-5 pt-6 pb-5"><Logo /></div>
        <nav className="flex-1 px-3 space-y-0.5 overflow-y-auto pb-4" aria-label="Principal">
          {GRUPOS.map((g) => (
            <div key={g} className="pt-3">
              <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-text-subtle">{g}</div>
              {NAV.filter((n) => n.grupo === g).map((n) => (
                <a key={n.id} href={'#/' + n.id} aria-current={route === n.id ? 'page' : undefined}
                  className={cn('flex items-center gap-3 px-3 h-9 rounded-[10px] text-[13px] font-medium transition-colors',
                    route === n.id ? 'bg-brand-500/10 text-brand-text' : 'text-text-muted hover:bg-surface-2 hover:text-text-main')}>
                  <n.icon className="size-[18px]" />{n.label}
                </a>
              ))}
              {g === 'Produtos' && registro.map((p) => (
                <a key={p.id} href={'#/p/' + p.id} aria-current={route === 'p/' + p.id ? 'page' : undefined} data-produto={p.id}
                  className={cn('flex items-center gap-3 px-3 h-8 rounded-[10px] text-[13px] transition-colors',
                    route === 'p/' + p.id ? 'bg-brand-500/10 text-brand-text' : 'text-text-muted hover:bg-surface-2 hover:text-text-main',
                    p.tipo === 'variante' && 'pl-8 text-[12px]')}>
                  {p.nome}{p.tipo === 'em_preparo' && <span className="text-[10px] text-text-subtle">em breve</span>}{p.tipo === 'em_teste' && <span className="text-[10px] text-text-subtle">em teste</span>}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="p-3 border-t border-border-subtle flex items-center justify-between">
          <Button variant="ghost" size="sm" icon={LogOut} onClick={onLogout}>Sair</Button>
          <IconButton label={dark ? 'Modo claro' : 'Modo escuro'} icon={dark ? Sun : Moon} onClick={toggleTheme} />
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        {/* Topo (celular/tablet) */}
        <header className="lg:hidden sticky top-0 z-30 flex items-center justify-between gap-3 px-4 h-14 border-b border-border-subtle bg-surface/85 backdrop-blur-xl">
          <Logo small />
          <div className="flex items-center">
            <IconButton label={dark ? 'Modo claro' : 'Modo escuro'} icon={dark ? Sun : Moon} onClick={toggleTheme} />
            <IconButton label="Sair" icon={LogOut} onClick={onLogout} />
          </div>
        </header>
        <main className="flex-1 w-full max-w-[1280px] mx-auto px-4 sm:px-6 lg:px-8 py-5 lg:py-8 pb-24 lg:pb-8">
          <Page key={route} />
        </main>
        {/* Navegação inferior (celular/tablet) */}
        <nav aria-label="Principal" className="lg:hidden fixed bottom-0 inset-x-0 z-30 flex overflow-x-auto snap-x border-t border-border-subtle bg-surface/90 backdrop-blur-xl pb-[env(safe-area-inset-bottom)] [scrollbar-width:none]">
          {NAV.map((n) => (
            <a key={n.id} href={'#/' + n.id} onClick={() => go(n.id)} aria-current={route === n.id ? 'page' : undefined}
              className={cn('flex flex-col items-center justify-center gap-0.5 h-16 min-w-[84px] flex-1 shrink-0 snap-start px-2 text-[11px] font-medium whitespace-nowrap',
                route === n.id ? 'text-brand-text' : 'text-text-muted')}>
              <n.icon className="size-5" />{n.short || n.label}
            </a>
          ))}
        </nav>
      </div>
    </div>
  )
}

export default function App() {
  const [auth, setAuth] = useState('checking')
  const [dark, toggleTheme] = useTheme()
  const logout = useCallback(async () => {
    try { await api.logout() } catch {}
    setAuth('out')
  }, [])
  useEffect(() => {
    setUnauthorizedHandler(() => setAuth('out'))
    api.me().then(() => setAuth('in')).catch(() => setAuth('out'))
  }, [])

  if (auth === 'checking') {
    return <div className="min-h-screen grid place-items-center text-text-muted"><Loader2 className="size-6 animate-spin" aria-label="Carregando" /></div>
  }
  return (
    <ToastProvider>
      {auth === 'out'
        ? <Login dark={dark} toggleTheme={toggleTheme} onDone={() => setAuth('in')} />
        : <Shell dark={dark} toggleTheme={toggleTheme} onLogout={logout} />}
    </ToastProvider>
  )
}
