import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, ExternalLink } from "lucide-react"

import { api, ApiError, formatTime } from "@/src/lib/api"
import { cn } from "@/src/lib/utils"
import { helpDocHref } from "@/src/pages/HelpDoc"
import { useStore } from "@/src/store/useStore"
import { Badge } from "@/src/components/ui/badge"
import { Button } from "@/src/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/src/components/ui/card"
import { Input } from "@/src/components/ui/input"
import {
  BaselineBadges,
  hasBaselineVersionId,
  normalizeBaselineMeta,
  resolveBaselineSelectionDefaults,
  type BaselineMeta,
} from "./baselineMeta"

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
  baseline?: BaselineMeta
}

interface SoulPreviewPayload {
  overlay?: string | null
  effective?: EffectiveSoul
}

interface BaselineVersionItem {
  version_id: string
  label?: string | null
  created_at?: string | null
  created_by?: string | null
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

function normalizeBaselineVersionItem(raw: unknown): BaselineVersionItem | null {
  if (!raw || typeof raw !== "object") return null
  const versionId = String((raw as any).version_id || (raw as any).id || "").trim()
  if (!versionId) return null
  const label = String((raw as any).label || "").trim()
  const createdBy = String((raw as any).created_by || "").trim()
  const createdAt = String((raw as any).created_at || "").trim()
  return {
    version_id: versionId,
    label: label || null,
    created_by: createdBy || null,
    created_at: createdAt || null,
  }
}

function normalizeBaselineVersionList(raw: unknown): BaselineVersionItem[] {
  const candidates: unknown[] =
    Array.isArray(raw)
      ? raw
      : Array.isArray((raw as any)?.versions)
        ? (raw as any).versions
        : Array.isArray((raw as any)?.items)
          ? (raw as any).items
          : []
  return candidates
    .map((item) => normalizeBaselineVersionItem(item))
    .filter((item): item is BaselineVersionItem => Boolean(item))
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
  const isOwner = role === "owner"
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
  const [baselineError, setBaselineError] = useState("")
  const [baselineVersionsLoading, setBaselineVersionsLoading] = useState(false)
  const [baselineVersions, setBaselineVersions] = useState<BaselineVersionItem[]>([])
  const [creatingBaselineVersion, setCreatingBaselineVersion] = useState(false)
  const [publishingBaseline, setPublishingBaseline] = useState(false)
  const [rollingBackBaseline, setRollingBackBaseline] = useState(false)
  const [newVersionNote, setNewVersionNote] = useState("")
  const [publishVersionId, setPublishVersionId] = useState("")
  const [publishStrategy, setPublishStrategy] = useState<"all" | "canary">("all")
  const [canaryPercent, setCanaryPercent] = useState("10")
  const [controlVersionId, setControlVersionId] = useState("")
  const [rollbackVersionId, setRollbackVersionId] = useState("")
  const [publishVersionTouched, setPublishVersionTouched] = useState(false)
  const [controlVersionTouched, setControlVersionTouched] = useState(false)
  const [rollbackVersionTouched, setRollbackVersionTouched] = useState(false)
  const [effectiveBaselineTenantId, setEffectiveBaselineTenantId] = useState("")
  const [effectiveBaseline, setEffectiveBaseline] = useState<BaselineMeta | null>(null)
  const [effectiveBaselineResolvedTenantId, setEffectiveBaselineResolvedTenantId] = useState("")
  const [effectiveBaselineLoading, setEffectiveBaselineLoading] = useState(false)
  const [effectiveBaselineError, setEffectiveBaselineError] = useState("")

  const hasSoulState = payload !== null
  const writable = Boolean(payload?.writable)
  const writeBlocked = hasSoulState && !writable
  const writeBlockedReason = String(payload?.write_block_reason || payload?.runtime_warning || "").trim()
  const soulInteractionBlocked = !hasSoulState

  const isDirty = useMemo(() => content !== baseContent, [content, baseContent])
  const baselineMeta = useMemo(() => normalizeBaselineMeta(payload?.baseline), [payload])

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

  async function loadBaselineVersions() {
    if (!isOwner) {
      setBaselineVersions([])
      return
    }
    setBaselineVersionsLoading(true)
    setBaselineError("")
    try {
      const data = await api.get<unknown>("/api/admin/baseline/versions")
      setBaselineVersions(normalizeBaselineVersionList(data))
    } catch (err) {
      const msg = getErrorMessage(err, "加载 baseline 版本失败")
      setBaselineError(msg)
    } finally {
      setBaselineVersionsLoading(false)
    }
  }

  async function createBaselineVersion() {
    if (!isOwner || !hasSoulState || creatingBaselineVersion) return
    setCreatingBaselineVersion(true)
    setBaselineError("")
    const label = String(newVersionNote || "").trim()
    try {
      await api.post("/api/admin/baseline/versions", label ? { label } : {})
      setNewVersionNote("")
      addToast({ type: "success", message: "Baseline 版本已创建" })
      await Promise.all([loadSoul(), loadBaselineVersions()])
    } catch (err) {
      const msg = getErrorMessage(err, "创建版本失败")
      setBaselineError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setCreatingBaselineVersion(false)
    }
  }

  async function publishBaselineRollout() {
    if (!isOwner || !hasSoulState || publishingBaseline) return
    const targetVersionId = String(publishVersionId || "").trim()
    if (!targetVersionId) {
      addToast({ type: "error", message: "请选择目标版本" })
      return
    }
    const nextPercent = Math.floor(Number(canaryPercent))
    if (publishStrategy === "canary" && (!Number.isFinite(nextPercent) || nextPercent < 0 || nextPercent > 100)) {
      addToast({ type: "error", message: "灰度百分比必须在 0-100 之间" })
      return
    }

    const payload: Record<string, unknown> = {
      strategy: publishStrategy,
      candidate_version_id: targetVersionId,
    }
    if (publishStrategy === "canary") {
      payload.canary_percent = nextPercent
      if (String(controlVersionId || "").trim()) {
        payload.control_version_id = String(controlVersionId).trim()
      }
    }

    setPublishingBaseline(true)
    setBaselineError("")
    try {
      await api.post("/api/admin/baseline/rollout", payload)
      addToast({ type: "success", message: "Baseline 发布策略已更新" })
      await Promise.all([loadSoul(), loadBaselineVersions()])
    } catch (err) {
      const msg = getErrorMessage(err, "发布失败")
      setBaselineError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setPublishingBaseline(false)
    }
  }

  async function rollbackBaseline() {
    if (!isOwner || !hasSoulState || rollingBackBaseline) return
    const targetVersionId = String(rollbackVersionId || "").trim()
    if (!targetVersionId) {
      addToast({ type: "error", message: "请选择回滚版本" })
      return
    }
    setRollingBackBaseline(true)
    setBaselineError("")
    try {
      await api.post("/api/admin/baseline/rollback", { version_id: targetVersionId })
      addToast({ type: "success", message: "Baseline 已回滚" })
      await Promise.all([loadSoul(), loadBaselineVersions()])
    } catch (err) {
      const msg = getErrorMessage(err, "回滚失败")
      setBaselineError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setRollingBackBaseline(false)
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
    if (!hasSoulState || previewing) return
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

  async function loadEffectiveBaseline() {
    if (!isOwner || !hasSoulState || effectiveBaselineLoading) return
    const targetTenantId = String(effectiveBaselineTenantId || "").trim()
    if (!targetTenantId) {
      const msg = "请输入 tenant_id"
      setEffectiveBaselineError(msg)
      addToast({ type: "error", message: msg })
      return
    }
    setEffectiveBaselineLoading(true)
    setEffectiveBaselineError("")
    try {
      const data = await api.get<unknown>(
        `/api/admin/baseline/effective?tenant_id=${encodeURIComponent(targetTenantId)}`
      )
      setEffectiveBaseline(normalizeBaselineMeta(data))
      setEffectiveBaselineResolvedTenantId(targetTenantId)
    } catch (err) {
      const msg = getErrorMessage(err, "加载 tenant baseline 失败")
      setEffectiveBaseline(null)
      setEffectiveBaselineError(msg)
      addToast({ type: "error", message: msg })
    } finally {
      setEffectiveBaselineLoading(false)
    }
  }

  useEffect(() => {
    void loadSoul()
  }, [])

  useEffect(() => {
    if (!isOwner) return
    void loadBaselineVersions()
  }, [isOwner])

  useEffect(() => {
    if (!isOwner) return
    const defaults = resolveBaselineSelectionDefaults(baselineMeta, baselineVersions)

    setPublishVersionId((prev) => {
      if (publishVersionTouched && hasBaselineVersionId(baselineVersions, prev)) return prev
      return defaults.publishVersionId
    })
    setRollbackVersionId((prev) => {
      if (rollbackVersionTouched && hasBaselineVersionId(baselineVersions, prev)) return prev
      return defaults.rollbackVersionId
    })
    setControlVersionId((prev) => {
      if (controlVersionTouched && hasBaselineVersionId(baselineVersions, prev)) return prev
      return defaults.controlVersionId
    })
  }, [
    isOwner,
    baselineMeta?.candidate_version_id,
    baselineMeta?.selected_version_id,
    baselineMeta?.effective_version_id,
    baselineMeta?.control_version_id,
    baselineVersions,
    publishVersionTouched,
    rollbackVersionTouched,
    controlVersionTouched,
  ])

  useEffect(() => {
    if (!isOwner) return
    const nextTenantId = String(payload?.subject?.tenant_id || "").trim()
    if (!nextTenantId) return
    setEffectiveBaselineTenantId((prev) => prev || nextTenantId)
  }, [isOwner, payload?.subject?.tenant_id])

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
          <a
            href={helpDocHref("effective-policy-and-soul")}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
          >
            了解 Effective Policy/Soul（解释性预览，不等于能力授权） <ExternalLink className="h-3 w-3" />
          </a>
        </div>
        <Button
          variant="outline"
          onClick={() =>
            Promise.all([loadSoul(), isOwner ? loadBaselineVersions() : Promise.resolve()]).catch(
              () => {}
            )
          }
          disabled={loading || saving || previewing || baselineVersionsLoading}
        >
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

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Baseline Rollout</CardTitle>
              <CardDescription>
                平台基线版本用于控制平台 Base Soul 与系统策略上限。Owner 可创建、灰度发布与回滚。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <BaselineBadges meta={baselineMeta} variant="secondary" />
              {baselineError && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                  {baselineError}
                </div>
              )}

              {isOwner && (
                <div className="space-y-3 rounded-md border border-muted/60 bg-muted/20 p-3">
                  <div className="space-y-1">
                    <div className="text-sm font-medium">Tenant Effective Baseline</div>
                    <div className="text-xs text-muted-foreground">
                      默认填充当前 tenant；可改成任意 tenant_id 查看 bucket / canary 命中结果。
                    </div>
                  </div>
                  <div className="grid gap-2 md:grid-cols-[1fr_auto]">
                    <Input
                      value={effectiveBaselineTenantId}
                      onChange={(e) => setEffectiveBaselineTenantId(e.target.value)}
                      placeholder="输入 tenant_id 查看 effective baseline"
                      disabled={soulInteractionBlocked || effectiveBaselineLoading}
                    />
                    <Button
                      variant="outline"
                      onClick={() => loadEffectiveBaseline().catch(() => {})}
                      disabled={
                        soulInteractionBlocked ||
                        effectiveBaselineLoading ||
                        !String(effectiveBaselineTenantId || "").trim()
                      }
                    >
                      {effectiveBaselineLoading ? "查询中..." : "查看 effective baseline"}
                    </Button>
                  </div>
                  {effectiveBaselineError && (
                    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                      {effectiveBaselineError}
                    </div>
                  )}
                  {effectiveBaseline ? (
                    <div className="space-y-2 rounded-md border bg-background p-3">
                      <div className="text-xs text-muted-foreground">
                        tenant: {effectiveBaselineResolvedTenantId || "-"}
                      </div>
                      <BaselineBadges meta={effectiveBaseline} variant="outline" />
                      <div className="text-xs text-muted-foreground">
                        该查询复用 `/api/admin/baseline/effective`，用于模拟指定 tenant 的 baseline 命中。
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">
                      输入 tenant_id 后可查看 effective / candidate / control / bucket 等 rollout 状态。
                    </div>
                  )}
                </div>
              )}

              {isOwner ? (
                <div className="space-y-4">
                  <div className="grid gap-2 md:grid-cols-[1fr_auto]">
                    <Input
                      value={newVersionNote}
                      onChange={(e) => setNewVersionNote(e.target.value)}
                      placeholder="版本标签（可选）"
                      disabled={soulInteractionBlocked || creatingBaselineVersion}
                    />
                    <Button
                      onClick={() => createBaselineVersion().catch(() => {})}
                      disabled={soulInteractionBlocked || creatingBaselineVersion}
                    >
                      {creatingBaselineVersion ? "创建中..." : "创建版本"}
                    </Button>
                  </div>

                  <div className="grid gap-2 md:grid-cols-2">
                    <label className="space-y-1 text-sm">
                      <span className="text-xs text-muted-foreground">目标版本</span>
                      <select
                        className="w-full rounded-md border bg-background px-3 py-2"
                        value={publishVersionId}
                        onChange={(e) => {
                          setPublishVersionTouched(true)
                          setPublishVersionId(e.target.value)
                        }}
                        disabled={soulInteractionBlocked || publishingBaseline || baselineVersionsLoading}
                      >
                        <option value="">请选择</option>
                        {baselineVersions.map((item) => (
                          <option key={item.version_id} value={item.version_id}>
                            {item.version_id}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="space-y-1 text-sm">
                      <span className="text-xs text-muted-foreground">发布策略</span>
                      <select
                        className="w-full rounded-md border bg-background px-3 py-2"
                        value={publishStrategy}
                        onChange={(e) => setPublishStrategy(e.target.value as "all" | "canary")}
                        disabled={soulInteractionBlocked || publishingBaseline}
                      >
                        <option value="all">all（全量）</option>
                        <option value="canary">canary（灰度）</option>
                      </select>
                    </label>
                  </div>

                  {publishStrategy === "canary" && (
                    <div className="grid gap-2 md:grid-cols-2">
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        value={canaryPercent}
                        onChange={(e) => setCanaryPercent(e.target.value)}
                        placeholder="灰度比例 0-100"
                        disabled={soulInteractionBlocked || publishingBaseline}
                      />
                      <select
                        className="w-full rounded-md border bg-background px-3 py-2"
                        value={controlVersionId}
                        onChange={(e) => {
                          setControlVersionTouched(true)
                          setControlVersionId(e.target.value)
                        }}
                        disabled={soulInteractionBlocked || publishingBaseline || baselineVersionsLoading}
                      >
                        <option value="">control 版本（可选）</option>
                        {baselineVersions.map((item) => (
                          <option key={`control:${item.version_id}`} value={item.version_id}>
                            {item.version_id}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={() => publishBaselineRollout().catch(() => {})}
                      disabled={soulInteractionBlocked || publishingBaseline || baselineVersionsLoading}
                    >
                      {publishingBaseline ? "发布中..." : "更新发布策略"}
                    </Button>
                    <select
                      className="min-w-[220px] rounded-md border bg-background px-3 py-2 text-sm"
                      value={rollbackVersionId}
                      onChange={(e) => {
                        setRollbackVersionTouched(true)
                        setRollbackVersionId(e.target.value)
                      }}
                      disabled={soulInteractionBlocked || rollingBackBaseline || baselineVersionsLoading}
                    >
                      <option value="">选择回滚版本</option>
                      {baselineVersions.map((item) => (
                        <option key={`rollback:${item.version_id}`} value={item.version_id}>
                          {item.version_id}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="outline"
                      onClick={() => rollbackBaseline().catch(() => {})}
                      disabled={soulInteractionBlocked || rollingBackBaseline || !rollbackVersionId}
                    >
                      {rollingBackBaseline ? "回滚中..." : "回滚"}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">当前角色只读；仅 Owner 可以调整 rollout。</div>
              )}

              <div className="space-y-2">
                <div className="text-sm font-medium">版本列表</div>
                {baselineVersionsLoading ? (
                  <div className="text-sm text-muted-foreground">加载中...</div>
                ) : baselineVersions.length === 0 ? (
                  <div className="text-sm text-muted-foreground">暂无版本</div>
                ) : (
                  <div className="space-y-1 rounded-md border p-2 text-xs">
                    {baselineVersions.slice(0, 8).map((item) => (
                      <div key={`ver:${item.version_id}`} className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline">{item.version_id}</Badge>
                        {item.label && <span className="text-muted-foreground">{item.label}</span>}
                        <span className="text-muted-foreground">
                          {formatTime(item.created_at || "")}
                        </span>
                        {item.created_by && (
                          <span className="text-muted-foreground">by {item.created_by}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
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
                  disabled={soulInteractionBlocked || !canEdit || writeBlocked || saving}
                />
                {!canEdit && <div className="text-sm text-muted-foreground">仅 Admin/Owner 可以修改。</div>}
                {canEdit && writeBlocked && (
                  <div className="text-sm text-muted-foreground">{writeBlockedReason || "当前模式不可写。"}</div>
                )}
              </CardContent>
              <CardFooter className="flex justify-end gap-2 pt-4 border-t">
                <Button
                  onClick={() => saveSoul().catch(() => {})}
                  disabled={soulInteractionBlocked || !canEdit || writeBlocked || saving || !isDirty}
                >
                  {saving ? "保存中..." : "保存"}
                </Button>
              </CardFooter>
            </Card>

            <Card className="flex flex-col">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2">
                  Effective Preview {overlayActive && <Badge variant="secondary">overlay</Badge>}
                </CardTitle>
                <CardDescription>
                  合并结果用于解释“实际生效”的 Soul。Overlay 仅用于预览，不会修改权限或授权能力。
                </CardDescription>
              </CardHeader>
              <CardContent className="flex-1 space-y-4">
                <div className="space-y-2">
                  <div className="text-sm font-medium">Overlay（可选）</div>
                  <textarea
                    className={cn(INPUT_TEXTAREA_CLASS, "min-h-[120px]")}
                    value={overlay}
                    onChange={(e) => setOverlay(e.target.value)}
                    placeholder="输入 overlay 内容后点击预览..."
                    disabled={soulInteractionBlocked || previewing}
                  />
                  {previewError && (
                    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                      {previewError}
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    {overlayActive && (
                      <Button
                        variant="outline"
                        onClick={() => setPreview(null)}
                        disabled={soulInteractionBlocked || previewing || saving}
                      >
                        清除预览
                      </Button>
                    )}
                    <Button
                      onClick={() => previewOverlay().catch(() => {})}
                      disabled={soulInteractionBlocked || previewing || saving}
                    >
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
