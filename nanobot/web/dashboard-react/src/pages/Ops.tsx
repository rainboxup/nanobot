import { useEffect, useMemo, useState } from "react"
import { Activity, Copy, RefreshCw, Server, Database, Wifi, ShieldAlert } from "lucide-react"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"

import { api, ApiError, formatTime } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Badge } from "@/src/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/src/components/ui/card"
import { Skeleton } from "@/src/components/ui/skeleton"
import { helpDocHref } from "@/src/pages/HelpDoc"

function formatPercent(value: any): string {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return "0%"
  return `${(n * 100).toFixed(1)}%`
}

function statusLabel(value: any): string {
  const v = String(value || "").toLowerCase()
  if (v === "ready") return "正常"
  if (v === "degraded") return "异常"
  if (v === "unknown") return "未知"
  return String(value || "") || "未知"
}

function pressureLabel(value: any): string {
  const v = String(value || "").toLowerCase()
  if (v === "high") return "高"
  if (v === "elevated") return "偏高"
  return "正常"
}

function pressureVariant(value: any): "success" | "warning" | "secondary" {
  const v = String(value || "").toLowerCase()
  if (v === "high" || v === "elevated") return "warning"
  if (v === "normal") return "success"
  return "secondary"
}

export function Ops() {
  const { user, addToast } = useStore()
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState("")
  const [error, setError] = useState("")
  const [data, setData] = useState<any>(null)

  const lastJson = useMemo(() => JSON.stringify(data || {}, null, 2), [data])

  async function load() {
    setError("")
    setIsRefreshing(true)
    try {
      const payload = await api.get<any>("/api/ops/runtime")
      setData(payload)
      setLastUpdated(new Date().toLocaleTimeString())
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      if (msg.toLowerCase().includes("insufficient role")) {
        setError("运维页面仅对 Owner 角色开放。")
      } else {
        setError(msg)
      }
      setData(null)
    } finally {
      setIsRefreshing(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [])

  const handleCopyDiagnostics = () => {
    navigator.clipboard.writeText(lastJson || "")
    addToast({ type: "success", message: "运维快照已复制到剪贴板" })
  }

  if (!user || String(user.role || "").toLowerCase() !== "owner") {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] flex-col items-center justify-center p-4">
        <div className="flex max-w-md flex-col items-center space-y-4 text-center">
          <div className="rounded-full bg-destructive/10 p-4">
            <ShieldAlert className="h-12 w-12 text-destructive" />
          </div>
          <h2 className="text-2xl font-bold tracking-tight">无访问权限</h2>
          <p className="text-muted-foreground">运维大盘仅供 Owner 角色访问。</p>
        </div>
      </div>
    )
  }

  const status = String(data?.status || "")
  const version = data?.version ? `v${String(data.version)}` : ""
  const warnings: string[] = Array.isArray(data?.warnings) ? data.warnings : []
  const runtime = data?.runtime || {}
  const summary = runtime?.summary || {}
  const queue = runtime?.queue || {}
  const channels = runtime?.channels || {}
  const registered: string[] = Array.isArray(channels?.registered) ? channels.registered : []
  const channelRows: any[] = Array.isArray(channels?.rows) ? channels.rows : []
  const workspaceRows: any[] = Array.isArray(channels?.workspace_rows) ? channels.workspace_rows : []
  const channelStatus = channels?.status || {}
  const activeWeb = Number(channels?.active_web_connections || 0)
  const attention: any[] = Array.isArray(runtime?.attention) ? runtime.attention : []
  const guides: any[] = Array.isArray(data?.guides) ? data.guides : []

  const noticeVariant = String(status || "").toLowerCase() === "ready" ? "success" : "warning"

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col p-4 md:p-8 overflow-y-auto bg-muted/10">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">运维</h2>
            <p className="text-muted-foreground">运行时快照，用于排查问题与支持。{lastUpdated ? `最后更新: ${lastUpdated}` : ""}</p>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => load().catch(() => {})} disabled={isRefreshing}>
              <RefreshCw className={`mr-2 h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
              刷新
            </Button>
            <Button variant="outline" onClick={handleCopyDiagnostics} disabled={!data}>
              <Copy className="mr-2 h-4 w-4" />
              复制 JSON
            </Button>
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {!data && isRefreshing ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, idx) => (
              <Card key={idx}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium">加载中</CardTitle>
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-28" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <>
            <div className="rounded-md border bg-card p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-muted-foreground" />
                  <div className="font-medium">
                    {statusLabel(status)} {version}
                  </div>
                </div>
                <Badge variant={noticeVariant}>{String(status || "unknown").toUpperCase()}</Badge>
              </div>
              {warnings.length ? (
                <ul className="mt-3 list-disc pl-5 text-sm text-muted-foreground">
                  {warnings.map((w, idx) => (
                    <li key={idx}>{String(w)}</li>
                  ))}
                </ul>
              ) : (
                <div className="mt-3 text-sm text-muted-foreground">暂无告警</div>
              )}
              {guides.length ? (
                <div className="mt-3 flex flex-wrap gap-3 text-sm">
                  {guides.map((guide, idx) => (
                    <a
                      key={`${String(guide?.slug || "")}:${idx}`}
                      href={helpDocHref(String(guide?.slug || ""))}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      {String(guide?.title || guide?.slug || "操作指南")}
                    </a>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">已注册渠道</CardTitle>
                  <Wifi className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold">{String(summary?.registered_channel_count ?? 0)}</div>
                  <div className="text-sm text-muted-foreground">{registered.join(", ") || "（无）"}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">运行中渠道</CardTitle>
                  <Activity className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold">{String(summary?.running_channel_count ?? 0)}</div>
                  <div className="text-sm text-muted-foreground">
                    未运行：{Math.max(0, Number(summary?.registered_channel_count || 0) - Number(summary?.running_channel_count || 0))}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">工作区运行时</CardTitle>
                  <Server className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold">
                    {String(summary?.workspace_runtime_running_count ?? 0)} / {String(summary?.workspace_runtime_count ?? 0)}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    未激活：{String(summary?.workspace_runtime_inactive_count ?? 0)}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">活跃 Web 连接</CardTitle>
                  <Wifi className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold">{String(summary?.active_web_connections ?? activeWeb)}</div>
                  <div className="text-sm text-muted-foreground">当前在线会话连接数</div>
                </CardContent>
              </Card>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">运行时间</CardTitle>
                  <Server className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-sm text-muted-foreground">启动时间：{formatTime(runtime?.started_at || "")}</div>
                  <div className="text-sm text-muted-foreground">运行时长（秒）：{String(runtime?.uptime_seconds ?? "")}</div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">队列（入站）</CardTitle>
                  <Database className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="mb-2">
                    <Badge variant={pressureVariant(queue?.inbound_pressure_level)}>
                      {pressureLabel(queue?.inbound_pressure_level)}
                    </Badge>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    深度：{String(queue?.inbound_depth ?? 0)} / {String(queue?.inbound_capacity ?? 0)}（{formatPercent(queue?.inbound_utilization)}）
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">队列（出站）</CardTitle>
                  <Database className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="mb-2">
                    <Badge variant={pressureVariant(queue?.outbound_pressure_level)}>
                      {pressureLabel(queue?.outbound_pressure_level)}
                    </Badge>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    深度：{String(queue?.outbound_depth ?? 0)} / {String(queue?.outbound_capacity ?? 0)}（{formatPercent(queue?.outbound_utilization)}）
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">渠道</CardTitle>
                  <Wifi className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-sm text-muted-foreground">已注册：{registered.join(", ") || "（无）"}</div>
                  <div className="text-sm text-muted-foreground">活跃 Web 连接：{String(activeWeb)}</div>
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">重点关注</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {attention.length ? (
                  attention.map((item, idx) => (
                    <div key={`${String(item?.reason_code || "")}:${idx}`} className="rounded-md border bg-muted/20 p-3">
                      <div className="flex items-center gap-2">
                        <Badge variant="warning">{String(item?.reason_code || "attention")}</Badge>
                        <div className="font-medium">{String(item?.summary || "")}</div>
                      </div>
                      {item?.details ? (
                        <div className="mt-2 text-sm text-muted-foreground">
                          {Object.entries(item.details as Record<string, unknown>).map(([key, value]) => (
                            <div key={key}>
                              {key}: {String(value)}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))
                ) : (
                  <div className="text-sm text-muted-foreground">暂无重点告警</div>
                )}
              </CardContent>
            </Card>

            <div className="grid gap-4 md:grid-cols-2">
              <Card className="col-span-1">
                <CardHeader>
                  <CardTitle className="text-base">渠道状态</CardTitle>
                </CardHeader>
                <CardContent className="p-0 overflow-hidden rounded-b-xl border-t">
                  <SyntaxHighlighter
                    language="json"
                    style={vscDarkPlus}
                    customStyle={{ margin: 0, padding: "1rem", fontSize: "0.875rem" }}
                  >
                    {JSON.stringify(channelStatus, null, 2)}
                  </SyntaxHighlighter>
                </CardContent>
              </Card>

              <Card className="col-span-1">
                <CardHeader>
                  <CardTitle className="text-base">工作区运行时明细</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {workspaceRows.length ? (
                    workspaceRows.map((row, idx) => (
                      <div key={`${String(row?.channel || "")}:${String(row?.tenant_id || "")}:${idx}`} className="rounded-md border bg-muted/20 p-3 text-sm">
                        <div className="font-medium">
                          {String(row?.channel || "")} / {String(row?.tenant_id || "")}
                        </div>
                        <div className="text-muted-foreground">
                          running={String(Boolean(row?.running))} · active_in_runtime={String(Boolean(row?.active_in_runtime))}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="text-sm text-muted-foreground">暂无工作区运行时</div>
                  )}
                </CardContent>
              </Card>

              <Card className="col-span-1 flex flex-col">
                <CardHeader>
                  <CardTitle className="text-base">原始 JSON</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 p-0 overflow-hidden rounded-b-xl border-t">
                  <SyntaxHighlighter
                    language="json"
                    style={vscDarkPlus}
                    customStyle={{ margin: 0, padding: "1rem", fontSize: "0.875rem" }}
                  >
                    {lastJson}
                  </SyntaxHighlighter>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

