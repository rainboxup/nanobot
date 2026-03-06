/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { HashRouter as Router, Routes, Route, Navigate } from "react-router-dom"
import { useEffect } from "react"
import { Layout } from "./components/layout/Layout"
import { Login } from "./pages/Login"
import { Chat } from "./pages/Chat"
import { Settings, SettingsIndex, getAllowedSettingsTabs, getDefaultSettingsTab, type SettingsTab } from "./pages/Settings"
import { Providers } from "./pages/settings/Providers"
import { Channels } from "./pages/settings/Channels"
import { ChannelsAdmin } from "./pages/settings/ChannelsAdmin"
import { ChannelsWorkspace } from "./pages/settings/ChannelsWorkspace"
import { Soul } from "./pages/settings/Soul"
import { ToolsPolicy } from "./pages/settings/ToolsPolicy"
import { Cron } from "./pages/settings/Cron"
import { Beta } from "./pages/settings/Beta"
import { Users } from "./pages/settings/Users"
import { Security } from "./pages/settings/Security"
import { Skills } from "./pages/Skills"
import { Ops } from "./pages/Ops"
import { useStore } from "./store/useStore"
import { api, clearAuthTokens, getAccessToken } from "./lib/api"

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, authReady } = useStore()
  if (!authReady) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-muted/40 px-4">
        <div className="text-sm text-muted-foreground">加载中...</div>
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

function SettingsRouteGuard({ tab, children }: { tab: SettingsTab; children: React.ReactNode }) {
  const { user } = useStore()
  const role = String(user?.role || "member")
  const isBetaAdmin = Boolean(user?.is_beta_admin)
  const allowed = getAllowedSettingsTabs(role, isBetaAdmin)
  if (allowed.includes(tab)) return <>{children}</>
  return <Navigate to={`/settings/${getDefaultSettingsTab(role, isBetaAdmin)}`} replace />
}

export default function App() {
  const { setUser, setAuthReady } = useStore()

  useEffect(() => {
    let cancelled = false

    async function bootstrap() {
      if (!getAccessToken()) {
        if (!cancelled) {
          setUser(null)
          setAuthReady(true)
        }
        return
      }

      try {
        const me = await api.get<{
          username: string
          tenant_id: string
          role: string
          is_beta_admin?: boolean
        }>("/api/auth/me")
        if (!cancelled) {
          setUser({
            username: String(me.username || ""),
            role: String(me.role || "member").toLowerCase() as any,
            tenant_id: String(me.tenant_id || ""),
            is_beta_admin: Boolean(me.is_beta_admin),
          })
        }
      } catch {
        clearAuthTokens()
        if (!cancelled) {
          setUser(null)
        }
      } finally {
        if (!cancelled) {
          setAuthReady(true)
        }
      }
    }

    bootstrap().catch(() => {})
    return () => {
      cancelled = true
    }
  }, [setAuthReady, setUser])

  return (
    <Router>
      <Routes>
        <Route path="/login" element={<Login />} />
        
        <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<Chat />} />
          
          <Route path="settings" element={<Settings />}>
            <Route index element={<SettingsIndex />} />
            <Route path="providers" element={<SettingsRouteGuard tab="providers"><Providers /></SettingsRouteGuard>} />
            <Route path="channels/*" element={<SettingsRouteGuard tab="channels"><Channels /></SettingsRouteGuard>}>
              <Route index element={<Navigate to="workspace" replace />} />
              <Route path="workspace" element={<ChannelsWorkspace />} />
              <Route path="admin" element={<ChannelsAdmin />} />
            </Route>
            <Route path="soul" element={<SettingsRouteGuard tab="soul"><Soul /></SettingsRouteGuard>} />
            <Route path="tools" element={<SettingsRouteGuard tab="tools"><ToolsPolicy /></SettingsRouteGuard>} />
            <Route path="cron" element={<SettingsRouteGuard tab="cron"><Cron /></SettingsRouteGuard>} />
            <Route path="beta" element={<SettingsRouteGuard tab="beta"><Beta /></SettingsRouteGuard>} />
            <Route path="users" element={<SettingsRouteGuard tab="users"><Users /></SettingsRouteGuard>} />
            <Route path="security" element={<SettingsRouteGuard tab="security"><Security /></SettingsRouteGuard>} />
          </Route>
          
          <Route path="skills" element={<Skills />} />
          <Route path="ops" element={<Ops />} />
        </Route>
      </Routes>
    </Router>
  )
}
