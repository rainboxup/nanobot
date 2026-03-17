import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { Users } from "./Users"
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

describe("Users", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedUseStore.mockReturnValue({
      addToast: vi.fn(),
      user: { username: "tenant-admin", role: "admin", tenant_id: "tenant-alpha" },
    } as any)
  })

  it("shows role boundaries and locks tenant creation scope for admins", async () => {
    mockedApiGet.mockImplementation(async (path: string) => {
      if (path === "/api/auth/users") {
        return [{ username: "member-a", tenant_id: "tenant-alpha", role: "member", active: true }] as any
      }
      if (path === "/api/security/boundaries") {
        return {
          role: "admin",
          surfaces: {
            users: {
              page_allowed: true,
              create_users: {
                allowed: true,
                scope: "current_tenant",
                summary: "Admins can create member/admin users only in the current tenant.",
              },
              change_roles: {
                allowed: false,
                scope: "owner_only",
                summary: "Only owners can change roles, including promoting admins or owners.",
              },
              manage_lifecycle: {
                allowed: true,
                scope: "current_tenant_members",
                summary:
                  "Admins can reset passwords, revoke sessions, disable, or delete member users in the current tenant.",
              },
            },
          },
        } as any
      }
      throw new Error(`Unhandled api.get mock for: ${path}`)
    })

    render(<Users />)

    expect(await screen.findByText("权限边界")).toBeInTheDocument()
    expect(
      screen.getAllByText("Admins can create member/admin users only in the current tenant.").length
    ).toBeGreaterThan(0)
    expect(screen.getByText("Only owners can change roles, including promoting admins or owners.")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Admins can reset passwords, revoke sessions, disable, or delete member users in the current tenant."
      )
    ).toBeInTheDocument()
    expect(screen.getByDisplayValue("tenant-alpha")).toBeDisabled()
  })
})
