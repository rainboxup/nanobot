import * as React from "react"
import { X } from "lucide-react"

interface DrawerProps {
  isOpen: boolean
  onClose: () => void
  title: string
  description?: string
  children: React.ReactNode
  footer?: React.ReactNode
}

export function Drawer({ isOpen, onClose, title, description, children, footer }: DrawerProps) {
  if (!isOpen) return null

  const titleId = React.useId()
  const descriptionId = React.useId()
  const dialogRef = React.useRef<HTMLDivElement>(null)
  const closeRef = React.useRef<HTMLButtonElement>(null)
  const lastActiveRef = React.useRef<HTMLElement | null>(null)

  React.useEffect(() => {
    lastActiveRef.current = document.activeElement as HTMLElement | null

    const t = window.setTimeout(() => {
      closeRef.current?.focus()
    }, 0)

    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"

    const getFocusable = () => {
      const root = dialogRef.current
      if (!root) return []
      const nodes = root.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'
      )
      return Array.from(nodes).filter((el) => !el.hasAttribute("disabled") && el.tabIndex !== -1)
    }

    const rootContains = (node: HTMLElement | null) => {
      const root = dialogRef.current
      if (!root || !node) return false
      return root.contains(node)
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== "Tab") return
      const focusables = getFocusable()
      if (!focusables.length) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (e.shiftKey) {
        if (active === first || !rootContains(active)) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (active === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    document.addEventListener("keydown", onKeyDown, true)

    return () => {
      window.clearTimeout(t)
      document.body.style.overflow = prevOverflow
      document.removeEventListener("keydown", onKeyDown, true)
      try {
        lastActiveRef.current?.focus()
      } catch {
        // ignore focus restore failures
      }
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm animate-in fade-in-0"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className="relative w-full max-w-md h-full border-l bg-background p-6 shadow-lg animate-in slide-in-from-right-full flex flex-col"
      >
        <button
          onClick={onClose}
          type="button"
          ref={closeRef}
          className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:pointer-events-none data-[state=open]:bg-accent data-[state=open]:text-muted-foreground"
        >
          <X className="h-4 w-4" />
          <span className="sr-only">Close</span>
        </button>
        <div className="flex flex-col space-y-1.5 text-left mb-6">
          <h2 id={titleId} className="text-lg font-semibold leading-none tracking-tight">
            {title}
          </h2>
          {description && (
            <p id={descriptionId} className="text-sm text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        <div className="flex-1 overflow-y-auto py-4">{children}</div>
        {footer && <div className="flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 mt-6 pt-4 border-t">{footer}</div>}
      </div>
    </div>
  )
}
