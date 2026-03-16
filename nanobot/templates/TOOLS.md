# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Demo Kits & Pilot Framing

The same toolset supports two product stories without splitting the runtime:

- **private-domain pilot**: operator runbooks, routing triage, audit-friendly exports, rollout evidence
- **internal knowledge / technical SMB**: knowledge assistant demos, workspace automation, searchable docs

Before promising a workflow, check `DEMO_KIT.md` as the active-kit marker; for legacy workspaces, fall back to `.nanobot-demo-kit`, then read `demo/*/README.md` for the actual kit framing.
Do not imply capabilities that the current workspace does not actually ship.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## cron — Scheduled Reminders

- Please refer to cron skill for usage.
