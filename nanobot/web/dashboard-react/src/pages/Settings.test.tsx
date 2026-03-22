import { describe, expect, it } from "vitest"

import { getAllowedSettingsTabs } from "./Settings"

describe("getAllowedSettingsTabs", () => {
  it("keeps channels available to members without exposing admin-only tabs", () => {
    const memberTabs = getAllowedSettingsTabs("member", false)

    expect(memberTabs).toContain("channels")
    expect(memberTabs).toContain("users")
    expect(memberTabs).not.toContain("providers")
    expect(memberTabs).not.toContain("integrations")
    expect(memberTabs).not.toContain("tools")
    expect(memberTabs).not.toContain("soul")
    expect(memberTabs).not.toContain("cron")
    expect(memberTabs).not.toContain("security")
  })

  it("exposes integrations to admins while keeping owner-only tabs hidden", () => {
    const adminTabs = getAllowedSettingsTabs("admin", false)

    expect(adminTabs).toContain("providers")
    expect(adminTabs).toContain("channels")
    expect(adminTabs).toContain("integrations")
    expect(adminTabs).toContain("tools")
    expect(adminTabs).toContain("soul")
    expect(adminTabs).toContain("users")
    expect(adminTabs).not.toContain("cron")
    expect(adminTabs).not.toContain("security")
  })
})
