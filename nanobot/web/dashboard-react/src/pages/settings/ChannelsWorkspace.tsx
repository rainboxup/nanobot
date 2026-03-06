import { useEffect, useMemo, useState } from "react"
import { AlertTriangle, Copy, Edit2, RefreshCw } from "lucide-react"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
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
  writable?: boolean
  write_block_reason?: string | null
}

interface BindingInstructionsResponse {
  name: string
  channel: string
  instructions: string
  runtime_warning?: string
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

export function ChannelsWorkspace() {
  const { addToast } = useStore()
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
  }, [])

  const runtimeWarning = useMemo(() => {
    return String(channels.find((item) => item.runtime_warning)?.runtime_warning || "").trim()
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

  async function openBindingInstructions(name: string) {
    setBindingChannel(name)
    setBindingInstructions("")
    setBindingError("")
    try {
      const detail = await api.get<BindingInstructionsResponse>(
        `/api/channels/${encodeURIComponent(name)}/binding-instructions`
      )
      setBindingInstructions(String(detail.instructions || ""))
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : String((err as any)?.message || "Load failed")
      setBindingError(message)
    }
  }

  function copyBindingInstructions() {
    if (!bindingInstructions) return
    navigator.clipboard.writeText(bindingInstructions)
    addToast({ type: "success", message: "Binding instructions copied" })
  }

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
          {runtimeWarning && (
            <div className="inline-flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
              <span>{runtimeWarning}</span>
            </div>
          )}
        </div>
        <Button variant="outline" onClick={() => loadChannels().catch(() => {})} disabled={loading}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error && <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</div>}

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
            {channels.map((channel) => (
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
                    {!channel.writable && channel.write_block_reason && <div>{channel.write_block_reason}</div>}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => openBindingInstructions(channel.name)}>
                      <Copy className="mr-2 h-4 w-4" />
                      Binding
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openEditor(channel.name)}
                      disabled={!channel.writable}
                    >
                      <Edit2 className="mr-2 h-4 w-4" />
                      Edit
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
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
                placeholder="One sender ID per line. Leave empty to inherit the system rule."
                disabled={!editing.writable || saving}
              />
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
        isOpen={Boolean(bindingChannel)}
        onClose={() => setBindingChannel(null)}
        title={bindingChannel ? `${bindingChannel} binding instructions` : "Binding instructions"}
        description="Use !link to bind the current identity to an existing workspace."
        footer={
          <>
            <Button variant="outline" onClick={() => setBindingChannel(null)}>
              Close
            </Button>
            <Button onClick={copyBindingInstructions} disabled={!bindingInstructions}>
              <Copy className="mr-2 h-4 w-4" />
              Copy
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {bindingError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {bindingError}
            </div>
          )}
          {!bindingError && (
            <pre className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm leading-6">
              {bindingInstructions || "Loading..."}
            </pre>
          )}
        </div>
      </Drawer>
    </div>
  )
}
