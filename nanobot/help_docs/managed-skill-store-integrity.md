# Managed Skill Store：Manifest 与完整性校验

这份文档面向 **运维 / Owner / 平台管理员**，说明本地托管技能商店（managed skill store）现在如何做安装前检查，以及如何为技能包提供可验证的 manifest。

适用场景：

- 技能来自本地托管商店目录，而不是 ClawHub 远端 ZIP
- 你希望在安装前知道：这个技能包是否被改过、是否超过平台允许的大小、为什么安装被拒绝

另见：

- Effective explainability：`docs/howto/effective-policy-and-soul.md`
- Workspace Routing / `!link`：`docs/howto/workspace-routing-and-binding.md`

---

## 1) 当前行为：Managed store 仍是 install source，但 runtime canonical layer 已是 `managed`

当前实现保持设计文档里的 MVP 边界：

- **Managed store 仍然是本地托管安装来源**
- 对于来自这一层的技能，运行时对外暴露的 canonical `source` 已统一为 `managed`
- 当前运行时优先级已经是 **workspace > managed > bundled**
- 已安装到 workspace 的技能仍然优先于 managed / bundled

因此，这里的完整性校验关注的是：

- **安装前是否可信**
- **包是否过大**
- **包结构是否安全**

同时，API 为了平滑迁移仍会保留兼容字段，用来区分“运行时 canonical source”和“来源追溯 / 安装来源”。

---

## 2) 现在会检查什么？

对于本地托管商店中的技能目录，安装前会做以下检查：

### 2.1 目录大小（package size）

- 会统计技能目录总大小
- 当前内建上限为 **64 MiB**
- 超过上限会拒绝安装

对应 `reason_code`：

- `source_package_too_large`

### 2.2 目录指纹（SHA-256）

- 会对技能目录内容计算一个稳定的 SHA-256 摘要
- 摘要覆盖目录里的普通文件内容与相对路径
- manifest 文件本身不会参与摘要计算

如果没有 manifest：

- 仍允许安装
- 完整性状态会显示为 `unverified`

### 2.3 可选 manifest 校验

如果技能目录里存在：

```text
.nanobot-skill-manifest.json
```

则安装前会校验：

- `sha256` 是否匹配目录实际内容
- 如果 manifest 提供了 `size_bytes`，则它也必须匹配实际统计值

对应 `reason_code`：

- `source_manifest_invalid`
- `source_integrity_mismatch`

### 2.4 不安全目录结构

如果技能目录里包含不支持的符号链接，安装会被拒绝。

对应 `reason_code`：

- `source_package_symlink_unsupported`

如果目录内容无法读取，也会拒绝安装：

- `source_package_unreadable`

---

## 3) Manifest 格式

manifest 文件名固定为：

```text
.nanobot-skill-manifest.json
```

推荐格式：

```json
{
  "integrity": {
    "sha256": "8a4d0b7b9f0f0e0d3f1f9a1d3a4f4c3a1b2c3d4e5f60718293a4b5c6d7e8f901",
    "size_bytes": 12345
  }
}
```

兼容格式：

```json
{
  "sha256": "8a4d0b7b9f0f0e0d3f1f9a1d3a4f4c3a1b2c3d4e5f60718293a4b5c6d7e8f901",
  "size_bytes": 12345
}
```

字段说明：

- `sha256`：必填，64 位小写十六进制摘要
- `size_bytes`：可选，目录总字节数

---

## 4) UI / API 里会看到什么？

### 4.1 Skills source contract

技能相关接口现在统一采用下面这组字段语义：

- `source`：**运行时 canonical source**。客户端应该优先使用它做展示、筛选和分支判断。
- `origin_source`：**兼容 / 来源追溯字段**。它保留迁移期来源信息，例如本地托管商店条目可能表现为 `source=managed`、`origin_source=store`。
- `install_source`：**安装来源**。当前主值是 `local` 或 `clawhub`，表示安装动作来自本地来源还是 ClawHub 远端 ZIP。

当前常见组合：

- workspace 已安装技能：`source=workspace`，`origin_source=workspace`，`install_source=local`
- 本地托管商店条目：`source=managed`，`origin_source=store`，`install_source=local`
- bundled 技能：`source=builtin`，`origin_source=builtin`，`install_source=local`
- ClawHub 远端目录条目：`source=clawhub`，`origin_source=clawhub`，`install_source=clawhub`

主要接口的大致字段集合如下：

- `GET /api/skills`：返回已知技能列表，重点字段包括 `name`、`description`、`path`、`source`、`origin_source`、`installed`
- `GET /api/skills/{name}`：返回单个技能详情，在列表字段之外还会包含 `content`、`metadata`，以及适用时的 `install_source` / `store_metadata`
- `GET /api/skills/catalog`：返回可安装目录项；条目字段以 `source`、`origin_source`、`install_source`、`installed` 为核心，可选带 `store_metadata`
- `GET /api/skills/catalog/v2`：与 catalog 条目契约保持一致，但包装在 `items[]` 中，并带 `next_cursor` / `warnings`
- `POST /api/skills/install`：请求体主路径应发送 `source=local|clawhub`；响应会返回 `name`、`installed`、`already_installed`、`repaired`、`source`、`origin_source`、`install_source`

安装请求兼容策略：

- 前端 / 新客户端主路径应发送 `local` 或 `clawhub`
- 兼容期内，后端仍接受 `store`、`managed`、`builtin`、`workspace` 这类 alias，并将它们归一到本地安装路径
- 客户端不应再把 `store` 当作 runtime canonical source；迁移完成后应仅以 `source` 为准

产品展示建议：

- 普通用户界面只应展示产品化后的主来源标签，例如“工作区”“平台托管”“内置”“ClawHub”
- `origin_source`、`install_source` 这类技术字段更适合放在 Owner / Admin / Support 的详情、排障或 debug 视图中
- 当产品展示与技术字段同时存在时，应始终以 `source` 作为主口径，`origin_source` 仅用于来源追溯

### 4.2 托管商店完整性元数据

当某个技能来自本地托管商店时，以下响应会额外带上 `store_metadata`：

- `GET /api/skills/{name}` 详情接口
- `GET /api/skills/catalog` / `GET /api/skills/catalog/v2`，且请求显式传入 `include_store_metadata=true`

字段包括：

- `store_metadata.package_size_bytes`
- `store_metadata.manifest_present`
- `store_metadata.integrity.algorithm`
- `store_metadata.integrity.status`
- `store_metadata.integrity.digest`
- `store_metadata.integrity.reason_code`

常见状态：

- `verified`：manifest 存在，且摘要/大小匹配
- `unverified`：没有 manifest，但目录结构安全、大小合法
- `mismatch`：manifest 存在，但摘要或大小不匹配
- `invalid`：manifest 非法，或目录结构不安全/不可读

---

## 5) 运维建议：什么时候该补 manifest？

推荐在这些场景补 manifest：

- 你维护的是团队共享的托管商店
- 技能会被多个 workspace 反复安装
- 你希望排查“商店包是否被人手工改动过”
- 你需要把安装失败原因解释给 Owner / Admin

如果是临时试验技能：

- 可以先不提供 manifest
- 系统会标记为 `unverified`
- 但仍然建议在正式投入使用前补上 manifest

---

## 6) 如何生成 manifest（建议流程）

建议在把技能放入托管商店之前，在构建/发布步骤里生成 manifest：

1. 准备技能目录（确保包含 `SKILL.md`）
2. 计算目录内容摘要（不包含 `.nanobot-skill-manifest.json`）
3. 统计目录总字节数
4. 写入 `.nanobot-skill-manifest.json`
5. 再把整个技能目录复制到托管商店

一个最小的伪代码流程：

```text
walk skill directory in stable order
skip .nanobot-skill-manifest.json
for each regular file:
  hash(relative_path + file_size + file_bytes)
  add to total_bytes
write manifest with sha256 + size_bytes
```

如果你已经先把技能放进商店，再手工改动了 `SKILL.md` / 资源文件：

- 记得重新生成 manifest
- 否则安装会因为 `source_integrity_mismatch` 被拒绝

---

## 7) 安装失败怎么排障？

### `source_package_too_large`

- 说明技能目录超过平台允许的托管商店包大小
- 处理方式：减小包体积，或由开发侧调整平台限制

### `source_manifest_invalid`

- manifest 文件存在，但 JSON 结构不合法，或 `sha256`/`size_bytes` 字段格式不正确
- 处理方式：修复 manifest 内容后重试

### `source_integrity_mismatch`

- manifest 存在，但记录值与目录实际内容不一致
- 常见原因：更新技能后忘了重算 manifest

### `source_package_symlink_unsupported`

- 技能目录包含符号链接
- 处理方式：改为普通文件，或在打包前展开链接内容

### `source_package_unreadable`

- 安装时无法读取技能目录中的某些文件
- 处理方式：检查权限、占用、磁盘与损坏情况

---

## 8) 当前边界与后续方向

当前这套机制解决的是：

- 本地托管商店的安装前 explainability
- 基础完整性约束
- 包大小控制

它**没有**解决：

- 运行时 managed cache layer
- 多实例分发一致性
- 技能版本回滚编排

这些属于后续更重的 store/runtime 架构问题，不是当前这一步的目标。

---

## 9) 缓存调优（可选）

当你显式请求 `store_metadata` 时，系统会在进程内缓存目录检查结果。默认值：

- TTL：`300` 秒
- 最大条目：`2048`

可通过环境变量调整：

- `NANOBOT_SKILL_SOURCE_DETAILS_CACHE_TTL_S`
- `NANOBOT_SKILL_SOURCE_DETAILS_CACHE_MAX_ENTRIES`
