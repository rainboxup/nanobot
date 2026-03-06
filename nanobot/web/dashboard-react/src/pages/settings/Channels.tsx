import { Navigate, NavLink, Outlet, useLocation } from "react-router-dom"

import { cn } from "@/src/lib/utils"
import { useStore } from "@/src/store/useStore"

export function Channels() {
  const { user } = useStore()
  const location = useLocation()
  const role = String(user?.role || "member").toLowerCase()
  const isOwner = role === "owner"

  if (location.pathname === "/settings/channels" || location.pathname === "/settings/channels/") {
    return <Navigate to="/settings/channels/workspace" replace />
  }

  if (!isOwner && location.pathname.startsWith("/settings/channels/admin")) {
    return <Navigate to="/settings/channels/workspace" replace />
  }

  const tabs = [
    {
      label: "Workspace Routing",
      description: "Control Feishu and DingTalk inbound routing for the current workspace.",
      to: "/settings/channels/workspace",
    },
    ...(isOwner
      ? [
          {
            label: "Platform Admin",
            description: "Manage system channel credentials, toggles, and runtime status.",
            to: "/settings/channels/admin",
          },
        ]
      : []),
  ]

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Channels</h1>
        <p className="text-sm text-muted-foreground">
          Separate system channel connections from workspace routing so saved settings match runtime behavior.
        </p>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap gap-2 border-b pb-3">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground"
                )
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </div>

        <div className="text-sm text-muted-foreground">
          {tabs.find((tab) => location.pathname.startsWith(tab.to))?.description || tabs[0]?.description}
        </div>
      </div>

      <Outlet />
    </div>
  )
}
