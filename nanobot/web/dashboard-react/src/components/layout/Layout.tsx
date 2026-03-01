import { Outlet } from "react-router-dom"
import { Header } from "./Header"
import { ToastContainer } from "@/src/components/ui/toast"
import { useStore } from "@/src/store/useStore"
import { useEffect } from "react"

export function Layout() {
  const { theme } = useStore()

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove("light", "dark")

    const media = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = (next: "light" | "dark") => {
      root.classList.remove("light", "dark")
      root.classList.add(next)
      try {
        root.style.colorScheme = next
      } catch {
        // ignore style failures
      }
    }

    if (theme === "system") {
      apply(media.matches ? "dark" : "light")
      const handler = (ev: MediaQueryListEvent) => {
        apply(ev.matches ? "dark" : "light")
      }
      try {
        media.addEventListener("change", handler)
        return () => media.removeEventListener("change", handler)
      } catch {
        // Safari < 14
        // eslint-disable-next-line deprecation/deprecation
        media.addListener(handler as any)
        // eslint-disable-next-line deprecation/deprecation
        return () => media.removeListener(handler as any)
      }
    }

    apply(theme)
  }, [theme])

  return (
    <div className="relative flex min-h-screen flex-col bg-background font-sans antialiased">
      <Header />
      <main className="flex-1">
        <Outlet />
      </main>
      <ToastContainer />
    </div>
  )
}
