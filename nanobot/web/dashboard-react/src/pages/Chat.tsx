import { useEffect, useMemo, useRef, useState } from "react"
import { Link } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter"
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism"
import {
  AlertCircle,
  Bot,
  Check,
  Copy,
  PanelLeft,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Send,
  Square,
  Trash2,
  User,
  Wifi,
  WifiOff,
  X,
} from "lucide-react"

import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Drawer } from "@/src/components/ui/drawer"
import { Input } from "@/src/components/ui/input"
import { Modal } from "@/src/components/ui/modal"
import { cn } from "@/src/lib/utils"
import {
  ApiError,
  api,
  getAccessToken,
  handleUnauthorized,
  tryRefreshAccessToken,
  wsUrl,
} from "@/src/lib/api"

interface Message {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  timestamp?: string | number
}

function messageFingerprint(message: Pick<Message, "role" | "content" | "timestamp">): string {
  const ts =
    message.timestamp === undefined || message.timestamp === null
      ? ""
      : String(message.timestamp)
  return `${message.role}|${ts}|${message.content}`
}

function mergeHistoryWithLive(history: Message[], live: Message[]): Message[] {
  if (!live.length) return history
  const seen = new Set(history.map((item) => messageFingerprint(item)))
  const merged = [...history]
  for (const item of live) {
    const key = messageFingerprint(item)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(item)
  }
  return merged
}

interface ChatSession {
  id: string
  title: string
  createdAt?: string | number
  updatedAt?: string | number
  preview?: string
}

function fallbackSessionName(sessionId: string): string {
  const tail = String(sessionId || "").split(":").pop() || String(sessionId || "")
  return `会话 ${tail.slice(-6) || "new"}`
}

function parseSession(item: any): ChatSession | null {
  if (!item || typeof item !== "object") return null
  const id = String(item.session_id || item.key || item.id || item.sessionId || "").trim()
  if (!id) return null

  const title = String(
    item.name ||
      item.title ||
      item.session_name ||
      item.display_name ||
      item.label ||
      item?.metadata?.title ||
      ""
  ).trim()
  const preview = String(
    item.preview || item.last_message || item.lastMessage || item.summary || ""
  ).trim()

  return {
    id,
    title: title || fallbackSessionName(id),
    createdAt: item.created_at || item.createdAt || "",
    updatedAt: item.updated_at || item.updatedAt || item.last_message_at || item.lastMessageAt || "",
    preview: preview || undefined,
  }
}

function sessionSortValue(session: ChatSession): number {
  const value = session.updatedAt || session.createdAt || ""
  if (typeof value === "number") return value
  const parsed = Date.parse(String(value || ""))
  return Number.isFinite(parsed) ? parsed : 0
}

function sortSessions(list: ChatSession[]): ChatSession[] {
  return [...list].sort((a, b) => sessionSortValue(b) - sessionSortValue(a))
}

function upsertSession(list: ChatSession[], session: ChatSession): ChatSession[] {
  const idx = list.findIndex((item) => item.id === session.id)
  if (idx < 0) return [...list, session]
  const next = [...list]
  next[idx] = { ...next[idx], ...session }
  return next
}

function formatSessionTime(value?: string | number): string {
  if (value === undefined || value === null || value === "") return "—"
  try {
    const d = typeof value === "number" ? new Date(value) : new Date(String(value))
    if (Number.isNaN(d.getTime())) return "—"
    return d.toLocaleString()
  } catch {
    return "—"
  }
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [hasModels, setHasModels] = useState<boolean | null>(null)
  const [activeSessionId, setActiveSessionId] = useState("")
  const [boundSessionId, setBoundSessionId] = useState("")
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionQuery, setSessionQuery] = useState("")
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [sessionsReady, setSessionsReady] = useState(false)
  const [sessionsError, setSessionsError] = useState("")
  const [historyLoading, setHistoryLoading] = useState(false)
  const [creatingSession, setCreatingSession] = useState(false)
  const [mobileSessionsOpen, setMobileSessionsOpen] = useState(false)
  const [renamingSessionId, setRenamingSessionId] = useState("")
  const [renameValue, setRenameValue] = useState("")
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null)
  const [sessionActionLoadingId, setSessionActionLoadingId] = useState("")
  const [wsStatus, setWsStatus] = useState<"connecting" | "open" | "closed">("connecting")
  const [wsError, setWsError] = useState("")
  const [awaitingResponse, setAwaitingResponse] = useState(false)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const historyReqRef = useRef(0)
  const sessionsReqRef = useRef(0)
  const activeSessionIdRef = useRef("")
  const boundSessionIdRef = useRef("")
  const authRetryCountRef = useRef(0)
  const awaitingResponseRef = useRef(false)
  const activeRequestIdRef = useRef("")

  const { addToast } = useStore()

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [activeSessionId, sessions]
  )

  const filteredSessions = useMemo(() => {
    const q = String(sessionQuery || "").trim().toLowerCase()
    if (!q) return sessions
    return sessions.filter((session) => {
      const haystack = [session.title, session.id, session.preview || ""].join(" ").toLowerCase()
      return haystack.includes(q)
    })
  }, [sessionQuery, sessions])

  const displaySessionId = boundSessionId || activeSessionId
  const isSessionSwitching = Boolean(activeSessionId) && activeSessionId !== boundSessionId
  const sessionActionLocked = creatingSession || Boolean(sessionActionLoadingId) || sessionsLoading
  const lastUserMessage = useMemo(
    () =>
      [...messages]
        .reverse()
        .find((msg) => msg.role === "user" && String(msg.content || "").trim() && !String(msg.content).trim().startsWith("/")) || null,
    [messages]
  )

  const setAwaitingResponseState = (next: boolean) => {
    awaitingResponseRef.current = next
    setAwaitingResponse(next)
  }

  const clearAwaitingResponseState = () => {
    activeRequestIdRef.current = ""
    setAwaitingResponseState(false)
  }

  const touchSession = (sessionId: string, patch?: Partial<ChatSession>) => {
    if (!sessionId) return
    setSessions((prev) => {
      const now = new Date().toISOString()
      const nextTitle = String(patch?.title || "").trim()
      const mergedPatch: Partial<ChatSession> = {
        ...patch,
        title: nextTitle || undefined,
        updatedAt: patch?.updatedAt ?? now,
      }
      const existing = prev.find((item) => item.id === sessionId)
      if (!existing) {
        const created: ChatSession = {
          id: sessionId,
          title: mergedPatch.title || fallbackSessionName(sessionId),
          createdAt: mergedPatch.createdAt || now,
          updatedAt: mergedPatch.updatedAt || now,
          preview: mergedPatch.preview,
        }
        return sortSessions([...prev, created])
      }
      const merged: ChatSession = {
        ...existing,
        ...mergedPatch,
        title: mergedPatch.title || existing.title || fallbackSessionName(sessionId),
      }
      return sortSessions(upsertSession(prev, merged))
    })
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId
  }, [activeSessionId])

  useEffect(() => {
    boundSessionIdRef.current = boundSessionId
  }, [boundSessionId])

  useEffect(() => {
    awaitingResponseRef.current = awaitingResponse
  }, [awaitingResponse])

  async function checkChatStatus() {
    try {
      const cfg = await api.get<any>("/api/chat/status")
      const kind = String(cfg && cfg.provider_kind ? cfg.provider_kind : "")
      const hasAnyKey = Boolean(cfg && cfg.has_any_api_key)
      if (kind === "oauth" || kind === "direct") {
        setHasModels(true)
        return
      }
      setHasModels(Boolean(hasAnyKey))
    } catch {
      setHasModels(null)
    }
  }

  async function fetchSessions(preferredSessionId?: string) {
    const reqId = sessionsReqRef.current + 1
    sessionsReqRef.current = reqId
    setSessionsLoading(true)
    setSessionsError("")
    try {
      const raw = await api.get<any[]>("/api/chat/sessions")
      const mapped = sortSessions(
        (Array.isArray(raw) ? raw : [])
          .map((item) => parseSession(item))
          .filter((item): item is ChatSession => Boolean(item))
      )
      if (sessionsReqRef.current !== reqId) return
      setSessions(mapped)
      setActiveSessionId((prev) => {
        const current = String(prev || "").trim()
        if (current && mapped.some((item) => item.id === current)) return current
        const preferred = String(preferredSessionId || "").trim()
        if (preferred && mapped.some((item) => item.id === preferred)) return preferred
        if (current) {
          const bound = String(boundSessionIdRef.current || "").trim()
          if (bound && bound === current) return current
          if (wsStatus !== "closed") return current
        }
        return mapped[0]?.id || ""
      })
      if (mapped.length === 0) {
        setBoundSessionId("")
      }
    } catch (err) {
      if (sessionsReqRef.current !== reqId) return
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载会话失败")
      setSessionsError(msg)
    } finally {
      if (sessionsReqRef.current === reqId) {
        setSessionsLoading(false)
        setSessionsReady(true)
      }
    }
  }

  async function loadHistory(nextSessionId: string) {
    if (!nextSessionId) {
      setMessages([])
      setHistoryLoading(false)
      return
    }

    const reqId = historyReqRef.current + 1
    historyReqRef.current = reqId
    setHistoryLoading(true)

    try {
      const history = await api.get<any[]>(
        `/api/chat/history?session_id=${encodeURIComponent(nextSessionId)}`
      )
      if (historyReqRef.current !== reqId) return
      const mapped = (history || []).map((m, idx) => ({
        id: `h:${nextSessionId}:${idx}:${String(m.timestamp || "")}`,
        role: (m.role || "assistant") as "user" | "assistant" | "system",
        content: String(m.content || ""),
        timestamp: m.timestamp,
      }))
      setMessages((prev) => mergeHistoryWithLive(mapped, prev))
    } catch (err) {
      if (historyReqRef.current !== reqId) return
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "加载历史消息失败")
      setMessages([])
      addToast({ type: "error", message: msg })
    } finally {
      if (historyReqRef.current === reqId) {
        setHistoryLoading(false)
      }
    }
  }

  async function handleCreateSession() {
    if (creatingSession || Boolean(sessionActionLoadingId)) return
    setCreatingSession(true)
    try {
      const raw = await api.post<any>("/api/chat/sessions")
      const parsed =
        parseSession(raw) ||
        parseSession(raw?.session) ||
        parseSession(raw?.data) ||
        (() => {
          const id = String(raw?.session_id || raw?.id || "").trim()
          if (!id) return null
          const now = new Date().toISOString()
          return {
            id,
            title: fallbackSessionName(id),
            createdAt: now,
            updatedAt: now,
          } as ChatSession
        })()

      if (!parsed) {
        throw new Error("创建会话失败：返回中缺少 session_id")
      }

      setSessions((prev) => sortSessions(upsertSession(prev, parsed)))
      setActiveSessionId(parsed.id)
      setRenamingSessionId("")
      setRenameValue("")
      setMessages([])
      setWsError("")
      setMobileSessionsOpen(false)
      addToast({ type: "success", message: "已创建新会话" })
      fetchSessions(parsed.id).catch(() => {})
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "创建会话失败")
      addToast({ type: "error", message: msg })
    } finally {
      setCreatingSession(false)
    }
  }

  const handleSelectSession = (sessionId: string) => {
    if (!sessionId || sessionId === activeSessionId) return
    if (sessionActionLocked || historyLoading) return
    setWsError("")
    setRenamingSessionId("")
    setRenameValue("")
    setMessages([])
    setHistoryLoading(true)
    setActiveSessionId(sessionId)
    setMobileSessionsOpen(false)
  }

  const beginRenameSession = (session: ChatSession) => {
    setRenamingSessionId(session.id)
    setRenameValue(session.title)
  }

  const cancelRenameSession = () => {
    setRenamingSessionId("")
    setRenameValue("")
  }

  async function submitRenameSession(session: ChatSession) {
    const nextName = String(renameValue || "").trim()
    if (!nextName) {
      addToast({ type: "error", message: "会话名称不能为空" })
      return
    }
    if (nextName === session.title) {
      cancelRenameSession()
      return
    }

    setSessionActionLoadingId(session.id)
    try {
      await api.patch(`/api/chat/sessions/${encodeURIComponent(session.id)}`, { title: nextName })
      touchSession(session.id, { title: nextName, updatedAt: new Date().toISOString() })
      cancelRenameSession()
      addToast({ type: "success", message: "会话已重命名" })
      fetchSessions(session.id).catch(() => {})
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "重命名失败")
      addToast({ type: "error", message: msg })
    } finally {
      setSessionActionLoadingId("")
    }
  }

  async function handleDeleteSession(session: ChatSession) {
    if (sessionActionLocked) return

    setSessionActionLoadingId(session.id)
    try {
      await api.delete(`/api/chat/sessions/${encodeURIComponent(session.id)}`)
      const remaining = sessions.filter((item) => item.id !== session.id)
      setSessions(remaining)
      if (renamingSessionId === session.id) {
        cancelRenameSession()
      }
      if (boundSessionId === session.id) {
        setBoundSessionId("")
      }
      if (activeSessionId === session.id) {
        const nextSessionId = remaining[0]?.id || ""
        setActiveSessionId(nextSessionId)
        setMessages([])
        setWsError("")
      }
      addToast({ type: "success", message: "会话已删除" })
      fetchSessions(activeSessionId === session.id ? remaining[0]?.id : activeSessionId).catch(() => {})
      setDeleteTarget(null)
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : String((err as any)?.message || "删除会话失败")
      addToast({ type: "error", message: msg })
    } finally {
      setSessionActionLoadingId("")
    }
  }

  useEffect(() => {
    checkChatStatus().catch(() => {})
  }, [])

  useEffect(() => {
    fetchSessions().catch(() => {
      setSessionsReady(true)
    })
  }, [])

  useEffect(() => {
    if (!activeSessionId) {
      historyReqRef.current += 1
      setHistoryLoading(false)
      setMessages([])
      setBoundSessionId("")
      clearAwaitingResponseState()
      return
    }
    clearAwaitingResponseState()
    loadHistory(activeSessionId).catch(() => {})
  }, [activeSessionId])

  useEffect(() => {
    if (!sessionsReady) return

    let cancelled = false
    let reconnectTimer: number | null = null
    let attempts = 0
    const targetSessionId = activeSessionId

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
        clearAwaitingResponseState()
        return
      }

      setWsError("")
      setWsStatus("connecting")

      const params = targetSessionId ? { session_id: targetSessionId } : undefined
      const ws = new WebSocket(wsUrl("/ws/chat", params), ["nanobot", token])
      wsRef.current = ws

      ws.onopen = () => {
        if (wsRef.current !== ws) return
        attempts = 0
        authRetryCountRef.current = 0
        if (targetSessionId) {
          setBoundSessionId(targetSessionId)
        }
        setWsStatus("open")
      }

      ws.onclose = (ev) => {
        if (wsRef.current !== ws) return
        wsRef.current = null
        setWsStatus("closed")
        clearAwaitingResponseState()
        if (ev.reason) {
          setWsError(String(ev.reason))
        }

        void (async () => {
          if (cancelled) return
          if (ev.code === 1008) {
            if (authRetryCountRef.current >= 1) {
              setWsError("鉴权失败，请重新登录。")
              handleUnauthorized("鉴权失败，请重新登录。")
              return
            }
            const refreshed = await tryRefreshAccessToken().catch(() => false)
            if (refreshed) {
              authRetryCountRef.current += 1
              scheduleReconnect(500)
              return
            }
            setWsError("登录已失效，请重新登录。")
            handleUnauthorized("登录已失效，请重新登录")
            return
          }
          attempts += 1
          const baseDelay = Math.min(30_000, 800 * Math.pow(2, Math.min(attempts, 6)))
          const reason = String(ev.reason || "")
          const isRateLimited = ev.code === 1013 || /too\s*many\s*requests/i.test(reason)
          const delay = isRateLimited ? Math.max(baseDelay, 10_000) : baseDelay
          scheduleReconnect(delay)
        })()
      }

      ws.onerror = () => {
        if (wsRef.current !== ws) return
        setWsError("WebSocket 错误")
        clearAwaitingResponseState()
      }

      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return
        const text = String(ev.data || "")
        let data: any = null
        try {
          data = JSON.parse(text)
        } catch {
          data = null
        }

        if (data && data.type === "session" && data.session_id) {
          const sid = String(data.session_id)
          setBoundSessionId(sid)
          setActiveSessionId((prev) => (prev === sid ? prev : sid))
          touchSession(sid, {
            title: String(data.title || data.name || "").trim() || undefined,
            updatedAt: data.updated_at || data.updatedAt || new Date().toISOString(),
          })
          return
        }

        if (data && data.type === "request") {
          const acceptedRequestId = String(data.request_id || "")
          if (acceptedRequestId && activeRequestIdRef.current === acceptedRequestId) {
            setAwaitingResponseState(true)
          }
          return
        }

        if (data && data.type === "error") {
          clearAwaitingResponseState()
          const msg = String(data.detail || data.error || "对话不可用")
          setWsError(msg)
          setMessages((prev) => [
            ...prev,
            { id: `e:${Date.now()}`, role: "system", content: msg, timestamp: Date.now() },
          ])
          return
        }

        if (data && typeof data === "object") {
          const metadata = data.metadata && typeof data.metadata === "object" ? data.metadata : {}
          const requestId = String(metadata.web_request_id || data.request_id || "")
          const responseState = String(metadata._response_state || "")
          const isProgress = Boolean(metadata._progress) || responseState === "delta"
          const isTerminal =
            responseState === "completed" ||
            responseState === "failed" ||
            responseState === "stopped" ||
            (!isProgress && responseState !== "delta")

          const maybeContent =
            data.content !== undefined && data.content !== null
              ? String(data.content)
              : data.message !== undefined && data.message !== null
                ? String(data.message)
                : ""
          if (maybeContent) {
            const role =
              data.role === "user" || data.role === "assistant" || data.role === "system"
                ? data.role
                : "assistant"
            const ts = data.timestamp || Date.now()
            setMessages((prev) => [
              ...prev,
              { id: `m:${Date.now()}`, role, content: maybeContent, timestamp: ts },
            ])
            const sid = String(
              data.session_id || boundSessionIdRef.current || activeSessionIdRef.current || ""
            )
            if (sid) {
              touchSession(sid, {
                preview: maybeContent,
                updatedAt: data.updated_at || data.updatedAt || ts,
              })
            }
            if (awaitingResponseRef.current) {
              const activeRequestId = String(activeRequestIdRef.current || "")
              const stopTargetRequestId = String(metadata.web_stop_target_request_id || "")
              const requestMatched = Boolean(activeRequestId && requestId && activeRequestId === requestId)
              const stopMatched =
                responseState === "stopped" &&
                (!activeRequestId || !stopTargetRequestId || stopTargetRequestId === activeRequestId)
              if (stopMatched || (requestMatched && isTerminal) || (!activeRequestId && isTerminal)) {
                clearAwaitingResponseState()
              }
            }
            return
          }
        }

        setMessages((prev) => [
          ...prev,
          { id: `a:${Date.now()}`, role: "assistant", content: text, timestamp: Date.now() },
        ])
        if (awaitingResponseRef.current) {
          clearAwaitingResponseState()
        }
        const sid = String(boundSessionIdRef.current || activeSessionIdRef.current || "")
        if (sid) {
          touchSession(sid, { preview: text, updatedAt: new Date().toISOString() })
        }
      }
    }

    connect().catch(() => {})

    return () => {
      cancelled = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      clearAwaitingResponseState()
      const ws = wsRef.current
      wsRef.current = null
      try {
        ws?.close()
      } catch {
        // ignore
      }
    }
  }, [activeSessionId, sessionsReady])

  function sendMessage(
    rawText: string,
    options?: { appendUserMessage?: boolean; markAwaiting?: boolean; targetRequestId?: string }
  ): boolean {
    const text = String(rawText || "").trim()
    if (!text) return false

    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addToast({ type: "error", message: "未连接到服务器" })
      return false
    }
    if (activeSessionId && activeSessionId !== boundSessionId) {
      addToast({ type: "error", message: "会话切换中，请稍后再试" })
      return false
    }

    const requestId = `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    const payload: Record<string, string> = { type: "chat", content: text, request_id: requestId }
    const targetRequestId = String(options?.targetRequestId || "").trim()
    if (targetRequestId) {
      payload.target_request_id = targetRequestId
    }
    ws.send(JSON.stringify(payload))

    const appendUserMessage = options?.appendUserMessage !== false
    if (appendUserMessage) {
      const now = Date.now()
      setMessages((prev) => [
        ...prev,
        { id: `u:${now}`, role: "user", content: text, timestamp: now },
      ])
      const sid = String(activeSessionId || boundSessionId || "")
      if (sid) {
        touchSession(sid, { preview: text, updatedAt: now })
      }
    }

    if (options?.markAwaiting !== false) {
      activeRequestIdRef.current = requestId
      setAwaitingResponseState(true)
    }
    return true
  }

  const handleSend = () => {
    const ok = sendMessage(input, { appendUserMessage: true, markAwaiting: true })
    if (ok) setInput("")
  }

  const handleStopGeneration = () => {
    const targetRequestId = String(activeRequestIdRef.current || "")
    const ok = sendMessage("/stop", {
      appendUserMessage: false,
      markAwaiting: false,
      targetRequestId,
    })
    if (!ok) return
    addToast({ type: "info", message: "已发送停止请求" })
  }

  const handleRetryLastUser = () => {
    if (!lastUserMessage?.content) {
      addToast({ type: "error", message: "没有可重试的上一条用户消息" })
      return
    }
    sendMessage(lastUserMessage.content, { appendUserMessage: true, markAwaiting: true })
  }

  const handleResend = (content: string) => {
    sendMessage(content, { appendUserMessage: true, markAwaiting: true })
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      if (awaitingResponse) return
      handleSend()
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    addToast({ type: "success", message: "已复制到剪贴板" })
  }

  const renderSessionPanel = () => (
    <>
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">会话</div>
        <Button size="sm" onClick={() => handleCreateSession().catch(() => {})} disabled={sessionActionLocked}>
          <Plus className="mr-1 h-4 w-4" />
          新建
        </Button>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          value={sessionQuery}
          onChange={(e) => setSessionQuery(e.target.value)}
          className="pl-8"
          placeholder="搜索会话..."
        />
      </div>

      {sessionsError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
          {sessionsError}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
        {sessionsLoading && sessions.length === 0 ? (
          <div className="text-sm text-muted-foreground">加载会话中...</div>
        ) : filteredSessions.length === 0 ? (
          <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
            {sessionQuery.trim() ? "未找到匹配会话" : "暂无会话，点击“新建”开始"}
          </div>
        ) : (
          filteredSessions.map((session) => {
            const isActive = session.id === activeSessionId
            const isRenaming = session.id === renamingSessionId
            const isActionLoading = session.id === sessionActionLoadingId
            const isSessionLocked = sessionActionLocked || historyLoading
            return (
              <div
                key={session.id}
                className={cn(
                  "group rounded-md border bg-background p-2 transition-colors",
                  isActive
                    ? "border-primary shadow-sm"
                    : "border-border hover:border-muted-foreground/30"
                )}
              >
                {isRenaming ? (
                  <div className="space-y-2">
                    <Input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          submitRenameSession(session).catch(() => {})
                        }
                        if (e.key === "Escape") {
                          cancelRenameSession()
                        }
                      }}
                    />
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={cancelRenameSession}
                        disabled={isActionLoading}
                      >
                        <X className="h-4 w-4" />
                        <span className="sr-only">取消重命名</span>
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => submitRenameSession(session).catch(() => {})}
                        disabled={isActionLoading}
                      >
                        <Check className="h-4 w-4" />
                        <span className="sr-only">确认重命名</span>
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      className="w-full text-left"
                      onClick={() => handleSelectSession(session.id)}
                      disabled={isSessionLocked}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="truncate text-sm font-medium">{session.title}</div>
                        <div className="shrink-0 text-[11px] text-muted-foreground">
                          {formatSessionTime(session.updatedAt || session.createdAt)}
                        </div>
                      </div>
                      <div className="mt-1 truncate font-mono text-[11px] text-muted-foreground">
                        {session.id}
                      </div>
                      {session.preview && (
                        <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                          {session.preview}
                        </div>
                      )}
                    </button>

                    <div className="mt-2 flex justify-end gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          beginRenameSession(session)
                        }}
                        disabled={isSessionLocked}
                        title="重命名"
                      >
                        <Pencil className="h-4 w-4" />
                        <span className="sr-only">重命名</span>
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        onClick={(e) => {
                          e.preventDefault()
                          e.stopPropagation()
                          setDeleteTarget(session)
                        }}
                        disabled={isSessionLocked}
                        title="删除会话"
                      >
                        <Trash2 className="h-4 w-4" />
                        <span className="sr-only">删除</span>
                      </Button>
                    </div>
                  </>
                )}
              </div>
            )
          })
        )}
      </div>
    </>
  )

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {hasModels === false && (
        <div className="border-b bg-yellow-50 px-4 py-3 text-yellow-900 dark:bg-yellow-900/20 dark:text-yellow-100">
          <div className="mx-auto flex max-w-6xl items-start justify-between gap-4">
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

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-72 shrink-0 flex-col gap-3 border-r bg-muted/20 p-4 md:flex md:w-80">
          {renderSessionPanel()}
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="border-b bg-background p-3 md:hidden">
            <div className="flex items-center justify-between gap-2">
              <Button variant="outline" size="sm" onClick={() => setMobileSessionsOpen(true)}>
                <PanelLeft className="mr-1 h-4 w-4" />
                会话
              </Button>
              <div className="min-w-0 text-right">
                <div className="truncate text-sm text-muted-foreground">
                  {activeSession?.title || "会话未选择"}
                </div>
                <div className="truncate font-mono text-[11px] text-muted-foreground">
                  {displaySessionId ? `session: ${displaySessionId}` : ""}
                </div>
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4 sm:p-6">
            <div className="mx-auto max-w-3xl space-y-6">
              <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                  {wsStatus === "open" ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                  <span>{wsStatus === "open" ? "已连接" : wsStatus === "connecting" ? "连接中" : "未连接"}</span>
                  {isSessionSwitching && <span>· 会话切换中...</span>}
                  {historyLoading && <span>· 加载历史中...</span>}
                  {awaitingResponse && <span>· 生成中...</span>}
                </div>
                <div className="text-right">
                  <div className="truncate text-muted-foreground">{activeSession?.title || "会话未选择"}</div>
                  <div className="font-mono">{displaySessionId ? `session: ${displaySessionId}` : ""}</div>
                </div>
              </div>

              <div className="flex justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRetryLastUser}
                  disabled={!lastUserMessage || wsStatus !== "open" || isSessionSwitching || awaitingResponse}
                >
                  <RotateCcw className="mr-1 h-3.5 w-3.5" />
                  重试上条
                </Button>
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
                                <div className="group relative mb-2 mt-2">
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
                                    className="!m-0 rounded-md !bg-zinc-950"
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
                    {msg.role === "user" && (
                      <div className="mt-1 flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          onClick={() => handleResend(msg.content)}
                          disabled={wsStatus !== "open" || isSessionSwitching || awaitingResponse}
                        >
                          <RotateCcw className="mr-1 h-3.5 w-3.5" />
                          重发
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          </div>

          <div className="border-t bg-background p-4 sm:p-6">
            <div className="relative mx-auto flex max-w-3xl items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  isSessionSwitching
                    ? "会话切换中，暂不可发送..."
                    : awaitingResponse
                      ? "生成中，可点击右侧停止..."
                    : wsStatus === "open"
                      ? "输入消息... (Shift + Enter 换行)"
                      : "连接中，暂不可发送..."
                }
                className="min-h-[60px] w-full resize-none rounded-md border border-input bg-background px-3 py-3 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                rows={1}
                style={{ height: "auto", maxHeight: "200px" }}
              />
              <Button
                size="icon"
                variant={awaitingResponse ? "destructive" : "default"}
                className="h-[60px] w-[60px] shrink-0"
                onClick={awaitingResponse ? handleStopGeneration : handleSend}
                disabled={
                  awaitingResponse
                    ? wsStatus !== "open" || isSessionSwitching
                    : !input.trim() || wsStatus !== "open" || isSessionSwitching
                }
              >
                {awaitingResponse ? <Square className="h-5 w-5" /> : <Send className="h-5 w-5" />}
                <span className="sr-only">{awaitingResponse ? "停止生成" : "发送"}</span>
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Drawer
        isOpen={mobileSessionsOpen}
        onClose={() => setMobileSessionsOpen(false)}
        title="会话"
        description="移动端会话列表"
      >
        <div className="flex h-full min-h-0 flex-col gap-3">{renderSessionPanel()}</div>
      </Drawer>

      <Modal
        isOpen={Boolean(deleteTarget)}
        onClose={() => {
          if (!sessionActionLoadingId) setDeleteTarget(null)
        }}
        title="删除会话"
        description={
          deleteTarget ? `确定删除会话「${deleteTarget.title}」吗？该操作不可撤销。` : undefined
        }
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={Boolean(sessionActionLoadingId)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleteTarget) handleDeleteSession(deleteTarget).catch(() => {})
              }}
              disabled={Boolean(sessionActionLoadingId)}
            >
              {sessionActionLoadingId ? "删除中..." : "确认删除"}
            </Button>
          </>
        }
      >
        <div className="text-sm text-muted-foreground">
          删除后会移除该会话的聊天入口，且无法在页面直接恢复。
        </div>
      </Modal>
    </div>
  )
}
