import { useEffect, useMemo, useState } from "react"
import { Edit2, Search, PlayCircle, StopCircle } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Drawer } from "@/src/components/ui/drawer"
import { Input } from "@/src/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

const SENSITIVE_KEYS = new Set([
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
}

interface ChannelDetail {
  name: string
  config: Record<string, any>
}

type FieldKind = "bool" | "number" | "array" | "string" | "sensitive"

export function Channels() {
  const { user, addToast } = useStore()
  const canEdit = String(user?.role || "").toLowerCase() !== "member"

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

  async function loadList() {
    setLoading(true)
    setError("")
    try {
      const list = await api.get<ChannelItem[]>("/api/channels")
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

  function initValuesFromConfig(config: Record<string, any>) {
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
          const isSensitive = SENSITIVE_KEYS.has(k)
          next[path] = isSensitive ? "" : String(v ?? "")
        }
      }
    }

    walk(config || {}, "")
    setValues(next)
  }

  async function openEditor(name: string) {
    setDrawerOpen(true)
    setActiveChannel(null)
    setDetailError("")
    setFieldFilter("")
    setValues({})
    try {
      const detail = await api.get<ChannelDetail>(`/api/channels/${encodeURIComponent(name)}`)
      setActiveChannel(detail)
      initValuesFromConfig(detail.config || {})
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setDetailError(msg)
    }
  }

  async function toggleChannel(name: string) {
    if (!canEdit) return
    try {
      await api.post(`/api/channels/${encodeURIComponent(name)}/toggle`, {})
      await loadList()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "操作失败")
      addToast({ type: "error", message: msg })
    }
  }

  function fieldKindForValue(value: any, key: string): FieldKind {
    if (typeof value === "boolean") return "bool"
    if (typeof value === "number") return "number"
    if (Array.isArray(value)) return "array"
    if (SENSITIVE_KEYS.has(key)) return "sensitive"
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

        const kind = fieldKindForValue(v, k)
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
    if (!canEdit) return
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
      await api.put(`/api/channels/${encodeURIComponent(activeChannel.name)}`, payload)
      addToast({ id: `channel:${activeChannel.name}`, type: "success", message: "已保存" })
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

      const kind = fieldKindForValue(v, k)
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
                disabled={!canEdit}
              />
              启用
            </label>
          ) : (
            <Input
              type={isSensitive ? "password" : kind === "number" ? "number" : "text"}
              value={String(value ?? "")}
              onChange={(e) => setValues((prev) => ({ ...prev, [path]: e.target.value }))}
              placeholder={isSensitive ? "保持不变" : ""}
              disabled={!canEdit}
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
          <p className="text-muted-foreground">启用/禁用渠道并编辑配置（敏感字段不回填）。</p>
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
                  <TableCell>
                    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                      {channel.enabled ? (
                        <>
                          <PlayCircle className="h-4 w-4 text-green-600" /> 已启用
                        </>
                      ) : (
                        <>
                          <StopCircle className="h-4 w-4 text-muted-foreground" /> 未启用
                        </>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => toggleChannel(channel.name).catch(() => {})}
                        disabled={!canEdit}
                        title={!canEdit ? "仅 Admin/Owner 可操作" : channel.enabled ? "禁用" : "启用"}
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
                        disabled={!canEdit}
                        title={!canEdit ? "仅 Admin/Owner 可编辑" : "编辑配置"}
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
        title={activeChannel ? `编辑：${activeChannel.name}` : "编辑渠道"}
        description="敏感字段不会自动填充；留空将保持不变。"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => {
                setDrawerOpen(false)
                setActiveChannel(null)
              }}
            >
              取消
            </Button>
            <Button onClick={() => saveConfig().catch(() => {})} disabled={!activeChannel || !canEdit || saving}>
              {saving ? "保存中..." : "保存"}
            </Button>
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
            {!canEdit && <div className="text-sm text-muted-foreground">仅 Admin/Owner 可以修改渠道配置。</div>}
          </div>
        )}
      </Drawer>
    </div>
  )
}

