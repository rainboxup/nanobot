import { useEffect, useMemo, useRef, useState } from "react"
import { Code2, Copy, Download, ExternalLink, HardDrive, Puzzle, Search, Server, Trash2 } from "lucide-react"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"

import { api, ApiError } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Badge } from "@/src/components/ui/badge"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/src/components/ui/card"
import { Drawer } from "@/src/components/ui/drawer"
import { Input } from "@/src/components/ui/input"

interface SkillListItem {
  name: string
  description?: string
  source?: string
  origin_source?: string
  install_source?: string
  path?: string
}

interface SkillDetail extends SkillListItem {
  content?: string
  metadata?: Record<string, any>
}

interface SkillCatalogItem {
  name: string
  description?: string
  author?: string
  version?: string
  slug?: string
  source?: string
  origin_source?: string
  install_source?: string
  installed?: boolean
  category?: string
}

type SkillCatalogResponse = SkillCatalogItem[] | { items?: SkillCatalogItem[]; next_cursor?: string }
interface SkillCatalogV2Response {
  items?: SkillCatalogItem[]
  next_cursor?: string
  partial?: boolean
  warnings?: Array<{ source?: string; status_code?: number; detail?: string }>
}

interface MCPPresetItem {
  id: string
  name: string
  category?: string
  description?: string
  transport?: string
  installed?: boolean
}

interface MCPServerItem {
  name: string
  transport?: string
  command?: string
  url?: string
  tool_timeout?: number
}

interface OpenSpaceApiKeyStatus {
  server: string
  installed: boolean
  configured: boolean
  masked_api_key?: string | null
  takes_effect?: string
  writable: boolean
  write_block_reason_code?: string | null
  write_block_reason?: string | null
}

function groupByCategory<T extends { category?: string }>(items: T[]): Array<{ category: string; items: T[] }> {
  const map = new Map<string, T[]>()
  for (const item of items) {
    const category = String(item.category || "未分类").trim() || "未分类"
    const list = map.get(category)
    if (list) list.push(item)
    else map.set(category, [item])
  }
  return Array.from(map.entries())
    .map(([category, grouped]) => ({ category, items: grouped }))
    .sort((a, b) => a.category.localeCompare(b.category, "zh-CN"))
}

const INSTALL_CENTER_CARD_CLASS =
  "flex h-full flex-col border border-border/60 transition-all hover:border-primary/50 hover:shadow-md"
const INSTALL_CENTER_FOOTER_CLASS = "mt-auto flex items-center justify-between gap-2 border-t pt-3"

type InstallPayloadSource = "local" | "clawhub"

function normalizeSkillSourceToken(source?: string): string {
  const value = String(source || "").trim().toLowerCase()
  if (!value) return ""
  if (value === "store") return "managed"
  if (value === "builtin") return "bundled"
  return value
}

function normalizeSkillSource(source?: string): string {
  const normalized = normalizeSkillSourceToken(source)
  return normalized || "-"
}

function normalizeRawSkillSourceToken(source?: string): string {
  const value = String(source || "").trim().toLowerCase()
  return value || ""
}

function productizedSkillSourceLabel(source?: string): string {
  const normalized = normalizeSkillSourceToken(source)
  if (!normalized) return "-"
  if (normalized === "workspace") return "工作区"
  if (normalized === "managed") return "平台托管"
  if (normalized === "bundled") return "内置"
  if (normalized === "clawhub") return "ClawHub"
  return normalized
}

function resolveSkillSourceContract(item: Pick<SkillCatalogItem, "source" | "origin_source" | "install_source">): {
  source: string
  originSource: string
  installSource: string
  sourceLabel: string
  originSourceLabel: string
  installSourceLabel: string
  installPayloadSource: InstallPayloadSource | ""
} {
  const source = normalizeSkillSourceToken(item.source)
  const originSource = normalizeRawSkillSourceToken(item.origin_source)
  const installSource = normalizeSkillSourceToken(item.install_source)
  const preferredSource = installSource || source
  const installPayloadSource: InstallPayloadSource | "" =
    preferredSource === "clawhub"
      ? "clawhub"
      : preferredSource === "local" ||
          preferredSource === "workspace" ||
          preferredSource === "bundled" ||
          preferredSource === "managed"
        ? "local"
        : ""
  return {
    source,
    originSource,
    installSource,
    sourceLabel: productizedSkillSourceLabel(source),
    originSourceLabel: originSource || "-",
    installSourceLabel: installSource || "-",
    installPayloadSource,
  }
}

function formatSkillField(value?: string): string {
  const text = String(value || "").trim()
  return text || "-"
}

function normalizeSkillCatalogResponse(payload: SkillCatalogResponse | unknown): SkillCatalogItem[] {
  if (Array.isArray(payload)) return payload
  if (!payload || typeof payload !== "object") return []
  const items = (payload as { items?: unknown }).items
  return Array.isArray(items) ? (items as SkillCatalogItem[]) : []
}

function mergeSkillCatalogItems(localItems: SkillCatalogItem[], remoteItems: SkillCatalogItem[]): SkillCatalogItem[] {
  const merged = new Map<string, SkillCatalogItem>()
  for (const item of localItems) {
    const name = String(item.name || "").trim()
    if (!name) continue
    merged.set(name, item)
  }
  for (const item of remoteItems) {
    const name = String(item.name || "").trim()
    if (!name) continue
    const existing = merged.get(name)
    if (!existing) {
      merged.set(name, item)
      continue
    }
    merged.set(name, {
      ...existing,
      description: existing.description || item.description,
      author: existing.author || item.author,
      version: item.version || existing.version,
      slug: item.slug || existing.slug,
      install_source: existing.install_source || item.install_source,
    })
  }
  return Array.from(merged.values()).sort((a, b) => {
    const installedOrder = Number(Boolean(a.installed)) === Number(Boolean(b.installed)) ? 0 : a.installed ? -1 : 1
    if (installedOrder !== 0) return installedOrder
    return String(a.name || "").localeCompare(String(b.name || ""), "zh-CN")
  })
}

async function fetchCatalogV2Page(params: URLSearchParams): Promise<SkillCatalogV2Response> {
  const raw = await api.get<SkillCatalogV2Response>(`/api/skills/catalog/v2?${params.toString()}`)
  if (!raw || typeof raw !== "object") return { items: [] }
  return raw
}

async function fetchSkillCatalogFromV2(): Promise<SkillCatalogItem[]> {
  const localParams = new URLSearchParams({ source: "local", limit: "500", include_store_metadata: "false" })
  const localPayload = await fetchCatalogV2Page(localParams)
  const localItems = normalizeSkillCatalogResponse(localPayload)

  const remoteItems: SkillCatalogItem[] = []
  const maxPages = 20
  const maxItems = 2000
  let cursor: string | undefined
  for (let pageIndex = 0; pageIndex < maxPages; pageIndex += 1) {
    const remoteParams = new URLSearchParams({ source: "clawhub", limit: "200" })
    if (cursor) remoteParams.set("cursor", cursor)
    const remotePayload = await fetchCatalogV2Page(remoteParams)
    const pageItems = normalizeSkillCatalogResponse(remotePayload)
    if (pageItems.length > 0) remoteItems.push(...pageItems)
    if (remoteItems.length >= maxItems) break
    const nextCursor = String(remotePayload.next_cursor || "").trim() || undefined
    if (!nextCursor || nextCursor === cursor) break
    cursor = nextCursor
  }
  return mergeSkillCatalogItems(localItems, remoteItems)
}

async function fetchSkillCatalogWithFallback(): Promise<SkillCatalogItem[]> {
  try {
    return await fetchSkillCatalogFromV2()
  } catch {
    try {
      const latest = await api.get<SkillCatalogResponse>(
        "/api/skills/catalog?source=all&limit=200&include_store_metadata=false",
      )
      return normalizeSkillCatalogResponse(latest)
    } catch {
      const legacy = await api.get<SkillCatalogResponse>("/api/skills/catalog?include_store_metadata=false")
      return normalizeSkillCatalogResponse(legacy)
    }
  }
}

export function Skills() {
  const { addToast, user } = useStore()
  const role = String(user?.role || "member").trim().toLowerCase()
  const canSeeTechnicalSourceDetails = role === "owner" || role === "admin"
  const canManageOpenSpaceKey = role === "owner" || role === "admin"

  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [skillCatalog, setSkillCatalog] = useState<SkillCatalogItem[]>([])
  const [mcpCatalog, setMcpCatalog] = useState<MCPPresetItem[]>([])
  const [mcpServers, setMcpServers] = useState<MCPServerItem[]>([])
  const [openSpaceKeyStatus, setOpenSpaceKeyStatus] = useState<OpenSpaceApiKeyStatus | null>(null)
  const [openSpaceKeyInput, setOpenSpaceKeyInput] = useState("")
  const [openSpaceKeySaving, setOpenSpaceKeySaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [installTarget, setInstallTarget] = useState<"skill" | "mcp">("skill")
  const [mutatingKey, setMutatingKey] = useState("")
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [viewportWidth, setViewportWidth] = useState<number>(() =>
    typeof window === "undefined" ? 1280 : window.innerWidth
  )
  const detailRequestIdRef = useRef(0)
  const mutationInFlightRef = useRef(false)
  const selectedSkillSourceContract = selectedSkill ? resolveSkillSourceContract(selectedSkill) : null

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth)
    window.addEventListener("resize", onResize)
    return () => {
      window.removeEventListener("resize", onResize)
    }
  }, [])

  const collapsedCardCount = useMemo(() => {
    const columns = viewportWidth >= 1280 ? 4 : viewportWidth >= 1024 ? 3 : viewportWidth >= 640 ? 2 : 1
    return columns * 2
  }, [viewportWidth])

  useEffect(() => {
    let cancelled = false

    async function fetchInstalledSkills() {
      const list = await api.get<SkillListItem[]>("/api/skills")
      return Array.isArray(list) ? list : []
    }

    async function fetchMcpCatalog() {
      const list = await api.get<MCPPresetItem[]>("/api/mcp/catalog")
      return Array.isArray(list) ? list : []
    }

    async function fetchMcpServers() {
      const list = await api.get<MCPServerItem[]>("/api/mcp/servers")
      return Array.isArray(list) ? list : []
    }

    async function fetchOpenSpaceApiKeyStatus() {
      return await api.get<OpenSpaceApiKeyStatus>("/api/mcp/openspace/key")
    }

    async function load() {
      setLoading(true)
      setError("")
      try {
        const [installedResult, catalogResult, mcpCatalogResult, mcpServerResult] = await Promise.allSettled([
          fetchInstalledSkills(),
          fetchSkillCatalogWithFallback(),
          fetchMcpCatalog(),
          fetchMcpServers(),
        ])
        if (cancelled) return
        const loadErrors: string[] = []
        if (installedResult.status === "fulfilled") {
          setSkills(installedResult.value)
        } else {
          loadErrors.push(getErrorMessage(installedResult.reason, "技能列表加载失败"))
        }
        if (catalogResult.status === "fulfilled") {
          setSkillCatalog(catalogResult.value)
        } else {
          loadErrors.push(getErrorMessage(catalogResult.reason, "技能目录加载失败"))
        }
        if (mcpCatalogResult.status === "fulfilled") {
          setMcpCatalog(mcpCatalogResult.value)
        } else {
          loadErrors.push(getErrorMessage(mcpCatalogResult.reason, "MCP目录加载失败"))
        }
        if (mcpServerResult.status === "fulfilled") {
          setMcpServers(mcpServerResult.value)
        } else {
          loadErrors.push(getErrorMessage(mcpServerResult.reason, "MCP服务器加载失败"))
        }

        if (canManageOpenSpaceKey) {
          try {
            const status = await fetchOpenSpaceApiKeyStatus()
            if (!cancelled) setOpenSpaceKeyStatus(status)
          } catch (err) {
            loadErrors.push(getErrorMessage(err, "OpenSpace key load failed"))
          }
        } else {
          setOpenSpaceKeyStatus(null)
        }

        const coreLoadFailed =
          installedResult.status !== "fulfilled" &&
          catalogResult.status !== "fulfilled" &&
          mcpCatalogResult.status !== "fulfilled" &&
          mcpServerResult.status !== "fulfilled"

        if (coreLoadFailed) {
          setError(loadErrors[0] || "加载失败")
        } else if (loadErrors.length > 0) {
          addToast({ type: "warning", message: `部分数据加载失败：${loadErrors[0]}` })
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err, "加载失败"))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load().catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const filteredSkills = useMemo(() => {
    const q = String(searchQuery || "").trim().toLowerCase()
    if (!q) return skills
    return skills.filter((s) => {
      const name = String(s.name || "").toLowerCase()
      const desc = String(s.description || "").toLowerCase()
      return name.includes(q) || desc.includes(q)
    })
  }, [searchQuery, skills])

  const filteredSkillCatalog = useMemo(() => {
    const q = String(searchQuery || "").trim().toLowerCase()
    if (!q) return skillCatalog
    return skillCatalog.filter((item) => {
      const name = String(item.name || "").toLowerCase()
      const desc = String(item.description || "").toLowerCase()
      const author = String(item.author || "").toLowerCase()
      const version = String(item.version || "").toLowerCase()
      const sourceContract = resolveSkillSourceContract(item)
      const source = sourceContract.source
      const installSource = sourceContract.installSource
      const technicalSourceMatch =
        canSeeTechnicalSourceDetails &&
        (
          sourceContract.originSource.includes(q) ||
          installSource.includes(q) ||
          sourceContract.originSourceLabel.includes(q) ||
          sourceContract.installSourceLabel.includes(q)
        )
      return (
        name.includes(q) ||
        desc.includes(q) ||
        author.includes(q) ||
        version.includes(q) ||
        source.includes(q) ||
        sourceContract.sourceLabel.includes(q) ||
        technicalSourceMatch
      )
    })
  }, [canSeeTechnicalSourceDetails, searchQuery, skillCatalog])

  const filteredMcpCatalog = useMemo(() => {
    const q = String(searchQuery || "").trim().toLowerCase()
    if (!q) return mcpCatalog
    return mcpCatalog.filter((item) => {
      const name = String(item.name || "").toLowerCase()
      const desc = String(item.description || "").toLowerCase()
      const category = String(item.category || "").toLowerCase()
      return name.includes(q) || desc.includes(q) || category.includes(q)
    })
  }, [searchQuery, mcpCatalog])

  const mcpServerGroups = useMemo(
    () =>
      groupByCategory(
        mcpServers.map((server) => ({
          ...server,
          category: String(server.transport || "stdio").toLowerCase() === "http" ? "HTTP" : "Stdio",
        }))
      ),
    [mcpServers]
  )

  async function openDetail(name: string) {
    const reqId = detailRequestIdRef.current + 1
    detailRequestIdRef.current = reqId
    setDetailLoading(true)
    setError("")
    try {
      const detail = await api.get<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`)
      if (detailRequestIdRef.current !== reqId) return
      setSelectedSkill(detail)
    } catch (err) {
      if (detailRequestIdRef.current !== reqId) return
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      addToast({ type: "error", message: msg })
      setSelectedSkill(null)
    } finally {
      if (detailRequestIdRef.current !== reqId) return
      setDetailLoading(false)
    }
  }

  const handleCopyContent = () => {
    if (!selectedSkill?.content) return
    navigator.clipboard.writeText(selectedSkill.content)
    addToast({ type: "success", message: "已复制技能内容" })
  }

  const toggleGroup = (key: string) => {
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const isGroupExpanded = (key: string) => Boolean(expandedGroups[key])

  const getErrorMessage = (err: unknown, fallback: string) =>
    err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)

  const notifyRefreshIssues = (errors: string[]) => {
    const detail = errors.filter(Boolean).join("；")
    addToast({
      type: "warning",
      message: detail ? `操作已成功，刷新失败：${detail}` : "操作已成功，刷新失败",
    })
  }

  const applySkillMutationFallback = (
    item: Pick<SkillCatalogItem, "name" | "description" | "source" | "origin_source" | "install_source">,
    installed: boolean
  ) => {
    const skillName = String(item.name || "").trim()
    if (!skillName) return
    setSkillCatalog((prev) => {
      const next = prev.map((row) => (row.name === skillName ? { ...row, installed } : row))
      if (installed && !next.some((row) => row.name === skillName)) {
        next.unshift({
          name: skillName,
          description: item.description,
          source: item.source,
          origin_source: item.origin_source,
          install_source: item.install_source,
          installed: true,
          category: "已安装",
        })
      }
      return next
    })
    if (installed) {
      setSkills((prev) => {
        if (prev.some((row) => row.name === skillName)) return prev
        return [{ name: skillName, description: item.description, source: "workspace" }, ...prev]
      })
      return
    }
    setSkills((prev) => prev.filter((row) => row.name !== skillName))
    setSelectedSkill((prev) => (prev?.name === skillName ? null : prev))
  }

  const markMcpPresetInstalledLocally = (presetId: string, installed: boolean) => {
    setMcpCatalog((prev) => prev.map((item) => (item.id === presetId ? { ...item, installed } : item)))
  }

  const removeMcpServerLocally = (serverName: string) => {
    setMcpServers((prev) => prev.filter((server) => server.name !== serverName))
    setMcpCatalog((prev) =>
      prev.map((item) =>
        item.id === serverName || item.name === serverName ? { ...item, installed: false } : item
      )
    )
  }

  async function reloadInstalledSkillsAndCatalog(): Promise<{ skillsUpdated: boolean; catalogUpdated: boolean; errors: string[] }> {
    const [installedResult, catalogResult] = await Promise.allSettled([
      api.get<SkillListItem[]>("/api/skills"),
      fetchSkillCatalogWithFallback(),
    ])
    const errors: string[] = []
    let skillsUpdated = false
    let catalogUpdated = false
    if (installedResult.status === "fulfilled") {
      setSkills(Array.isArray(installedResult.value) ? installedResult.value : [])
      skillsUpdated = true
    } else {
      errors.push(getErrorMessage(installedResult.reason, "技能列表刷新失败"))
    }
    if (catalogResult.status === "fulfilled") {
      setSkillCatalog(catalogResult.value)
      catalogUpdated = true
    } else {
      errors.push(getErrorMessage(catalogResult.reason, "技能目录刷新失败"))
    }
    return { skillsUpdated, catalogUpdated, errors }
  }

  async function reloadMcpData() {
    const [catalogList, serverList] = await Promise.all([
      api.get<MCPPresetItem[]>("/api/mcp/catalog"),
      api.get<MCPServerItem[]>("/api/mcp/servers"),
    ])
    setMcpCatalog(Array.isArray(catalogList) ? catalogList : [])
    setMcpServers(Array.isArray(serverList) ? serverList : [])
    if (canManageOpenSpaceKey) {
      const status = await api.get<OpenSpaceApiKeyStatus>("/api/mcp/openspace/key")
      setOpenSpaceKeyStatus(status)
    } else {
      setOpenSpaceKeyStatus(null)
    }
  }

  async function saveOpenSpaceApiKey(clearExisting = false) {
    if (!canManageOpenSpaceKey || openSpaceKeySaving) return
    const normalized = String(openSpaceKeyInput || "").trim()
    if (!clearExisting && !normalized) {
      addToast({ type: "warning", message: "Please enter an OpenSpace API key." })
      return
    }
    setOpenSpaceKeySaving(true)
    try {
      const status = await api.put<OpenSpaceApiKeyStatus>("/api/mcp/openspace/key", {
        api_key: clearExisting ? null : normalized,
      })
      setOpenSpaceKeyStatus(status)
      setOpenSpaceKeyInput("")
      addToast({ type: "success", message: clearExisting ? "OpenSpace API key cleared" : "OpenSpace API key saved" })
    } catch (err) {
      addToast({ type: "error", message: getErrorMessage(err, "OpenSpace API key update failed") })
    } finally {
      setOpenSpaceKeySaving(false)
    }
  }

  async function installSkill(item: SkillCatalogItem) {
    const name = item.name
    const key = `skill:install:${name}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    try {
      const payload: Record<string, string> = { name }
      const sourceContract = resolveSkillSourceContract(item)
      const source = sourceContract.installPayloadSource
      const slug = String(item.slug || "").trim()
      const version = String(item.version || "").trim()
      if (source) payload.source = source
      if (source === "clawhub") {
        if (slug) payload.slug = slug
        if (version) payload.version = version
      }
      await api.post("/api/skills/install", payload)
      addToast({ type: "success", message: `已安装 Skill：${name}` })
      const refresh = await reloadInstalledSkillsAndCatalog()
      if (refresh.errors.length > 0) {
        applySkillMutationFallback(item, true)
        notifyRefreshIssues(refresh.errors)
      }
    } catch (err) {
      const msg = getErrorMessage(err, "安装失败")
      addToast({ type: "error", message: msg })
      return
    } finally {
      setMutatingKey("")
      mutationInFlightRef.current = false
    }
  }

  async function uninstallSkill(item: SkillCatalogItem) {
    const name = item.name
    const key = `skill:uninstall:${name}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    try {
      await api.delete(`/api/skills/${encodeURIComponent(name)}`)
      addToast({ type: "success", message: `已卸载 Skill：${name}` })
      const refresh = await reloadInstalledSkillsAndCatalog()
      if (refresh.errors.length > 0) {
        applySkillMutationFallback(item, false)
        notifyRefreshIssues(refresh.errors)
      }
    } catch (err) {
      const msg = getErrorMessage(err, "卸载失败")
      addToast({ type: "error", message: msg })
      return
    } finally {
      setMutatingKey("")
      mutationInFlightRef.current = false
    }
  }

  async function installMcp(presetId: string) {
    const key = `mcp:install:${presetId}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    let succeeded = false
    try {
      await api.post("/api/mcp/install", { preset: presetId })
      succeeded = true
      markMcpPresetInstalledLocally(presetId, true)
      addToast({ type: "success", message: `已安装 MCP：${presetId}` })
    } catch (err) {
      const msg = getErrorMessage(err, "安装失败")
      addToast({ type: "error", message: msg })
      return
    } finally {
      setMutatingKey("")
      mutationInFlightRef.current = false
    }

    if (!succeeded) return
    try {
      await reloadMcpData()
    } catch (err) {
      notifyRefreshIssues([getErrorMessage(err, "MCP数据刷新失败")])
    }
  }

  async function uninstallMcpServer(serverName: string) {
    const key = `mcp:uninstall:${serverName}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    let succeeded = false
    try {
      await api.delete(`/api/mcp/servers/${encodeURIComponent(serverName)}`)
      succeeded = true
      removeMcpServerLocally(serverName)
      addToast({ type: "success", message: `已卸载 MCP：${serverName}` })
    } catch (err) {
      const msg = getErrorMessage(err, "卸载失败")
      addToast({ type: "error", message: msg })
      return
    } finally {
      setMutatingKey("")
      mutationInFlightRef.current = false
    }

    if (!succeeded) return
    try {
      await reloadMcpData()
    } catch (err) {
      notifyRefreshIssues([getErrorMessage(err, "MCP数据刷新失败")])
    }
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col p-4 md:p-8 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">技能</h2>
            <p className="text-muted-foreground">查看当前已安装的技能与内容。</p>
          </div>
          <div className="relative w-full sm:w-72">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="搜索技能..."
              className="pl-8"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
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
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredSkills.map((skill) => (
              <Card
                key={skill.name}
                className="flex h-full cursor-pointer flex-col border border-border/60 transition-all hover:border-primary/50 hover:shadow-md"
                onClick={() => openDetail(skill.name).catch(() => {})}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <Puzzle className="h-5 w-5 text-primary" />
                      <CardTitle className="text-base">{skill.name}</CardTitle>
                    </div>
                    <Badge variant="secondary" className="text-[10px] uppercase">
                      {productizedSkillSourceLabel(skill.source)}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="flex-1 pb-3">
                  <CardDescription className="line-clamp-3 text-sm">
                    {skill.description || "（无描述）"}
                  </CardDescription>
                </CardContent>
                <CardFooter className="pt-0 flex items-center justify-between text-xs text-muted-foreground border-t mt-auto pt-3">
                  <span className="truncate">{skill.path ? String(skill.path) : ""}</span>
                  <span className="flex items-center gap-1 hover:text-foreground">
                    查看详情 <ExternalLink className="h-3 w-3" />
                  </span>
                </CardFooter>
              </Card>
            ))}
          </div>
        )}

        {!loading && filteredSkills.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="rounded-full bg-muted p-4 mb-4">
              <Puzzle className="h-8 w-8 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-medium">未找到技能</h3>
            <p className="text-sm text-muted-foreground">尝试调整搜索关键词。</p>
          </div>
        )}

        <Card>
          <CardHeader className="space-y-4">
            <div>
              <CardTitle className="text-lg">安装中心</CardTitle>
              <CardDescription>可在此安装/卸载 Skill 与 MCP，卡片样式与技能列表保持统一。</CardDescription>
            </div>
            <div className="inline-flex w-full rounded-md border p-1 sm:w-auto">
              <Button
                variant={installTarget === "skill" ? "default" : "ghost"}
                size="sm"
                onClick={() => setInstallTarget("skill")}
              >
                安装 Skill
              </Button>
              <Button
                variant={installTarget === "mcp" ? "default" : "ghost"}
                size="sm"
                onClick={() => setInstallTarget("mcp")}
              >
                安装 MCP
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-6">
            {installTarget === "skill" ? (
              groupByCategory(filteredSkillCatalog).map((group) => {
                const groupKey = `skill-group:${group.category}`
                const expanded = isGroupExpanded(groupKey)
                const shownItems = expanded ? group.items : group.items.slice(0, collapsedCardCount)
                return (
                  <div key={group.category} className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Puzzle className="h-4 w-4 text-primary" />
                        <h4 className="text-sm font-semibold">{group.category}</h4>
                      </div>
                      <Badge variant="secondary">{group.items.length}</Badge>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {shownItems.map((item) => {
                        const isInstalled = Boolean(item.installed)
                        const sourceContract = resolveSkillSourceContract(item)
                        const sourceLabel = sourceContract.sourceLabel
                        const authorLabel = formatSkillField(item.author)
                        const versionLabel = formatSkillField(item.version)
                        const installKey = `skill:install:${item.name}`
                        const uninstallKey = `skill:uninstall:${item.name}`
                        const isInstalling = mutatingKey === installKey
                        const isUninstalling = mutatingKey === uninstallKey
                        const isMutating = Boolean(mutatingKey)
                        return (
                          <Card key={item.name} className={INSTALL_CENTER_CARD_CLASS}>
                            <CardHeader className="pb-2">
                              <div className="flex items-start justify-between gap-2">
                                <CardTitle className="text-sm leading-5">{item.name}</CardTitle>
                                <div className="flex flex-wrap items-center justify-end gap-1">
                                  <Badge variant="outline" className="text-[10px]">
                                    {sourceLabel}
                                  </Badge>
                                  <Badge variant={isInstalled ? "default" : "secondary"} className="text-[10px]">
                                    {isInstalled ? "已安装" : "未安装"}
                                  </Badge>
                                </div>
                              </div>
                            </CardHeader>
                            <CardContent className="flex-1 pb-2">
                              <CardDescription className="line-clamp-2 text-xs">
                                {item.description || "（无描述）"}
                              </CardDescription>
                              <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                                <span className="truncate">作者：{authorLabel}</span>
                                <span className="truncate">版本：{versionLabel}</span>
                                {canSeeTechnicalSourceDetails ? (
                                  <span className="truncate col-span-2">技术详情请查看技能详情面板</span>
                                ) : null}
                              </div>
                            </CardContent>
                            <CardFooter className={INSTALL_CENTER_FOOTER_CLASS}>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => openDetail(item.name).catch(() => {})}
                              >
                                详情
                              </Button>
                              <Button
                                variant={isInstalled ? "destructive" : "default"}
                                size="sm"
                                disabled={isMutating}
                                onClick={() =>
                                  (isInstalled ? uninstallSkill(item) : installSkill(item)).catch(() => {})
                                }
                              >
                                {isInstalled ? (
                                  <Trash2 className="mr-1 h-3.5 w-3.5" />
                                ) : (
                                  <Download className="mr-1 h-3.5 w-3.5" />
                                )}
                                {isInstalled ? (isUninstalling ? "卸载中..." : "卸载") : isInstalling ? "安装中..." : "安装"}
                              </Button>
                            </CardFooter>
                          </Card>
                        )
                      })}
                    </div>
                    {group.items.length > collapsedCardCount && (
                      <Button variant="ghost" size="sm" onClick={() => toggleGroup(groupKey)}>
                        {expanded ? "收起" : "展开更多"}
                      </Button>
                    )}
                  </div>
                )
              })
            ) : (
              <div className="space-y-8">
                {groupByCategory(filteredMcpCatalog).map((group) => {
                  const groupKey = `mcp-group:${group.category}`
                  const expanded = isGroupExpanded(groupKey)
                  const shownItems = expanded ? group.items : group.items.slice(0, collapsedCardCount)
                  return (
                    <div key={group.category} className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Server className="h-4 w-4 text-primary" />
                          <h4 className="text-sm font-semibold">{group.category}</h4>
                        </div>
                        <Badge variant="secondary">{group.items.length}</Badge>
                      </div>
                      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                        {shownItems.map((item) => {
                          const isInstalled = Boolean(item.installed)
                          const installKey = `mcp:install:${item.id}`
                          const isInstalling = mutatingKey === installKey
                          const isMutating = Boolean(mutatingKey)
                          return (
                            <Card key={item.id} className={INSTALL_CENTER_CARD_CLASS}>
                              <CardHeader className="pb-2">
                                <div className="flex items-start justify-between gap-2">
                                  <CardTitle className="text-sm leading-5">{item.name}</CardTitle>
                                  <div className="flex flex-wrap items-center justify-end gap-1">
                                    <Badge variant="outline" className="text-[10px]">
                                      {String(item.transport || "stdio")}
                                    </Badge>
                                    <Badge variant={isInstalled ? "default" : "secondary"} className="text-[10px]">
                                      {isInstalled ? "已安装" : "未安装"}
                                    </Badge>
                                  </div>
                                </div>
                              </CardHeader>
                              <CardContent className="flex-1 pb-2">
                                <CardDescription className="line-clamp-2 text-xs">
                                  {item.description || "（无描述）"}
                                </CardDescription>
                              </CardContent>
                              <CardFooter className={`${INSTALL_CENTER_FOOTER_CLASS} justify-end`}>
                                <Button
                                  size="sm"
                                  disabled={isInstalled || isInstalling || isMutating}
                                  onClick={() => installMcp(item.id).catch(() => {})}
                                >
                                  <Download className="mr-1 h-3.5 w-3.5" />
                                  {isInstalled ? "已安装" : isInstalling ? "安装中..." : "安装"}
                                </Button>
                              </CardFooter>
                            </Card>
                          )
                        })}
                      </div>
                      {group.items.length > collapsedCardCount && (
                        <Button variant="ghost" size="sm" onClick={() => toggleGroup(groupKey)}>
                          {expanded ? "收起" : "展开更多"}
                        </Button>
                      )}
                    </div>
                  )
                })}

                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <HardDrive className="h-4 w-4 text-primary" />
                    <h4 className="text-sm font-semibold">已配置 MCP 服务器（支持卸载）</h4>
                  </div>
                  {canManageOpenSpaceKey ? (
                    <Card className={INSTALL_CENTER_CARD_CLASS}>
                      <CardHeader className="pb-2">
                        <div className="flex items-start justify-between gap-2">
                          <CardTitle className="text-sm">OpenSpace Cloud API Key</CardTitle>
                          <Badge
                            variant={openSpaceKeyStatus?.configured ? "default" : "secondary"}
                            className="text-[10px]"
                          >
                            {openSpaceKeyStatus?.configured ? "Configured" : "Not configured"}
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-2 text-xs text-muted-foreground">
                        <div>
                          {openSpaceKeyStatus?.installed
                            ? "Tenant-scoped OpenSpace key. Local mode still works when key is empty."
                            : "MCP server 'openspace' is not installed for this tenant."}
                        </div>
                        {openSpaceKeyStatus?.configured ? (
                          <div>Stored key: {openSpaceKeyStatus.masked_api_key || "********"}</div>
                        ) : null}
                        {openSpaceKeyStatus?.write_block_reason ? (
                          <div className="text-destructive">{openSpaceKeyStatus.write_block_reason}</div>
                        ) : null}
                        <Input
                          type="password"
                          placeholder="sk-..."
                          value={openSpaceKeyInput}
                          onChange={(e) => setOpenSpaceKeyInput(e.target.value)}
                          disabled={
                            !openSpaceKeyStatus?.installed ||
                            !openSpaceKeyStatus?.writable ||
                            openSpaceKeySaving
                          }
                        />
                      </CardContent>
                      <CardFooter className={`${INSTALL_CENTER_FOOTER_CLASS} justify-end`}>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={
                            !openSpaceKeyStatus?.installed ||
                            !openSpaceKeyStatus?.writable ||
                            openSpaceKeySaving ||
                            !openSpaceKeyStatus?.configured
                          }
                          onClick={() => saveOpenSpaceApiKey(true).catch(() => {})}
                        >
                          {openSpaceKeySaving ? "Clearing..." : "Clear"}
                        </Button>
                        <Button
                          size="sm"
                          disabled={
                            !openSpaceKeyStatus?.installed ||
                            !openSpaceKeyStatus?.writable ||
                            openSpaceKeySaving ||
                            !String(openSpaceKeyInput || "").trim()
                          }
                          onClick={() => saveOpenSpaceApiKey(false).catch(() => {})}
                        >
                          {openSpaceKeySaving ? "Saving..." : "Save key"}
                        </Button>
                      </CardFooter>
                    </Card>
                  ) : null}
                  {mcpServers.length === 0 ? (
                    <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                      暂无已安装 MCP 服务器
                    </div>
                  ) : (
                    mcpServerGroups.map((group) => {
                      const groupKey = `mcp-server-group:${group.category}`
                      const expanded = isGroupExpanded(groupKey)
                      const shownItems = expanded
                        ? group.items
                        : group.items.slice(0, collapsedCardCount)
                      return (
                        <div key={group.category} className="space-y-3">
                          <div className="flex items-center justify-between">
                            <h5 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                              {group.category}
                            </h5>
                            <Badge variant="secondary">{group.items.length}</Badge>
                          </div>
                          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                            {shownItems.map((server) => {
                              const uninstallKey = `mcp:uninstall:${server.name}`
                              const isUninstalling = mutatingKey === uninstallKey
                              const isMutating = Boolean(mutatingKey)
                              return (
                                <Card key={`${group.category}:${server.name}`} className={INSTALL_CENTER_CARD_CLASS}>
                                  <CardHeader className="pb-2">
                                    <div className="flex items-start justify-between gap-2">
                                      <CardTitle className="text-sm">{server.name}</CardTitle>
                                      <Badge variant="outline" className="text-[10px]">
                                        {String(server.transport || "stdio")}
                                      </Badge>
                                    </div>
                                  </CardHeader>
                                  <CardContent className="space-y-1 text-xs text-muted-foreground">
                                    {server.command && <div className="truncate">cmd: {server.command}</div>}
                                    {server.url && <div className="truncate">url: {server.url}</div>}
                                    <div>timeout: {Number(server.tool_timeout || 30)}s</div>
                                  </CardContent>
                                  <CardFooter className={`${INSTALL_CENTER_FOOTER_CLASS} justify-end`}>
                                    <Button
                                      variant="destructive"
                                      size="sm"
                                      disabled={isMutating}
                                      onClick={() => uninstallMcpServer(server.name).catch(() => {})}
                                    >
                                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                                      {isUninstalling ? "卸载中..." : "卸载"}
                                    </Button>
                                  </CardFooter>
                                </Card>
                              )
                            })}
                          </div>
                          {group.items.length > collapsedCardCount && (
                            <Button variant="ghost" size="sm" onClick={() => toggleGroup(groupKey)}>
                              {expanded ? "收起" : "展开更多"}
                            </Button>
                          )}
                        </div>
                      )
                    })
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Drawer
        isOpen={selectedSkill !== null}
        onClose={() => setSelectedSkill(null)}
        title={selectedSkill?.name || "技能详情"}
        description={(() => {
          const parts = [
            selectedSkill?.source ? `来源: ${productizedSkillSourceLabel(selectedSkill.source)}` : "",
          ].filter(Boolean)
          return parts.join(" | ")
        })()}
        footer={
          <>
            <Button variant="outline" onClick={() => setSelectedSkill(null)}>
              关闭
            </Button>
            <Button onClick={handleCopyContent} disabled={!selectedSkill?.content || detailLoading}>
              <Copy className="mr-2 h-4 w-4" /> 复制内容
            </Button>
          </>
        }
      >
        {detailLoading ? (
          <div className="text-sm text-muted-foreground">加载中...</div>
        ) : selectedSkill ? (
          <div className="space-y-6">
            <div>
              <h4 className="text-sm font-medium mb-2">描述</h4>
              <p className="text-sm text-muted-foreground">{selectedSkill.description || "（无描述）"}</p>
            </div>
            {canSeeTechnicalSourceDetails ? (
              <div>
                <h4 className="text-sm font-medium mb-2">技术详情</h4>
                <div className="grid grid-cols-1 gap-2 rounded-md border p-3 text-sm text-muted-foreground">
                  <div>来源：{normalizeSkillSource(selectedSkill.source)}</div>
                  <div>原始来源：{selectedSkillSourceContract?.originSourceLabel || "-"}</div>
                  <div>安装源：{selectedSkillSourceContract?.installSourceLabel || "-"}</div>
                  <div className="break-all">路径：{selectedSkill.path || "-"}</div>
                </div>
              </div>
            ) : null}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-sm font-medium flex items-center gap-2">
                  <Code2 className="h-4 w-4" /> 内容
                </h4>
              </div>
              <div className="rounded-md overflow-hidden border">
                <SyntaxHighlighter
                  language="markdown"
                  style={vscDarkPlus}
                  customStyle={{ margin: 0, padding: "1rem", fontSize: "0.875rem" }}
                >
                  {String(selectedSkill.content || "")}
                </SyntaxHighlighter>
              </div>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  )
}

