/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { HashRouter as Router, Routes, Route, Navigate } from "react-router-dom"
import { useEffect } from "react"
import { Layout } from "./components/layout/Layout"
import { Login } from "./pages/Login"
import { Chat } from "./pages/Chat"
import { Settings, SettingsIndex } from "./pages/Settings"
import { Providers } from "./pages/settings/Providers"
import { Channels } from "./pages/settings/Channels"
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
        const me = await api.get<{ username: string; tenant_id: string; role: string }>("/api/auth/me")
        if (!cancelled) {
          setUser({
            username: String(me.username || ""),
            role: String(me.role || "member") as any,
            tenant_id: String(me.tenant_id || ""),
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
            <Route path="providers" element={<Providers />} />
            <Route path="channels" element={<Channels />} />
            <Route path="beta" element={<Beta />} />
            <Route path="users" element={<Users />} />
            <Route path="security" element={<Security />} />
          </Route>
          
          <Route path="skills" element={<Skills />} />
          <Route path="ops" element={<Ops />} />
        </Route>
      </Routes>
    </Router>
  )
}
