import { useEffect, useMemo, useState } from "react"
import { CheckCircle2, Copy, Plus, Trash2, XCircle } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Input } from "@/src/components/ui/input"
import { Badge } from "@/src/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

interface AllowlistResponse {
  closed_beta: boolean
  count: number
  users: string[]
}

interface InviteItem {
  code: string
  for_username?: string | null
  used_count?: number
  max_uses?: number
  expires_at?: string | null
  active?: boolean
  note?: string | null
}

export function Beta() {
  const { addToast } = useStore()

  const [activeTab, setActiveTab] = useState<"invites" | "allowlist">("invites")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const [allowlist, setAllowlist] = useState<AllowlistResponse | null>(null)
  const [invites, setInvites] = useState<InviteItem[]>([])

  const [allowUserInput, setAllowUserInput] = useState("")
  const [inviteForUser, setInviteForUser] = useState("")
  const [inviteTtlHours, setInviteTtlHours] = useState("72")
  const [inviteMaxUses, setInviteMaxUses] = useState("1")

  async function load() {
    setLoading(true)
    setError("")
    try {
      const [allow, inv] = await Promise.all([
        api.get<AllowlistResponse>("/api/beta/allowlist"),
        api.get<InviteItem[]>("/api/beta/invites"),
      ])
      setAllowlist(allow)
      setInvites(Array.isArray(inv) ? inv : [])
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setError(msg)
      setAllowlist(null)
      setInvites([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [])

  const allowUsers = useMemo(() => {
    const list = Array.isArray(allowlist?.users) ? allowlist!.users : []
    return [...list].sort((a, b) => String(a).localeCompare(String(b)))
  }, [allowlist])

  const handleCopy = (code: string) => {
    navigator.clipboard.writeText(code)
    addToast({ type: "success", message: "邀请码已复制" })
  }

  async function addAllowlistUser() {
    const username = String(allowUserInput || "").trim()
    if (!username) return
    try {
      const res = await api.post<any>("/api/beta/allowlist", { username })
      setAllowUserInput("")
      setAllowlist((prev) => (prev ? { ...prev, users: res.users || prev.users, count: res.count ?? prev.count } : prev))
      addToast({ type: "success", message: "已添加到白名单" })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function removeAllowlistUser(username: string) {
    try {
      const res = await api.delete<any>(`/api/beta/allowlist/${encodeURIComponent(username)}`)
      setAllowlist((prev) => (prev ? { ...prev, users: res.users || prev.users, count: res.count ?? prev.count } : prev))
      addToast({ type: "success", message: "已移除白名单" })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function createInvite() {
    const forUsername = String(inviteForUser || "").trim()
    const ttlHours = Math.max(1, Number(inviteTtlHours || 72))
    const maxUses = Math.max(1, Number(inviteMaxUses || 1))
    try {
      const payload: any = { ttl_hours: ttlHours, max_uses: maxUses }
      if (forUsername) payload.for_username = forUsername
      await api.post("/api/beta/invites", payload)
      addToast({ type: "success", message: "邀请码已创建" })
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "创建失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function revokeInvite(code: string) {
    try {
      await api.delete(`/api/beta/invites/${encodeURIComponent(code)}`)
      addToast({ type: "success", message: "邀请码已撤销" })
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "撤销失败")
      addToast({ type: "error", message: msg })
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">封闭 Beta</h2>
          <p className="text-muted-foreground">管理邀请码与白名单用户。</p>
          {allowlist && (
            <div className="mt-2 text-sm text-muted-foreground">
              模式：{" "}
              <Badge variant={allowlist.closed_beta ? "warning" : "secondary"}>
                {allowlist.closed_beta ? "已启用" : "未启用"}
              </Badge>
            </div>
          )}
        </div>
        <Button variant="outline" onClick={() => load().catch(() => {})} disabled={loading}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <div className="flex space-x-1 rounded-lg bg-muted p-1 w-fit">
        <button
          onClick={() => setActiveTab("invites")}
          className={`inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
            activeTab === "invites" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:bg-muted/80"
          }`}
        >
          邀请码
        </button>
        <button
          onClick={() => setActiveTab("allowlist")}
          className={`inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
            activeTab === "allowlist"
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:bg-muted/80"
          }`}
        >
          白名单
        </button>
      </div>

      <div className="rounded-md border bg-card">
        {activeTab === "invites" ? (
          <div className="p-4 space-y-4">
            <div className="flex flex-col sm:flex-row gap-2 sm:items-end sm:justify-between">
              <div className="flex flex-wrap gap-2">
                <div className="space-y-1">
                  <div className="text-xs text-muted-foreground">目标用户名（可选）</div>
                  <Input value={inviteForUser} onChange={(e) => setInviteForUser(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-muted-foreground">TTL（小时）</div>
                  <Input type="number" min={1} value={inviteTtlHours} onChange={(e) => setInviteTtlHours(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <div className="text-xs text-muted-foreground">最大使用次数</div>
                  <Input type="number" min={1} value={inviteMaxUses} onChange={(e) => setInviteMaxUses(e.target.value)} />
                </div>
              </div>
              <Button onClick={() => createInvite().catch(() => {})} disabled={loading}>
                <Plus className="mr-2 h-4 w-4" /> 创建邀请码
              </Button>
            </div>

            <div className="text-sm text-muted-foreground">将邀请码发送给用户；用户可使用 invite_code 登录一次以加入白名单。</div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>邀请码</TableHead>
                  <TableHead>目标用户</TableHead>
                  <TableHead>使用次数</TableHead>
                  <TableHead>过期时间</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(invites || []).length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                      暂无邀请码
                    </TableCell>
                  </TableRow>
                ) : (
                  invites.map((item) => (
                    <TableRow key={item.code}>
                      <TableCell className="font-mono font-medium">{item.code}</TableCell>
                      <TableCell>{item.for_username || "-"}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {Number(item.used_count || 0)} / {Number(item.max_uses || 1)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{item.expires_at || "-"}</TableCell>
                      <TableCell>
                        <Badge variant={item.active ? "success" : "secondary"}>
                          {item.active ? "有效" : "已失效"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end space-x-2">
                          <Button variant="ghost" size="sm" onClick={() => handleCopy(item.code)}>
                            <Copy className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            onClick={() => revokeInvite(item.code).catch(() => {})}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="p-4 space-y-4">
            <div className="flex flex-col sm:flex-row gap-2 sm:items-end sm:justify-between">
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">用户名</div>
                <Input
                  value={allowUserInput}
                  onChange={(e) => setAllowUserInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addAllowlistUser().catch(() => {})
                  }}
                  placeholder="例如：admin"
                />
              </div>
              <Button onClick={() => addAllowlistUser().catch(() => {})} disabled={loading}>
                <Plus className="mr-2 h-4 w-4" /> 添加白名单
              </Button>
            </div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户名</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {allowUsers.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={3} className="h-24 text-center text-muted-foreground">
                      暂无白名单用户
                    </TableCell>
                  </TableRow>
                ) : (
                  allowUsers.map((username) => (
                    <TableRow key={username}>
                      <TableCell className="font-mono font-medium">{username}</TableCell>
                      <TableCell>
                        <Badge variant="success">
                          <CheckCircle2 className="mr-1 h-3 w-3" /> active
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => removeAllowlistUser(username).catch(() => {})}
                          title="移除白名单"
                        >
                          <XCircle className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}

