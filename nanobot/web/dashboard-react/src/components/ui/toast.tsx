import * as React from "react"
import { useStore } from "@/src/store/useStore"
import type { Toast, ToastType } from "@/src/store/useStore"
import { cn } from "@/src/lib/utils"
import { X, CheckCircle2, AlertCircle, Info, AlertTriangle } from "lucide-react"

type RemoveToast = (id: string) => void
type ToastItemProps = { toast: Toast; removeToast: RemoveToast }

const TOAST_STYLES: Record<ToastType, string> = {
  info: "bg-background text-foreground border-border",
  success:
    "bg-green-50 text-green-900 border-green-200 dark:bg-green-900/20 dark:text-green-100 dark:border-green-900",
  error:
    "bg-red-50 text-red-900 border-red-200 dark:bg-red-900/20 dark:text-red-100 dark:border-red-900",
  warning:
    "bg-yellow-50 text-yellow-900 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-100 dark:border-yellow-900",
}

const TOAST_ICON_STYLES: Record<ToastType, string> = {
  info: "text-blue-600 dark:text-blue-400",
  success: "text-green-600 dark:text-green-400",
  error: "text-red-600 dark:text-red-400",
  warning: "text-yellow-600 dark:text-yellow-400",
}

const TOAST_ICONS: Record<ToastType, React.ComponentType<{ className?: string }>> = {
  info: Info,
  success: CheckCircle2,
  error: AlertCircle,
  warning: AlertTriangle,
}

const ToastItem = React.memo(function ToastItem({ toast, removeToast }: ToastItemProps) {
  const addToast = useStore((state) => state.addToast)
  const timerRef = React.useRef<number | null>(null)
  const timerStartedAtRef = React.useRef<number | null>(null)
  const remainingMsRef = React.useRef(toast.durationMs)
  const isPausedRef = React.useRef(false)
  const interactionRef = React.useRef({ hovered: false, focused: false })

  const clearTimer = React.useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    timerStartedAtRef.current = null
  }, [])

  const dismiss = React.useCallback(() => {
    removeToast(toast.id)
  }, [removeToast, toast.id])

  const scheduleDismiss = React.useCallback(() => {
    if (toast.persist || isPausedRef.current) {
      return
    }
    clearTimer()
    const delay = Math.max(0, remainingMsRef.current)
    if (delay === 0) {
      dismiss()
      return
    }
    timerStartedAtRef.current = Date.now()
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      timerStartedAtRef.current = null
      dismiss()
    }, delay)
  }, [clearTimer, dismiss, toast.persist])

  const pauseDismiss = React.useCallback(() => {
    if (toast.persist || isPausedRef.current) {
      return
    }
    isPausedRef.current = true
    if (timerStartedAtRef.current !== null) {
      const elapsed = Date.now() - timerStartedAtRef.current
      remainingMsRef.current = Math.max(0, remainingMsRef.current - elapsed)
    }
    clearTimer()
  }, [clearTimer, toast.persist])

  const resumeDismiss = React.useCallback(() => {
    if (toast.persist || !isPausedRef.current) {
      return
    }
    isPausedRef.current = false
    scheduleDismiss()
  }, [scheduleDismiss, toast.persist])

  const tryResumeDismiss = React.useCallback(() => {
    if (!interactionRef.current.hovered && !interactionRef.current.focused) {
      resumeDismiss()
    }
  }, [resumeDismiss])

  React.useEffect(() => {
    remainingMsRef.current = toast.durationMs
    const isInteracting = interactionRef.current.hovered || interactionRef.current.focused
    const isDocumentHidden = typeof document !== "undefined" && document.hidden
    if (toast.persist || isInteracting || isDocumentHidden) {
      isPausedRef.current = true
      clearTimer()
      return clearTimer
    }
    isPausedRef.current = false
    scheduleDismiss()
    return clearTimer
  }, [clearTimer, scheduleDismiss, toast.durationMs, toast.id, toast.persist, toast.updatedAt])

  React.useEffect(() => {
    if (toast.persist || typeof document === "undefined") {
      return
    }
    const handleVisibilityChange = () => {
      if (document.hidden) {
        pauseDismiss()
        return
      }
      tryResumeDismiss()
    }
    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [pauseDismiss, toast.persist, tryResumeDismiss])

  const onMouseEnter = React.useCallback(() => {
    interactionRef.current.hovered = true
    pauseDismiss()
  }, [pauseDismiss])

  const onMouseLeave = React.useCallback(() => {
    interactionRef.current.hovered = false
    tryResumeDismiss()
  }, [tryResumeDismiss])

  const onFocusCapture = React.useCallback(() => {
    interactionRef.current.focused = true
    pauseDismiss()
  }, [pauseDismiss])

  const onBlurCapture = React.useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      const nextTarget = event.relatedTarget
      if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) {
        return
      }
      interactionRef.current.focused = false
      tryResumeDismiss()
    },
    [tryResumeDismiss]
  )

  const isAssertive = toast.type === "error" || toast.type === "warning"
  const role = isAssertive ? "alert" : "status"
  const ariaLive = isAssertive ? "assertive" : "polite"
  const Icon = TOAST_ICONS[toast.type]
  const handleActionClick = React.useCallback(async () => {
    if (!toast.action) return
    try {
      await Promise.resolve(toast.action.onClick())
    } catch {
      addToast({ type: "error", message: "操作失败，请稍后重试" })
    }
  }, [addToast, toast.action])

  return (
    <div
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onFocusCapture={onFocusCapture}
      onBlurCapture={onBlurCapture}
      role={role}
      aria-live={ariaLive}
      aria-atomic="true"
      className={cn(
        "group pointer-events-auto relative mt-2 flex w-full items-center justify-between space-x-2 overflow-hidden rounded-md border p-4 pr-6 shadow-lg transition-all data-[swipe=cancel]:translate-x-0 data-[swipe=end]:translate-x-[var(--radix-toast-swipe-end-x)] data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[swipe=move]:transition-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[swipe=end]:animate-out data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full data-[state=open]:slide-in-from-top-full data-[state=open]:sm:slide-in-from-bottom-full",
        TOAST_STYLES[toast.type]
      )}
    >
      <div className="flex items-start gap-3">
        <Icon className={cn("h-5 w-5", TOAST_ICON_STYLES[toast.type])} />
        <div className="flex flex-col gap-1">
          <div className="text-sm font-medium">{toast.message}</div>
          {toast.action && (
            <button
              type="button"
              onClick={() => {
                void handleActionClick()
              }}
              className="text-left text-xs font-semibold underline underline-offset-2 hover:opacity-80"
            >
              {toast.action.label}
            </button>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={dismiss}
        className="absolute right-1 top-1 rounded-md p-1 text-foreground/50 opacity-0 transition-opacity hover:text-foreground focus:opacity-100 focus:outline-none focus:ring-1 group-hover:opacity-100"
        aria-label="关闭通知"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
})

ToastItem.displayName = "ToastItem"

export function ToastContainer() {
  const toasts = useStore((state) => state.toasts)
  const removeToast = useStore((state) => state.removeToast)

  return (
    <div
      className="fixed bottom-0 right-0 z-50 flex max-h-screen w-full flex-col-reverse p-4 sm:bottom-0 sm:right-0 sm:top-auto sm:flex-col md:max-w-[420px]"
      role="region"
      aria-label="通知"
      aria-relevant="additions text"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} removeToast={removeToast} />
      ))}
    </div>
  )
}
