import { useEffect, useState, useCallback } from 'react'
import { Users, Activity, Server, Settings as Cog, LogOut, Sun, Moon, ShieldCheck, Loader2 } from 'lucide-react'
import { api, setUnauthorizedHandler } from './lib/api.js'
import { cn } from './lib/format.js'
import { ToastProvider, Button, Card, Field, Input, IconButton } from './components/ui.jsx'
import People from './pages/People.jsx'
import Usage from './pages/Usage.jsx'
import Machines from './pages/Machines.jsx'
import Settings from './pages/Settings.jsx'

const NAV = [
  { id: 'pessoas', label: 'Pessoas', icon: Users, el: People },
  { id: 'uso', label: 'Uso', icon: Activity, el: Usage },
  { id: 'maquinas', label: 'Máquinas', icon: Server, el: Machines },
  { id: 'config', label: 'Configurações', icon: Cog, el: Settings },
]

function useHashRoute() {
  const read = () => (location.hash.replace(/^#\/?/, '') || 'pessoas').split(/[/?]/)[0]
  const [r, setR] = useState(read)
  useEffect(() => {
    const h = () => setR(read())
    addEventListener('hashchange', h)
    return () => removeEventListener('hashchange', h)
  }, [])
  return NAV.some((n) => n.id === r) ? r : 'pessoas'
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
        <div className="font-semibold tracking-tight text-text-main">Acesso LLM</div>
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
  const Page = NAV.find((n) => n.id === route).el
  const go = (id) => { location.hash = '#/' + id }
  return (
    <div className="min-h-screen lg:flex">
      {/* Barra lateral (desktop) */}
      <aside className="hidden lg:flex w-64 shrink-0 flex-col border-r border-border-subtle bg-bg-alt/70 sticky top-0 h-screen">
        <div className="px-5 pt-6 pb-5"><Logo /></div>
        <nav className="flex-1 px-3 space-y-0.5" aria-label="Principal">
          {NAV.map((n) => (
            <a key={n.id} href={'#/' + n.id} aria-current={route === n.id ? 'page' : undefined}
              className={cn('flex items-center gap-3 px-3 h-10 rounded-[10px] text-[13px] font-medium transition-colors',
                route === n.id ? 'bg-brand-500/10 text-brand-text' : 'text-text-muted hover:bg-surface-2 hover:text-text-main')}>
              <n.icon className="size-[18px]" />{n.label}
            </a>
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
        <nav aria-label="Principal" className="lg:hidden fixed bottom-0 inset-x-0 z-30 grid grid-cols-4 border-t border-border-subtle bg-surface/90 backdrop-blur-xl pb-[env(safe-area-inset-bottom)]">
          {NAV.map((n) => (
            <a key={n.id} href={'#/' + n.id} onClick={() => go(n.id)} aria-current={route === n.id ? 'page' : undefined}
              className={cn('flex flex-col items-center justify-center gap-0.5 h-16 text-[11px] font-medium',
                route === n.id ? 'text-brand-text' : 'text-text-muted')}>
              <n.icon className="size-5" />{n.label}
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
