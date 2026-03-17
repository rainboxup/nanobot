import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, Copy, Edit2, ExternalLink, RefreshCw } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { helpDocHref } from "@/src/pages/HelpDoc"
import { Badge } from "@/src/components/ui/badge"
import { Button } from "@/src/components/ui/button"
import { Drawer } from "@/src/components/ui/drawer"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/src/components/ui/table"

interface WorkspaceChannelItem {
  name: string
  enabled: boolean
  workspace_enabled: boolean
  system_enabled: boolean
  effective_enabled: boolean
  group_policy: "open" | "mention" | "allowlist"
  group_allow_from: string[]
  allow_from: string[]
  require_mention: boolean
  takes_effect?: string
  runtime_warning?: string
  help_slug?: string
  help_url?: string
  byo_supported?: boolean
  byo_configured?: boolean
  active_in_runtime?: boolean
  writable?: boolean
  write_block_reason?: string | null
}

interface WorkspaceChannelCredentialsDetail {
  name: string
  channel: string
  config: Record<string, string>
  configured: boolean
  byo_supported: boolean
  active_in_runtime: boolean
  redacted_value?: string
  sensitive_has_value?: Record<string, boolean>
  takes_effect?: string
  runtime_warning?: string
  help_slug?: string
  writable?: boolean
  write_block_reason?: string | null
}

interface WorkspaceBindingChallenge {
  code: string
  status: "pending" | "verified"
  expires_at?: string | null
  verified_identity?: string | null
  verification_command?: string | null
}

interface WorkspaceAccountBindingDetail {
  name: string
  channel: string
  account_id: string
  tenant_id: string
  identities: string[]
  proof_of_possession_supported?: boolean
  legacy_link_supported?: boolean
  active_challenge?: WorkspaceBindingChallenge | null
  runtime_warning?: string
  help_slug?: string
  help_url?: string
  writable?: boolean
  write_block_reason?: string | null
}

interface BoundaryRule {
  allowed: boolean
  scope: string
  summary: string
}

interface SecurityBoundariesPayload {
  role: string
  surfaces?: {
    workspace_channels?: {
      page_allowed?: boolean
      binding?: BoundaryRule
      workspace_routing?: BoundaryRule
      workspace_credentials?: BoundaryRule
      system_channels?: BoundaryRule
    }
  }
}

function toTextareaValue(items?: string[] | null) {
  return Array.isArray(items) ? items.join("\n") : ""
}

function parseTextareaList(value: string) {
  return value
    .split(/\r?\n|,/) 
    .map((item) => item.trim())
    .filter(Boolean)
}

function defaultBindingInstructions(channelName?: string | null) {
  const channelLabel = String(channelName || "target").trim() || "target"
  return [
    "Compatibility fallback (`!link`):",
    "1. In your current private chat/DM, send `!link` to generate a one-time code.",
    `2. In the target ${channelLabel} identity, send !link <CODE>.`,
    "3. Use `!whoami` if you need to inspect the current tenant and linked identities.",
  ].join("\n")
}

function bindingChallengeCommand(challenge?: WorkspaceBindingChallenge | null) {
  const code = String(challenge?.code || "").trim()
  if (!code) return ""
  const command = String(challenge?.verification_command || "").trim()
  return command || `!prove ${code}`
}

function boundaryScopeLabel(scope: string) {
  const normalized = String(scope || "").trim().toLowerCase()
  if (normalized === "current_account") return "current account"
  if (normalized === "current_tenant_admin") return "admin / owner"
  if (normalized === "owner_only") return "owner only"
  return normalized || "read-only"
}

const WORKSPACE_CREDENTIAL_FIELDS: Record<
  string,
  Array<{ name: string; label: string; sensitive?: boolean; placeholder?: string }>
> = {
  feishu: [
    { name: "app_id", label: "App ID", placeholder: "tenant-feishu-app-id" },
    { name: "app_secret", label: "App Secret", sensitive: true, placeholder: "Leave blank to keep existing secret" },
  ],
  dingtalk: [
    { name: "client_id", label: "Client ID", placeholder: "tenant-dingtalk-client-id" },
    { name: "client_secret", label: "Client Secret", sensitive: true, placeholder: "Leave blank to keep existing secret" },
  ],
}

export function ChannelsWorkspace() {
  const { user, addToast } = useStore()
  const [channels, setChannels] = useState<WorkspaceChannelItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const [editing, setEditing] = useState<WorkspaceChannelItem | null>(null)
  const [saving, setSaving] = useState(false)
  const [allowFromText, setAllowFromText] = useState("")
  const [groupAllowFromText, setGroupAllowFromText] = useState("")
  const [workspaceEnabled, setWorkspaceEnabled] = useState(true)
  const [groupPolicy, setGroupPolicy] = useState<WorkspaceChannelItem["group_policy"]>("mention")
  const [drawerError, setDrawerError] = useState("")

  const [bindingChannel, setBindingChannel] = useState<string | null>(null)
  const [bindingInstructions, setBindingInstructions] = useState("")
  const [bindingError, setBindingError] = useState("")
  const [bindingDetail, setBindingDetail] = useState<WorkspaceAccountBindingDetail | null>(null)
  const [bindingSaving, setBindingSaving] = useState(false)
  const [credentialsEditing, setCredentialsEditing] = useState<WorkspaceChannelCredentialsDetail | null>(null)
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({})
  const [credentialError, setCredentialError] = useState("")
  const [credentialSaving, setCredentialSaving] = useState(false)
  const [roleBoundaries, setRoleBoundaries] = useState<SecurityBoundariesPayload | null>(null)

  const actorRole = String(user?.role || "").toLowerCase()
  const isOwner = actorRole === "owner"
  const isAdmin = isOwner || actorRole === "admin"

  const workspaceBoundaries = useMemo(() => {
    return (
      roleBoundaries?.surfaces?.workspace_channels || {
        page_allowed: true,
        binding: {
          allowed: true,
          scope: "current_account",
          summary: "Members can bind or detach their own channel identities for the current account.",
        },
        workspace_routing: {
          allowed: isAdmin,
          scope: "current_tenant_admin",
          summary: isAdmin
            ? "Admins and owners can edit workspace routing and BYO credentials for the current tenant."
            : "Workspace routing and BYO credential edits require admin access in the current tenant.",
        },
        workspace_credentials: {
          allowed: isAdmin,
          scope: "current_tenant_admin",
          summary: isAdmin
            ? "Admins and owners can edit workspace routing and BYO credentials for the current tenant."
            : "Workspace routing and BYO credential edits require admin access in the current tenant.",
        },
        system_channels: {
          allowed: isOwner,
          scope: "owner_only",
          summary: "System channel settings and WeCom remain owner-managed in Platform Admin.",
        },
      }
    )
  }, [isAdmin, isOwner, roleBoundaries])

  async function loadRoleBoundaries() {
    try {
      const payload = await api.get<SecurityBoundariesPayload>("/api/security/boundaries")
      setRoleBoundaries(payload || null)
    } catch {
      setRoleBoundaries(null)
    }
  }

  async function loadChannels() {
    setLoading(true)
    setError("")
    try {
      const rows = await api.get<WorkspaceChannelItem[]>("/api/channels/workspace")
      setChannels(Array.isArray(rows) ? rows : [])
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Load failed")
      setError(message)
      setChannels([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadChannels().catch(() => {})
    loadRoleBoundaries().catch(() => {})
  }, [])

  const runtimeWarning = useMemo(() => {
    return String(channels.find((item) => item.runtime_warning)?.runtime_warning || "").trim()
  }, [channels])

  const helpHref = useMemo(() => {
    const helpSlug = String(channels.find((item) => item.help_slug)?.help_slug || "").trim()
    if (helpSlug) return helpDocHref(helpSlug)
    const externalUrl = String(channels.find((item) => item.help_url)?.help_url || "").trim()
    if (/^https?:\/\//i.test(externalUrl)) return externalUrl
    return helpDocHref("workspace-routing-and-binding")
  }, [channels])

  async function openEditor(name: string) {
    setDrawerError("")
    try {
      const detail = await api.get<WorkspaceChannelItem>(`/api/channels/${encodeURIComponent(name)}/routing`)
      setEditing(detail)
      setWorkspaceEnabled(Boolean(detail.workspace_enabled))
      setGroupPolicy(detail.group_policy)
      setAllowFromText(toTextareaValue(detail.allow_from))
      setGroupAllowFromText(toTextareaValue(detail.group_allow_from))
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Load failed")
      addToast({ type: "error", message })
    }
  }

  async function saveRouting() {
    if (!editing) return
    setSaving(true)
    setDrawerError("")
    try {
      await api.put(`/api/channels/${encodeURIComponent(editing.name)}/routing`, {
        enabled: workspaceEnabled,
        group_policy: groupPolicy,
        allow_from: parseTextareaList(allowFromText),
        group_allow_from: parseTextareaList(groupAllowFromText),
      })
      addToast({ type: "success", message: `${editing.name} routing saved` })
      setEditing(null)
      await loadChannels()
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Save failed")
      setDrawerError(message)
    } finally {
      setSaving(false)
    }
  }

  async function loadBindingDetail(name: string) {
    const accountBinding = await api.get<WorkspaceAccountBindingDetail>(
      `/api/channels/${encodeURIComponent(name)}/binding`
    )
    setBindingDetail(accountBinding)
  }

  async function openBindingInstructions(name: string) {
    setBindingChannel(name)
    setBindingInstructions(defaultBindingInstructions(name))
    setBindingError("")
    setBindingDetail(null)
    try {
      await loadBindingDetail(name)
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Load failed")
      setBindingError(message)
    }
  }

  async function refreshBindingDetail() {
    if (!bindingChannel) return
    setBindingSaving(true)
    setBindingError("")
    try {
      await loadBindingDetail(bindingChannel)
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Refresh failed")
      setBindingError(message)
    } finally {
      setBindingSaving(false)
    }
  }

  async function startBindingChallenge() {
    if (!bindingChannel) return
    setBindingSaving(true)
    setBindingError("")
    try {
      const detail = await api.post<WorkspaceAccountBindingDetail>(
        `/api/channels/${encodeURIComponent(bindingChannel)}/binding/challenges`,
        {}
      )
      setBindingDetail(detail)
      addToast({ type: "success", message: `${bindingChannel} verification code created` })
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Verification start failed")
      setBindingError(message)
    } finally {
      setBindingSaving(false)
    }
  }

  async function confirmBindingChallenge() {
    if (!bindingChannel || !bindingDetail?.active_challenge?.code) return
    setBindingSaving(true)
    setBindingError("")
    try {
      const detail = await api.post<WorkspaceAccountBindingDetail>(
        `/api/channels/${encodeURIComponent(bindingChannel)}/binding/confirm`,
        { code: bindingDetail.active_challenge.code }
      )
      setBindingDetail(detail)
      addToast({ type: "success", message: `${bindingChannel} identity attached` })
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Confirm failed")
      setBindingError(message)
    } finally {
      setBindingSaving(false)
    }
  }

  async function detachBindingIdentity(identity: string) {
    if (!bindingChannel) return
    const senderId = identity.includes(":") ? identity.split(":").slice(1).join(":") : identity
    setBindingSaving(true)
    setBindingError("")
    try {
      const detail = await api.post<WorkspaceAccountBindingDetail>(
        `/api/channels/${encodeURIComponent(bindingChannel)}/binding/detach`,
        { sender_id: senderId }
      )
      setBindingDetail(detail)
      addToast({ type: "success", message: `${bindingChannel} identity detached` })
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Detach failed")
      setBindingError(message)
    } finally {
      setBindingSaving(false)
    }
  }

  async function openCredentialsEditor(name: string) {
    setCredentialError("")
    setCredentialValues({})
    try {
      const detail = await api.get<WorkspaceChannelCredentialsDetail>(
        `/api/channels/${encodeURIComponent(name)}/credentials`
      )
      const nextValues: Record<string, string> = {}
      const redactedValue = String(detail.redacted_value || "****")
      for (const field of WORKSPACE_CREDENTIAL_FIELDS[detail.name] || []) {
        const raw = String(detail.config?.[field.name] || "")
        nextValues[field.name] = field.sensitive && raw === redactedValue ? "" : raw
      }
      setCredentialValues(nextValues)
      setCredentialsEditing(detail)
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Load failed")
      addToast({ type: "error", message })
    }
  }

  async function saveCredentials() {
    if (!credentialsEditing) return
    setCredentialSaving(true)
    setCredentialError("")
    try {
      const payload: Record<string, string> = {}
      for (const field of WORKSPACE_CREDENTIAL_FIELDS[credentialsEditing.name] || []) {
        payload[field.name] = String(credentialValues[field.name] || "")
      }
      const detail = await api.put<WorkspaceChannelCredentialsDetail>(
        `/api/channels/${encodeURIComponent(credentialsEditing.name)}/credentials`,
        payload
      )
      addToast({ type: "success", message: `${credentialsEditing.name} BYO credentials saved` })
      setCredentialsEditing(detail)
      await loadChannels()
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Save failed")
      setCredentialError(message)
    } finally {
      setCredentialSaving(false)
    }
  }

  function copyBindingInstructions() {
    const command = bindingChallengeCommand(bindingDetail?.active_challenge)
    const value = command || bindingInstructions
    if (!value) return
    navigator.clipboard.writeText(value)
    addToast({
      type: "success",
      message: command ? "Verification command copied" : "Fallback instructions copied",
    })
  }

  const proofOfPossessionSupported = Boolean(bindingDetail?.proof_of_possession_supported || bindingDetail?.active_challenge)
  const legacyLinkSupported = bindingDetail ? bindingDetail.legacy_link_supported !== false : true
  const verificationCommand = bindingChallengeCommand(bindingDetail?.active_challenge)

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-medium">Workspace Routing</h2>
            <Badge variant="outline">tenant scope</Badge>
            <Badge variant="outline">takes_effect: immediate</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Control whether Feishu and DingTalk messages can enter the current workspace.
          </p>
          <p className="text-sm text-muted-foreground">
            WeCom remains owner-managed in Platform Admin and does not support workspace BYO credentials or workspace routing in this MVP.
          </p>
          {runtimeWarning && (
            <div className="inline-flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <span>{runtimeWarning}</span>
            </div>
          )}
          <div className="rounded-md border border-warning/30 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-800 dark:text-yellow-300">
            Workspace routing only narrows inbound access for this workspace. System channel allowlists still apply before messages reach the workspace, so workspace sender IDs must stay within the system policy.
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline">
            <a href={helpHref} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-2 h-4 w-4" />
              Learn more
            </a>
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              loadChannels().catch(() => {})
              loadRoleBoundaries().catch(() => {})
            }}
            disabled={loading}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {error && <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}

      <div className="rounded-lg border bg-muted/20 p-4">
        <div className="flex items-center gap-2">
          <div className="font-medium">权限边界</div>
          <Badge variant={isOwner ? "success" : isAdmin ? "outline" : "secondary"}>
            {isOwner ? "Owner" : isAdmin ? "Admin" : "Member"}
          </Badge>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <div className="rounded-md border bg-card p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium">Identity binding</div>
              <Badge variant={workspaceBoundaries.binding?.allowed ? "success" : "secondary"}>
                {boundaryScopeLabel(String(workspaceBoundaries.binding?.scope || ""))}
              </Badge>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">
              {String(workspaceBoundaries.binding?.summary || "")}
            </div>
          </div>
          <div className="rounded-md border bg-card p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium">Workspace routing</div>
              <Badge variant={workspaceBoundaries.workspace_routing?.allowed ? "success" : "secondary"}>
                {boundaryScopeLabel(String(workspaceBoundaries.workspace_routing?.scope || ""))}
              </Badge>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">
              {String(workspaceBoundaries.workspace_routing?.summary || "")}
            </div>
          </div>
          <div className="rounded-md border bg-card p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium">System channels</div>
              <Badge variant={workspaceBoundaries.system_channels?.allowed ? "success" : "secondary"}>
                {boundaryScopeLabel(String(workspaceBoundaries.system_channels?.scope || ""))}
              </Badge>
            </div>
            <div className="mt-2 text-sm text-muted-foreground">
              {String(workspaceBoundaries.system_channels?.summary || "")}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Channel</TableHead>
              <TableHead>State</TableHead>
              <TableHead>Group Policy</TableHead>
              <TableHead>Allowlists</TableHead>
              <TableHead>Notes</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {channels.map((channel) => {
              const canEditWorkspace = Boolean(
                channel.writable && workspaceBoundaries.workspace_routing?.allowed
              )
              return (
              <TableRow key={channel.name}>
                <TableCell className="font-medium uppercase">{channel.name}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant={channel.system_enabled ? "success" : "secondary"}>
                      system {channel.system_enabled ? "on" : "off"}
                    </Badge>
                    <Badge variant={channel.workspace_enabled ? "success" : "secondary"}>
                      workspace {channel.workspace_enabled ? "on" : "off"}
                    </Badge>
                    <Badge variant={channel.effective_enabled ? "success" : "warning"}>
                      effective {channel.effective_enabled ? "on" : "off"}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="space-y-1 text-sm">
                    <div>{channel.group_policy}</div>
                    {channel.require_mention && (
                      <div className="text-xs text-muted-foreground">Bot mention required</div>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div>sender: {channel.allow_from?.length || 0}</div>
                    <div>group: {channel.group_allow_from?.length || 0}</div>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div>{String(channel.takes_effect || "immediate")}</div>
                    {channel.byo_supported && (
                      <div>
                        BYO {channel.byo_configured ? "configured" : "not configured"} · runtime {channel.active_in_runtime ? "active" : "inactive"}
                      </div>
                    )}
                    {!canEditWorkspace && (
                      <div>
                        {String(
                          channel.write_block_reason ||
                            workspaceBoundaries.workspace_routing?.summary ||
                            ""
                        )}
                      </div>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => openBindingInstructions(channel.name)}>
                      <Copy className="mr-2 h-4 w-4" />
                      Binding
                    </Button>
                    {channel.byo_supported && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => openCredentialsEditor(channel.name)}
                        disabled={!canEditWorkspace}
                      >
                        <Edit2 className="mr-2 h-4 w-4" />
                        Credentials
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openEditor(channel.name)}
                      disabled={!canEditWorkspace}
                    >
                      <Edit2 className="mr-2 h-4 w-4" />
                      Edit
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            )})}
            {!loading && channels.length === 0 && !error && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">
                  No workspace channels available.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Drawer
        isOpen={Boolean(editing)}
        onClose={() => setEditing(null)}
        title={editing ? `${editing.name} workspace routing` : "Workspace routing"}
        description="Updates take effect immediately for inbound messages in this workspace."
        footer={
          <>
            <Button variant="outline" onClick={() => setEditing(null)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={() => saveRouting().catch(() => {})} disabled={saving || !editing?.writable}>
              Save
            </Button>
          </>
        }
      >
        {editing && (
          <div className="space-y-4">
            {!editing.writable && editing.write_block_reason && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {editing.write_block_reason}
              </div>
            )}
            {drawerError && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                {drawerError}
              </div>
            )}

            <label className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
              <span>Enable workspace routing</span>
              <input
                type="checkbox"
                checked={workspaceEnabled}
                onChange={(event) => setWorkspaceEnabled(event.target.checked)}
                disabled={!editing.writable || saving}
              />
            </label>

            <label className="space-y-2 text-sm">
              <span className="font-medium">Group policy</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2"
                value={groupPolicy}
                onChange={(event) => setGroupPolicy(event.target.value as WorkspaceChannelItem["group_policy"])}
                disabled={!editing.writable || saving}
              >
                <option value="open">open: allow all group messages</option>
                <option value="mention">mention: require a bot mention</option>
                <option value="allowlist">allowlist: only allow selected groups</option>
              </select>
            </label>

            <label className="space-y-2 text-sm">
              <span className="font-medium">Allowed sender IDs</span>
              <textarea
                className="min-h-28 w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={allowFromText}
                onChange={(event) => setAllowFromText(event.target.value)}
                placeholder="One sender ID per line. Leave empty to avoid adding workspace restrictions; system channel allow_from still applies."
                disabled={!editing.writable || saving}
              />
              <div className="text-xs text-muted-foreground">
                If the system channel allow_from is non-empty, this list must remain a subset of that system allowlist.
              </div>
            </label>

            <label className="space-y-2 text-sm">
              <span className="font-medium">Allowed group IDs</span>
              <textarea
                className="min-h-28 w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={groupAllowFromText}
                onChange={(event) => setGroupAllowFromText(event.target.value)}
                placeholder="One group ID per line when group policy is allowlist."
                disabled={!editing.writable || saving || groupPolicy !== "allowlist"}
              />
            </label>
          </div>
        )}
      </Drawer>

      <Drawer
        isOpen={Boolean(credentialsEditing)}
        onClose={() => setCredentialsEditing(null)}
        title={credentialsEditing ? `${credentialsEditing.name} BYO credentials` : "Workspace BYO credentials"}
        description="Stored per workspace. Restart the service to load updated workspace runtimes; active_in_runtime reflects whether the current runtime matches the stored credentials."
        footer={
          <>
            <Button variant="outline" onClick={() => setCredentialsEditing(null)} disabled={credentialSaving}>
              Close
            </Button>
            <Button
              onClick={() => saveCredentials().catch(() => {})}
              disabled={credentialSaving || !credentialsEditing?.writable}
            >
              Save
            </Button>
          </>
        }
      >
        {credentialsEditing && (
          <div className="space-y-4">
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {String(
                credentialsEditing.runtime_warning ||
                  "Restart required. active_in_runtime reflects whether the current workspace runtime matches the stored credentials."
              )}
            </div>
            {!credentialsEditing.writable && credentialsEditing.write_block_reason && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {credentialsEditing.write_block_reason}
              </div>
            )}
            {credentialError && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                {credentialError}
              </div>
            )}
            <div className="rounded-md border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              Saving here updates tenant-scoped credentials. New values take effect after service restart; until then, existing channel connections continue using the currently loaded runtime.
            </div>
            {(WORKSPACE_CREDENTIAL_FIELDS[credentialsEditing.name] || []).map((field) => {
              const hasStoredSecret = Boolean(credentialsEditing.sensitive_has_value?.[field.name])
              return (
                <label key={field.name} className="space-y-2 text-sm block">
                  <span className="font-medium">{field.label}</span>
                  <input
                    className="w-full rounded-md border bg-background px-3 py-2"
                    type={field.sensitive ? "password" : "text"}
                    value={credentialValues[field.name] || ""}
                    onChange={(event) =>
                      setCredentialValues((prev) => ({ ...prev, [field.name]: event.target.value }))
                    }
                    placeholder={field.placeholder}
                    disabled={!credentialsEditing.writable || credentialSaving}
                  />
                  {field.sensitive && hasStoredSecret && (
                    <div className="text-xs text-muted-foreground">
                      Secret already stored. Leave blank to keep the existing value.
                    </div>
                  )}
                </label>
              )
            })}
          </div>
        )}
      </Drawer>

      <Drawer
        isOpen={Boolean(bindingChannel)}
        onClose={() => setBindingChannel(null)}
        title={bindingChannel ? `${bindingChannel} binding` : "Binding"}
        description="Preferred: create a short-lived verification code in the dashboard, prove it in a private chat, then confirm the verified identity here."
        footer={
          <>
            <Button variant="outline" onClick={() => setBindingChannel(null)}>
              Close
            </Button>
            <Button
              variant="outline"
              onClick={() => refreshBindingDetail().catch(() => {})}
              disabled={!bindingChannel || bindingSaving}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh status
            </Button>
            <Button
              onClick={copyBindingInstructions}
              disabled={!bindingInstructions && !verificationCommand}
            >
              <Copy className="mr-2 h-4 w-4" />
              {verificationCommand ? "Copy !prove" : "Copy fallback"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {bindingDetail && (
            <div className="space-y-3 rounded-md border bg-muted/20 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <div>Account: {bindingDetail.account_id || "-"}</div>
                <Badge variant="outline">Tenant: {bindingDetail.tenant_id || "-"}</Badge>
                {bindingDetail.proof_of_possession_supported && <Badge variant="success">proof-of-possession</Badge>}
              </div>

              <div className="space-y-2">
                <div className="font-medium">Verify a new identity</div>
                {!proofOfPossessionSupported ? (
                  <div className="space-y-2 rounded-md border bg-background px-3 py-3">
                    <p className="text-sm text-muted-foreground">
                      Dashboard verification is not available for this channel/runtime. Use the compatibility flow below.
                    </p>
                  </div>
                ) : !bindingDetail.active_challenge ? (
                  <div className="space-y-3 rounded-md border bg-background px-3 py-3">
                    <p className="text-sm text-muted-foreground">
                      Start a short-lived verification code, then send it privately from the target identity.
                    </p>
                    <Button onClick={() => startBindingChallenge().catch(() => {})} disabled={bindingSaving}>
                      Start verification
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-3 rounded-md border bg-background px-3 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={bindingDetail.active_challenge.status === "verified" ? "success" : "warning"}>
                        {bindingDetail.active_challenge.status}
                      </Badge>
                      {bindingDetail.active_challenge.expires_at && (
                        <span className="text-xs text-muted-foreground">
                          Expires at {bindingDetail.active_challenge.expires_at}
                        </span>
                      )}
                    </div>
                    <div className="rounded-md border bg-muted/30 px-3 py-2 font-mono text-sm">
                      {bindingDetail.active_challenge.code}
                    </div>
                    {bindingDetail.active_challenge.status === "pending" ? (
                      <div className="space-y-1 text-sm text-muted-foreground">
                        <div>In the target private chat/DM, send:</div>
                        <div className="font-mono text-foreground">{verificationCommand}</div>
                        <div>After sending it, click Refresh status here.</div>
                      </div>
                    ) : (
                      <div className="space-y-1 text-sm">
                        <div className="text-muted-foreground">Verified identity</div>
                        <div className="font-mono text-xs text-foreground">
                          {bindingDetail.active_challenge.verified_identity || "-"}
                        </div>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      {bindingDetail.active_challenge.status === "verified" ? (
                        <Button onClick={() => confirmBindingChallenge().catch(() => {})} disabled={bindingSaving}>
                          Confirm binding
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          onClick={() => refreshBindingDetail().catch(() => {})}
                          disabled={!bindingChannel || bindingSaving}
                        >
                          Refresh status
                        </Button>
                      )}
                      <Button variant="outline" onClick={() => startBindingChallenge().catch(() => {})} disabled={bindingSaving}>
                        New code
                      </Button>
                    </div>
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <div className="font-medium">Attached identities</div>
                {bindingDetail.identities.length === 0 ? (
                  <div className="text-muted-foreground">No identities attached yet.</div>
                ) : (
                  <div className="space-y-2">
                    {bindingDetail.identities.map((identity) => (
                      <div key={identity} className="flex items-center justify-between gap-2 rounded-md border bg-background px-3 py-2">
                        <span className="font-mono text-xs">{identity}</span>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={bindingSaving}
                          onClick={() => detachBindingIdentity(identity).catch(() => {})}
                        >
                          Detach
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
          {bindingError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {bindingError}
            </div>
          )}
          {legacyLinkSupported ? (
            <div className="rounded-md border bg-muted/30 p-3 text-sm leading-6">
              <div className="mb-2 font-medium">Compatibility fallback</div>
              <pre className="whitespace-pre-wrap text-sm leading-6">{bindingInstructions || "Loading..."}</pre>
            </div>
          ) : (
            <div className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
              Legacy `!link` fallback is not available for this channel.
            </div>
          )}
        </div>
      </Drawer>
    </div>
  )
}
