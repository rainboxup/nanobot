import { useCallback, useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"
import { FileText } from "lucide-react"

import { Button } from "@/src/components/ui/button"
import { ApiError, api } from "@/src/lib/api"
import { cn } from "@/src/lib/utils"

export type HelpDocSlug = string

type HelpDocSource = {
  kind: string
  path: string
}

type HelpDocSummary = {
  slug: string
  title: string
  source: HelpDocSource
}

type HelpDocPayload = HelpDocSummary & {
  markdown: string
}

const HELP_DESCRIPTIONS: Record<string, string> = {
  "workspace-routing-and-binding": "Routing rules, binding flow, and common troubleshooting steps.",
  "effective-policy-and-soul": "How 'effective' previews work and what they do (and do not) mean.",
  "managed-skill-store-integrity": "How local managed-store skills are validated before install, including manifest and integrity status.",
}

const REPO_DOC_TO_SLUG: Record<string, string> = {
  "docs/howto/workspace-routing-and-binding.md": "workspace-routing-and-binding",
  "docs/howto/effective-policy-and-soul.md": "effective-policy-and-soul",
  "docs/howto/managed-skill-store-integrity.md": "managed-skill-store-integrity",
}

export function helpDocPath(slug: HelpDocSlug): string {
  const normalized = String(slug || "").trim()
  return `/help/${encodeURIComponent(normalized)}`
}

export function helpDocHref(slug: HelpDocSlug): string {
  const basePath = typeof window === "undefined" ? "" : String(window.location.pathname || "")
  const normalizedBase = basePath.replace(/\/$/, "")
  return `${normalizedBase}/#${helpDocPath(slug)}`
}

function getErrorMessage(err: unknown, fallback = "Load failed"): string {
  return err instanceof ApiError ? err.detail : String((err as any)?.message || fallback)
}

function normalizeHelpSlug(raw: string | undefined): string {
  return String(raw || "").trim()
}

function normalizeMarkdownHref(href?: string | null): string | null {
  const value = String(href || "").trim()
  if (!value) return null
  if (/^(?:https?:|mailto:)/i.test(value)) return value

  const helpMatch = value.match(/(?:^|\/)#?\/?help\/([^/?#]+)/i)
  if (helpMatch && helpMatch[1]) {
    try {
      return helpDocHref(decodeURIComponent(helpMatch[1]))
    } catch {
      return helpDocHref(helpMatch[1])
    }
  }

  const normalizedRepoPath = value.replace(/^\.\//, "").replace(/^\//, "")
  const mappedSlug = REPO_DOC_TO_SLUG[normalizedRepoPath]
  if (mappedSlug) return helpDocHref(mappedSlug)

  return value
}

export function HelpDoc() {
  const { slug: rawSlug } = useParams<{ slug?: string }>()
  const slug = useMemo(() => normalizeHelpSlug(rawSlug), [rawSlug])

  const [list, setList] = useState<HelpDocSummary[]>([])
  const [doc, setDoc] = useState<HelpDocPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const loadCurrent = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      if (slug) {
        const data = await api.get<HelpDocPayload>(`/api/help/${encodeURIComponent(slug)}`)
        setDoc(data || null)
        setList([])
        return
      }
      const data = await api.get<HelpDocSummary[]>("/api/help")
      setList(Array.isArray(data) ? data : [])
      setDoc(null)
    } catch (err) {
      setDoc(null)
      setList([])
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    loadCurrent().catch(() => {})
  }, [loadCurrent])

  const title = doc?.title || (slug ? slug : "Help")
  const description = slug
    ? String(HELP_DESCRIPTIONS[doc?.slug || slug] || "Documentation & troubleshooting.")
    : "Documentation & troubleshooting."

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col overflow-y-auto bg-muted/10 p-4 md:p-8">
      <div className="mx-auto w-full max-w-5xl space-y-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-muted-foreground" />
              <h2 className="text-2xl font-bold tracking-tight">{title}</h2>
            </div>
            <p className="text-sm text-muted-foreground">{description}</p>
            {doc?.source?.path && (
              <p className="text-xs text-muted-foreground">Source: {doc.source.path}</p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {slug && (
              <Button asChild variant="outline">
                <Link to="/help">All topics</Link>
              </Button>
            )}
            <Button variant="outline" onClick={() => loadCurrent().catch(() => {})} disabled={loading}>
              Refresh
            </Button>
          </div>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-sm text-muted-foreground">Loading...</div>
        ) : slug ? (
          doc ? (
            <div className="rounded-lg border bg-card p-4 md:p-6">
              <div className="prose prose-sm dark:prose-invert max-w-none break-words">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ className, href, ...props }) => {
                      const nextHref = normalizeMarkdownHref(href) || href || "#"
                      const external = /^(?:https?:|mailto:)/i.test(String(nextHref || ""))
                      return (
                        <a
                          {...props}
                          href={nextHref}
                          className={cn("underline underline-offset-4", className)}
                          target={external ? (props.target ?? "_blank") : undefined}
                          rel={external ? (props.rel ?? "noreferrer") : undefined}
                        />
                      )
                    },
                    code({ inline, className, children, ...props }: any) {
                      const match = /language-(\w+)/.exec(className || "")
                      const codeString = String(children || "").replace(/\n$/, "")
                      if (!inline && match) {
                        return (
                          <div className="overflow-hidden rounded-md border !bg-zinc-950">
                            <SyntaxHighlighter
                              language={match[1]}
                              style={vscDarkPlus}
                              customStyle={{ margin: 0, padding: "1rem", fontSize: "0.875rem" }}
                            >
                              {codeString}
                            </SyntaxHighlighter>
                          </div>
                        )
                      }
                      return (
                        <code
                          {...props}
                          className={cn(
                            "rounded bg-muted px-[0.3rem] py-[0.2rem] font-mono text-sm",
                            className
                          )}
                        >
                          {children}
                        </code>
                      )
                    },
                  }}
                >
                  {String(doc.markdown || "")}
                </ReactMarkdown>
              </div>
            </div>
          ) : (
            <div className="rounded-lg border bg-card p-4 md:p-6">
              <div className="text-sm text-muted-foreground">Help doc not available.</div>
            </div>
          )
        ) : (
          <div className="rounded-lg border bg-card p-4 md:p-6">
            <div className="text-sm text-muted-foreground">Select a help topic:</div>
            <div className="mt-3 flex flex-col gap-2">
              {list.map((item) => (
                <Link
                  key={item.slug}
                  to={helpDocPath(item.slug)}
                  className="rounded-md border bg-muted/20 px-3 py-2 text-sm hover:bg-muted/30"
                >
                  <div className="font-medium">{item.title}</div>
                  {HELP_DESCRIPTIONS[item.slug] && (
                    <div className="mt-1 text-xs text-muted-foreground">{HELP_DESCRIPTIONS[item.slug]}</div>
                  )}
                </Link>
              ))}
              {!list.length && !error && (
                <div className="rounded-md border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
                  No help topics available.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
