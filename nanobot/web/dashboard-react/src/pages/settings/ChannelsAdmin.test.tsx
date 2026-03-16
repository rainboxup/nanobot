import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ChannelsAdmin } from "./ChannelsAdmin"
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
const mockedUseStore = vi.mocked(useStore)

describe("ChannelsAdmin", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    mockedUseStore.mockReturnValue({
      addToast: vi.fn(),
      user: { username: "owner-user", role: "owner" },
    } as any)
  })

  it("labels WeCom as a system-scope MVP with no workspace routing promises", async () => {
    mockedApiGet.mockResolvedValue([
      {
        name: "wecom",
        enabled: false,
        config_summary: {
          corp_id: "ww-demo",
          has_corp_secret: true,
          agent_id: "1000002",
        },
        config_ready: true,
        missing_required_fields: [],
        runtime_registered: false,
        runtime_running: false,
        runtime_mode: "multi",
        runtime_scope: "global",
        runtime_warning: "restart required",
        config_scope: "system",
        takes_effect: "restart",
        writable: true,
        write_block_reason_code: null,
        write_block_reason: null,
      },
      {
        name: "telegram",
        enabled: true,
        config_summary: { has_token: true },
        config_ready: true,
        missing_required_fields: [],
        runtime_registered: true,
        runtime_running: true,
        runtime_mode: "multi",
        runtime_scope: "global",
        runtime_warning: "restart required",
        config_scope: "system",
        takes_effect: "restart",
        writable: true,
        write_block_reason_code: null,
        write_block_reason: null,
      },
    ] as any)

    render(<ChannelsAdmin />)

    expect(await screen.findByText("wecom")).toBeInTheDocument()
    expect(screen.getByText("telegram")).toBeInTheDocument()
    expect(screen.getAllByText("system scope")).toHaveLength(1)
    expect(screen.getAllByText("MVP")).toHaveLength(1)
    expect(screen.getByText(/Owner-managed Enterprise WeChat MVP/i)).toBeInTheDocument()
  })
})
