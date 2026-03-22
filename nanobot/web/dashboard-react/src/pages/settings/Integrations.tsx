import { useEffect, useMemo, useState } from "react"

import { api, ApiError, formatTime } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Badge } from "@/src/components/ui/badge"
import { Button } from "@/src/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/src/components/ui/card"
import { Input } from "@/src/components/ui/input"

interface IntegrationLatestStatus {
  connector?: string | null
  provider?: string | null
  domain?: string | null
  operation?: string | null
  status?: string | null
  reason_code?: string | null
  sync_id?: string | null
  synced_count?: number | null
  updated_at?: string | null
}

interface IntegrationHealthState {
  ready?: boolean
  provider_available?: boolean
  last_sync_status?: string | null
}

interface IntegrationRow {
  connector: string
  tenant_id?: string | null
  enabled: boolean
  provider: string
  base_url?: string | null
  timeout_s?: number | null
  health?: IntegrationHealthState
  latest_status?: IntegrationLatestStatus | null
}

interface IntegrationsHealthSummary {
  tenant_id?: string
  configured_connectors: number
  ready_connectors: number
  degraded_connectors: string[]
  available_providers: string[]
}

interface IntegrationSyncResponse {
  result?: {
    status?: string
    connector?: string
    provider?: string
    operation?: string
    output?: Record<string, unknown>
  }
  latest_status?: IntegrationLatestStatus | null
}

function getApiErrorMessage(err: unknown, fallback = "加载失败"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

function getStatusBadgeVariant(
  value: string | null | undefined
): "secondary" | "success" | "warning" | "destructive" {
  const normalized = String(value || "").trim().toLowerCase()
  if (normalized === "succeeded" || normalized === "ready" || normalized === "enabled") return "success"
  if (normalized === "failed" || normalized === "error" || normalized === "degraded") return "destructive"
  if (normalized === "disabled" || normalized === "unknown") return "warning"
  return "secondary"
}

function detectOperationOptions(
  provider: string | null | undefined,
  latestOperation: string | null | undefined
): string[] {
  const normalized = String(provider || "").trim().toLowerCase()
  let options: string[]
  if (normalized.includes("crm")) {
    options = ["sync_contacts", "sync_accounts"]
  } else if (normalized.includes("order")) {
    options = ["sync_orders", "sync_fulfillment"]
  } else {
    options = ["sync"]
  }
  const latest = String(latestOperation || "").trim()
  if (latest && !options.includes(latest)) options = [latest, ...options]
  return options
}

function isObjectLike(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function parseList(raw: unknown): IntegrationRow[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((item) => isObjectLike(item))
    .map((item) => ({
      connector: String(item.connector || "").trim(),
      tenant_id: item.tenant_id == null ? null : String(item.tenant_id),
      enabled: Boolean(item.enabled),
      provider: String(item.provider || "").trim(),
      base_url: item.base_url == null ? null : String(item.base_url),
      timeout_s: Number(item.timeout_s || 0),
      health: isObjectLike(item.health)
        ? {
            ready: Boolean(item.health.ready),
            provider_available: Boolean(item.health.provider_available),
            last_sync_status: item.health.last_sync_status == null ? null : String(item.health.last_sync_status),
          }
        : undefined,
      latest_status: isObjectLike(item.latest_status)
        ? {
            connector: item.latest_status.connector == null ? null : String(item.latest_status.connector),
            provider: item.latest_status.provider == null ? null : String(item.latest_status.provider),
            domain: item.latest_status.domain == null ? null : String(item.latest_status.domain),
            operation: item.latest_status.operation == null ? null : String(item.latest_status.operation),
            status: item.latest_status.status == null ? null : String(item.latest_status.status),
            reason_code: item.latest_status.reason_code == null ? null : String(item.latest_status.reason_code),
            sync_id: item.latest_status.sync_id == null ? null : String(item.latest_status.sync_id),
            synced_count: Number(item.latest_status.synced_count || 0),
            updated_at: item.latest_status.updated_at == null ? null : String(item.latest_status.updated_at),
          }
        : null,
    }))
    .filter((row) => Boolean(row.connector))
}

function parseHealthSummary(raw: unknown): IntegrationsHealthSummary {
  const base = isObjectLike(raw) ? raw : {}
  return {
    tenant_id: base.tenant_id == null ? "" : String(base.tenant_id),
    configured_connectors: Number(base.configured_connectors || 0),
    ready_connectors: Number(base.ready_connectors || 0),
    degraded_connectors: Array.isArray(base.degraded_connectors)
      ? base.degraded_connectors.map((item) => String(item))
      : [],
    available_providers: Array.isArray(base.available_providers)
      ? base.available_providers.map((item) => String(item))
      : [],
  }
}

function canEditByRole(role: string | null | undefined): boolean {
  const normalized = String(role || "").trim().toLowerCase()
  return normalized === "owner" || normalized === "admin"
}

export function Integrations() {
  const { user, addToast } = useStore()
  const canEdit = canEditByRole(user?.role)

  const [rows, setRows] = useState<IntegrationRow[]>([])
  const [health, setHealth] = useState<IntegrationsHealthSummary>({
    tenant_id: "",
    configured_connectors: 0,
    ready_connectors: 0,
    degraded_connectors: [],
    available_providers: [],
  })
  const [operationByConnector, setOperationByConnector] = useState<Record<string, string>>({})
  const [payloadByConnector, setPayloadByConnector] = useState<Record<string, string>>({})
  const [idempotencyByConnector, setIdempotencyByConnector] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [syncingConnector, setSyncingConnector] = useState("")
  const [error, setError] = useState("")

  const hasRows = rows.length > 0

  const availableProvidersText = useMemo(() => {
    if (!health.available_providers.length) return "-"
    return health.available_providers.join(", ")
  }, [health.available_providers])

  async function load() {
    setLoading(true)
    setError("")
    try {
      const [listResp, healthResp] = await Promise.all([
        api.get<unknown>("/api/integrations"),
        api.get<unknown>("/api/integrations/health"),
      ])
      const list = parseList(listResp)
      setRows(list)
      setHealth(parseHealthSummary(healthResp))
      setOperationByConnector((prev) => {
        const next = { ...prev }
        for (const row of list) {
          if (!next[row.connector]) {
            next[row.connector] = detectOperationOptions(row.provider, row.latest_status?.operation)[0]
          }
        }
        return next
      })
      setPayloadByConnector((prev) => {
        const next = { ...prev }
        for (const row of list) {
          if (!Object.prototype.hasOwnProperty.call(next, row.connector)) {
            next[row.connector] = "{}"
          }
        }
        return next
      })
    } catch (err) {
      setError(getApiErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function runSync(row: IntegrationRow) {
    if (!canEdit || syncingConnector) return
    const connector = String(row.connector || "").trim()
    if (!connector) return

    const operation = String(operationByConnector[connector] || "").trim()
    if (!operation) {
      const msg = "请选择同步操作。"
      setError(msg)
      addToast({ type: "error", message: msg })
      return
    }

    const payloadText = String(payloadByConnector[connector] || "").trim() || "{}"
    let payload: Record<string, unknown>
    try {
      const parsed = JSON.parse(payloadText)
      if (!isObjectLike(parsed)) {
        throw new Error("payload should be object")
      }
      payload = parsed
    } catch {
      const msg = "Payload 必须是合法 JSON 对象。"
      setError(msg)
      addToast({ type: "error", message: msg })
      return
    }

    setSyncingConnector(connector)
    setError("")
    try {
      const response = await api.post<IntegrationSyncResponse>("/api/integrations/sync", {
        connector,
        operation,
        payload,
        idempotency_key: String(idempotencyByConnector[connector] || "").trim() || undefined,
      })
      const latestStatus = response?.latest_status || null
      setRows((prev) =>
        prev.map((item) => {
          if (item.connector !== connector) return item
          return {
            ...item,
            latest_status: latestStatus,
            health: {
              ...(item.health || {}),
              last_sync_status: String((latestStatus || {}).status || "succeeded"),
            },
          }
        })
      )
      addToast({ type: "success", message: `连接器 ${connector} 同步已触发` })
    } catch (err) {
      const msg = getApiErrorMessage(err, "同步失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setSyncingConnector("")
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">业务集成</h2>
          <p className="text-muted-foreground">查看连接器健康状态，并按连接器手动触发 CRM / 订单同步。</p>
        </div>
        <Button variant="outline" onClick={() => load().catch(() => {})} disabled={loading || Boolean(syncingConnector)}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">连接器健康总览</CardTitle>
          <CardDescription>按 tenant 展示当前配置连接器与运行时 provider 可用性。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2 text-sm">
            <Badge variant="secondary">tenant: {String(health.tenant_id || "-")}</Badge>
            <Badge variant="outline">configured: {String(health.configured_connectors)}</Badge>
            <Badge variant={health.ready_connectors > 0 ? "success" : "warning"}>
              ready: {String(health.ready_connectors)}
            </Badge>
            <Badge variant={health.degraded_connectors.length ? "destructive" : "success"}>
              degraded: {String(health.degraded_connectors.length)}
            </Badge>
          </div>
          <div className="text-xs text-muted-foreground">providers: {availableProvidersText}</div>
          {health.degraded_connectors.length > 0 && (
            <div className="text-xs text-muted-foreground">
              degraded connectors: {health.degraded_connectors.join(", ")}
            </div>
          )}
        </CardContent>
      </Card>

      {loading ? (
        <div className="text-sm text-muted-foreground">加载中...</div>
      ) : !hasRows ? (
        <div className="rounded-md border border-muted p-4 text-sm text-muted-foreground">
          当前 tenant 未配置集成连接器。请先在配置中添加 `workspace.integrations.connectors`。
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {rows.map((row) => {
            const connector = String(row.connector || "")
            const latest = row.latest_status || null
            const operationOptions = detectOperationOptions(row.provider, latest?.operation)
            const selectedOperation = operationByConnector[connector] || operationOptions[0]
            const ready = Boolean(row.health?.ready)
            const enabled = Boolean(row.enabled)
            const canSync = canEdit && ready && enabled && !syncingConnector

            return (
              <Card key={connector}>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-base">{connector}</CardTitle>
                    <Badge variant={ready ? "success" : "warning"}>{ready ? "ready" : "degraded"}</Badge>
                  </div>
                  <CardDescription>
                    provider: {row.provider || "-"} · base_url: {row.base_url || "-"}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <Badge variant={enabled ? "success" : "warning"}>{enabled ? "enabled" : "disabled"}</Badge>
                    <Badge variant="outline">timeout: {String(row.timeout_s || 0)}s</Badge>
                    <Badge variant={getStatusBadgeVariant(row.health?.last_sync_status)}>
                      last sync: {String(row.health?.last_sync_status || "none")}
                    </Badge>
                  </div>

                  {latest && (
                    <div className="rounded-md border border-muted/60 bg-muted/20 p-3 text-xs text-muted-foreground space-y-1">
                      <div>
                        status:{" "}
                        <span className="font-medium text-foreground">{String(latest.status || "-")}</span>
                      </div>
                      <div>operation: {String(latest.operation || "-")}</div>
                      <div>sync_id: {String(latest.sync_id || "-")}</div>
                      <div>synced_count: {String(latest.synced_count || 0)}</div>
                      <div>updated_at: {latest.updated_at ? formatTime(latest.updated_at) : "-"}</div>
                      {latest.reason_code && <div>reason_code: {String(latest.reason_code)}</div>}
                    </div>
                  )}

                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor={`integration-operation-${connector}`}>
                      同步操作
                    </label>
                    <select
                      id={`integration-operation-${connector}`}
                      className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                      value={selectedOperation}
                      onChange={(e) =>
                        setOperationByConnector((prev) => ({ ...prev, [connector]: e.target.value }))
                      }
                      disabled={!canEdit || Boolean(syncingConnector)}
                    >
                      {operationOptions.map((option) => (
                        <option key={`${connector}:${option}`} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor={`integration-payload-${connector}`}>
                      Payload(JSON)
                    </label>
                    <textarea
                      id={`integration-payload-${connector}`}
                      className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                      value={payloadByConnector[connector] ?? "{}"}
                      onChange={(e) => setPayloadByConnector((prev) => ({ ...prev, [connector]: e.target.value }))}
                      disabled={!canEdit || Boolean(syncingConnector)}
                    />
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor={`integration-idempotency-${connector}`}>
                      Idempotency Key（可选）
                    </label>
                    <Input
                      id={`integration-idempotency-${connector}`}
                      placeholder="sync-20260322-001"
                      value={idempotencyByConnector[connector] || ""}
                      onChange={(e) =>
                        setIdempotencyByConnector((prev) => ({ ...prev, [connector]: e.target.value }))
                      }
                      disabled={!canEdit || Boolean(syncingConnector)}
                    />
                  </div>

                  {!canEdit && (
                    <div className="text-xs text-muted-foreground">仅 Admin/Owner 可以触发手动同步。</div>
                  )}
                  {canEdit && !ready && (
                    <div className="text-xs text-muted-foreground">当前连接器处于 degraded，修复 provider 后可触发同步。</div>
                  )}
                  {canEdit && ready && !enabled && (
                    <div className="text-xs text-muted-foreground">当前连接器已禁用，请先启用后再触发同步。</div>
                  )}

                  <div className="flex justify-end">
                    <Button
                      aria-label={`sync-${connector}`}
                      onClick={() => runSync(row).catch(() => {})}
                      disabled={!canSync}
                    >
                      {syncingConnector === connector ? "同步中..." : "立即同步"}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
