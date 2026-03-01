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

export function Providers() {
  const { user, addToast } = useStore()
  const canEdit = String(user?.role || "").toLowerCase() !== "member"

  const [providers, setProviders] = useState<ProviderItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editing, setEditing] = useState<
    (ProviderItem & { api_key: string; clear_key: boolean }) | null
  >(null)

  async function load() {
    setLoading(true)
    setError("")
    try {
      const list = await api.get<ProviderItem[]>("/api/providers")
      setProviders(Array.isArray(list) ? list : [])
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch(() => {})
  }, [])

  const sortedProviders = useMemo(() => {
    return [...providers].sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
  }, [providers])

  const handleEdit = (provider: ProviderItem) => {
    setEditing({ ...provider, api_key: "", clear_key: false })
    setIsModalOpen(true)
  }

  const handleSave = async () => {
    if (!editing) return
    if (!canEdit) return

    const name = String(editing.name || "").trim()
    if (!name) return

    try {
      const payload: any = { api_base: String(editing.api_base || "") }
      if (editing.api_key !== "") payload.api_key = editing.api_key
      else if (editing.clear_key) payload.api_key = ""
      await api.put(`/api/providers/${encodeURIComponent(name)}`, payload)
      addToast({ id: `provider:${name}`, type: "success", message: "已保存" })
      setIsModalOpen(false)
      setEditing(null)
      await load()
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "保存失败")
      addToast({ id: `provider:${name}`, type: "error", message: msg })
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">模型服务</h2>
          <p className="text-muted-foreground">配置 Provider 的 API Base 与密钥（密钥不回填）。</p>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

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
                  disabled={!canEdit}
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
          setIsModalOpen(false)
          setEditing(null)
        }}
        title={editing ? `编辑：${editing.name}` : "编辑模型服务"}
        description="密钥不会自动填充；留空将保持不变。"
        footer={
          <div className="flex w-full justify-end items-center gap-2">
            <Button variant="outline" onClick={() => setIsModalOpen(false)}>
              取消
            </Button>
            <Button onClick={handleSave} disabled={!editing || !canEdit}>
              保存
            </Button>
          </div>
        }
      >
        {editing && (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">API Base</label>
              <Input
                value={String(editing.api_base || "")}
                onChange={(e) =>
                  setEditing((prev) => (prev ? { ...prev, api_base: e.target.value } : prev))
                }
                placeholder="https://api.openai.com/v1"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">API Key</label>
              <Input
                type="password"
                value={editing.api_key}
                onChange={(e) =>
                  setEditing((prev) => (prev ? { ...prev, api_key: e.target.value } : prev))
                }
                placeholder="留空保持不变"
                autoComplete="off"
              />
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={editing.clear_key}
                  onChange={(e) =>
                    setEditing((prev) =>
                      prev ? { ...prev, clear_key: Boolean(e.target.checked) } : prev
                    )
                  }
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

