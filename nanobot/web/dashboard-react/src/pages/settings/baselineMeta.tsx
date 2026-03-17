import type { BadgeProps } from "@/src/components/ui/badge"
import { Badge } from "@/src/components/ui/badge"
import { cn } from "@/src/lib/utils"

export interface BaselineMeta {
  selected_version_id?: string | null
  effective_version_id?: string | null
  candidate_version_id?: string | null
  strategy?: string | null
  canary_percent?: number | null
  control_version_id?: string | null
  bucket?: number | null
  is_canary?: boolean | null
}

export interface BaselineVersionLike {
  version_id: string
}

export interface BaselineSelectionDefaults {
  publishVersionId: string
  rollbackVersionId: string
  controlVersionId: string
}

function normalizePercent(raw: unknown): number | null {
  const n = Number(raw)
  if (!Number.isFinite(n)) return null
  return Math.min(100, Math.max(0, n))
}

function normalizeBucket(raw: unknown): number | null {
  const n = Number(raw)
  if (!Number.isFinite(n)) return null
  return Math.max(0, Math.floor(n))
}

export function normalizeBaselineMeta(raw: unknown): BaselineMeta | null {
  if (!raw || typeof raw !== "object") return null
  const selectedVersionId = String((raw as any).selected_version_id || "").trim()
  const effectiveVersionId = String((raw as any).effective_version_id || "").trim()
  const candidateVersionId = String((raw as any).candidate_version_id || "").trim()
  const strategy = String((raw as any).strategy || "").trim()
  const controlVersionId = String((raw as any).control_version_id || "").trim()
  return {
    selected_version_id: selectedVersionId || null,
    effective_version_id: effectiveVersionId || null,
    candidate_version_id: candidateVersionId || null,
    strategy: strategy || null,
    canary_percent: normalizePercent((raw as any).canary_percent),
    control_version_id: controlVersionId || null,
    bucket: normalizeBucket((raw as any).bucket),
    is_canary: typeof (raw as any).is_canary === "boolean" ? Boolean((raw as any).is_canary) : null,
  }
}

export function baselineLabel(value: unknown): string {
  const text = String(value || "").trim()
  return text || "-"
}

export function hasBaselineVersionId(
  versions: BaselineVersionLike[],
  versionId: string | null | undefined
): boolean {
  const targetVersionId = String(versionId || "").trim()
  if (!targetVersionId) return false
  return versions.some((item) => String(item.version_id || "").trim() === targetVersionId)
}

function resolveBaselineVersionId(
  preferredVersionId: string | null | undefined,
  versions: BaselineVersionLike[],
  fallbackVersionId = ""
): string {
  const targetVersionId = String(preferredVersionId || "").trim()
  if (hasBaselineVersionId(versions, targetVersionId)) return targetVersionId
  return String(fallbackVersionId || "").trim()
}

export function resolveBaselineSelectionDefaults(
  meta: BaselineMeta | null,
  versions: BaselineVersionLike[]
): BaselineSelectionDefaults {
  const firstVersionId = String(versions[0]?.version_id || "").trim()
  return {
    publishVersionId: resolveBaselineVersionId(
      meta?.candidate_version_id || meta?.selected_version_id,
      versions,
      firstVersionId
    ),
    rollbackVersionId: resolveBaselineVersionId(
      meta?.effective_version_id || meta?.selected_version_id,
      versions,
      firstVersionId
    ),
    controlVersionId: resolveBaselineVersionId(meta?.control_version_id, versions),
  }
}

export function BaselineBadges({
  meta,
  variant = "outline",
  className,
}: {
  meta: BaselineMeta | null
  variant?: BadgeProps["variant"]
  className?: string
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2 text-sm", className)}>
      <Badge variant={variant}>selected: {baselineLabel(meta?.selected_version_id)}</Badge>
      <Badge variant={variant}>effective: {baselineLabel(meta?.effective_version_id)}</Badge>
      <Badge variant={variant}>candidate: {baselineLabel(meta?.candidate_version_id)}</Badge>
      <Badge variant={variant}>control: {baselineLabel(meta?.control_version_id)}</Badge>
      <Badge variant={variant}>strategy: {baselineLabel(meta?.strategy || "all")}</Badge>
      <Badge variant={variant}>
        canary: {meta?.canary_percent == null ? "-" : `${meta.canary_percent}%`}
      </Badge>
      <Badge variant={variant}>bucket: {meta?.bucket == null ? "-" : String(meta.bucket)}</Badge>
      {meta?.is_canary != null && (
        <Badge variant={meta.is_canary ? "success" : "outline"}>
          {meta.is_canary ? "canary hit" : "control hit"}
        </Badge>
      )}
    </div>
  )
}
