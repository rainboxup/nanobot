import { useEffect, useMemo, useState } from "react"
import { KeyRound, LogOut, RefreshCw, ShieldAlert, Trash2, UserPlus } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Input } from "@/src/components/ui/input"
import { Badge } from "@/src/components/ui/badge"
import { Modal } from "@/src/components/ui/modal"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

type Role = "owner" | "admin" | "member"

interface UserRow {
  username: string
  tenant_id: string
  role: Role
  active: boolean
}

function roleLabel(role: string): string {
  const r = String(role || "").toLowerCase()
  if (r === "member") return "成员"
  if (r === "admin") return "管理员"
  if (r === "owner") return "拥有者"
  return String(role || "")
}

export function Users() {
  const { user, addToast } = useStore()

  const [rows, setRows] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const [createUsername, setCreateUsername] = useState("")
  const [createPassword, setCreatePassword] = useState("")
  const [createRole, setCreateRole] = useState<Role>("member")
  const [createTenant, setCreateTenant] = useState("")
  const [creating, setCreating] = useState(false)

  const [oldPwd, setOldPwd] = useState("")
  const [newPwd, setNewPwd] = useState("")
  const [newPwdConfirm, setNewPwdConfirm] = useState("")
  const [changingPwd, setChangingPwd] = useState(false)

  const [modal, setModal] = useState<null | { type: "reset" | "kick" | "delete"; username: string }>(null)
  const [modalPwd, setModalPwd] = useState("")
  const [modalPwdConfirm, setModalPwdConfirm] = useState("")
  const [modalReason, setModalReason] = useState("")
  const [modalBusy, setModalBusy] = useState(false)

  const actorRole = String(user?.role || "").toLowerCase()
  const isOwner = actorRole === "owner"
  const isAdmin = actorRole === "admin" || isOwner
  const actorUsername = String(user?.username || "").toLowerCase()
  const actorTenant = String(user?.tenant_id || "").toLowerCase()

  const roleOptions: Role[] = useMemo(() => {
    return isOwner ? ["member", "admin", "owner"] : ["member", "admin"]
  }, [isOwner])

  function canManageLifecycle(target: UserRow): boolean {
    const targetUsername = String(target.username || "").toLowerCase()
    if (!targetUsername || targetUsername === actorUsername) return false
    if (isOwner) return true
    const targetTenant = String(target.tenant_id || "").toLowerCase()
    const targetRole = String(target.role || "").toLowerCase()
    return targetTenant === actorTenant && targetRole === "member"
  }

  async function load() {
    if (!isAdmin) {
      setRows([])
      setError("用户管理仅对 Admin/Owner 角色开放。")
      return
    }

    setLoading(true)
    setError("")
    try {
      const list = await api.get<any[]>("/api/auth/users")
      const next: UserRow[] = (Array.isArray(list) ? list : []).map((item) => ({
        username: String(item.username || ""),
        tenant_id: String(item.tenant_id || ""),
        role: String(item.role || "member") as Role,
        active: Boolean(item.active),
      }))
      setRows(next)
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setError(msg)
      setRows([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [isAdmin])

  async function createUser() {
    if (!isAdmin) return
    if (creating) return
    setCreating(true)
    setError("")
    try {
      const username = String(createUsername || "").trim()
      const password = String(createPassword || "")
      const tenant = String(createTenant || "").trim()
      if (!username) throw new Error("请输入用户名")
      if (password.length < 6) throw new Error("密码至少 6 位")

      const payload: any = { username, password, role: createRole }
      if (tenant) payload.tenant_id = tenant
      await api.post("/api/auth/users", payload)
      setCreatePassword("")
      addToast({ type: "success", message: "用户已创建" })
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "创建失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setCreating(false)
    }
  }

  async function updateRole(targetUsername: string, nextRole: Role) {
    if (!isOwner) return
    setError("")
    try {
      await api.put(`/api/auth/users/${encodeURIComponent(targetUsername)}/role`, { role: nextRole })
      addToast({ type: "success", message: "角色已更新" })
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "更新失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    }
  }

  async function toggleStatus(target: UserRow) {
    if (!canManageLifecycle(target)) return
    setError("")
    try {
      const nextActive = !Boolean(target.active)
      await api.put(`/api/auth/users/${encodeURIComponent(target.username)}/status`, { active: nextActive })
      addToast({ type: "success", message: "状态已更新" })
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "更新失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    }
  }

  async function runModalAction() {
    if (!modal) return
    if (modalBusy) return
    setModalBusy(true)
    setError("")

    try {
      if (modal.type === "reset") {
        const pwd = String(modalPwd || "")
        const confirm = String(modalPwdConfirm || "")
        if (pwd.length < 6) throw new Error("密码至少 6 位")
        if (pwd !== confirm) throw new Error("两次输入的密码不一致")
        await api.post(`/api/auth/users/${encodeURIComponent(modal.username)}/reset-password`, {
          new_password: pwd,
        })
        addToast({ type: "success", message: "已重置密码" })
      }

      if (modal.type === "kick") {
        const reason = String(modalReason || "").trim()
        if (!reason) throw new Error("请填写原因")
        await api.post(`/api/auth/users/${encodeURIComponent(modal.username)}/sessions/revoke-all`, { reason })
        addToast({ type: "warning", message: "已强制全部退出" })
      }

      if (modal.type === "delete") {
        await api.delete(`/api/auth/users/${encodeURIComponent(modal.username)}`)
        addToast({ type: "success", message: "用户已删除" })
      }

      setModal(null)
      setModalPwd("")
      setModalPwdConfirm("")
      setModalReason("")
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setModalBusy(false)
    }
  }

  async function changeMyPassword() {
    if (changingPwd) return
    setChangingPwd(true)
    setError("")
    try {
      if (!oldPwd || !newPwd) throw new Error("请输入旧密码和新密码")
      if (newPwd.length < 6) throw new Error("新密码至少 6 位")
      if (newPwd !== newPwdConfirm) throw new Error("两次输入的新密码不一致")
      await api.post("/api/auth/change-password", { old_password: oldPwd, new_password: newPwd })
      setOldPwd("")
      setNewPwd("")
      setNewPwdConfirm("")
      addToast({ type: "success", message: "密码已更新" })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setChangingPwd(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">用户与权限</h2>
          <p className="text-muted-foreground">创建用户、管理角色与会话。</p>
        </div>
        <Button variant="outline" onClick={() => load().catch(() => {})} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {isAdmin && (
        <div className="rounded-md border bg-card p-4">
          <div className="flex items-center justify-between">
            <div className="font-medium">创建用户</div>
            <div className="text-sm text-muted-foreground">Admin 只能在自己的租户内创建用户。</div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Input
              placeholder="用户名"
              value={createUsername}
              onChange={(e) => setCreateUsername(e.target.value)}
              className="min-w-[160px]"
            />
            <Input
              placeholder="密码（>=6）"
              type="password"
              value={createPassword}
              onChange={(e) => setCreatePassword(e.target.value)}
              className="min-w-[180px]"
            />
            <select
              className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              value={createRole}
              onChange={(e) => setCreateRole(e.target.value as Role)}
            >
              {roleOptions.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
            <Input
              placeholder="租户ID（可选）"
              value={createTenant}
              onChange={(e) => setCreateTenant(e.target.value)}
              className="min-w-[160px]"
            />
            <Button onClick={() => createUser().catch(() => {})} disabled={creating}>
              <UserPlus className="mr-2 h-4 w-4" />
              {creating ? "创建中..." : "创建"}
            </Button>
          </div>
        </div>
      )}

      <div className="rounded-md border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead>租户</TableHead>
              <TableHead>角色</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                  加载中...
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                  暂无用户数据
                </TableCell>
              </TableRow>
            ) : (
              rows.map((r) => {
                const manageable = canManageLifecycle(r)
                const canEditRole = isOwner && String(r.username || "").toLowerCase() !== actorUsername
                return (
                  <TableRow key={r.username}>
                    <TableCell className="font-mono font-medium">{r.username}</TableCell>
                    <TableCell className="font-mono text-muted-foreground">{r.tenant_id || "-"}</TableCell>
                    <TableCell>
                      {canEditRole ? (
                        <select
                          className="h-8 rounded-md border border-input bg-background px-2 py-1 text-xs ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          value={r.role}
                          onChange={(e) => updateRole(r.username, e.target.value as Role).catch(() => {})}
                        >
                          {roleOptions.map((role) => (
                            <option key={role} value={role}>
                              {roleLabel(role)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="text-sm">{roleLabel(r.role)}</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={r.active ? "success" : "secondary"}>{r.active ? "启用" : "禁用"}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleStatus(r).catch(() => {})}
                          disabled={!manageable}
                          title={!manageable ? "无权限或不可操作" : r.active ? "禁用" : "启用"}
                        >
                          <ShieldAlert className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setModal({ type: "reset", username: r.username })}
                          disabled={!manageable}
                          title="重置密码"
                        >
                          <KeyRound className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setModal({ type: "kick", username: r.username })}
                          disabled={!manageable}
                          title="强制全部退出"
                        >
                          <LogOut className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setModal({ type: "delete", username: r.username })}
                          disabled={!manageable}
                          title="删除用户"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>

      <div className="rounded-md border bg-card p-4">
        <div className="font-medium">修改我的密码</div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Input
            placeholder="旧密码"
            type="password"
            value={oldPwd}
            onChange={(e) => setOldPwd(e.target.value)}
            className="min-w-[180px]"
          />
          <Input
            placeholder="新密码（>=6）"
            type="password"
            value={newPwd}
            onChange={(e) => setNewPwd(e.target.value)}
            className="min-w-[180px]"
          />
          <Input
            placeholder="确认新密码"
            type="password"
            value={newPwdConfirm}
            onChange={(e) => setNewPwdConfirm(e.target.value)}
            className="min-w-[180px]"
          />
          <Button variant="outline" onClick={() => changeMyPassword().catch(() => {})} disabled={changingPwd}>
            {changingPwd ? "更新中..." : "更新密码"}
          </Button>
        </div>
      </div>

      <Modal
        isOpen={modal !== null}
        onClose={() => {
          if (modalBusy) return
          setModal(null)
          setModalPwd("")
          setModalPwdConfirm("")
          setModalReason("")
        }}
        title={
          modal?.type === "reset"
            ? "重置密码"
            : modal?.type === "kick"
              ? "强制全部退出"
              : modal?.type === "delete"
                ? "删除用户"
                : "操作"
        }
        description={
          modal?.type === "reset"
            ? `用户：${modal?.username}`
            : modal?.type === "kick"
              ? `将撤销用户 ${modal?.username} 的所有会话（原因必填）`
              : modal?.type === "delete"
                ? `确定永久删除用户 ${modal?.username} 吗？此操作不可撤销。`
                : ""
        }
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                if (modalBusy) return
                setModal(null)
                setModalPwd("")
                setModalPwdConfirm("")
                setModalReason("")
              }}
            >
              取消
            </Button>
            <Button
              variant={modal?.type === "delete" ? "destructive" : "default"}
              onClick={() => runModalAction().catch(() => {})}
              disabled={modalBusy}
            >
              {modalBusy ? "处理中..." : "确认"}
            </Button>
          </>
        }
      >
        {modal?.type === "reset" && (
          <div className="space-y-3">
            <Input
              placeholder="新密码（>=6）"
              type="password"
              value={modalPwd}
              onChange={(e) => setModalPwd(e.target.value)}
            />
            <Input
              placeholder="确认新密码"
              type="password"
              value={modalPwdConfirm}
              onChange={(e) => setModalPwdConfirm(e.target.value)}
            />
          </div>
        )}

        {modal?.type === "kick" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-md bg-destructive/15 p-3 text-sm text-destructive">
              <ShieldAlert className="h-4 w-4" />
              这是一个高危操作，用户将立即失去访问权限。
            </div>
            <Input
              placeholder="踢出原因（必填）"
              value={modalReason}
              onChange={(e) => setModalReason(e.target.value)}
            />
          </div>
        )}
      </Modal>
    </div>
  )
}

