# Enterprise Bundle Bootstrap

这份说明面向 **Owner / 运维 / 交付负责人**，用于在初始化阶段把运行时能力、帮助文档和工作区脚手架保持一致。

## 初始化命令

```bash
nanobot onboard --packaging-profile enterprise
```

如果 workspace 已有内容，onboard 会跳过 enterprise overlay 文件复制，以避免静默混合；但仍会更新配置中的 packaging profile。

## 结果基线

- `config.packaging.active_profile = enterprise`
- enterprise profile 对应的 capability 需求会被写入并补齐
- 新建空白 workspace 会额外生成 `bootstrap/enterprise/README.md`
- 标记文件：
  - `PACKAGING_PROFILE.md`
  - `.nanobot-packaging-profile`

## 验收建议

1. 调用 `/api/ready`，确认 `packaging_profile=enterprise`
2. 确认 `packaging_ready=true`
3. 核对 `packaging_reasons` 为空或仅包含预期项
