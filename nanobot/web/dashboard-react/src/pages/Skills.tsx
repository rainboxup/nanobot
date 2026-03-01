import { useEffect, useMemo, useState } from "react"
import { Code2, ExternalLink, Puzzle, Search, Copy } from "lucide-react"
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

export function Skills() {
  const { addToast } = useStore()

  const [skills, setSkills] = useState<SkillListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError("")
      try {
        const list = await api.get<SkillListItem[]>("/api/skills")
        if (!cancelled) setSkills(Array.isArray(list) ? list : [])
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

  async function openDetail(name: string) {
    setDetailLoading(true)
    setError("")
    try {
      const detail = await api.get<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`)
      setSelectedSkill(detail)
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载失败")
      addToast({ type: "error", message: msg })
      setSelectedSkill(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleCopyContent = () => {
    if (!selectedSkill?.content) return
    navigator.clipboard.writeText(selectedSkill.content)
    addToast({ type: "success", message: "已复制技能内容" })
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
                className="flex flex-col cursor-pointer transition-all hover:border-primary/50 hover:shadow-md"
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

