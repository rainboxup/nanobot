import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, ShieldCheck, Wrench } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Badge } from "@/src/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/src/components/ui/card"
import { BaselineBadges, normalizeBaselineMeta, type BaselineMeta } from "./baselineMeta"

type ToolKey = "exec" | "web"

interface ToolPolicyState {
  enabled: boolean
  reason_codes?: string[]
}

interface ToolsPolicyPayload {
  runtime_mode?: string
  runtime_scope?: string
  runtime_cache?: {
    max_entries?: number
    current_cached_tenant_session_managers?: number
    evictions_total?: number
    utilization?: number
  }
  runtime_cache_redacted?: boolean
  runtime_warning?: string
  writable?: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
  takes_effect?: Record<string, string>
  subject?: { tenant_id?: string }
  system_cap?: Record<ToolKey, { enabled?: boolean; whitelist_redacted?: boolean }>
  tenant_policy?: Record<ToolKey, { allowlisted?: boolean }>
  user_setting?: Record<ToolKey, { enabled?: boolean }>
  effective?: Record<ToolKey, ToolPolicyState>
  warnings?: string[]
  baseline?: BaselineMeta
}

const REASON_TEXT: Record<string, string> = {
  system_disabled: "系统总开关已关闭",
  system_allowlist: "未命中系统白名单",
  tenant_disabled: "租户策略已关闭",
  tenant_allowlist: "未命中租户白名单",
  tenant_policy: "租户策略不允许",
  user_disabled: "当前设置已关闭",
}

function reasonLabel(code: string): string {
  const normalized = String(code || "").trim().toLowerCase()
  return REASON_TEXT[normalized] || normalized || "-"
}

function isEditRole(role: string | null | undefined): boolean {
  const normalized = String(role || "").trim().toLowerCase()
  return normalized === "owner" || normalized === "admin"
}

function normalizeEnabled(value: unknown): boolean {
  return Boolean(value)
}

function safeNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function getErrorMessage(err: unknown, fallback = "加载失败"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

export function ToolsPolicy() {
  const { user, addToast } = useStore()
  const canEdit = isEditRole(user?.role)

  const [policy, setPolicy] = useState<ToolsPolicyPayload | null>(null)
  const [execEnabled, setExecEnabled] = useState(false)
  const [webEnabled, setWebEnabled] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  const hasPolicyState = policy !== null
  const writable = Boolean(policy?.writable)
  const writeBlocked = hasPolicyState && !writable

  const isDirty = useMemo(() => {
    if (!policy) return false
    const currentExec = normalizeEnabled(policy.user_setting?.exec?.enabled)
    const currentWeb = normalizeEnabled(policy.user_setting?.web?.enabled)
    return currentExec !== execEnabled || currentWeb !== webEnabled
  }, [policy, execEnabled, webEnabled])

  async function loadPolicy() {
    setLoading(true)
    setError("")
    try {
      const payload = await api.get<ToolsPolicyPayload>("/api/tools/policy")
      setPolicy(payload || {})
      setExecEnabled(normalizeEnabled(payload?.user_setting?.exec?.enabled))
      setWebEnabled(normalizeEnabled(payload?.user_setting?.web?.enabled))
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function savePolicy() {
    if (!policy || saving || !canEdit || !writable || !isDirty) return
    setSaving(true)
    setError("")
    try {
      const payload = await api.put<ToolsPolicyPayload>("/api/tools/policy", {
        exec_enabled: execEnabled,
        web_enabled: webEnabled,
      })
      setPolicy(payload || {})
      setExecEnabled(normalizeEnabled(payload?.user_setting?.exec?.enabled))
      setWebEnabled(normalizeEnabled(payload?.user_setting?.web?.enabled))
      addToast({ type: "success", message: "工具权限策略已保存" })
    } catch (err) {
      const msg = getErrorMessage(err, "保存失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    void loadPolicy()
  }, [])

  const execEffective = policy?.effective?.exec || { enabled: false, reason_codes: [] }
  const webEffective = policy?.effective?.web || { enabled: false, reason_codes: [] }
  const warnings = Array.isArray(policy?.warnings) ? policy?.warnings : []
  const baselineMeta = useMemo(() => normalizeBaselineMeta(policy?.baseline), [policy])
  const runtimeCacheRedacted = Boolean(policy?.runtime_cache_redacted)
  const cacheMaxEntries = safeNumber(policy?.runtime_cache?.max_entries, 0)
  const cacheCurrent = safeNumber(policy?.runtime_cache?.current_cached_tenant_session_managers, 0)
  const cacheEvictions = safeNumber(policy?.runtime_cache?.evictions_total, 0)
  const cacheUtilization = Math.min(1, Math.max(0, safeNumber(policy?.runtime_cache?.utilization, 0)))

  function renderToolCard(key: ToolKey, title: string, description: string) {
    const requested = key === "exec" ? execEnabled : webEnabled
    const setRequested = key === "exec" ? setExecEnabled : setWebEnabled
    const effective = key === "exec" ? execEffective : webEffective
    const reasonCodes = Array.isArray(effective.reason_codes) ? effective.reason_codes : []
    const takesEffect = String(policy?.takes_effect?.[key] || "runtime")
    const systemEnabled = normalizeEnabled(policy?.system_cap?.[key]?.enabled)
    const tenantAllowlisted = normalizeEnabled(policy?.tenant_policy?.[key]?.allowlisted)

    return (
      <Card key={key}>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base">{title}</CardTitle>
            <Badge variant={effective.enabled ? "success" : "warning"}>
              {effective.enabled ? "已生效" : "未生效"}
            </Badge>
          </div>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={requested}
              onChange={(e) => setRequested(Boolean(e.target.checked))}
              disabled={!hasPolicyState || !canEdit || writeBlocked || saving}
            />
            请求开启（tenant 设置）
          </label>
          <div className="text-xs text-muted-foreground">
            系统上限：{systemEnabled ? "开启" : "关闭"} · 租户策略：{tenantAllowlisted ? "允许" : "不允许"} · 生效时机：
            {takesEffect}
          </div>
          {reasonCodes.length > 0 && (
            <div className="space-y-1">
              <div className="text-xs font-medium text-muted-foreground">阻断原因</div>
              <div className="flex flex-wrap gap-2">
                {reasonCodes.map((code) => (
                  <Badge key={`${key}:${code}`} variant="outline">
                    {reasonLabel(code)}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">工具权限</h2>
          <p className="text-muted-foreground">配置 Exec / Web 的租户级开关，并查看实际生效与阻断原因。</p>
        </div>
        <Button variant="outline" onClick={() => loadPolicy().catch(() => {})} disabled={loading || saving}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-muted-foreground">加载中...</div>
      ) : (
        <>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <ShieldCheck className="h-4 w-4" />
                运行上下文
              </CardTitle>
              <CardDescription>当前服务运行模式决定了配置是否可写以及变更作用域。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="secondary">mode: {String(policy?.runtime_mode || "-")}</Badge>
                <Badge variant="secondary">scope: {String(policy?.runtime_scope || "-")}</Badge>
                <Badge variant={writable ? "success" : "warning"}>
                  {writable ? "可写" : "只读"}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                tenant: {String(policy?.subject?.tenant_id || "-")}
              </div>
              <BaselineBadges meta={baselineMeta} variant="outline" className="text-xs text-muted-foreground" />
              <div className="text-xs text-muted-foreground">
                baseline 会影响系统工具上限；如需按 tenant 查看 bucket / canary 命中，请到 Soul 页面查看 tenant baseline 模拟。
              </div>
              <div className="rounded-md border border-muted/60 bg-muted/20 p-3 text-xs text-muted-foreground">
                <div className="font-medium text-foreground mb-1">Web 会话缓存（运行时）</div>
                {runtimeCacheRedacted ? (
                  <div>仅 owner 可见（已脱敏）</div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">max: {String(cacheMaxEntries)}</Badge>
                    <Badge variant="outline">current: {String(cacheCurrent)}</Badge>
                    <Badge variant="outline">evictions: {String(cacheEvictions)}</Badge>
                    <Badge variant="outline">utilization: {(cacheUtilization * 100).toFixed(1)}%</Badge>
                  </div>
                )}
              </div>
              {(policy?.runtime_warning || policy?.write_block_reason) && (
                <div className="rounded-md border border-warning/30 bg-yellow-500/10 p-3 text-sm text-yellow-800 dark:text-yellow-300">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4" />
                    {String(policy?.write_block_reason || policy?.runtime_warning || "")}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            {renderToolCard("exec", "Exec 工具", "命令执行能力，受系统与租户策略叠加约束。")}
            {renderToolCard("web", "Web 工具", "联网检索能力，受系统与租户策略叠加约束。")}
          </div>

          {warnings.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <Wrench className="h-4 w-4" />
                  策略提示
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {warnings.map((item, idx) => (
                  <div
                    key={`warn:${idx}`}
                    className="rounded-md border border-muted p-2 text-sm text-muted-foreground"
                  >
                    {String(item)}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <div className="flex justify-end">
            <Button
              onClick={() => savePolicy().catch(() => {})}
              disabled={!hasPolicyState || !canEdit || writeBlocked || saving || !isDirty}
            >
              {saving ? "保存中..." : "保存工具策略"}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
