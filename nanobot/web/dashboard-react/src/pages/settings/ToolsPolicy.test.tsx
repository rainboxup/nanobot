import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ToolsPolicy } from "./ToolsPolicy"
import { api } from "@/src/lib/api"
import { useStore } from "@/src/store/useStore"

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

function mockAdminStore() {
  mockedUseStore.mockReturnValue({
    addToast: vi.fn(),
    user: { username: "admin-user", role: "admin" },
  } as any)
}

describe("ToolsPolicy", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAdminStore()
  })

  it("renders extended baseline summary and a Soul-page hint", async () => {
    mockedApiGet.mockResolvedValue({
      runtime_mode: "multi",
      runtime_scope: "tenant",
      writable: true,
      subject: { tenant_id: "tenant-alpha" },
      system_cap: {
        exec: { enabled: true },
        web: { enabled: true },
      },
      tenant_policy: {
        exec: { allowlisted: true },
        web: { allowlisted: true },
      },
      user_setting: {
        exec: { enabled: true },
        web: { enabled: true },
      },
      effective: {
        exec: { enabled: true, reason_codes: [] },
        web: { enabled: true, reason_codes: [] },
      },
      baseline: {
        selected_version_id: "base-v2",
        effective_version_id: "base-v2",
        candidate_version_id: "base-v3",
        control_version_id: "base-v1",
        strategy: "canary",
        canary_percent: 20,
        bucket: 8,
        is_canary: false,
      },
      warnings: [],
    } as any)

    render(<ToolsPolicy />)

    expect(await screen.findByText(/effective:\s*base-v2/i)).toBeInTheDocument()
    expect(screen.getByText(/candidate:\s*base-v3/i)).toBeInTheDocument()
    expect(screen.getByText(/control:\s*base-v1/i)).toBeInTheDocument()
    expect(screen.getByText(/bucket:\s*8/i)).toBeInTheDocument()
    expect(screen.getByText(/Soul 页面查看 tenant baseline 模拟/i)).toBeInTheDocument()
  })

  it("disables toggles and save after the initial load fails", async () => {
    mockedApiGet.mockRejectedValue(new Error("Policy load failed"))

    render(<ToolsPolicy />)

    expect(await screen.findByText("Policy load failed")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "刷新" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "保存工具策略" })).toBeDisabled()

    const toggles = screen.getAllByRole("checkbox")
    expect(toggles).toHaveLength(2)
    for (const toggle of toggles) {
      expect(toggle).toBeDisabled()
    }
  })
})
