import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { Integrations } from "./Integrations"
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
    formatTime: (value: string | number) => String(value),
  }
})

vi.mock("@/src/store/useStore", () => ({
  useStore: vi.fn(),
}))

const mockedApiGet = vi.mocked(api.get)
const mockedApiPost = vi.mocked(api.post)
const mockedUseStore = vi.mocked(useStore)

describe("Integrations", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedUseStore.mockReturnValue({
      addToast: vi.fn(),
      user: { username: "tenant-admin", role: "admin", tenant_id: "tenant-alpha" },
    } as any)
  })

  it("renders connector health summary and exposes ERP operation options", async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/integrations") {
        return [
          {
            connector: "crm_core",
            tenant_id: "tenant-alpha",
            enabled: true,
            provider: "crm_native",
            base_url: "https://crm.example.com",
            timeout_s: 30,
            health: { ready: true, provider_available: true, last_sync_status: "succeeded" },
            latest_status: {
              status: "succeeded",
              operation: "sync_contacts",
              sync_id: "crm-1",
              synced_count: 2,
              updated_at: "2026-03-22T08:00:00Z",
            },
          },
          {
            connector: "erp_core",
            tenant_id: "tenant-alpha",
            enabled: true,
            provider: "erp_native",
            base_url: "",
            timeout_s: 30,
            health: { ready: true, provider_available: true, last_sync_status: "succeeded" },
            latest_status: null,
          },
        ] as any
      }
      if (path === "/api/integrations/health") {
        return {
          tenant_id: "tenant-alpha",
          configured_connectors: 2,
          ready_connectors: 2,
          degraded_connectors: [],
          available_providers: ["crm_native", "order_native", "erp_native"],
        } as any
      }
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    render(<Integrations />)

    expect(await screen.findByText("连接器健康总览")).toBeInTheDocument()
    expect(screen.getByText(/configured: 2/i)).toBeInTheDocument()
    expect(screen.getByText(/ready: 2/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "sync-crm_core" })).toBeEnabled()
    expect(screen.getByRole("button", { name: "sync-erp_core" })).toBeEnabled()
    expect(screen.getByRole("option", { name: "sync_products" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "sync_inventory" })).toBeInTheDocument()
  })

  it("submits manual sync and updates latest status in place", async () => {
    const addToast = vi.fn()
    mockedUseStore.mockReturnValue({
      addToast,
      user: { username: "tenant-admin", role: "admin", tenant_id: "tenant-alpha" },
    } as any)

    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/integrations") {
        return [
          {
            connector: "crm_core",
            tenant_id: "tenant-alpha",
            enabled: true,
            provider: "crm_native",
            base_url: "https://crm.example.com",
            timeout_s: 30,
            health: { ready: true, provider_available: true, last_sync_status: "failed" },
            latest_status: {
              status: "failed",
              operation: "sync_contacts",
              sync_id: null,
              synced_count: 0,
              reason_code: "connector_execution_failed",
              updated_at: "2026-03-22T08:00:00Z",
            },
          },
        ] as any
      }
      if (path === "/api/integrations/health") {
        return {
          tenant_id: "tenant-alpha",
          configured_connectors: 1,
          ready_connectors: 1,
          degraded_connectors: [],
          available_providers: ["crm_native"],
        } as any
      }
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    mockedApiPost.mockResolvedValue({
      result: {
        status: "succeeded",
        connector: "crm_core",
        provider: "crm_native",
        operation: "sync_accounts",
      },
      latest_status: {
        status: "succeeded",
        operation: "sync_accounts",
        sync_id: "crm-9",
        synced_count: 3,
        updated_at: "2026-03-22T09:00:00Z",
      },
    } as any)

    render(<Integrations />)

    expect(await screen.findByRole("button", { name: "sync-crm_core" })).toBeEnabled()

    fireEvent.change(screen.getByLabelText("同步操作"), { target: { value: "sync_accounts" } })
    fireEvent.change(screen.getByLabelText("Payload(JSON)"), { target: { value: "{\"count\":3}" } })
    fireEvent.change(screen.getByLabelText("Idempotency Key（可选）"), {
      target: { value: "sync-20260322-001" },
    })
    fireEvent.click(screen.getByRole("button", { name: "sync-crm_core" }))

    await waitFor(() => {
      expect(mockedApiPost).toHaveBeenCalledWith("/api/integrations/sync", {
        connector: "crm_core",
        operation: "sync_accounts",
        payload: { count: 3 },
        idempotency_key: "sync-20260322-001",
      })
    })

    expect(await screen.findByText(/sync_id:\s*crm-9/i)).toBeInTheDocument()
    expect(addToast).toHaveBeenCalledWith(
      expect.objectContaining({ type: "success", message: expect.stringContaining("crm_core") })
    )
  })
})
