# OpenSpace 接入指南（nanobot）

本指南用于你当前这个深度改造版 nanobot，目标是把 OpenSpace 作为 MCP worker 接入，并能通过内置 skills 触发。

## 1) 已内置内容

仓库已新增以下 bundled skills（`nanobot/skills/`）：

- `skill-discovery`
- `delegate-task`

它们默认按当前 nanobot 的 MCP 命名约定编写，调用名为：

- `mcp_openspace_search_skills`
- `mcp_openspace_execute_task`
- `mcp_openspace_fix_skill`
- `mcp_openspace_upload_skill`

如果你的 MCP server 名不是 `openspace`，将前缀替换为 `mcp_<server_name>_...`。

## 2) 配置 MCP Server

在实例配置（例如 `~/.nanobot/config.json` 或实例目录 `config.json`）中添加：

```json
{
  "tools": {
    "mcpServers": {
      "openspace": {
        "command": "openspace-mcp",
        "toolTimeout": 1200,
        "env": {
          "OPENSPACE_HOST_SKILL_DIRS": "D:/code/linshi/nanobot/nanobot/skills",
          "OPENSPACE_WORKSPACE": "D:/code/linshi/OpenSpace",
          "OPENSPACE_API_KEY": "sk-xxx"
        }
      }
    }
  }
}
```

说明：

- `OPENSPACE_API_KEY` 不是所有能力都必须，但建议配置。
- `OPENSPACE_HOST_SKILL_DIRS` 指向 host skills 目录；多路径按 OpenSpace 规范配置。
- `OPENSPACE_WORKSPACE` 建议单独目录，便于隔离 OpenSpace 产物。

## 3) 验收点（Acceptance Criteria）

### A. 代码层验收

- `nanobot/skills/skill-discovery/SKILL.md` 存在且包含 `mcp_openspace_search_skills`
- `nanobot/skills/delegate-task/SKILL.md` 存在且包含 `mcp_openspace_execute_task`
- MCP 配置加载支持 `tools.mcpServers.openspace`（含 env 原样键名）

### B. 自动化验收

运行：

```bash
pytest -q tests/test_openspace_skill_docs.py
pytest -q tests/test_config_loader.py -k openspace
```

通过标准：

- 以上测试全部通过。

### C. 运行时验收（手工）

1. 启动 gateway/agent 后，请求一次技能搜索场景（触发 `skill-discovery`）。
2. 确认 tool 列表中存在 `mcp_openspace_search_skills` 等工具。
3. 发起一次委托任务（触发 `delegate-task`），确认返回 OpenSpace 执行结果而非 tool-not-found。

通过标准：

- 无 `MCP server 'openspace' failed to connect` 错误。
- 无 `tool not found: mcp_openspace_*` 错误。
- 委托调用返回结构化结果（成功或失败均可，但需来自 OpenSpace）。
