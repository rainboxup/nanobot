import { useEffect, useMemo, useState } from "react"
import { Download, RefreshCw, ShieldAlert } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Input } from "@/src/components/ui/input"
import { Badge } from "@/src/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

const MAX_UNLOCK_BATCH = 100

function formatDateTime(value: any): string {
  try {
    return new Date(value).toLocaleString()
  } catch {
    return ""
  }
}

function formatRetry(seconds: any): string {
  const total = Math.max(0, Number(seconds || 0))
  if (!total) return "-"
  if (total < 60) return `${total}s`
  const mins = Math.floor(total / 60)
  const secs = total % 60
  if (!secs) return `${mins}m`
  return `${mins}m ${secs}s`
}

export function Security() {
  const { user, addToast } = useStore()
  const isOwner = String(user?.role || "").toLowerCase() === "owner"

  const [lockUsername, setLockUsername] = useState("")
  const [lockIp, setLockIp] = useState("")
  const [lockScope, setLockScope] = useState("")
  const [lockView, setLockView] = useState<"active" | "all">("active")
  const [lockLimit, setLockLimit] = useState("100")
  const [unlockReason, setUnlockReason] = useState("")
  const [locksLoading, setLocksLoading] = useState(false)
  const [lockError, setLockError] = useState("")
  const [lockSummary, setLockSummary] = useState("")
  const [lockRows, setLockRows] = useState<any[]>([])
  const [selectedLockKeys, setSelectedLockKeys] = useState<Set<string>>(new Set())

  const [eventFilter, setEventFilter] = useState("")
  const [actorFilter, setActorFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")
  const [metaMode, setMetaMode] = useState("")
  const [metaUsername, setMetaUsername] = useState("")
  const [metaReason, setMetaReason] = useState("")
  const [metaSubjectKey, setMetaSubjectKey] = useState("")
  const [eventLimit, setEventLimit] = useState("100")
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState("")
  const [eventsRows, setEventsRows] = useState<any[]>([])
  const [nextBeforeTs, setNextBeforeTs] = useState("")

  const [retentionLoading, setRetentionLoading] = useState(false)
  const [retentionSummary, setRetentionSummary] = useState("")

  const lockedKeys = useMemo(() => {
    return (lockRows || [])
      .filter((x) => Boolean(x && x.locked))
      .map((x) => String(x.subject_key || ""))
      .filter(Boolean)
  }, [lockRows])

  const selectedLockedCount = useMemo(() => {
    const locked = new Set(lockedKeys)
    let count = 0
    for (const k of selectedLockKeys) {
      if (locked.has(k)) count += 1
    }
    return count
  }, [lockedKeys, selectedLockKeys])

  function requireReason(): string | null {
    const text = String(unlockReason || "").trim()
    if (!text) {
      setLockError("请填写解锁原因。")
      return null
    }
    return text
  }

  async function loadLocks() {
    if (!isOwner) return
    if (locksLoading) return
    setLocksLoading(true)
    setLockError("")

    const includeUnlocked = lockView === "all"
    const limit = Math.max(1, Math.min(500, Number(lockLimit || 100)))

    const params = new URLSearchParams()
    params.set("limit", String(limit))
    if (includeUnlocked) params.set("include_unlocked", "true")
    if (lockUsername.trim()) params.set("username", lockUsername.trim())
    if (lockIp.trim()) params.set("ip", lockIp.trim())
    if (lockScope.trim()) params.set("scope", lockScope.trim())

    try {
      const snapshot = await api.get<any>(`/api/security/login-locks?${params.toString()}`)
      const items = Array.isArray(snapshot?.items) ? snapshot.items : []
      setLockRows(items)
      setSelectedLockKeys((prev) => {
        const valid = new Set(items.filter((x: any) => Boolean(x && x.locked)).map((x: any) => String(x.subject_key || "")))
        return new Set([...prev].filter((k) => valid.has(k)))
      })
      const activeCount = Number(snapshot?.active_lock_count || 0)
      const subjectCount = Number(snapshot?.subject_count || 0)
      const generatedAt = formatDateTime(snapshot?.generated_at || "")
      setLockSummary(
        `当前锁定：${activeCount}；跟踪主体：${subjectCount}${generatedAt ? `；更新时间：${generatedAt}` : ""}`
      )
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      if (msg.toLowerCase().includes("insufficient role")) {
        setLockError("登录锁定状态仅对 Owner 角色开放。")
      } else {
        setLockError(msg)
      }
      setLockRows([])
      setLockSummary("")
    } finally {
      setLocksLoading(false)
    }
  }

  async function unlockOne(subjectKey: string) {
    const reason = requireReason()
    if (!reason) return
    try {
      await api.post("/api/security/login-locks/unlock", { subject_key: subjectKey, reason })
      addToast({ type: "success", message: "已解锁" })
      setSelectedLockKeys((prev) => {
        const next = new Set(prev)
        next.delete(subjectKey)
        return next
      })
      await loadLocks()
      focusUnlockAudit({ subjectKey, reason, mode: "single" })
      await loadEvents(false)
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "解锁失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function unlockSelected() {
    const reason = requireReason()
    if (!reason) return
    const locked = new Set(lockedKeys)
    const subjectKeys = [...selectedLockKeys].filter((k) => locked.has(k))
    if (!subjectKeys.length) {
      setLockError("未选择任何锁定主体。")
      return
    }
    try {
      for (let i = 0; i < subjectKeys.length; i += MAX_UNLOCK_BATCH) {
        const chunk = subjectKeys.slice(i, i + MAX_UNLOCK_BATCH)
        await api.post("/api/security/login-locks/unlock-batch", { subject_keys: chunk, reason })
      }
      addToast({ type: "success", message: `已批量解锁（${subjectKeys.length}）` })
      setSelectedLockKeys(new Set())
      await loadLocks()
      focusUnlockAudit({ reason, mode: "batch" })
      await loadEvents(false)
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "批量解锁失败")
      addToast({ type: "error", message: msg })
    }
  }

  function focusUnlockAudit({
    subjectKey = "",
    reason = "",
    mode = "",
  }: { subjectKey?: string; reason?: string; mode?: string } = {}) {
    setEventFilter("security.login_lock.unlock")
    setStatusFilter("succeeded")
    if (subjectKey) setMetaSubjectKey(subjectKey)
    if (reason) setMetaReason(reason)
    if (mode) setMetaMode(mode)
    setNextBeforeTs("")
  }

  function buildEventParams(append: boolean) {
    const limit = Math.max(1, Math.min(500, Number(eventLimit || 100)))
    const params = new URLSearchParams()
    params.set("limit", String(limit))
    if (eventFilter.trim()) params.set("event", eventFilter.trim())
    if (actorFilter.trim()) params.set("actor", actorFilter.trim())
    if (statusFilter.trim()) params.set("status", statusFilter.trim())
    if (metaMode.trim()) params.set("meta_mode", metaMode.trim())
    if (metaUsername.trim()) params.set("meta_username", metaUsername.trim())
    if (metaReason.trim()) params.set("meta_reason", metaReason.trim())
    if (metaSubjectKey.trim()) params.set("meta_subject_key", metaSubjectKey.trim())
    if (append && nextBeforeTs) params.set("before", nextBeforeTs)
    return { params, limit }
  }

  async function loadEvents(append: boolean) {
    if (!isOwner) return
    if (eventsLoading) return
    setEventsLoading(true)
    setEventsError("")
    try {
      const { params, limit } = buildEventParams(append)
      const pageRows = await api.get<any[]>(`/api/audit/events?${params.toString()}`)
      const nextRows = append ? eventsRows.concat(pageRows || []) : pageRows || []
      setEventsRows(nextRows)
      const nextBefore = nextRows.length ? String(nextRows[nextRows.length - 1].ts || "") : ""
      setNextBeforeTs(nextBefore)
      const hasMore = (pageRows || []).length >= limit && Boolean(nextBefore)
      if (!hasMore) {
        // keep nextBeforeTs for export, but disable button via hasMore
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      if (msg.toLowerCase().includes("insufficient role")) {
        setEventsError("安全事件仅对 Owner 角色开放。")
      } else {
        setEventsError(msg)
      }
      if (!append) setEventsRows([])
    } finally {
      setEventsLoading(false)
    }
  }

  async function exportEventsCsv() {
    if (!isOwner) return
    try {
      const { params } = buildEventParams(false)
      const res = await api.fetch(`/api/audit/events/export?${params.toString()}`)
      const blob = await res.blob()

      const stamp = new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-")
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = `audit-events-${stamp}.csv`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      const msg = String((err as any)?.message || "导出失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function loadRetention() {
    if (!isOwner) return
    if (retentionLoading) return
    setRetentionLoading(true)
    try {
      const status = await api.get<any>("/api/audit/retention")
      const enabled = Boolean(status?.enabled)
      const days = Number(status?.retention_days || 0)
      const lastRunAt = formatDateTime(status?.last_run_at || "")
      const lastPruned = Number(status?.pruned_lines || 0)
      setRetentionSummary(
        enabled
          ? `清理策略：保留 ${days} 天${lastRunAt ? `；上次运行：${lastRunAt}` : ""}${lastRunAt ? `；清理行数：${lastPruned}` : ""}`
          : "清理策略已禁用。"
      )
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setRetentionSummary(msg)
    } finally {
      setRetentionLoading(false)
    }
  }

  async function runRetentionNow() {
    if (!isOwner) return
    if (retentionLoading) return
    setRetentionLoading(true)
    try {
      await api.post("/api/audit/retention/run", {})
      addToast({ type: "success", message: "已触发清理" })
      await Promise.all([loadRetention(), loadEvents(false)])
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "执行失败")
      addToast({ type: "error", message: msg })
    } finally {
      setRetentionLoading(false)
    }
  }

  useEffect(() => {
    if (!isOwner) return
    loadLocks().catch(() => {})
    loadEvents(false).catch(() => {})
    loadRetention().catch(() => {})
  }, [isOwner])

  if (!user || !isOwner) {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] flex-col items-center justify-center p-4">
        <div className="flex max-w-md flex-col items-center space-y-4 text-center">
          <div className="rounded-full bg-destructive/10 p-4">
            <ShieldAlert className="h-12 w-12 text-destructive" />
          </div>
          <h2 className="text-2xl font-bold tracking-tight">无访问权限</h2>
          <p className="text-muted-foreground">安全与审计仅对 Owner 角色开放。</p>
        </div>
      </div>
    )
  }

  const totalLocked = lockedKeys.length
  const selectAllChecked = totalLocked > 0 && selectedLockedCount === totalLocked
  const selectAllIndeterminate = selectedLockedCount > 0 && selectedLockedCount < totalLocked

  return (
    <div className="space-y-10">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">安全与审计</h2>
            <p className="text-muted-foreground">登录防护与审计观测面板（Owner）。</p>
          </div>
          <Button variant="outline" onClick={() => Promise.all([loadLocks(), loadEvents(false), loadRetention()]).catch(() => {})}>
            <RefreshCw className="mr-2 h-4 w-4" /> 刷新
          </Button>
        </div>
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <div className="text-lg font-semibold">登录锁定状态</div>
            <div className="text-sm text-muted-foreground">{lockSummary}</div>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Input placeholder="用户名（精确）" value={lockUsername} onChange={(e) => setLockUsername(e.target.value)} className="w-[160px]" />
            <Input placeholder="IP（精确）" value={lockIp} onChange={(e) => setLockIp(e.target.value)} className="w-[140px]" />
            <select
              className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              value={lockScope}
              onChange={(e) => setLockScope(e.target.value)}
            >
              <option value="">全部范围</option>
              <option value="user_ip">用户+IP</option>
              <option value="ip">IP</option>
            </select>
            <select
              className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              value={lockView}
              onChange={(e) => setLockView(e.target.value as any)}
            >
              <option value="active">仅显示锁定</option>
              <option value="all">全部主体</option>
            </select>
            <select
              className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              value={lockLimit}
              onChange={(e) => setLockLimit(e.target.value)}
            >
              <option value="50">50 行</option>
              <option value="100">100 行</option>
              <option value="200">200 行</option>
            </select>
            <Input placeholder="解锁原因（必填）" value={unlockReason} onChange={(e) => setUnlockReason(e.target.value)} className="w-[220px]" />
            <Button onClick={() => unlockSelected().catch(() => {})} disabled={locksLoading || selectedLockedCount === 0}>
              解锁选中（{selectedLockedCount}）
            </Button>
            <Button variant="outline" onClick={() => loadLocks().catch(() => {})} disabled={locksLoading}>
              刷新
            </Button>
          </div>
        </div>

        {lockError && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {lockError}
          </div>
        )}
        {selectedLockedCount > MAX_UNLOCK_BATCH && (
          <div className="text-xs text-muted-foreground">
            已选择 {selectedLockedCount} 项，提交时会按 {MAX_UNLOCK_BATCH} 条/批自动分批解锁。
          </div>
        )}

        <div className="rounded-md border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>
                  <input
                    type="checkbox"
                    checked={selectAllChecked}
                    ref={(el) => {
                      if (el) el.indeterminate = selectAllIndeterminate
                    }}
                    onChange={(e) => {
                      const shouldSelect = Boolean(e.target.checked)
                      if (!shouldSelect) {
                        setSelectedLockKeys(new Set())
                        return
                      }
                      setSelectedLockKeys(new Set(lockedKeys))
                    }}
                    disabled={locksLoading || totalLocked === 0}
                  />
                </TableHead>
                <TableHead>范围</TableHead>
                <TableHead>主体</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>重试</TableHead>
                <TableHead>失败次数</TableHead>
                <TableHead>最近失败</TableHead>
                <TableHead>锁定到</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {locksLoading ? (
                <TableRow>
                  <TableCell colSpan={9} className="h-24 text-center text-muted-foreground">
                    加载中...
                  </TableCell>
                </TableRow>
              ) : lockRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="h-24 text-center text-muted-foreground">
                    当前筛选条件下未找到锁定主体。
                  </TableCell>
                </TableRow>
              ) : (
                lockRows.map((item) => {
                  const subjectKey = String(item?.subject_key || "")
                  const isLocked = Boolean(item?.locked)
                  const checked = isLocked && selectedLockKeys.has(subjectKey)
                  const scopeText =
                    item?.scope === "user_ip" ? "用户+IP" : item?.scope === "ip" ? "IP" : String(item?.scope || "")
                  let subject = "-"
                  if (item?.scope === "user_ip") subject = `${item?.username || "-"}@${item?.ip || "-"}`
                  else if (item?.scope === "ip") subject = String(item?.ip || "-")
                  else subject = subjectKey || "-"

                  return (
                    <TableRow key={subjectKey || `${scopeText}:${subject}`}>
                      <TableCell>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!isLocked}
                          onChange={(e) => {
                            const should = Boolean(e.target.checked)
                            setSelectedLockKeys((prev) => {
                              const next = new Set(prev)
                              if (should) next.add(subjectKey)
                              else next.delete(subjectKey)
                              return next
                            })
                          }}
                        />
                      </TableCell>
                      <TableCell>{scopeText}</TableCell>
                      <TableCell className="font-mono text-xs">{subject}</TableCell>
                      <TableCell>{isLocked ? <Badge variant="warning">锁定</Badge> : "监控中"}</TableCell>
                      <TableCell className="font-mono text-xs">{formatRetry(item?.retry_after_s || 0)}</TableCell>
                      <TableCell className="font-mono text-xs">{String(item?.failure_count || 0)}</TableCell>
                      <TableCell className="font-mono text-xs">{formatDateTime(item?.last_failure_at || "")}</TableCell>
                      <TableCell className="font-mono text-xs">{formatDateTime(item?.locked_until || "")}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => unlockOne(subjectKey).catch(() => {})}
                          disabled={!isLocked || locksLoading}
                        >
                          解锁
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div>
            <div className="text-lg font-semibold">安全事件</div>
            <div className="text-sm text-muted-foreground">最新审计事件</div>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm text-muted-foreground">{retentionSummary}</div>
            <Button variant="outline" onClick={() => runRetentionNow().catch(() => {})} disabled={retentionLoading}>
              立即运行清理
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Input placeholder="事件包含..." value={eventFilter} onChange={(e) => setEventFilter(e.target.value)} className="w-[190px]" />
          <Input placeholder="操作者（精确）" value={actorFilter} onChange={(e) => setActorFilter(e.target.value)} className="w-[160px]" />
          <select
            className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">任意状态</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
            <option value="blocked">被拦截</option>
          </select>
          <select
            className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            value={metaMode}
            onChange={(e) => setMetaMode(e.target.value)}
          >
            <option value="">任意模式</option>
            <option value="single">单条</option>
            <option value="batch">批量</option>
          </select>
          <Input placeholder="用户名（精确）" value={metaUsername} onChange={(e) => setMetaUsername(e.target.value)} className="w-[180px]" />
          <Input placeholder="原因包含..." value={metaReason} onChange={(e) => setMetaReason(e.target.value)} className="w-[190px]" />
          <Input placeholder="subject_key（精确）" value={metaSubjectKey} onChange={(e) => setMetaSubjectKey(e.target.value)} className="w-[220px]" />
          <select
            className="h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            value={eventLimit}
            onChange={(e) => setEventLimit(e.target.value)}
          >
            <option value="50">50 行</option>
            <option value="100">100 行</option>
            <option value="200">200 行</option>
          </select>
          <Button onClick={() => { setNextBeforeTs(""); loadEvents(false).catch(() => {}) }} disabled={eventsLoading}>
            应用
          </Button>
          <Button variant="outline" onClick={() => exportEventsCsv().catch(() => {})} disabled={eventsLoading}>
            <Download className="mr-2 h-4 w-4" /> 导出 CSV
          </Button>
          <Button
            variant="outline"
            onClick={() => loadEvents(true).catch(() => {})}
            disabled={eventsLoading || !nextBeforeTs}
            title={!nextBeforeTs ? "没有更多数据" : "加载更早"}
          >
            加载更早
          </Button>
        </div>

        {eventsError && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {eventsError}
          </div>
        )}

        <div className="rounded-md border bg-card">
          <Table className="text-xs sm:text-sm">
            <TableHeader>
              <TableRow className="bg-muted/50">
                <TableHead className="w-[200px]">时间</TableHead>
                <TableHead>事件</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>操作者</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>元数据</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {eventsLoading && eventsRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                    加载中...
                  </TableCell>
                </TableRow>
              ) : eventsRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                    当前筛选条件下未找到审计事件。
                  </TableCell>
                </TableRow>
              ) : (
                eventsRows.map((row, idx) => (
                  <TableRow key={`${String(row?.ts || "")}::${idx}`}>
                    <TableCell className="font-mono text-muted-foreground">{formatDateTime(row?.ts || "")}</TableCell>
                    <TableCell>
                      <span className="font-mono">{String(row?.event || "")}</span>
                    </TableCell>
                    <TableCell>{String(row?.status || "")}</TableCell>
                    <TableCell className="font-mono">{String(row?.actor || "-")}</TableCell>
                    <TableCell className="font-mono">{String(row?.ip || "-")}</TableCell>
                    <TableCell className="font-mono text-xs">{JSON.stringify(row?.metadata || {})}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}
