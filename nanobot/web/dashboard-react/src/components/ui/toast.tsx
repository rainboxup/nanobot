import * as React from "react"
import { useStore } from "@/src/store/useStore"
import { cn } from "@/src/lib/utils"
import { X, CheckCircle2, AlertCircle, Info, AlertTriangle } from "lucide-react"

function ToastItem({ toast, removeToast }: { toast: any, removeToast: (id: string) => void }) {
  const [isHovered, setIsHovered] = React.useState(false)
  const [isFocused, setIsFocused] = React.useState(false)

  React.useEffect(() => {
    if (toast.persist) return
    if (isHovered || isFocused) return
    const timer = setTimeout(() => {
      removeToast(toast.id)
    }, Math.max(1000, Number(toast.durationMs ?? 4000)))
    return () => clearTimeout(timer)
  }, [toast.id, toast.updatedAt, toast.durationMs, toast.persist, removeToast, isHovered, isFocused])

  const isAssertive = toast.type === "error" || toast.type === "warning"
  const role = isAssertive ? "alert" : "status"

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onFocusCapture={() => setIsFocused(true)}
      onBlurCapture={() => setIsFocused(false)}
      role={role}
      aria-atomic="true"
      className={cn(
        "group pointer-events-auto relative flex w-full items-center justify-between space-x-2 overflow-hidden rounded-md border p-4 pr-6 shadow-lg transition-all data-[swipe=cancel]:translate-x-0 data-[swipe=end]:translate-x-[var(--radix-toast-swipe-end-x)] data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[swipe=move]:transition-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[swipe=end]:animate-out data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full data-[state=open]:slide-in-from-top-full data-[state=open]:sm:slide-in-from-bottom-full mt-2",
        {
          "bg-background text-foreground border-border": toast.type === "info",
          "bg-green-50 text-green-900 border-green-200 dark:bg-green-900/20 dark:text-green-100 dark:border-green-900": toast.type === "success",
          "bg-red-50 text-red-900 border-red-200 dark:bg-red-900/20 dark:text-red-100 dark:border-red-900": toast.type === "error",
          "bg-yellow-50 text-yellow-900 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-100 dark:border-yellow-900": toast.type === "warning",
        }
      )}
    >
      <div className="flex items-start gap-3">
        {toast.type === "success" && <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" />}
        {toast.type === "error" && <AlertCircle className="h-5 w-5 text-red-600 dark:text-red-400" />}
        {toast.type === "warning" && <AlertTriangle className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />}
        {toast.type === "info" && <Info className="h-5 w-5 text-blue-600 dark:text-blue-400" />}
        
        <div className="flex flex-col gap-1">
          <div className="text-sm font-medium">{toast.message}</div>
          {toast.action && (
            <button
              onClick={toast.action.onClick}
              className="text-xs font-semibold underline underline-offset-2 hover:opacity-80 text-left"
            >
              {toast.action.label}
            </button>
          )}
        </div>
      </div>
      <button
        onClick={() => removeToast(toast.id)}
        className="absolute right-1 top-1 rounded-md p-1 text-foreground/50 opacity-0 transition-opacity hover:text-foreground focus:opacity-100 focus:outline-none focus:ring-1 group-hover:opacity-100"
        aria-label="关闭通知"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}

export function ToastContainer() {
  const { toasts, removeToast } = useStore()

  return (
    <div
      className="fixed bottom-0 right-0 z-50 flex max-h-screen w-full flex-col-reverse p-4 sm:bottom-0 sm:right-0 sm:top-auto sm:flex-col md:max-w-[420px]"
      aria-live="polite"
      aria-relevant="additions text"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} removeToast={removeToast} />
      ))}
    </div>
  )
}
