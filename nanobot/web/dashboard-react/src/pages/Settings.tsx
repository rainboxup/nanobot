import { Outlet, NavLink, Navigate } from "react-router-dom"
import { cn } from "@/src/lib/utils"
import { Database, Network, Users, Shield, Key } from "lucide-react"
import { useStore } from "@/src/store/useStore"

function allowedSettingsTabs(
  role: string,
  isBetaAdmin: boolean
): Array<"providers" | "channels" | "beta" | "users" | "security"> {
  const r = String(role || "").toLowerCase()
  const isOwner = r === "owner"
  const isAdmin = r === "admin" || isOwner

  const tabs: Array<"providers" | "channels" | "beta" | "users" | "security"> = ["users"]
  if (isAdmin) {
    tabs.unshift("channels")
    tabs.unshift("providers")
  }
  if (isOwner && isBetaAdmin) {
    tabs.push("beta")
  }
  if (isOwner) {
    tabs.push("security")
  }
  return tabs
}

export function SettingsIndex() {
  const { lastSettingsPath, user } = useStore()
  const allowed = allowedSettingsTabs(user?.role || "member", Boolean(user?.is_beta_admin))
  const desired = (lastSettingsPath || "").trim() as any
  const target = (desired && allowed.includes(desired)) ? desired : allowed[0]
  return <Navigate to={`/settings/${target}`} replace />
}

export function Settings() {
  const { setLastSettingsPath, user } = useStore()
  const allowed = allowedSettingsTabs(user?.role || "member", Boolean(user?.is_beta_admin))

  const navItems = [
    ...(allowed.includes("providers") ? [{ name: "模型服务", path: "/settings/providers", icon: Database }] : []),
    ...(allowed.includes("channels") ? [{ name: "渠道管理", path: "/settings/channels", icon: Network }] : []),
    ...(allowed.includes("beta") ? [{ name: "封闭 Beta", path: "/settings/beta", icon: Key }] : []),
    { name: "用户与权限", path: "/settings/users", icon: Users },
    ...(allowed.includes("security") ? [{ name: "安全与审计", path: "/settings/security", icon: Shield }] : []),
  ]

  return (
    <div className="flex flex-col md:flex-row h-[calc(100vh-3.5rem)]">
      <aside className="w-full md:w-64 border-r bg-muted/20 p-4 md:p-6 flex-shrink-0 overflow-y-auto">
        <nav className="flex flex-col space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              onClick={() => setLastSettingsPath(item.path.split('/').pop() || 'providers')}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )
              }
            >
              <item.icon className="h-4 w-4" />
              {item.name}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-y-auto p-4 md:p-8 bg-background">
        <div className="mx-auto max-w-5xl">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
