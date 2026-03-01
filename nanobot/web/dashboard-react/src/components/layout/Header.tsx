import { useState, useRef, useEffect } from "react"
import { Link, useLocation } from "react-router-dom"
import { useStore } from "@/src/store/useStore"
import { cn } from "@/src/lib/utils"
import { Moon, Sun, Monitor, LogOut, User as UserIcon, Activity, Settings, ShieldAlert } from "lucide-react"
import { Button } from "@/src/components/ui/button"
import { Badge } from "@/src/components/ui/badge"
import { api, clearAuthTokens } from "@/src/lib/api"

export function Header() {
  const { theme, setTheme, user, setUser, systemStatus, setSystemStatus, addToast } = useStore()
  const isOwner = String(user?.role || "").toLowerCase() === "owner"
  const location = useLocation()
  const [showStatus, setShowStatus] = useState(false)
  const [showUserMenu, setShowUserMenu] = useState(false)
  const statusRef = useRef<HTMLDivElement>(null)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const [readySummary, setReadySummary] = useState<{ version?: string; warnings?: string[] } | null>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (statusRef.current && !statusRef.current.contains(event.target as Node)) {
        setShowStatus(false)
      }
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setShowUserMenu(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  useEffect(() => {
    let cancelled = false

    async function refreshReady() {
      try {
        const res = await fetch("/api/ready", { cache: "no-store" })
        const data = (await res.json().catch(() => null)) as any
        const status = String((data && data.status) || "").toLowerCase()
        const warnings = Array.isArray(data && data.warnings) ? data.warnings : []

        if (!cancelled) {
          setReadySummary({ version: String((data && data.version) || ""), warnings })
          if (!res.ok) {
            setSystemStatus("error")
          } else if (status === "ready" && warnings.length === 0) {
            setSystemStatus("ready")
          } else {
            setSystemStatus("warning")
          }
        }
      } catch {
        if (!cancelled) {
          setReadySummary(null)
          setSystemStatus("error")
        }
      }
    }

    refreshReady().catch(() => {})
    const timer = window.setInterval(() => {
      refreshReady().catch(() => {})
    }, 30_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [setSystemStatus])

  const navItems = [
    { name: "对话", path: "/chat" },
    { name: "设置", path: "/settings" },
    { name: "技能", path: "/skills" },
    ...(isOwner ? [{ name: "运维", path: "/ops" }] : []),
  ]

  const statusColors = {
    ready: "success",
    warning: "warning",
    error: "destructive",
  } as const

  return (
    <header className="sticky top-0 z-40 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-14 items-center px-4">
        <div className="mr-4 flex">
          <Link to="/" className="mr-6 flex items-center space-x-2">
            <span className="font-bold sm:inline-block">nanobot</span>
          </Link>
          <nav className="flex items-center space-x-6 text-sm font-medium">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  "transition-colors hover:text-foreground/80",
                  location.pathname.startsWith(item.path) ? "text-foreground" : "text-foreground/60"
                )}
              >
                {item.name}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex flex-1 items-center justify-end space-x-4">
          <div className="relative flex items-center space-x-2" ref={statusRef}>
            <Badge 
              variant={statusColors[systemStatus]} 
              className="cursor-pointer"
              onClick={() => setShowStatus(!showStatus)}
            >
              <Activity className="mr-1 h-3 w-3" />
              {systemStatus.toUpperCase()}
            </Badge>
            {showStatus && (
              <div className="absolute right-0 top-full mt-2 w-64 rounded-md border bg-popover p-4 text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95">
                <h4 className="font-medium mb-2 flex items-center gap-2">
                  <Activity className="h-4 w-4" /> 诊断信息摘要
                </h4>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Version</span>
                    <span className="font-mono">{readySummary?.version || "-"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Warnings</span>
                    <span className="font-mono">{readySummary?.warnings?.length ?? "-"}</span>
                  </div>
                </div>
                {isOwner ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full mt-4"
                    asChild
                    onClick={() => setShowStatus(false)}
                  >
                    <Link to="/ops">查看完整大盘</Link>
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" className="w-full mt-4" disabled title="仅 Owner 可用">
                    查看完整大盘
                  </Button>
                )}
              </div>
            )}
          </div>
          <nav className="flex items-center space-x-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                if (theme === 'light') setTheme('dark')
                else if (theme === 'dark') setTheme('system')
                else setTheme('light')
              }}
              title={`Theme: ${theme}`}
            >
              {theme === 'light' ? <Sun className="h-4 w-4" /> : theme === 'dark' ? <Moon className="h-4 w-4" /> : <Monitor className="h-4 w-4" />}
            </Button>
            {user ? (
              <div className="relative flex items-center space-x-2" ref={userMenuRef}>
                <Button 
                  variant="ghost" 
                  size="sm" 
                  className="gap-2"
                  onClick={() => setShowUserMenu(!showUserMenu)}
                >
                  <UserIcon className="h-4 w-4" />
                  <span className="hidden sm:inline-block">{user.username}</span>
                </Button>
                {showUserMenu && (
                  <div className="absolute right-0 top-full mt-2 w-48 rounded-md border bg-popover py-1 text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95">
                    <div className="px-4 py-2 border-b">
                      <p className="text-sm font-medium">{user.username}</p>
                      <p className="text-xs text-muted-foreground capitalize">{user.role}</p>
                    </div>
                    <div className="py-1">
                      <Link 
                        to="/settings/users" 
                        className="flex items-center px-4 py-2 text-sm hover:bg-muted"
                        onClick={() => setShowUserMenu(false)}
                      >
                        <Settings className="mr-2 h-4 w-4" />
                        个人设置
                      </Link>
                    </div>
                    <div className="border-t py-1">
                      <button 
                        className="flex w-full items-center px-4 py-2 text-sm text-destructive hover:bg-muted"
                        onClick={async () => {
                          try {
                            await api.post("/api/auth/logout", { revoke_all: true })
                          } catch {
                            // ignore best-effort logout failures
                          } finally {
                            clearAuthTokens()
                            setUser(null)
                            setShowUserMenu(false)
                            addToast({ type: "success", message: "已退出登录" })
                          }
                        }}
                      >
                        <LogOut className="mr-2 h-4 w-4" />
                        退出登录
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <Button variant="default" size="sm" asChild>
                <Link to="/login">Login</Link>
              </Button>
            )}
          </nav>
        </div>
      </div>
    </header>
  )
}
