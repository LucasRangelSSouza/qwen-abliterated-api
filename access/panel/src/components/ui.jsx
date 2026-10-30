import { useEffect, useRef, useState, createContext, useContext, useCallback } from 'react'
import { Loader2, X, CheckCircle2, AlertCircle, Info, Inbox, TriangleAlert, Copy, Check } from 'lucide-react'
import { cn } from '../lib/format.js'

/* ---------- Button ---------- */
const bv = {
  primary: 'bg-brand-500 hover:bg-brand-600 text-white shadow-sm disabled:bg-surface-3 disabled:text-text-muted',
  secondary: 'bg-surface-2 hover:bg-surface-3 text-text-main border border-border',
  outline: 'border border-border text-text-main hover:bg-surface-2 hover:border-brand-500/40',
  ghost: 'text-text-muted hover:bg-surface-2 hover:text-text-main',
  danger: 'bg-red-600 hover:bg-red-700 text-white shadow-sm',
}
const bs = {
  xs: 'h-7 px-2.5 text-xs rounded-[8px] gap-1.5',
  sm: 'h-8 px-3 text-xs rounded-[8px] gap-1.5',
  md: 'h-10 px-4 text-sm rounded-[10px] gap-2',
}
export function Button({ variant = 'primary', size = 'md', icon: Icon, loading, className, children, ...p }) {
  return (
    <button
      {...p}
      disabled={p.disabled || loading}
      className={cn('inline-flex items-center justify-center font-semibold transition-all duration-150 cursor-pointer select-none whitespace-nowrap',
        'active:scale-[0.97] disabled:opacity-60 disabled:cursor-not-allowed disabled:active:scale-100', bv[variant], bs[size], className)}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : Icon ? <Icon className="size-4" /> : null}
      {children}
    </button>
  )
}
export function IconButton({ label, icon: Icon, className, ...p }) {
  return (
    <button {...p} aria-label={label} title={label}
      className={cn('inline-grid place-items-center size-9 rounded-[10px] text-text-muted hover:bg-surface-2 hover:text-text-main transition-colors cursor-pointer disabled:opacity-50', className)}>
      <Icon className="size-[18px]" />
    </button>
  )
}

/* ---------- Card ---------- */
export function Card({ className, children, ...p }) {
  return <div {...p} className={cn('bg-surface border border-border-subtle rounded-[14px] shadow-[var(--shadow-soft)]', className)}>{children}</div>
}
export function CardHeader({ title, subtitle, action, icon: Icon }) {
  return (
    <div className="flex items-start justify-between gap-3 p-5 pb-3">
      <div className="flex items-center gap-3 min-w-0">
        {Icon && <div className="p-2 rounded-[10px] bg-bg-alt text-text-muted shrink-0"><Icon className="size-5" /></div>}
        <div className="min-w-0">
          <h3 className="font-semibold text-text-main">{title}</h3>
          {subtitle && <p className="text-sm text-text-muted">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

/* ---------- Badge ---------- */
const badgeV = {
  default: ['bg-surface-2 text-text-muted', 'bg-gray-400'],
  primary: ['bg-brand-500/10 text-brand-text', 'bg-brand-500'],
  success: ['bg-green-500/12 text-green-700 dark:text-green-400', 'bg-green-500'],
  warning: ['bg-amber-500/14 text-amber-700 dark:text-amber-400', 'bg-amber-500'],
  error: ['bg-red-500/12 text-red-700 dark:text-red-400', 'bg-red-500'],
  info: ['bg-blue-500/12 text-blue-700 dark:text-blue-400', 'bg-blue-500'],
}
export function Badge({ variant = 'default', dot, pulse, className, children }) {
  const [c, d] = badgeV[variant]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full font-semibold px-2.5 py-0.5 text-xs whitespace-nowrap', c, className)}>
      {dot && <span className={cn('size-1.5 rounded-full', d, pulse && 'animate-pulse')} />}
      {children}
    </span>
  )
}

/* ---------- Form ---------- */
export function Field({ label, hint, error, children, htmlFor, className }) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {label && <label htmlFor={htmlFor} className="text-sm font-medium text-text-main">{label}</label>}
      {children}
      {error ? <p role="alert" className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1"><AlertCircle className="size-3.5" />{error}</p>
        : hint ? <p className="text-xs text-text-muted">{hint}</p> : null}
    </div>
  )
}
export const inputCls = 'w-full h-10 px-3 text-[16px] sm:text-sm text-text-main bg-surface-2 rounded-[10px] border border-transparent placeholder:text-text-subtle ' +
  'focus:outline-none focus:ring-2 focus:ring-brand-500/35 focus:border-brand-500/50 transition-all disabled:opacity-50 disabled:cursor-not-allowed'
export function Input({ icon: Icon, className, ...p }) {
  return (
    <div className="relative">
      {Icon && <Icon className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-text-muted pointer-events-none" />}
      <input {...p} className={cn(inputCls, Icon && 'pl-9', className)} />
    </div>
  )
}
export function Textarea({ className, ...p }) {
  return <textarea {...p} className={cn(inputCls, 'h-auto py-2 min-h-[72px] resize-y', className)} />
}
export function Select({ className, children, ...p }) {
  return <select {...p} className={cn(inputCls, 'pr-8 cursor-pointer', className)}>{children}</select>
}
export function Toggle({ checked, onChange, label, description, id }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-text-main cursor-pointer">{label}</label>
        {description && <p className="text-xs text-text-muted">{description}</p>}
      </div>
      <button id={id} type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={cn('relative inline-flex shrink-0 w-11 h-6 rounded-full cursor-pointer transition-colors duration-200 focus-visible:outline-2',
          checked ? 'bg-brand-500' : 'bg-surface-3')}>
        <span className={cn('absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow-sm transition-transform duration-200', checked && 'translate-x-5')} />
      </button>
    </div>
  )
}

/* ---------- Progress ---------- */
export function Progress({ used, limit }) {
  if (limit == null) return <div className="h-1.5 rounded-full bg-surface-2" aria-hidden />
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100
  const color = pct >= 100 ? 'bg-red-500' : pct >= 80 ? 'bg-amber-500' : 'bg-brand-500'
  return (
    <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
      <div className={cn('h-full rounded-full transition-all duration-500', color)} style={{ width: pct + '%' }} />
    </div>
  )
}

/* ---------- Overlays ---------- */
function useEscape(open, onClose) {
  useEffect(() => {
    if (!open) return
    const h = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', h)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', h); document.body.style.overflow = prev }
  }, [open, onClose])
}
function useAutofocus(open) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const el = ref.current?.querySelector('[data-autofocus], input:not([disabled]), textarea, button')
    el?.focus()
  }, [open])
  return ref
}
export function Drawer({ open, onClose, title, subtitle, children, footer }) {
  useEscape(open, onClose)
  const ref = useAutofocus(open)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px] fade-in" onClick={onClose} />
      <div ref={ref} className="slide-in absolute right-0 top-0 h-full w-full sm:max-w-[460px] bg-surface border-l border-border-subtle shadow-[var(--shadow-elev)] flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border-subtle shrink-0">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-text-main">{title}</h2>
            {subtitle && <p className="text-sm text-text-muted">{subtitle}</p>}
          </div>
          <IconButton label="Fechar" icon={X} onClick={onClose} />
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <div className="px-5 py-4 border-t border-border-subtle shrink-0 flex gap-2 justify-end">{footer}</div>}
      </div>
    </div>
  )
}
export function Modal({ open, onClose, title, children, footer, wide }) {
  useEscape(open, onClose)
  const ref = useAutofocus(open)
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px] fade-in" onClick={onClose} />
      <div ref={ref} className={cn('pop-in relative w-full bg-surface border border-border-subtle rounded-[14px] shadow-[var(--shadow-elev)] max-h-[90vh] flex flex-col', wide ? 'max-w-lg' : 'max-w-md')}>
        <div className="flex items-center justify-between gap-3 px-5 pt-5">
          <h2 className="text-lg font-semibold text-text-main">{title}</h2>
          <IconButton label="Fechar" icon={X} onClick={onClose} className="-mr-2" />
        </div>
        <div className="px-5 py-3 overflow-y-auto text-sm text-text-muted">{children}</div>
        {footer && <div className="px-5 pb-5 pt-2 flex gap-2 justify-end flex-wrap">{footer}</div>}
      </div>
    </div>
  )
}
export function ConfirmModal({ open, onClose, onConfirm, title, children, confirmLabel = 'Confirmar', danger, loading }) {
  return (
    <Modal open={open} onClose={onClose} title={title}
      footer={<>
        <Button variant="secondary" onClick={onClose} disabled={loading}>Cancelar</Button>
        <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} loading={loading} data-autofocus>{confirmLabel}</Button>
      </>}>
      {children}
    </Modal>
  )
}

/* ---------- Secret key (mostrada uma vez) ---------- */
export function KeyReveal({ apiKey, onClose, title = 'Chave de API gerada' }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(apiKey) }
    catch {
      const t = document.createElement('textarea'); t.value = apiKey; document.body.appendChild(t); t.select()
      try { document.execCommand('copy') } catch {} t.remove()
    }
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }
  return (
    <Modal open={!!apiKey} onClose={onClose} title={title} wide
      footer={<Button onClick={onClose}>Já guardei a chave</Button>}>
      <div className="flex gap-2.5 p-3 rounded-[10px] bg-amber-500/12 text-amber-800 dark:text-amber-300 mb-4">
        <TriangleAlert className="size-5 shrink-0 mt-0.5" />
        <p className="text-sm"><strong>Copie agora.</strong> Esta é a única vez que a chave completa aparece. Depois só será possível ver o final dela. Se perder, gere uma nova.</p>
      </div>
      <div className="flex items-stretch gap-2">
        <code data-autofocus tabIndex={0} className="flex-1 min-w-0 break-all select-all px-3 py-2.5 rounded-[10px] bg-surface-2 text-text-main text-[13px] font-mono leading-relaxed">{apiKey}</code>
        <Button variant="secondary" onClick={copy} icon={copied ? Check : Copy} className="self-stretch">{copied ? 'Copiada' : 'Copiar'}</Button>
      </div>
    </Modal>
  )
}

/* ---------- Estados ---------- */
export function Skeleton({ className }) { return <div className={cn('skeleton', className)} /> }
export function EmptyState({ icon: Icon = Inbox, title, text, action }) {
  return (
    <div className="flex flex-col items-center text-center py-14 px-6">
      <div className="size-12 rounded-full bg-bg-alt grid place-items-center text-text-muted mb-4"><Icon className="size-6" /></div>
      <h3 className="font-semibold text-text-main">{title}</h3>
      {text && <p className="text-sm text-text-muted mt-1 max-w-sm">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
export function ErrorState({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center text-center py-14 px-6" role="alert">
      <div className="size-12 rounded-full bg-red-500/10 grid place-items-center text-red-600 dark:text-red-400 mb-4"><AlertCircle className="size-6" /></div>
      <h3 className="font-semibold text-text-main">Não foi possível carregar</h3>
      <p className="text-sm text-text-muted mt-1 max-w-sm">{message}</p>
      {onRetry && <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry}>Tentar de novo</Button>}
    </div>
  )
}
export function Notice({ variant = 'warning', children }) {
  const c = variant === 'warning' ? 'bg-amber-500/12 text-amber-800 dark:text-amber-300' : 'bg-blue-500/12 text-blue-800 dark:text-blue-300'
  return (
    <div className={cn('flex gap-2.5 p-3 rounded-[10px] text-sm', c)}>
      {variant === 'warning' ? <TriangleAlert className="size-4 shrink-0 mt-0.5" /> : <Info className="size-4 shrink-0 mt-0.5" />}
      <div>{children}</div>
    </div>
  )
}

/* ---------- Toasts ---------- */
const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((kind, text) => {
    const id = Math.random()
    setItems((l) => [...l, { id, kind, text }])
    setTimeout(() => setItems((l) => l.filter((x) => x.id !== id)), kind === 'error' ? 6000 : 3500)
  }, [])
  const api = useRef({ success: (t) => push('success', t), error: (t) => push('error', t) }).current
  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-[60] flex flex-col gap-2 items-stretch sm:items-end pointer-events-none" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} role={t.kind === 'error' ? 'alert' : 'status'}
            className="pop-in pointer-events-auto flex items-center gap-2.5 pl-3 pr-4 py-2.5 rounded-[12px] bg-surface border border-border shadow-[var(--shadow-elev)] text-sm text-text-main max-w-sm">
            {t.kind === 'error' ? <AlertCircle className="size-[18px] text-red-500 shrink-0" /> : <CheckCircle2 className="size-[18px] text-green-500 shrink-0" />}
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

/* ---------- Hook de carga ---------- */
export function useLoad(fn, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const run = useCallback((silent) => {
    if (!silent) setState((s) => ({ ...s, loading: true, error: null }))
    let alive = true
    fn().then((data) => alive && setState({ loading: false, data, error: null }))
      .catch((e) => alive && setState((s) => ({ loading: false, data: silent ? s.data : null, error: e.message })))
    return () => { alive = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => run(false), [run])
  return { ...state, reload: () => run(false), refresh: () => run(true) }
}
