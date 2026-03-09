import { useEffect, useMemo, useState } from "react"
import { Activity, AlertTriangle, Edit2, Search, PlayCircle, StopCircle } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Drawer } from "@/src/components/ui/drawer"
import { Input } from "@/src/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

const DEFAULT_SENSITIVE_KEYS = new Set([
  "token",
  "secret",
  "app_secret",
  "client_secret",
  "encrypt_key",
  "verification_token",
  "imap_password",
  "smtp_password",
  "bot_token",
  "app_token",
])

function buildSensitiveKeySet(keys?: string[] | null): Set<string> {
  const source = Array.isArray(keys) && keys.length > 0 ? keys : Array.from(DEFAULT_SENSITIVE_KEYS)
  return new Set(source.map((k) => String(k || "").trim().toLowerCase()).filter(Boolean))
}

function buildSensitivePathSet(paths?: string[] | null): Set<string> {
  if (!Array.isArray(paths) || paths.length === 0) return new Set()
  return new Set(paths.map((p) => String(p || "").trim()).filter(Boolean))
}

function isPlainObject(value: any): value is Record<string, any> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function setByPath(obj: Record<string, any>, path: string, value: any) {
  const parts = path.split(".")
  let cur = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const k = parts[i]
    if (!cur[k] || typeof cur[k] !== "object") cur[k] = {}
    cur = cur[k]
  }
  cur[parts[parts.length - 1]] = value
}

interface ChannelItem {
  name: string
  enabled: boolean
  config_summary?: Record<string, any>
  config_ready?: boolean
  missing_required_fields?: string[]
  runtime_registered?: boolean
  runtime_running?: boolean
  runtime_mode?: string
  runtime_scope?: string
  runtime_warning?: string
  config_scope?: string
  takes_effect?: string
  writable?: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
}

interface ChannelDetail {
  name: string
  config: Record<string, any>
  sensitive_keys?: string[]
  redacted_value?: string
  sensitive_paths?: string[]
  sensitive_has_value?: Record<string, boolean>
  runtime_mode?: string
  runtime_scope?: string
  runtime_warning?: string
  config_scope?: string
  takes_effect?: string
  writable?: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
}

interface ChannelStatus {
  name: string
  enabled: boolean
  config_ready: boolean
  missing_required_fields: string[]
  runtime_registered: boolean
  runtime_running: boolean
  runtime_mode?: string
  runtime_scope?: string
  runtime_warning?: string
  config_scope?: string
  takes_effect?: string
  writable?: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
}

type FieldKind = "bool" | "number" | "array" | "string" | "sensitive"

export function ChannelsAdmin() {
  const { user, addToast } = useStore()
  const role = String(user?.role || "member").toLowerCase()
  const isOwner = role === "owner"

  const [channels, setChannels] = useState<ChannelItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [searchQuery, setSearchQuery] = useState("")

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [activeChannel, setActiveChannel] = useState<ChannelDetail | null>(null)
  const [fieldFilter, setFieldFilter] = useState("")
  const [values, setValues] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [detailError, setDetailError] = useState("")

  const runtimeState = useMemo(() => {
    const probe = channels.find(
      (item) => typeof item.writable === "boolean" || Boolean(item.runtime_warning)
    )
    const writable = typeof probe?.writable === "boolean" ? probe.writable : isOwner
    const systemWarning = String(probe?.runtime_warning || "").trim()
    const writeBlockedReason = String(probe?.write_block_reason || "").trim()
    return { known: Boolean(probe), writable, systemWarning, writeBlockedReason }
  }, [channels, isOwner])

  const systemWarning =
    runtimeState.systemWarning || "渠道配置为系统级（所有租户共享），修改后需要重启服务才会生效。"
  const writeBlocked = runtimeState.known ? !runtimeState.writable : !isOwner
  const writeBlockedReason = runtimeState.writeBlockedReason || "仅 Owner 可以修改系统渠道配置。"
  const canWrite = !writeBlocked
  const writeDeniedTitle = writeBlockedReason

  async function loadList() {
    setLoading(true)
    setError("")
    try {
      const list = await api.get<ChannelItem[]>("/api/admin/channels")
      setChannels(Array.isArray(list) ? list : [])
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadList().catch(() => {})
  }, [])

  const filteredChannels = useMemo(() => {
    const q = String(searchQuery || "").trim().toLowerCase()
    if (!q) return channels
    return channels.filter((c) => String(c.name || "").toLowerCase().includes(q))
  }, [channels, searchQuery])

  const activeSensitiveKeys = useMemo(
    () => buildSensitiveKeySet(activeChannel?.sensitive_keys || null),
    [activeChannel]
  )

  const activeSensitivePaths = useMemo(
    () => buildSensitivePathSet(activeChannel?.sensitive_paths || null),
    [activeChannel]
  )

  const activeRedactedValue = String(activeChannel?.redacted_value || "****")

  function initValuesFromConfig(
    config: Record<string, any>,
    {
      sensitiveKeys,
      sensitivePaths,
      redactedValue,
    }: {
      sensitiveKeys: Set<string>
      sensitivePaths: Set<string>
      redactedValue: string
    }
  ) {
    const next: Record<string, any> = {}

    function walk(obj: any, prefix: string) {
      for (const [k, v] of Object.entries(obj || {})) {
        const path = prefix ? `${prefix}.${k}` : k
        if (isPlainObject(v)) {
          walk(v, path)
          continue
        }

        if (typeof v === "boolean") next[path] = Boolean(v)
        else if (typeof v === "number") next[path] = String(v)
        else if (Array.isArray(v)) next[path] = v.join(", ")
        else {
          const keyLower = String(k || "").toLowerCase()
          const isSensitive =
            sensitivePaths.has(path) || sensitiveKeys.has(keyLower) || String(v ?? "") === redactedValue
          next[path] = isSensitive ? "" : String(v ?? "")
        }
      }
    }

    walk(config || {}, "")
    setValues(next)
  }

  async function openEditor(name: string) {
    if (!canWrite) {
      addToast({ id: `channel:edit:${name}`, type: "error", message: writeDeniedTitle })
      return
    }
    setDrawerOpen(true)
    setActiveChannel(null)
    setDetailError("")
    setFieldFilter("")
    setValues({})
    try {
      const detail = await api.get<ChannelDetail>(`/api/admin/channels/${encodeURIComponent(name)}`)
      const sensitiveKeys = buildSensitiveKeySet(detail.sensitive_keys || null)
      const sensitivePaths = buildSensitivePathSet(detail.sensitive_paths || null)
      setActiveChannel(detail)
      initValuesFromConfig(detail.config || {}, {
        sensitiveKeys,
        sensitivePaths,
        redactedValue: String(detail.redacted_value || "****"),
      })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setDetailError(msg)
    }
  }

  async function toggleChannel(name: string) {
    if (!canWrite) {
      addToast({ id: `channel:toggle:${name}`, type: "error", message: writeDeniedTitle })
      return
    }
    try {
      await api.post(`/api/admin/channels/${encodeURIComponent(name)}/toggle`, {})
      addToast({ id: `channel:toggle:${name}`, type: "success", message: "已保存（需重启服务生效）" })
      await loadList()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      addToast({ type: "error", message: msg })
    }
  }

  async function checkChannelStatus(name: string) {
    try {
      const status = await api.get<ChannelStatus>(`/api/admin/channels/${encodeURIComponent(name)}/status`)
      setChannels((prev) =>
        prev.map((item) =>
          item.name === name
            ? {
                ...item,
                enabled: status.enabled,
                config_ready: status.config_ready,
                missing_required_fields: status.missing_required_fields,
                runtime_registered: status.runtime_registered,
                runtime_running: status.runtime_running,
                runtime_mode: status.runtime_mode,
                runtime_scope: status.runtime_scope,
                runtime_warning: status.runtime_warning,
                writable: status.writable,
                write_block_reason_code: status.write_block_reason_code,
                write_block_reason: status.write_block_reason,
              }
            : item
        )
      )

      const readyMsg =
        status.config_ready
          ? "配置已就绪"
          : `缺少字段：${(status.missing_required_fields || []).slice(0, 3).join(", ")}${(status.missing_required_fields || []).length > 3 ? ", ..." : ""}`
      const runtimeMsg = status.runtime_running
        ? "运行中"
        : status.runtime_registered
          ? "已注册未运行"
          : "未注册（通常需重启服务）"
      addToast({ id: `channel:check:${name}`, type: status.config_ready ? "success" : "error", message: `${name} 检查结果：${readyMsg}；${runtimeMsg}` })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "检查失败")
      addToast({ id: `channel:check:${name}`, type: "error", message: msg })
    }
  }

  function renderStatusSummary(channel: ChannelItem) {
    const enabled = Boolean(channel.enabled)
    const configReady = Boolean(channel.config_ready)
    const runtimeRunning = Boolean(channel.runtime_running)
    const runtimeRegistered = Boolean(channel.runtime_registered)
    const missingCount = Array.isArray(channel.missing_required_fields)
      ? channel.missing_required_fields.length
      : 0

    const configText = configReady ? "配置就绪" : `配置待补全（${missingCount}）`
    const runtimeText = runtimeRunning
      ? "运行中"
      : runtimeRegistered
        ? "已注册未运行"
        : "未注册"

    return (
      <div className="space-y-1">
        <div className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          {enabled ? (
            <>
              <PlayCircle className="h-4 w-4 text-green-600" /> 已启用
            </>
          ) : (
            <>
              <StopCircle className="h-4 w-4 text-muted-foreground" /> 未启用
            </>
          )}
        </div>
        <div className="text-xs text-muted-foreground">接入：{configText}</div>
        <div className="text-xs text-muted-foreground">运行：{runtimeText}</div>
      </div>
    )
  }

  function fieldKindForValue(
    value: any,
    path: string,
    key: string,
    sensitiveKeys: Set<string>,
    sensitivePaths: Set<string>,
    redactedValue: string
  ): FieldKind {
    if (typeof value === "boolean") return "bool"
    if (typeof value === "number") return "number"
    if (Array.isArray(value)) return "array"
    const keyLower = String(key || "").toLowerCase()
    const isSensitive =
      sensitivePaths.has(path) || sensitiveKeys.has(keyLower) || String(value ?? "") === redactedValue
    if (isSensitive) return "sensitive"
    return "string"
  }

  function buildUpdateFromValues(config: Record<string, any>) {
    const update: Record<string, any> = {}
    const errors: string[] = []

    function walk(obj: any, prefix: string) {
      for (const [k, v] of Object.entries(obj || {})) {
        const path = prefix ? `${prefix}.${k}` : k
        if (isPlainObject(v)) {
          walk(v, path)
          continue
        }

        const kind = fieldKindForValue(v, path, k, activeSensitiveKeys, activeSensitivePaths, activeRedactedValue)
        const nextVal = values[path]

        if (kind === "sensitive") {
          const text = String(nextVal || "")
          if (text !== "") setByPath(update, path, text)
          continue
        }

        if (kind === "bool") {
          setByPath(update, path, Boolean(nextVal))
          continue
        }

        if (kind === "number") {
          const raw = String(nextVal ?? "").trim()
          if (raw === "") {
            errors.push(`${path} 不能为空`)
            continue
          }
          const n = Number(raw)
          if (!Number.isFinite(n)) {
            errors.push(`${path} 必须是数字`)
            continue
          }
          setByPath(update, path, n)
          continue
        }

        if (kind === "array") {
          const text = String(nextVal || "")
          const list = text
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean)
          setByPath(update, path, list)
          continue
        }

        setByPath(update, path, String(nextVal ?? ""))
      }
    }

    walk(config || {}, "")
    return { update, errors }
  }

  async function saveConfig() {
    if (!activeChannel) return
    if (!canWrite) {
      setDetailError(writeDeniedTitle)
      addToast({ id: `channel:${activeChannel.name}`, type: "error", message: writeDeniedTitle })
      return
    }
    if (saving) return
    setSaving(true)
    setDetailError("")
    try {
      const { update: payload, errors } = buildUpdateFromValues(activeChannel.config || {})
      if (errors.length > 0) {
        const msg = `请先修正配置字段：${errors.slice(0, 5).join("；")}${errors.length > 5 ? "；..." : ""}`
        setDetailError(msg)
        addToast({ id: `channel:${activeChannel.name}`, type: "error", message: msg })
        return
      }
      await api.put(`/api/admin/channels/${encodeURIComponent(activeChannel.name)}`, payload)
      addToast({
        id: `channel:${activeChannel.name}`,
        type: "success",
        message: "已保存（需重启服务生效）",
      })
      setDrawerOpen(false)
      setActiveChannel(null)
      await loadList()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "保存失败")
      setDetailError(msg)
      addToast({ id: `channel:${activeChannel.name}`, type: "error", message: msg })
    } finally {
      setSaving(false)
    }
  }

  function matchesField(path: string) {
    const q = String(fieldFilter || "").trim().toLowerCase()
    if (!q) return true
    return path.toLowerCase().includes(q)
  }

  function renderFields(obj: any, prefix: string): any {
    const items: any[] = []
    for (const [k, v] of Object.entries(obj || {})) {
      const path = prefix ? `${prefix}.${k}` : k

      if (isPlainObject(v)) {
        const child = renderFields(v, path)
        if (child.length > 0) {
          items.push(
            <div key={path} className="rounded-md border bg-muted/20 p-3">
              <div className="text-xs font-mono text-muted-foreground mb-2">{path}</div>
              <div className="space-y-3">{child}</div>
            </div>
          )
        }
        continue
      }

      if (!matchesField(path)) continue

      const kind = fieldKindForValue(v, path, k, activeSensitiveKeys, activeSensitivePaths, activeRedactedValue)
      const value = values[path]
      const isSensitive = kind === "sensitive"

      items.push(
        <div key={path} className="space-y-2">
          <label className="text-sm font-medium">{path}</label>
          {kind === "bool" ? (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={Boolean(value)}
                onChange={(e) => setValues((prev) => ({ ...prev, [path]: Boolean(e.target.checked) }))}
                disabled={!canWrite}
              />
              启用
            </label>
          ) : (
            <Input
              type={isSensitive ? "password" : kind === "number" ? "number" : "text"}
              value={String(value ?? "")}
              onChange={(e) => setValues((prev) => ({ ...prev, [path]: e.target.value }))}
              placeholder={isSensitive ? "保持不变" : ""}
              disabled={!canWrite}
              autoComplete="off"
            />
          )}
          {isSensitive && (
            <div className="text-xs text-muted-foreground">敏感字段不会自动填充；留空将保持不变。</div>
          )}
        </div>
      )
    }
    return items
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">渠道</h2>
          <p className="text-muted-foreground">{systemWarning} 可直接检查接入就绪与运行状态。</p>
          <p className="text-sm text-muted-foreground">
            Workspace BYO 凭据如已配置，请在 Workspace 页面管理；保存后需重启服务才会载入新的工作区级渠道运行时，active_in_runtime 会反映当前运行时是否与已存凭据一致。
          </p>
        </div>
      </div>

      <div className="flex items-center space-x-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索渠道名称..."
            className="pl-8"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={() => loadList().catch(() => {})} disabled={loading}>
          刷新
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="rounded-md border border-warning/30 bg-yellow-500/10 p-3 text-sm text-yellow-800 dark:text-yellow-300">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />
          {systemWarning}
        </div>
        {writeBlocked && <div className="mt-2 text-xs text-muted-foreground">{writeBlockedReason}</div>}
      </div>

      <div className="rounded-md border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>渠道名称</TableHead>
              <TableHead>摘要</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">
                  加载中...
                </TableCell>
              </TableRow>
            ) : filteredChannels.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">
                  暂无数据
                </TableCell>
              </TableRow>
            ) : (
              filteredChannels.map((channel) => (
                <TableRow key={channel.name}>
                  <TableCell className="font-medium">{channel.name}</TableCell>
                  <TableCell className="text-xs text-muted-foreground font-mono truncate">
                    {channel.config_summary ? JSON.stringify(channel.config_summary) : "{}"}
                  </TableCell>
                  <TableCell>{renderStatusSummary(channel)}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => checkChannelStatus(channel.name).catch(() => {})}
                        title="检查接入与运行状态"
                      >
                        <Activity className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => toggleChannel(channel.name).catch(() => {})}
                        disabled={!canWrite}
                        title={canWrite ? (channel.enabled ? "禁用" : "启用") : writeDeniedTitle}
                      >
                        {channel.enabled ? (
                          <StopCircle className="h-4 w-4 text-destructive" />
                        ) : (
                          <PlayCircle className="h-4 w-4 text-green-600" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEditor(channel.name).catch(() => {})}
                        disabled={!canWrite}
                        title={canWrite ? "编辑配置" : writeDeniedTitle}
                      >
                        <Edit2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <Drawer
        isOpen={drawerOpen}
        onClose={() => {
          setDrawerOpen(false)
          setActiveChannel(null)
          setDetailError("")
        }}
        title={
          activeChannel
            ? canWrite
              ? `编辑：${activeChannel.name}`
              : `查看：${activeChannel.name}`
            : canWrite
              ? "编辑渠道"
              : "查看渠道"
        }
        description={`${systemWarning} 敏感字段不会自动填充；留空将保持不变。`}
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setDrawerOpen(false)
                setActiveChannel(null)
              }}
            >
              {canWrite ? "取消" : "关闭"}
            </Button>
            {canWrite && (
              <Button onClick={() => saveConfig().catch(() => {})} disabled={!activeChannel || saving}>
                {saving ? "保存中..." : "保存"}
              </Button>
            )}
          </>
        }
      >
        {detailError && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {detailError}
          </div>
        )}

        {!activeChannel ? (
          <div className="text-sm text-muted-foreground">加载中...</div>
        ) : (
          <div className="space-y-6">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="搜索字段... 例如 openai.api_key / channels.telegram / smtp_password"
                className="pl-8 bg-muted/50"
                value={fieldFilter}
                onChange={(e) => setFieldFilter(e.target.value)}
              />
            </div>
            <div className="space-y-4">{renderFields(activeChannel.config || {}, "")}</div>
            {!isOwner && (
              <div className="text-sm text-muted-foreground">
                仅 Owner 可以修改系统渠道配置；当前为只读模式。
              </div>
            )}
            {isOwner && writeBlocked && (
              <div className="text-sm text-muted-foreground">{writeBlockedReason}</div>
            )}
          </div>
        )}
      </Drawer>
    </div>
  )
}

