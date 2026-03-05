import { useEffect, useMemo, useState } from "react"
import { AlertTriangle } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { cn } from "@/src/lib/utils"
import { useStore } from "@/src/store/useStore"
import { Badge } from "@/src/components/ui/badge"
import { Button } from "@/src/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/src/components/ui/card"

interface SoulLayer {
  title: string
  source: string
  precedence: number
}

interface EffectiveSoul {
  merged_content: string
  layers: SoulLayer[]
}

interface SoulWorkspacePayload {
  filename?: string
  exists?: boolean
  content?: string
}

interface SoulPayload {
  runtime_mode?: string
  runtime_scope?: string
  runtime_warning?: string
  writable?: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
  takes_effect?: string
  subject?: { tenant_id?: string }
  workspace?: SoulWorkspacePayload
  effective?: EffectiveSoul
}

interface SoulPreviewPayload {
  overlay?: string | null
  effective?: EffectiveSoul
}

function getErrorMessage(err: unknown, fallback = "加载失败"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

function normalizeLayers(raw: unknown): SoulLayer[] {
  if (!Array.isArray(raw)) return []
  const out: SoulLayer[] = []
  for (const item of raw) {
    if (!item || typeof item !== "object") continue
    const title = String((item as any).title || "").trim()
    const source = String((item as any).source || "").trim()
    const precedence = Number((item as any).precedence)
    if (!title && !source) continue
    out.push({
      title,
      source,
      precedence: Number.isFinite(precedence) ? precedence : 0,
    })
  }
  return out.sort((a, b) => (a.precedence || 0) - (b.precedence || 0))
}

function normalizeEffective(raw: unknown): EffectiveSoul | null {
  if (!raw || typeof raw !== "object") return null
  return {
    merged_content: String((raw as any).merged_content || ""),
    layers: normalizeLayers((raw as any).layers),
  }
}

const INPUT_TEXTAREA_CLASS = cn(
  "flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background",
  "font-mono leading-5",
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
  "disabled:cursor-not-allowed disabled:opacity-50"
)

const READONLY_TEXTAREA_CLASS = cn(
  "flex w-full rounded-md border border-input bg-muted/20 px-3 py-2 text-sm ring-offset-background",
  "font-mono leading-5",
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
)

export function Soul() {
  const { user, addToast } = useStore()
  const role = String(user?.role || "").toLowerCase()
  const canEdit = role === "owner" || role === "admin"

  const [payload, setPayload] = useState<SoulPayload | null>(null)
  const [content, setContent] = useState("")
  const [baseContent, setBaseContent] = useState("")
  const [overlay, setOverlay] = useState("")
  const [preview, setPreview] = useState<EffectiveSoul | null>(null)

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState("")
  const [previewError, setPreviewError] = useState("")

  const writable = Boolean(payload?.writable)
  const writeBlocked = payload !== null && !writable
  const writeBlockedReason = String(payload?.write_block_reason || payload?.runtime_warning || "").trim()

  const isDirty = useMemo(() => content !== baseContent, [content, baseContent])

  const effective = useMemo(() => {
    return preview || normalizeEffective(payload?.effective) || { merged_content: "", layers: [] }
  }, [preview, payload])

  async function loadSoul() {
    setLoading(true)
    setError("")
    try {
      const data = await api.get<SoulPayload>("/api/soul")
      setPayload(data || {})
      const nextContent = String(data?.workspace?.content || "")
      setContent(nextContent)
      setBaseContent(nextContent)
      setPreview(null)
      setPreviewError("")
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function saveSoul() {
    if (!payload || saving || !canEdit || writeBlocked || !isDirty) return
    setSaving(true)
    setError("")
    try {
      const data = await api.put<SoulPayload>("/api/soul", { content })
      setPayload(data || {})
      const nextContent = String(data?.workspace?.content || "")
      setContent(nextContent)
      setBaseContent(nextContent)
      setPreview(null)
      setPreviewError("")
      addToast({ type: "success", message: "Soul 已保存" })
    } catch (err) {
      const msg = getErrorMessage(err, "保存失败")
      setError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setSaving(false)
    }
  }

  async function previewOverlay() {
    if (previewing) return
    setPreviewing(true)
    setPreviewError("")
    try {
      const data = await api.post<SoulPreviewPayload>("/api/soul/preview", {
        overlay: overlay.trim() ? overlay : null,
        workspace_content: content,
      })
      setPreview(normalizeEffective(data?.effective))
      addToast({ type: "success", message: "已生成 Effective Preview" })
    } catch (err) {
      const msg = getErrorMessage(err, "预览失败")
      setPreviewError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setPreviewing(false)
    }
  }

  useEffect(() => {
    void loadSoul()
  }, [])

  const tenantId = String(payload?.subject?.tenant_id || "-")
  const workspaceFilename = String(payload?.workspace?.filename || "-")
  const workspaceExists = Boolean(payload?.workspace?.exists)
  const takesEffect = String(payload?.takes_effect || "-")
  const overlayActive = Boolean(preview)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Soul</h2>
          <p className="text-muted-foreground">
            编辑 workspace Soul，并查看 Platform Base + Workspace + Overlay 的合并结果（Effective Preview）。
          </p>
        </div>
        <Button variant="outline" onClick={() => loadSoul().catch(() => {})} disabled={loading || saving || previewing}>
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
              <CardTitle className="text-base">运行上下文</CardTitle>
              <CardDescription>运行模式决定了 Soul 是否可写以及变更生效时机。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant="secondary">mode: {String(payload?.runtime_mode || "-")}</Badge>
                <Badge variant="secondary">scope: {String(payload?.runtime_scope || "-")}</Badge>
                <Badge variant={writable ? "success" : "warning"}>{writable ? "可写" : "只读"}</Badge>
                <Badge variant="outline">takes_effect: {takesEffect}</Badge>
              </div>
              <div className="text-xs text-muted-foreground">tenant: {tenantId}</div>
              <div className="text-xs text-muted-foreground">
                workspace: {workspaceFilename} {workspaceExists ? "" : "(missing)"}
              </div>
              {writeBlockedReason && (
                <div className="rounded-md border border-warning/30 bg-yellow-500/10 p-3 text-sm text-yellow-800 dark:text-yellow-300">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4" />
                    {writeBlockedReason}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            <Card className="flex flex-col">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Workspace Soul</CardTitle>
                <CardDescription>保存后将用于该 workspace 的后续消息。</CardDescription>
              </CardHeader>
              <CardContent className="flex-1 space-y-3">
                <textarea
                  className={cn(INPUT_TEXTAREA_CLASS, "min-h-[360px]")}
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="在这里编辑 SOUL.md 内容..."
                  disabled={!canEdit || writeBlocked || saving}
                />
                {!canEdit && <div className="text-sm text-muted-foreground">仅 Admin/Owner 可以修改。</div>}
                {canEdit && writeBlocked && (
                  <div className="text-sm text-muted-foreground">{writeBlockedReason || "当前模式不可写。"}</div>
                )}
              </CardContent>
              <CardFooter className="flex justify-end gap-2 pt-4 border-t">
                <Button onClick={() => saveSoul().catch(() => {})} disabled={!canEdit || writeBlocked || saving || !isDirty}>
                  {saving ? "保存中..." : "保存"}
                </Button>
              </CardFooter>
            </Card>

            <Card className="flex flex-col">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  Effective Preview {overlayActive && <Badge variant="secondary">overlay</Badge>}
                </CardTitle>
                <CardDescription>合并结果用于解释“实际生效”的 Soul。Overlay 仅用于预览。</CardDescription>
              </CardHeader>
              <CardContent className="flex-1 space-y-4">
                <div className="space-y-2">
                  <div className="text-sm font-medium">Overlay（可选）</div>
                  <textarea
                    className={cn(INPUT_TEXTAREA_CLASS, "min-h-[120px]")}
                    value={overlay}
                    onChange={(e) => setOverlay(e.target.value)}
                    placeholder="输入 overlay 内容后点击预览..."
                    disabled={previewing}
                  />
                  {previewError && (
                    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                      {previewError}
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    {overlayActive && (
                      <Button variant="outline" onClick={() => setPreview(null)} disabled={previewing || saving}>
                        清除预览
                      </Button>
                    )}
                    <Button onClick={() => previewOverlay().catch(() => {})} disabled={previewing || saving}>
                      {previewing ? "生成中..." : "预览"}
                    </Button>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium">Layers</div>
                  {effective.layers.length === 0 ? (
                    <div className="text-sm text-muted-foreground">（无）</div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {effective.layers.map((layer) => (
                        <Badge
                          key={`${layer.source}:${layer.precedence}:${layer.title}`}
                          variant="outline"
                          title={`precedence: ${layer.precedence}`}
                        >
                          {layer.title} · {layer.source}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium">Merged Content</div>
                  <textarea className={cn(READONLY_TEXTAREA_CLASS, "min-h-[300px]")} value={String(effective.merged_content || "")} readOnly />
                </div>
              </CardContent>
              <CardFooter className="flex items-center justify-between pt-4 border-t">
                <div className="text-xs text-muted-foreground">Preview 仅用于解释与验证；能力由 Policy 决定。</div>
                <Badge variant="outline">{String(effective.merged_content || "").length} chars</Badge>
              </CardFooter>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
