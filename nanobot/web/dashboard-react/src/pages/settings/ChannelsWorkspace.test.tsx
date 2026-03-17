import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ChannelsWorkspace } from "./ChannelsWorkspace"
import { api } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"

vi.mock("@/src/components/ui/drawer", () => ({
  Drawer: ({ isOpen, title, description, children, footer }: any) => {
    if (!isOpen) return null
    return (
      <div role="dialog" aria-label={title || "drawer"}>
        {title ? <h2>{title}</h2> : null}
        {description ? <p>{description}</p> : null}
        <div>{children}</div>
        <div>{footer}</div>
      </div>
    )
  },
}))

vi.mock("@/src/lib/api", () => {
  class MockApiError extends Error {
    status: number
    detail: string

    constructor(status: number, detail: string) {
      super(detail || `HTTP ${status}`)
      this.status = status
      this.detail = detail || `HTTP ${status}`
    }
  }

  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
      put: vi.fn(),
      fetch: vi.fn(),
    },
    ApiError: MockApiError,
  }
})

vi.mock("@/src/store/useStore", () => ({
  useStore: vi.fn(),
}))

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedUseStore = vi.mocked(useStore)
let mockedClipboardWriteText: any

const baseChannel = {
  name: "feishu",
  enabled: true,
  workspace_enabled: true,
  system_enabled: true,
  effective_enabled: true,
  group_policy: "mention" as const,
  group_allow_from: [],
  allow_from: [],
  require_mention: true,
  byo_supported: true,
  byo_configured: false,
  active_in_runtime: true,
  writable: true,
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

describe("ChannelsWorkspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockedClipboardWriteText = vi.spyOn(window.navigator.clipboard, "writeText").mockResolvedValue(undefined)
    mockedUseStore.mockReturnValue({
      addToast: vi.fn(),
      user: { username: "member-user", role: "member" },
    } as any)
  })

  it("uses challenge-first binding flow instead of manual sender entry", async () => {
    const bindingDetail = {
      name: "feishu",
      channel: "feishu",
      account_id: "acct-1",
      tenant_id: "tenant-1",
      identities: [],
      proof_of_possession_supported: true,
      legacy_link_supported: true,
      active_challenge: null,
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/channels/workspace") return [clone(baseChannel)] as any
      if (path === "/api/channels/feishu/binding") return clone(bindingDetail) as any
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })
    mockedApiPost.mockImplementation(async (path: string) => {
      if (path === "/api/channels/feishu/binding/challenges") {
        bindingDetail.active_challenge = {
          code: "VERIFY123",
          status: "pending",
          expires_at: "2026-03-09T12:05:00Z",
          verification_command: "!prove VERIFY123",
          verified_identity: null,
        }
        return clone(bindingDetail) as any
      }
      throw new Error(`Unhandled api.post mock for: ${path}`)
    })

    render(<ChannelsWorkspace />)

    fireEvent.click(await screen.findByRole("button", { name: "Binding" }))

    expect(await screen.findByText("Verify a new identity")).toBeInTheDocument()
    expect(screen.queryByText("Sender ID")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Attach Identity" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Start verification" }))

    expect(await screen.findByText("VERIFY123")).toBeInTheDocument()
    expect(screen.getByText("!prove VERIFY123")).toBeInTheDocument()
    expect(screen.getByText("Compatibility fallback")).toBeInTheDocument()
    expect(mockedApiPost).toHaveBeenCalledWith("/api/channels/feishu/binding/challenges", {})
  })

  it("explains that WeCom stays owner-managed in the admin surface", async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/channels/workspace") return [clone(baseChannel)] as any
      if (path === "/api/security/boundaries") {
        return {
          role: "member",
          surfaces: {
            workspace_channels: {
              binding: {
                allowed: true,
                scope: "current_account",
                summary: "Members can bind or detach their own channel identities for the current account.",
              },
              workspace_routing: {
                allowed: false,
                scope: "current_tenant_admin",
                summary: "Workspace routing and BYO credential edits require admin access in the current tenant.",
              },
              system_channels: {
                allowed: false,
                scope: "owner_only",
                summary: "System channel settings and WeCom remain owner-managed in Platform Admin.",
              },
            },
          },
        } as any
      }
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    render(<ChannelsWorkspace />)

    expect(await screen.findByText("Workspace Routing")).toBeInTheDocument()
    expect(screen.getByText("权限边界")).toBeInTheDocument()
    expect(
      screen.getByText("Members can bind or detach their own channel identities for the current account.")
    ).toBeInTheDocument()
    expect(
      screen.getAllByText("Workspace routing and BYO credential edits require admin access in the current tenant.")
        .length
    ).toBeGreaterThan(0)
    expect(screen.getByText(/WeCom remains owner-managed/i)).toBeInTheDocument()
    expect(screen.getByText("System channel settings and WeCom remain owner-managed in Platform Admin.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Binding" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "Credentials" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled()
  })

  it("confirms a verified identity without asking for sender id", async () => {
    const bindingDetail = {
      name: "feishu",
      channel: "feishu",
      account_id: "acct-1",
      tenant_id: "tenant-1",
      identities: [],
      proof_of_possession_supported: true,
      legacy_link_supported: true,
      active_challenge: {
        code: "VERIFY456",
        status: "verified",
        expires_at: "2026-03-09T12:05:00Z",
        verification_command: "!prove VERIFY456",
        verified_identity: "feishu:user-9",
      },
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/channels/workspace") return [clone(baseChannel)] as any
      if (path === "/api/channels/feishu/binding") return clone(bindingDetail) as any
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })
    mockedApiPost.mockImplementation(async (path: string, body?: unknown) => {
      if (path === "/api/channels/feishu/binding/confirm") {
        expect(body).toEqual({ code: "VERIFY456" })
        bindingDetail.identities = ["feishu:user-9"]
        bindingDetail.active_challenge = null
        return clone(bindingDetail) as any
      }
      throw new Error(`Unhandled api.post mock for: ${path}`)
    })

    render(<ChannelsWorkspace />)

    fireEvent.click(await screen.findByRole("button", { name: "Binding" }))

    expect(await screen.findByText("Verified identity")).toBeInTheDocument()
    expect(screen.getByText("feishu:user-9")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Confirm binding" }))

    await waitFor(() => {
      expect(screen.getByText("Attached identities")).toBeInTheDocument()
      expect(screen.getAllByText("feishu:user-9").length).toBeGreaterThan(0)
    })
    expect(screen.queryByRole("button", { name: "Confirm binding" })).not.toBeInTheDocument()
    expect(screen.queryByText("Sender ID")).not.toBeInTheDocument()
  })

  it("copies a derived !prove command when the backend omits verification_command", async () => {
    const bindingDetail = {
      name: "feishu",
      channel: "feishu",
      account_id: "acct-1",
      tenant_id: "tenant-1",
      identities: [],
      proof_of_possession_supported: true,
      legacy_link_supported: true,
      active_challenge: {
        code: "VERIFY789",
        status: "pending",
        expires_at: "2026-03-09T12:05:00Z",
        verification_command: null,
        verified_identity: null,
      },
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/channels/workspace") return [clone(baseChannel)] as any
      if (path === "/api/channels/feishu/binding") return clone(bindingDetail) as any
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    render(<ChannelsWorkspace />)

    fireEvent.click(await screen.findByRole("button", { name: "Binding" }))
    fireEvent.click(await screen.findByRole("button", { name: "Copy !prove" }))

    await waitFor(() => {
      expect(mockedClipboardWriteText).toHaveBeenCalledWith("!prove VERIFY789")
    })
  })

  it("falls back cleanly when proof-of-possession or legacy link is unavailable", async () => {
    const bindingDetail = {
      name: "feishu",
      channel: "feishu",
      account_id: "acct-1",
      tenant_id: "tenant-1",
      identities: [],
      proof_of_possession_supported: false,
      legacy_link_supported: false,
      active_challenge: null,
    }

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/channels/workspace") return [clone(baseChannel)] as any
      if (path === "/api/channels/feishu/binding") return clone(bindingDetail) as any
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    render(<ChannelsWorkspace />)

    fireEvent.click(await screen.findByRole("button", { name: "Binding" }))

    expect(await screen.findByText("Verify a new identity")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Start verification" })).not.toBeInTheDocument()
    expect(
      screen.getByText("Dashboard verification is not available for this channel/runtime. Use the compatibility flow below.")
    ).toBeInTheDocument()
    expect(screen.queryByText("Compatibility fallback")).not.toBeInTheDocument()
    expect(screen.getByText("Legacy `!link` fallback is not available for this channel.")).toBeInTheDocument()
  })
})
