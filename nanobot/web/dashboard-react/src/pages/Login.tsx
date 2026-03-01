import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useStore } from "@/src/store/useStore"
import { Button } from "@/src/components/ui/button"
import { Input } from "@/src/components/ui/input"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/src/components/ui/card"
import { Eye, EyeOff, AlertTriangle, Github, Globe } from "lucide-react"
import { api, ApiError, setAuthTokens } from "@/src/lib/api"

export function Login() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [inviteCode, setInviteCode] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [capsLock, setCapsLock] = useState(false)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  
  const { setUser, addToast, user } = useStore()
  const navigate = useNavigate()

  useEffect(() => {
    if (user) {
      navigate("/chat", { replace: true })
    }
  }, [navigate, user])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")

    if (loading) return
    setLoading(true)
    try {
      const payload: any = {
        username: (username || "").trim() || "admin",
        password: password || "",
      }
      const invite = (inviteCode || "").trim()
      if (invite) payload.invite_code = invite

      const res = await api.post<any>("/api/auth/login", payload)
      setAuthTokens(String(res.access_token || res.token || ""), String(res.refresh_token || ""))
      setUser({
        username: String(res.username || payload.username || ""),
        role: String(res.role || "member") as any,
        tenant_id: String(res.tenant_id || ""),
      })
      addToast({ type: "success", message: "登录成功" })
      navigate("/chat")
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : String((err as any)?.message || "登录失败")
      setError(msg)
      addToast({ type: "error", message: msg || "登录失败，请检查凭证" })
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.getModifierState("CapsLock")) {
      setCapsLock(true)
    } else {
      setCapsLock(false)
    }
  }

  return (
    <div className="flex h-screen w-full items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1 text-center">
          <CardTitle className="text-2xl font-bold tracking-tight">nanobot 控制台</CardTitle>
          <CardDescription>
            输入您的凭证以访问管理后台
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleLogin}>
          <CardContent className="space-y-4">
            {error && (
              <div className="flex items-center gap-2 rounded-md bg-destructive/15 p-3 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4" />
                {error}
              </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70" htmlFor="username">
                用户名
              </label>
              <Input
                id="username"
                placeholder="admin"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2 relative">
              <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70" htmlFor="password">
                密码
              </label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={handleKeyDown}
                  required
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3 py-2 hover:bg-transparent"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <Eye className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span className="sr-only">{showPassword ? "Hide password" : "Show password"}</span>
                </Button>
              </div>
              {capsLock && (
                <p className="text-[0.8rem] text-muted-foreground flex items-center gap-1 mt-1">
                  <AlertTriangle className="h-3 w-3" /> 大写锁定已开启
                </p>
              )}
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70" htmlFor="inviteCode">
                邀请码 (可选)
              </label>
              <Input
                id="inviteCode"
                placeholder="Beta 邀请码"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
              />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-4">
            <Button className="w-full" type="submit">
              {loading ? "登录中..." : "登录"}
            </Button>
            <div className="relative w-full">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-card px-2 text-muted-foreground">
                  其他登录方式 (预留)
                </span>
              </div>
            </div>
            <div className="flex w-full gap-2">
              <Button variant="outline" className="w-full" type="button" disabled>
                <Github className="mr-2 h-4 w-4" /> GitHub
              </Button>
              <Button variant="outline" className="w-full" type="button" disabled>
                <Globe className="mr-2 h-4 w-4" /> Google
              </Button>
            </div>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
