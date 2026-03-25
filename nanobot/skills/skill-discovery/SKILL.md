---
name: skill-discovery
description: Discover reusable skills from OpenSpace through MCP before implementing complex tasks manually.
metadata: {"nanobot":{"emoji":"🧭"}}
---

# OpenSpace Skill Discovery

Use this skill to search OpenSpace skills and decide whether to reuse, delegate, or continue manually.

## Prerequisites

- MCP server is configured in `tools.mcpServers` with server name `openspace`.
- The tool list includes `mcp_openspace_search_skills`.

If your server name is not `openspace`, convert tool names with:

- `mcp_<server_name>_search_skills`

## When To Use

- User asks "is there a skill for X?"
- You are entering an unfamiliar area and want to avoid trial-and-error.
- You need to decide between self-implementation and OpenSpace delegation.

## Search

Call:

```text
mcp_openspace_search_skills(query="automated deployment with rollback", source="all")
```

Recommended inputs:

- `query`: natural language task description.
- `source`: usually `all`.
- `limit`: default is usually enough; raise only for broad exploration.
- `auto_import`: keep default unless user explicitly wants no imports.

## Decision After Search

- Match found and you can execute directly:
  - Read the returned `local_path` SKILL.md and follow it yourself.
- Match found but capability/tooling is missing:
  - Use the `delegate-task` skill and call OpenSpace execution tool.
- No match:
  - Continue with native nanobot tools or delegate a broader task.

## Notes

- This skill is discovery-only.
- Always summarize back to user:
  - what was found,
  - what will be reused,
  - and what action you will take next.
