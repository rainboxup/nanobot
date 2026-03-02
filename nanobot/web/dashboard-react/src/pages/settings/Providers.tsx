import { useEffect, useMemo, useState } from "react"
import { Edit2 } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Input } from "@/src/components/ui/input"
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/src/components/ui/card"
import { Badge } from "@/src/components/ui/badge"
import { Modal } from "@/src/components/ui/modal"

interface ProviderItem {
  name: string
  api_base?: string | null
  has_key?: boolean
  masked_key?: string | null
}

interface AgentDefaultsState {
  model: string
  provider: string
  providers: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function normalizeProviderValue(value: string | null | undefined): string {
  return String(value || "").trim().toLowerCase() || "auto"
}

function getApiErrorMessage(err: unknown, fallback = "加载失败"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

function dedupeProviderNames(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.map((v) => String(v || "").trim().toLowerCase()).filter(Boolean))).sort(
    (a, b) => a.localeCompare(b)
  )
}

function parseProviderNames(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  return dedupeProviderNames(raw.map((item) => (typeof item === "string" ? item : "")))
}

function parseProviderItems(raw: unknown): ProviderItem[] {
  if (!Array.isArray(raw)) return []
  const parsed: ProviderItem[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const name = String(item.name || "").trim().toLowerCase()
    if (!name) continue
    parsed.push({
      name,
      api_base: item.api_base == null ? null : String(item.api_base),
      has_key: Boolean(item.has_key),
      masked_key: item.masked_key == null ? null : String(item.masked_key),
    })
  }
  return parsed
}

function toDefaultsState(raw: unknown, providerNames: string[]): AgentDefaultsState {
  const obj = isRecord(raw) ? raw : {}
  return {
    model: String(obj.model || "").trim(),
    provider: normalizeProviderValue(String(obj.provider || "")),
    providers: dedupeProviderNames([...parseProviderNames(obj.providers), ...providerNames]),
  }
}

export function Providers() {
  const { user, addToast } = useStore()
  const role = String(user?.role || "").toLowerCase()
  const canEdit = role === "owner" || role === "admin"
  const defaultsModelInputId = "providers-default-model"
  const defaultsProviderSelectId = "providers-default-provider"
  const providerApiBaseInputId = "providers-api-base"
  const providerApiKeyInputId = "providers-api-key"

  const [providers, setProviders] = useState<ProviderItem[]>([])
  const [defaults, setDefaults] = useState<AgentDefaultsState | null>(null)
  const [defaultsBase, setDefaultsBase] = useState<AgentDefaultsState | null>(null)
  const [loading, setLoading] = useState(false)
  const [defaultsLoading, setDefaultsLoading] = useState(false)
  const [defaultsSaving, setDefaultsSaving] = useState(false)
  const [providerSaving, setProviderSaving] = useState(false)
  const [providersError, setProvidersError] = useState("")
  const [defaultsError, setDefaultsError] = useState("")

  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editing, setEditing] = useState<
    (ProviderItem & { api_key: string; clear_key: boolean }) | null
  >(null)

  const providerNamesFromCards = useMemo(
    () => dedupeProviderNames(providers.map((item) => item.name)),
    [providers]
  )

  const hasUnknownSelectedProvider = useMemo(() => {
    if (!defaults) return false
    return defaults.provider !== "auto" && !defaults.providers.includes(defaults.provider)
  }, [defaults])

  const sortedProviders = useMemo(() => {
    return [...providers].sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
  }, [providers])

  function closeEditor() {
    setIsModalOpen(false)
    setEditing(null)
  }

  function applyProviderNames(providerNames: string[]) {
    const normalized = dedupeProviderNames(providerNames)
    setDefaults((prev) => {
      if (!prev) return prev
      return { ...prev, providers: normalized }
    })
    setDefaultsBase((prev) => {
      if (!prev) return prev
      return { ...prev, providers: normalized }
    })
  }

  async function refreshProvidersOnly() {
    setLoading(true)
    setProvidersError("")
    try {
      const list = await api.get<unknown>("/api/providers")
      const nextProviders = parseProviderItems(list)
      setProviders(nextProviders)
      applyProviderNames(nextProviders.map((item) => String(item.name || "")))
    } catch (err) {
      const msg = getApiErrorMessage(err)
      setProvidersError(msg)
      throw err
    } finally {
      setLoading(false)
    }
  }

  async function load() {
    setLoading(true)
    setDefaultsLoading(true)
    setProvidersError("")
    setDefaultsError("")
    try {
      let currentProviderNames = providerNamesFromCards
      let defaultsPayload: unknown = null

      const providersTask = api
        .get<unknown>("/api/providers")
        .then((value) => {
          const nextProviders = parseProviderItems(value)
          setProviders(nextProviders)
          currentProviderNames = dedupeProviderNames(nextProviders.map((item) => item.name))
          applyProviderNames(currentProviderNames)
        })
        .catch((err) => {
          setProvidersError(getApiErrorMessage(err))
        })
        .finally(() => {
          setLoading(false)
        })

      const defaultsTask = api
        .get<unknown>("/api/providers/defaults")
        .then((value) => {
          defaultsPayload = value
          const normalized = toDefaultsState(value, currentProviderNames)
          setDefaults(normalized)
          setDefaultsBase(normalized)
        })
        .catch((err) => {
          setDefaultsError(getApiErrorMessage(err))
        })
        .finally(() => {
          setDefaultsLoading(false)
        })

      await Promise.allSettled([providersTask, defaultsTask])

      if (defaultsPayload !== null) {
        const normalized = toDefaultsState(defaultsPayload, currentProviderNames)
        setDefaults(normalized)
        setDefaultsBase(normalized)
      }
    } catch (err) {
      const msg = getApiErrorMessage(err)
      setProvidersError((prev) => prev || msg)
      setDefaultsError((prev) => prev || msg)
      setLoading(false)
      setDefaultsLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const handleEdit = (provider: ProviderItem) => {
    setEditing({ ...provider, api_key: "", clear_key: false })
    setIsModalOpen(true)
  }

  const handleSave = async () => {
    if (!editing) return
    if (!canEdit) return
    if (providerSaving) return

    const name = String(editing.name || "").trim()
    if (!name) return

    setProviderSaving(true)
    try {
      const payload: any = { api_base: String(editing.api_base || "").trim() }
      if (editing.clear_key) payload.api_key = ""
      else if (editing.api_key !== "") payload.api_key = editing.api_key
      await api.put(`/api/providers/${encodeURIComponent(name)}`, payload)
      try {
        await refreshProvidersOnly()
      } catch (refreshErr) {
        const refreshMsg = getApiErrorMessage(refreshErr)
        addToast({ id: `provider:${name}`, type: "warning", message: `已保存，但刷新失败：${refreshMsg}` })
        closeEditor()
        return
      }
      addToast({ id: `provider:${name}`, type: "success", message: "已保存" })
      closeEditor()
    } catch (err) {
      const msg = getApiErrorMessage(err, "保存失败")
      addToast({ id: `provider:${name}`, type: "error", message: msg })
    } finally {
      setProviderSaving(false)
    }
  }

  const saveDefaults = async () => {
    if (!defaults) return
    if (!canEdit) return
    if (defaultsSaving) return

    const model = String(defaults.model || "").trim()
    const provider = normalizeProviderValue(defaults.provider)
    if (!model) {
      const msg = "模型不能为空"
      setDefaultsError(msg)
      addToast({ id: "defaults:model", type: "error", message: msg })
      return
    }
    if (hasUnknownSelectedProvider) {
      const msg = "当前 Provider 已无效，请重新选择。"
      setDefaultsError(msg)
      addToast({ id: "defaults:provider", type: "error", message: msg })
      return
    }

    const baseline = defaultsBase || defaults
    const payload: Record<string, string> = {}
    if (model !== baseline.model) payload.model = model
    if (provider !== baseline.provider) payload.provider = provider
    if (Object.keys(payload).length === 0) {
      addToast({ id: "defaults:nochange", type: "success", message: "默认模型未变更" })
      return
    }

    setDefaultsSaving(true)
    try {
      const saved = await api.put<AgentDefaultsState>("/api/providers/defaults", payload)
      const normalized = toDefaultsState(saved, providerNamesFromCards)
      setDefaults(normalized)
      setDefaultsBase(normalized)
      setDefaultsError("")
      addToast({ id: "defaults:save", type: "success", message: "默认模型已保存" })
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "保存失败")
      setDefaultsError(msg)
      addToast({ id: "defaults:save", type: "error", message: msg })
    } finally {
      setDefaultsSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">模型服务</h2>
          <p className="text-muted-foreground">
            配置默认模型与 Provider 的 API Base/密钥（密钥不回填）。
          </p>
        </div>
      </div>

      {providersError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {providersError}
        </div>
      )}

      <Card>
        <CardHeader className="pb-4">
          <CardTitle className="text-lg">默认模型</CardTitle>
          <CardDescription>配置当前租户的默认模型与 Provider 策略。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {defaultsError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {defaultsError}
            </div>
          )}
          {defaultsLoading ? (
            <div className="text-sm text-muted-foreground">加载中...</div>
          ) : !defaults ? (
            <div className="text-sm text-muted-foreground">无法读取默认模型配置。</div>
          ) : (
            <>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor={defaultsModelInputId}>
                  模型标识
                </label>
                <Input
                  id={defaultsModelInputId}
                  value={defaults.model}
                  onChange={(e) =>
                    setDefaults((prev) => (prev ? { ...prev, model: e.target.value } : prev))
                  }
                  placeholder="anthropic/claude-opus-4-5"
                  disabled={!canEdit || defaultsSaving}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor={defaultsProviderSelectId}>
                  Provider 策略
                </label>
                <select
                  id={defaultsProviderSelectId}
                  className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  value={defaults.provider}
                  onChange={(e) =>
                    setDefaults((prev) => (prev ? { ...prev, provider: e.target.value } : prev))
                  }
                  disabled={!canEdit || defaultsSaving}
                >
                  {hasUnknownSelectedProvider && (
                    <option value={defaults.provider}>{defaults.provider}（未知 Provider）</option>
                  )}
                  <option value="auto">auto（按模型自动匹配）</option>
                  {defaults.providers.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                {hasUnknownSelectedProvider && (
                  <div className="text-xs text-muted-foreground">
                    当前配置中的 Provider 不在可用列表，请选择新的 Provider 或切换为 auto。
                  </div>
                )}
              </div>
              {!canEdit && (
                <div className="text-sm text-muted-foreground">仅 Admin/Owner 可以修改默认模型配置。</div>
              )}
            </>
          )}
        </CardContent>
        <CardFooter className="flex justify-end pt-4 border-t">
          <Button
            onClick={() => saveDefaults().catch(() => {})}
            disabled={
              !defaults || !canEdit || defaultsLoading || defaultsSaving || hasUnknownSelectedProvider
            }
          >
            {defaultsSaving ? "保存中..." : "保存默认模型"}
          </Button>
        </CardFooter>
      </Card>

      {loading ? (
        <div className="text-sm text-muted-foreground">加载中...</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {sortedProviders.map((provider) => (
            <Card key={provider.name} className="flex flex-col">
              <CardHeader className="pb-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg">{provider.name}</CardTitle>
                  <Badge variant={provider.has_key ? "success" : "secondary"}>
                    {provider.has_key ? "Configured" : "Missing Key"}
                  </Badge>
                </div>
                <CardDescription className="truncate" title={String(provider.api_base || "")}>
                  {provider.api_base || "（默认 API Base）"}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex-1">
                <div className="text-sm font-mono bg-muted p-2 rounded-md truncate">
                  {provider.masked_key || "（未配置）"}
                </div>
              </CardContent>
              <CardFooter className="flex justify-end pt-4 border-t">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleEdit(provider)}
                  disabled={!canEdit || providerSaving}
                  title={!canEdit ? "仅 Admin/Owner 可编辑" : "编辑"}
                >
                  <Edit2 className="mr-2 h-4 w-4" /> 编辑
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

      <Modal
        isOpen={isModalOpen}
        onClose={() => {
          if (!providerSaving) closeEditor()
        }}
        title={editing ? `编辑：${editing.name}` : "编辑模型服务"}
        description="密钥不会自动填充；留空将保持不变。"
        footer={
          <div className="flex w-full justify-end items-center gap-2">
            <Button variant="outline" onClick={closeEditor} disabled={providerSaving}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={!editing || !canEdit || providerSaving}>
              {providerSaving ? "保存中..." : "保存"}
            </Button>
          </div>
        }
      >
        {editing && (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor={providerApiBaseInputId}>
                API Base
              </label>
              <Input
                id={providerApiBaseInputId}
                value={String(editing.api_base || "")}
                onChange={(e) =>
                  setEditing((prev) => (prev ? { ...prev, api_base: e.target.value } : prev))
                }
                placeholder="https://api.openai.com/v1"
                disabled={providerSaving}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor={providerApiKeyInputId}>
                API Key
              </label>
              <Input
                id={providerApiKeyInputId}
                type="password"
                value={editing.api_key}
                onChange={(e) =>
                  setEditing((prev) => (prev ? { ...prev, api_key: e.target.value } : prev))
                }
                placeholder="留空保持不变"
                autoComplete="off"
                disabled={providerSaving || editing.clear_key}
              />
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={editing.clear_key}
                  onChange={(e) =>
                    setEditing((prev) =>
                      prev
                        ? {
                            ...prev,
                            clear_key: Boolean(e.target.checked),
                            api_key: e.target.checked ? "" : prev.api_key,
                          }
                        : prev
                    )
                  }
                  disabled={providerSaving}
                />
                清空密钥
              </label>
            </div>
            {!canEdit && (
              <div className="text-sm text-muted-foreground">仅 Admin/Owner 可以修改模型服务配置。</div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
