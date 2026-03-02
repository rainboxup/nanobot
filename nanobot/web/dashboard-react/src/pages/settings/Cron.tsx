import { useEffect, useMemo, useState } from "react"
import { CalendarClock, PauseCircle, PlayCircle, ShieldAlert, Trash2 } from "lucide-react"

import { api, ApiError, formatTime } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Badge } from "@/src/components/ui/badge"
import { Button } from "@/src/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card"
import { Input } from "@/src/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

type ScheduleKind = "every" | "cron" | "at"

interface CronSchedule {
  kind: ScheduleKind
  at_ms?: number | null
  every_ms?: number | null
  expr?: string | null
  tz?: string | null
}

interface CronPayload {
  message: string
  deliver: boolean
  channel?: string | null
  to?: string | null
}

interface CronState {
  next_run_at_ms?: number | null
  last_run_at_ms?: number | null
  last_status?: "ok" | "error" | "skipped" | null
  last_error?: string | null
}

interface CronJob {
  id: string
  name: string
  enabled: boolean
  schedule: CronSchedule
  payload: CronPayload
  state: CronState
}

interface CronStatus {
  enabled: boolean
  jobs: number
  next_wake_at_ms?: number | null
  store_path?: string
  execution_available?: boolean
  execution_reason?: string
}

function apiError(err: unknown, fallback = "操作失败"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

function scheduleLabel(job: CronJob): string {
  const schedule = job.schedule || { kind: "every" }
  if (schedule.kind === "every") {
    const everyMs = Math.max(1, Math.floor(Number(schedule.every_ms || 0)))
    if (everyMs % 1000 === 0) {
      return `每 ${Math.floor(everyMs / 1000)}s`
    }
    return `每 ${everyMs}ms`
  }
  if (schedule.kind === "cron") {
    const expr = String(schedule.expr || "").trim() || "-"
    const tz = String(schedule.tz || "").trim()
    return tz ? `${expr} (${tz})` : expr
  }
  if (schedule.kind === "at") {
    return `单次 @ ${formatTime(Number(schedule.at_ms || 0))}`
  }
  return "-"
}

function stateBadge(job: CronJob) {
  const value = String(job.state?.last_status || "").toLowerCase()
  if (value === "ok") return <Badge variant="success">最近成功</Badge>
  if (value === "error") return <Badge variant="destructive">最近失败</Badge>
  if (value === "skipped") return <Badge variant="warning">最近跳过</Badge>
  return <Badge variant="secondary">未执行</Badge>
}

function parseDateTimeLocalToMs(value: string): number | null {
  const text = String(value || "").trim()
  if (!text) return null
  const dt = new Date(text)
  const ms = dt.getTime()
  if (!Number.isFinite(ms)) return null
  return Math.floor(ms)
}

export function Cron() {
  const { user, addToast } = useStore()
  const isOwner = String(user?.role || "").toLowerCase() === "owner"

  const [jobs, setJobs] = useState<CronJob[]>([])
  const [statusData, setStatusData] = useState<CronStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [creating, setCreating] = useState(false)
  const [actingJobId, setActingJobId] = useState("")

  const [name, setName] = useState("")
  const [message, setMessage] = useState("")
  const [scheduleKind, setScheduleKind] = useState<ScheduleKind>("every")
  const [everySeconds, setEverySeconds] = useState("3600")
  const [cronExpr, setCronExpr] = useState("0 9 * * *")
  const [timezone, setTimezone] = useState("")
  const [atLocal, setAtLocal] = useState("")
  const [deliver, setDeliver] = useState(false)
  const [channel, setChannel] = useState("")
  const [to, setTo] = useState("")

  const sortedJobs = useMemo(() => {
    return [...jobs].sort((a, b) => {
      const left = Number(a.state?.next_run_at_ms || Number.MAX_SAFE_INTEGER)
      const right = Number(b.state?.next_run_at_ms || Number.MAX_SAFE_INTEGER)
      return left - right
    })
  }, [jobs])

  async function reload(): Promise<boolean> {
    if (!isOwner) return false
    setLoading(true)
    setError("")
    try {
      const [statusRes, jobsRes] = await Promise.all([
        api.get<CronStatus>("/api/cron/status"),
        api.get<CronJob[]>("/api/cron/jobs?include_disabled=true"),
      ])
      setStatusData(statusRes)
      setJobs(Array.isArray(jobsRes) ? jobsRes : [])
      return true
    } catch (err) {
      setError(apiError(err, "加载定时任务失败"))
      setJobs([])
      setStatusData(null)
      return false
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload().catch(() => {})
  }, [isOwner])

  async function createJob() {
    if (!isOwner || creating) return

    const trimmedName = String(name || "").trim()
    const trimmedMessage = String(message || "").trim()
    if (!trimmedName) {
      addToast({ id: "cron:name", type: "error", message: "任务名称不能为空" })
      return
    }
    if (!trimmedMessage) {
      addToast({ id: "cron:message", type: "error", message: "任务消息不能为空" })
      return
    }

    const schedule: Record<string, any> = { kind: scheduleKind }
    if (scheduleKind === "every") {
      const seconds = Number(String(everySeconds || "").trim())
      if (!Number.isFinite(seconds) || seconds < 1) {
        addToast({ id: "cron:every", type: "error", message: "循环秒数必须 >= 1" })
        return
      }
      if (!Number.isInteger(seconds)) {
        addToast({ id: "cron:every", type: "error", message: "循环秒数必须是整数" })
        return
      }
      schedule.every_ms = Math.round(seconds * 1000)
    } else if (scheduleKind === "cron") {
      const expr = String(cronExpr || "").trim()
      if (!expr) {
        addToast({ id: "cron:expr", type: "error", message: "Cron 表达式不能为空" })
        return
      }
      schedule.expr = expr
      const tz = String(timezone || "").trim()
      if (tz) schedule.tz = tz
    } else {
      const atMs = parseDateTimeLocalToMs(atLocal)
      if (!atMs) {
        addToast({ id: "cron:at", type: "error", message: "请选择有效的执行时间" })
        return
      }
      if (atMs <= Date.now()) {
        addToast({ id: "cron:at", type: "error", message: "执行时间必须晚于当前时间" })
        return
      }
      schedule.at_ms = atMs
    }

    const payload: Record<string, any> = {
      name: trimmedName,
      schedule,
      payload: {
        message: trimmedMessage,
        deliver: Boolean(deliver),
      },
      delete_after_run: scheduleKind === "at",
    }
    if (deliver) {
      const c = String(channel || "").trim()
      const t = String(to || "").trim()
      if (c) payload.payload.channel = c
      if (t) payload.payload.to = t
    }

    setCreating(true)
    try {
      await api.post<CronJob>("/api/cron/jobs", payload)
      addToast({ id: "cron:create", type: "success", message: "定时任务已创建" })
      setName("")
      setMessage("")
      if (scheduleKind === "at") setAtLocal("")
    } catch (err) {
      addToast({ id: "cron:create", type: "error", message: apiError(err, "创建失败") })
      return
    } finally {
      setCreating(false)
    }

    const refreshed = await reload()
    if (!refreshed) {
      addToast({ id: "cron:create:refresh", type: "warning", message: "创建成功，但刷新列表失败" })
    }
  }

  async function toggleEnabled(job: CronJob) {
    if (!isOwner || actingJobId) return
    setActingJobId(job.id)
    try {
      await api.patch(`/api/cron/jobs/${encodeURIComponent(job.id)}/enabled`, {
        enabled: !job.enabled,
      })
      addToast({
        id: `cron:toggle:${job.id}`,
        type: "success",
        message: job.enabled ? "任务已停用" : "任务已启用",
      })
    } catch (err) {
      addToast({ id: `cron:toggle:${job.id}`, type: "error", message: apiError(err, "更新失败") })
      return
    } finally {
      setActingJobId("")
    }

    const refreshed = await reload()
    if (!refreshed) {
      addToast({ id: `cron:toggle:${job.id}:refresh`, type: "warning", message: "操作成功，但刷新列表失败" })
    }
  }

  async function runNow(job: CronJob) {
    if (!isOwner || actingJobId) return
    if (!statusData?.execution_available) {
      addToast({
        id: "cron:run:disabled",
        type: "warning",
        message: String(statusData?.execution_reason || "当前模式不支持立即执行"),
      })
      return
    }
    setActingJobId(job.id)
    try {
      await api.post(`/api/cron/jobs/${encodeURIComponent(job.id)}/run`, { force: true })
      addToast({ id: `cron:run:${job.id}`, type: "success", message: "任务已触发执行" })
    } catch (err) {
      addToast({ id: `cron:run:${job.id}`, type: "error", message: apiError(err, "执行失败") })
      return
    } finally {
      setActingJobId("")
    }

    const refreshed = await reload()
    if (!refreshed) {
      addToast({ id: `cron:run:${job.id}:refresh`, type: "warning", message: "执行成功，但刷新列表失败" })
    }
  }

  async function removeJob(job: CronJob) {
    if (!isOwner || actingJobId) return
    setActingJobId(job.id)
    try {
      await api.delete(`/api/cron/jobs/${encodeURIComponent(job.id)}`)
      addToast({ id: `cron:delete:${job.id}`, type: "success", message: "任务已删除" })
    } catch (err) {
      addToast({ id: `cron:delete:${job.id}`, type: "error", message: apiError(err, "删除失败") })
      return
    } finally {
      setActingJobId("")
    }

    const refreshed = await reload()
    if (!refreshed) {
      addToast({ id: `cron:delete:${job.id}:refresh`, type: "warning", message: "删除成功，但刷新列表失败" })
    }
  }

  if (!user || !isOwner) {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] flex-col items-center justify-center p-4">
        <div className="flex max-w-md flex-col items-center space-y-4 text-center">
          <div className="rounded-full bg-destructive/10 p-4">
            <ShieldAlert className="h-12 w-12 text-destructive" />
          </div>
          <h2 className="text-2xl font-bold tracking-tight">无访问权限</h2>
          <p className="text-muted-foreground">定时任务仅对 Owner 角色开放。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">定时任务</h2>
          <p className="text-muted-foreground">
            管理自动执行任务，支持循环、Cron 表达式和单次触发。
          </p>
        </div>
        <Button variant="outline" onClick={() => reload().catch(() => {})} disabled={loading || creating}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {statusData && !statusData.execution_available && (
        <div className="rounded-md border border-yellow-500/30 bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-400">
          {String(statusData.execution_reason || "当前模式仅支持配置，不支持立即执行")}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">新建任务</CardTitle>
          <CardDescription>创建后立即生效；单次任务执行后会自动删除。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">任务名称</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：每日巡检" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">调度类型</label>
              <select
                value={scheduleKind}
                onChange={(e) => setScheduleKind(String(e.target.value || "every") as ScheduleKind)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="every">每隔 N 秒</option>
                <option value="cron">Cron 表达式</option>
                <option value="at">单次时间点</option>
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">执行消息</label>
            <Input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="发送给 Agent 的任务内容"
            />
          </div>

          {scheduleKind === "every" && (
            <div className="space-y-2">
              <label className="text-sm font-medium">循环秒数</label>
              <Input
                type="number"
                min={1}
                step={1}
                value={everySeconds}
                onChange={(e) => setEverySeconds(e.target.value)}
                placeholder="3600"
              />
            </div>
          )}

          {scheduleKind === "cron" && (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm font-medium">Cron 表达式</label>
                <Input
                  value={cronExpr}
                  onChange={(e) => setCronExpr(e.target.value)}
                  placeholder="0 9 * * *"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">时区（可选）</label>
                <Input
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="Asia/Shanghai"
                />
              </div>
            </div>
          )}

          {scheduleKind === "at" && (
            <div className="space-y-2">
              <label className="text-sm font-medium">执行时间</label>
              <Input type="datetime-local" value={atLocal} onChange={(e) => setAtLocal(e.target.value)} />
            </div>
          )}

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={deliver}
                onChange={(e) => setDeliver(Boolean(e.target.checked))}
              />
              执行后主动发送结果
            </label>
            {deliver && (
              <div className="grid gap-3 md:grid-cols-2">
                <Input value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="channel，例如 web" />
                <Input value={to} onChange={(e) => setTo(e.target.value)} placeholder="to，例如 chat_id" />
              </div>
            )}
          </div>
        </CardContent>
        <CardFooter className="justify-end border-t pt-4">
          <Button onClick={() => createJob().catch(() => {})} disabled={creating || loading}>
            {creating ? "创建中..." : "创建任务"}
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">任务列表</CardTitle>
          <CardDescription>
            服务状态：
            {statusData?.enabled ? "运行中" : "未运行"}，任务总数：{Number(statusData?.jobs || 0)}
            {statusData?.next_wake_at_ms ? `，下次唤醒：${formatTime(Number(statusData.next_wake_at_ms))}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>调度</TableHead>
                <TableHead>下次执行</TableHead>
                <TableHead>最近状态</TableHead>
                <TableHead>启用状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    加载中...
                  </TableCell>
                </TableRow>
              ) : sortedJobs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-20 text-center text-muted-foreground">
                    暂无定时任务
                  </TableCell>
                </TableRow>
              ) : (
                sortedJobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell>
                      <div className="font-medium">{job.name}</div>
                      <div className="text-xs text-muted-foreground">{job.id}</div>
                    </TableCell>
                    <TableCell>{scheduleLabel(job)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {job.state?.next_run_at_ms ? formatTime(Number(job.state.next_run_at_ms)) : "-"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        {stateBadge(job)}
                        {job.state?.last_error && (
                          <span className="text-xs text-destructive line-clamp-1">{job.state.last_error}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={job.enabled ? "success" : "secondary"}>
                        {job.enabled ? "已启用" : "已停用"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => runNow(job).catch(() => {})}
                          disabled={Boolean(actingJobId) || !statusData?.execution_available}
                          title="立即执行"
                        >
                          <CalendarClock className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleEnabled(job).catch(() => {})}
                          disabled={Boolean(actingJobId)}
                          title={job.enabled ? "停用" : "启用"}
                        >
                          {job.enabled ? (
                            <PauseCircle className="h-4 w-4 text-destructive" />
                          ) : (
                            <PlayCircle className="h-4 w-4 text-green-600" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeJob(job).catch(() => {})}
                          disabled={Boolean(actingJobId)}
                          title="删除"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
