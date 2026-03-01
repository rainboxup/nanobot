import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"
import { AlertCircle, Bot, Copy, Send, User, Wifi, WifiOff } from "lucide-react"

import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { cn } from "@/src/lib/utils"
import { api, getAccessToken, tryRefreshAccessToken, wsUrl } from "@/src/lib/api"

interface Message {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  timestamp?: string | number
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [hasModels, setHasModels] = useState<boolean | null>(null)
  const [sessionId, setSessionId] = useState("")
  const [wsStatus, setWsStatus] = useState<"connecting" | "open" | "closed">("connecting")
  const [wsError, setWsError] = useState("")

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const { addToast } = useStore()

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  async function checkChatStatus() {
    try {
      const cfg = await api.get<any>("/api/chat/status")
      const kind = String(cfg && cfg.provider_kind ? cfg.provider_kind : "")
      const hasAnyKey = Boolean(cfg && cfg.has_any_api_key)
      if (kind === "oauth") {
        setHasModels(true)
        return
      }
      setHasModels(Boolean(hasAnyKey))
    } catch {
      setHasModels(null)
    }
  }

  async function loadHistory(nextSessionId: string) {
    if (!nextSessionId) return
    const history = await api.get<any[]>(
      `/api/chat/history?session_id=${encodeURIComponent(nextSessionId)}`
    )
    setMessages(
      (history || []).map((m, idx) => ({
        id: `h:${nextSessionId}:${idx}:${String(m.timestamp || "")}`,
        role: (m.role || "assistant") as any,
        content: String(m.content || ""),
        timestamp: m.timestamp,
      }))
    )
  }

  useEffect(() => {
    checkChatStatus().catch(() => {})
  }, [])

  useEffect(() => {
    let cancelled = false
    let reconnectTimer: number | null = null
    let attempts = 0

    const scheduleReconnect = (delayMs: number) => {
      if (cancelled) return
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      reconnectTimer = window.setTimeout(() => {
        connect().catch(() => {})
      }, delayMs)
    }

    const connect = async () => {
      const token = getAccessToken()
      if (!token) {
        setWsStatus("closed")
        return
      }

      setWsError("")
      setWsStatus("connecting")

      const ws = new WebSocket(wsUrl("/ws/chat"), ["nanobot", token])
      wsRef.current = ws

      ws.onopen = () => {
        attempts = 0
        setWsStatus("open")
      }

      ws.onclose = (ev) => {
        if (wsRef.current === ws) wsRef.current = null
        setWsStatus("closed")
        void (async () => {
          if (cancelled) return
          if (ev.code === 1008) {
            const refreshed = await tryRefreshAccessToken().catch(() => false)
            if (refreshed) {
              scheduleReconnect(200)
              return
            }
            setWsError("登录已失效，请重新登录。")
            return
          }
          attempts += 1
          const delay = Math.min(30_000, 800 * Math.pow(2, Math.min(attempts, 6)))
          scheduleReconnect(delay)
        })()
      }

      ws.onerror = () => {
        setWsError("WebSocket 错误")
      }

      ws.onmessage = (ev) => {
        const text = String(ev.data || "")
        try {
          const data = JSON.parse(text)
          if (data && data.type === "session" && data.session_id) {
            const sid = String(data.session_id)
            setSessionId(sid)
            loadHistory(sid).catch(() => {
              addToast({ type: "error", message: "加载历史消息失败" })
            })
            return
          }
          if (data && data.type === "error") {
            const msg = String(data.detail || data.error || "对话不可用")
            setWsError(msg)
            setMessages((prev) => [
              ...prev,
              { id: `e:${Date.now()}`, role: "system", content: msg, timestamp: Date.now() },
            ])
            return
          }
        } catch {
          // ignore
        }

        setMessages((prev) => [
          ...prev,
          { id: `a:${Date.now()}`, role: "assistant", content: text, timestamp: Date.now() },
        ])
      }
    }

    connect().catch(() => {})

    return () => {
      cancelled = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      const ws = wsRef.current
      wsRef.current = null
      try {
        ws?.close()
      } catch {
        // ignore
      }
    }
  }, [])

  const handleSend = () => {
    const text = (input || "").trim()
    if (!text) return

    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addToast({ type: "error", message: "未连接到服务器" })
      return
    }

    ws.send(text)
    setMessages((prev) => [
      ...prev,
      { id: `u:${Date.now()}`, role: "user", content: text, timestamp: Date.now() },
    ])
    setInput("")
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    addToast({ type: "success", message: "已复制到剪贴板" })
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {hasModels === false && (
        <div className="border-b bg-yellow-50 px-4 py-3 text-yellow-900 dark:bg-yellow-900/20 dark:text-yellow-100">
          <div className="mx-auto flex max-w-3xl items-start justify-between gap-4">
            <div>
              <div className="font-medium">未配置模型服务</div>
              <div className="text-sm opacity-90">请前往 设置 → 模型服务 填写 API Key 以启用回复。</div>
            </div>
            <Button asChild variant="outline" size="sm">
              <Link to="/settings/providers">打开设置</Link>
            </Button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-3xl space-y-6">
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              {wsStatus === "open" ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
              <span>
                {wsStatus === "open" ? "已连接" : wsStatus === "connecting" ? "连接中" : "未连接"}
              </span>
            </div>
            <div className="font-mono">{sessionId ? `session: ${sessionId}` : ""}</div>
          </div>

          {wsError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4" />
                {wsError}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={cn("flex w-full items-start gap-4", msg.role === "user" ? "flex-row-reverse" : "flex-row")}
            >
              <div
                className={cn(
                  "flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-md border shadow",
                  msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-background",
                  msg.role === "system" ? "border-destructive bg-destructive/10 text-destructive" : ""
                )}
              >
                {msg.role === "user" ? (
                  <User className="h-4 w-4" />
                ) : msg.role === "system" ? (
                  <AlertCircle className="h-4 w-4" />
                ) : (
                  <Bot className="h-4 w-4" />
                )}
              </div>
              <div
                className={cn(
                  "relative flex w-full max-w-[80%] flex-col gap-2 rounded-lg px-4 py-3 text-sm shadow-sm",
                  msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted/50",
                  msg.role === "system" ? "bg-destructive/10 text-destructive border border-destructive/20" : ""
                )}
              >
                {msg.role === "system" ? (
                  <div className="font-medium">{msg.content}</div>
                ) : (
                  <div className="prose prose-sm dark:prose-invert max-w-none break-words">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        code({ inline, className, children, ...props }: any) {
                          const match = /language-(\w+)/.exec(className || "")
                          const codeString = String(children).replace(/\n$/, "")
                          return !inline && match ? (
                            <div className="relative group mt-2 mb-2">
                              <div className="absolute right-2 top-2 z-10 opacity-0 transition-opacity group-hover:opacity-100">
                                <Button
                                  variant="secondary"
                                  size="icon"
                                  className="h-6 w-6 rounded-md bg-zinc-800 text-zinc-300 hover:bg-zinc-700 hover:text-white"
                                  onClick={() => copyToClipboard(codeString)}
                                >
                                  <Copy className="h-3 w-3" />
                                </Button>
                              </div>
                              <SyntaxHighlighter
                                {...props}
                                style={vscDarkPlus}
                                language={match[1]}
                                PreTag="div"
                                className="rounded-md !m-0 !bg-zinc-950"
                              >
                                {codeString}
                              </SyntaxHighlighter>
                            </div>
                          ) : (
                            <code
                              {...props}
                              className={cn("rounded bg-muted px-[0.3rem] py-[0.2rem] font-mono text-sm", className)}
                            >
                              {children}
                            </code>
                          )
                        },
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>
      <div className="border-t bg-background p-4 sm:p-6">
        <div className="mx-auto max-w-3xl relative flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息... (Shift + Enter 换行)"
            className="min-h-[60px] w-full resize-none rounded-md border border-input bg-background px-3 py-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            rows={1}
            style={{ height: "auto", maxHeight: "200px" }}
          />
          <Button
            size="icon"
            className="h-[60px] w-[60px] shrink-0"
            onClick={handleSend}
            disabled={!input.trim()}
          >
            <Send className="h-5 w-5" />
            <span className="sr-only">发送</span>
          </Button>
        </div>
      </div>
    </div>
  )
}
