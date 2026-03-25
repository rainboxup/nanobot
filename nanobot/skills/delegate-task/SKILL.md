---
name: delegate-task
description: Delegate complex tasks to OpenSpace MCP when native tools are insufficient or execution risk is high.
metadata: {"nanobot":{"emoji":"🛠️"}}
---

# Delegate Task To OpenSpace

Use OpenSpace as an MCP-backed worker for complex, multi-step, or capability-gapped tasks.

## Prerequisites

- MCP server is configured in `tools.mcpServers` with server name `openspace`.
- Tool list includes:
  - `mcp_openspace_execute_task`
  - `mcp_openspace_search_skills`
  - `mcp_openspace_fix_skill`
  - `mcp_openspace_upload_skill`

If server name differs, convert tool names with:

- `mcp_<server_name>_execute_task`
- `mcp_<server_name>_search_skills`
- `mcp_<server_name>_fix_skill`
- `mcp_<server_name>_upload_skill`

## When To Delegate

- You lack required capability or environment access.
- Prior attempts failed or are too error-prone.
- The task is long and benefits from OpenSpace skill reuse/evolution.
- User explicitly asks for delegation.

## Primary Call

```text
mcp_openspace_execute_task(
  task="Monitor Docker containers, find the highest memory one, restart it gracefully",
  search_scope="all",
  max_iterations=20
)
```

Parameter guidance:

- `task`: concrete objective and constraints.
- `search_scope`: keep `all` unless user requires local-only behavior.
- `max_iterations`: increase for complex workflows, reduce for bounded tasks.

## Optional Tools

- Search only:
  - `mcp_openspace_search_skills(...)`
- Repair evolved skill:
  - `mcp_openspace_fix_skill(skill_dir=..., direction=...)`
- Publish evolved skill:
  - `mcp_openspace_upload_skill(skill_dir=..., visibility="public"|"private")`

## Post-Execution Reporting

Always report back:

- task status/result,
- key outputs/artifacts,
- whether `evolved_skills` were produced,
- upload decision (and why).

## Upload Policy

- Reusable general improvement: prefer `public`.
- Project-specific or sensitive knowledge: prefer `private` or skip.
- Respect explicit user sharing preference.
