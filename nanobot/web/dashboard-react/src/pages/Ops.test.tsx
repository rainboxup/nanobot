import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { Ops } from "./Ops"
import { api } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"

vi.mock("react-syntax-highlighter", () => ({
  Prism: ({ children }: { children: unknown }) => (
    <pre data-testid="syntax-highlighter">{children as any}</pre>
  ),
}))

vi.mock("react-syntax-highlighter/dist/esm/styles/prism", () => ({
  vscDarkPlus: {},
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
    formatTime: (value: string) => value,
  }
})

vi.mock("@/src/store/useStore", () => ({
  useStore: vi.fn(),
}))

const mockedApiGet = vi.mocked(api.get)
const mockedUseStore = vi.mocked(useStore)

describe("Ops", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedUseStore.mockReturnValue({
      addToast: vi.fn(),
      user: { username: "owner-user", role: "owner" },
    } as any)
  })

  it("renders operator-friendly summary and attention items", async () => {
    mockedApiGet.mockResolvedValue({
      status: "ready",
      guides: [{ slug: "workspace-routing-and-binding", title: "Workspace Routing & Binding" }],
      runtime: {
        started_at: "2026-03-17T00:00:00Z",
        uptime_seconds: 123,
        summary: {
          registered_channel_count: 2,
          running_channel_count: 1,
          workspace_runtime_count: 1,
          workspace_runtime_running_count: 0,
          workspace_runtime_inactive_count: 1,
          active_web_connections: 3,
        },
        queue: {
          inbound_depth: 2,
          inbound_capacity: 100,
          inbound_utilization: 0.02,
          inbound_pressure_level: "normal",
          outbound_depth: 0,
          outbound_capacity: 100,
          outbound_utilization: 0,
          outbound_pressure_level: "normal",
        },
        channels: {
          registered: ["web", "feishu"],
          rows: [
            { name: "web", running: true },
            { name: "feishu", running: false },
          ],
          status: {
            web: { enabled: true, running: true },
            feishu: { enabled: true, running: false },
          },
          workspace_status: {
            feishu: [{ tenant_id: "tenant-ops-attention", running: false, active_in_runtime: false }],
          },
          workspace_rows: [
            {
              channel: "feishu",
              tenant_id: "tenant-ops-attention",
              running: false,
              active_in_runtime: false,
            },
          ],
          active_web_connections: 3,
        },
        attention: [
          {
            scope: "workspace_runtime",
            level: "warning",
            reason_code: "workspace_runtime_inactive",
            summary: "Workspace runtime is configured but not active.",
            details: {
              channel: "feishu",
              tenant_id: "tenant-ops-attention",
              running: false,
              active_in_runtime: false,
            },
          },
        ],
      },
    } as any)

    render(<Ops />)

    expect(await screen.findByText("已注册渠道")).toBeInTheDocument()
    expect(screen.getByText("运行中渠道")).toBeInTheDocument()
    expect(screen.getByText("工作区运行时")).toBeInTheDocument()
    expect(screen.getByText("重点关注")).toBeInTheDocument()
    expect(screen.getByText("Workspace runtime is configured but not active.")).toBeInTheDocument()
    expect(screen.getByText("feishu / tenant-ops-attention")).toBeInTheDocument()
  })
})
