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
  path?: string
}

interface SkillDetail extends SkillListItem {
  content?: string
  metadata?: Record<string, any>
}

interface SkillCatalogItem {
  name: string
  description?: string
  source?: string
  installed?: boolean
  category?: string
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

function normalizeSkillSource(source?: string): string {
  const value = String(source || "").trim().toLowerCase()
  if (value === "workspace" || value === "builtin" || value === "store") return value
  return value || "-"
}

export function Skills() {
  const { addToast } = useStore()

  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [skillCatalog, setSkillCatalog] = useState<SkillCatalogItem[]>([])
  const [mcpCatalog, setMcpCatalog] = useState<MCPPresetItem[]>([])
  const [mcpServers, setMcpServers] = useState<MCPServerItem[]>([])
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

    async function fetchSkillCatalog() {
      const list = await api.get<SkillCatalogItem[]>("/api/skills/catalog")
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

    async function load() {
      setLoading(true)
      setError("")
      try {
        const [installedList, catalogList, mcpCatalogList, mcpServerList] = await Promise.all([
          fetchInstalledSkills(),
          fetchSkillCatalog(),
          fetchMcpCatalog(),
          fetchMcpServers(),
        ])
        if (cancelled) return
        setSkills(installedList)
        setSkillCatalog(catalogList)
        setMcpCatalog(mcpCatalogList)
        setMcpServers(mcpServerList)
      } catch (err) {
        const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
        if (!cancelled) setError(msg)
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
      return name.includes(q) || desc.includes(q)
    })
  }, [searchQuery, skillCatalog])

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

  const notifyRefreshFailed = (err: unknown) => {
    const detail = err instanceof ApiError ? err.detail : String((err as any)?.message || "")
    addToast({
      type: "warning",
      message: detail ? `操作已成功，刷新失败：${detail}` : "操作已成功，刷新失败",
    })
  }

  const markSkillInstalledLocally = (name: string, installed: boolean) => {
    setSkillCatalog((prev) => prev.map((item) => (item.name === name ? { ...item, installed } : item)))
    if (!installed) {
      setSkills((prev) => prev.filter((item) => item.name !== name))
      setSelectedSkill((prev) => (prev?.name === name ? null : prev))
    }
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

  async function reloadInstalledSkillsAndCatalog() {
    const [installedList, catalogList] = await Promise.all([
      api.get<SkillListItem[]>("/api/skills"),
      api.get<SkillCatalogItem[]>("/api/skills/catalog"),
    ])
    setSkills(Array.isArray(installedList) ? installedList : [])
    setSkillCatalog(Array.isArray(catalogList) ? catalogList : [])
  }

  async function reloadMcpData() {
    const [catalogList, serverList] = await Promise.all([
      api.get<MCPPresetItem[]>("/api/mcp/catalog"),
      api.get<MCPServerItem[]>("/api/mcp/servers"),
    ])
    setMcpCatalog(Array.isArray(catalogList) ? catalogList : [])
    setMcpServers(Array.isArray(serverList) ? serverList : [])
  }

  async function installSkill(name: string) {
    const key = `skill:install:${name}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    let succeeded = false
    try {
      await api.post("/api/skills/install", { name })
      succeeded = true
      markSkillInstalledLocally(name, true)
      addToast({ type: "success", message: `已安装 Skill：${name}` })
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
      await reloadInstalledSkillsAndCatalog()
    } catch (err) {
      notifyRefreshFailed(err)
    }
  }

  async function uninstallSkill(name: string) {
    const key = `skill:uninstall:${name}`
    if (mutationInFlightRef.current) return
    mutationInFlightRef.current = true
    setMutatingKey(key)
    let succeeded = false
    try {
      await api.delete(`/api/skills/${encodeURIComponent(name)}`)
      succeeded = true
      markSkillInstalledLocally(name, false)
      addToast({ type: "success", message: `已卸载 Skill：${name}` })
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
      await reloadInstalledSkillsAndCatalog()
    } catch (err) {
      notifyRefreshFailed(err)
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
      notifyRefreshFailed(err)
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
      notifyRefreshFailed(err)
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
                      {String(skill.source || "-")}
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
                        const sourceLabel = normalizeSkillSource(item.source)
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
                                  (isInstalled ? uninstallSkill(item.name) : installSkill(item.name)).catch(() => {})
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
        description={`${selectedSkill?.source ? `来源: ${String(selectedSkill.source)}` : ""}${
          selectedSkill?.path ? ` | 路径: ${String(selectedSkill.path)}` : ""
        }`}
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

