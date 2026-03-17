# Configuration Scopes & Ownership (Multi-Tenant)

面向对象：**运维 / Owner / Admin**。

目标：让你能快速判断——某个设置该去哪改、什么时候生效、为什么“改了但不生效”、以及常见报错/拒绝的含义。

相关 How-to：

- Workspace Routing + 绑定（`!link`）+ 排障：`docs/howto/workspace-routing-and-binding.md`
- Explainability：Effective Policy + Soul：`docs/howto/effective-policy-and-soul.md`
- 多实例运行时路径：`docs/howto/multi-instance-runtime-layout.md`

---

## 1) 三个 Scope（系统 / 工作区 / 会话）

| Scope | 谁能改 | 存在哪 | 什么时候生效 | 典型内容 |
|---|---|---|---|---|
| **System（系统级）** | 运维/Owner | 默认 `~/.nanobot/config.json`，或由 `--config` 选定的实例配置文件 + 环境变量 `NANOBOT_*` | **需重启**（影响全局） | `channels.*`, `gateway.*`, `traffic.*` |
| **Workspace（工作区/租户级）** | Admin/Owner（Dashboard）或用户（聊天命令） | 当前实例根下的 `tenants/<tenant_id>/config.json` | **立即生效**（仅影响当前租户） | `providers.*`, `agents.*`, `tools.*`, `workspace.*`（含 workspace routing） |
| **Session（会话级）** | 当前会话 | 仅内存/请求 metadata | 立即生效（不持久化） | `session.*`（例如 overlay、临时开关） |

> “Effective（实际生效）”通常指：**系统基线（System baseline）** + **工作区覆盖（Workspace overrides）** + **会话临时项（Session metadata）** 合并后的结果，并且会被 Policy 规则进一步约束。

---

## 2) Dashboard 与 Scope 的对应关系（最常用）

- **Settings → Channels → Platform Admin（系统级）**
  - 改：`channels.*`
  - 特点：**所有租户共享**，大多需要**重启**才影响运行中的连接
- **Settings → Channels → Workspace Routing（工作区级）**
  - 改：`workspace.channels.feishu|dingtalk.*`
  - 特点：**立即生效**；只负责“能否进入当前 workspace”，不会改变系统连接本身
  - 绑定与排障：
    - workspace 成员可以做当前账号的 identity binding
    - workspace admin / owner 才能修改 routing 与 BYO credentials
    - 支持侧可用 `POST /api/channels/{name}/routing/explain` 查看 `reason_code` / `reason_summary` / `details`
- **Settings → Ops**
  - 看：owner 侧运行时快照（`/api/ops/runtime`）
  - 特点：只展示事实型信号：registered/running channels、workspace runtimes、queue pressure、active web connections、attention items
- **Settings → Users**
  - 改：用户、角色、会话生命周期（按角色/租户范围收敛）
  - 特点：Owner 可管理全部租户；Admin 仅能管理自己租户下的 member 用户
- **Settings → Security**
  - 看：登录锁定、审计事件、保留策略
  - 特点：这是 Owner 视角的运维/审计面板，不用于修改租户业务配置
  - 角色边界可通过 `GET /api/security/boundaries` 或 dashboard 中的“权限边界”卡片查看
- **Settings → Providers / Tools Policy / Soul（工作区级）**
  - 改：`providers.*`, `tools.*`, `agents.*` 等
  - 特点：立即生效；提供 Effective/Reason codes 解释入口

聊天命令（工作区级/会话级）：

- `!whoami`：查看当前 tenant/workspace 与已绑定身份（用于排障/配置 allowlist）
- `!link` / `!link <CODE>`：跨身份绑定到同一 workspace（请务必私聊/DM 使用；当前保留为兼容路径）
- `!apikey set ...` / `!model set ...`：写入工作区配置

---

## 2.1) Pilot 讲解时最常用的角色边界

| Surface | Owner | Admin | Member |
|---|---|---|---|
| Users | 可创建任意租户用户；可调角色；可管理除自己外任意账号生命周期 | 仅能在当前租户创建 member/admin；仅能管理当前租户 member 生命周期 | 不可进入 |
| Workspace Routing / BYO | 可改 | 可改（当前租户） | 只读 |
| Workspace Binding | 可用 | 可用 | 可用（仅当前账号） |
| Ops | 可看 | 不可看 | 不可看 |
| Security / Audit | 可看可操作 | 不可看 | 不可看 |

补充说明：

- **WeCom remains owner-managed in Platform Admin.**
- WeCom 当前仍是 **system-scoped MVP**，不承诺 workspace BYO / workspace routing。
- 对 pilot / 销售演示，优先展示 dashboard 里的“权限边界”与 owner/admin/member 的差异，不要把它描述成完整企业 IAM。

---

## 3) 关键原则：不能越权，只能更严格

### 3.1 禁止越权（Privilege escalation）

工作区配置不能修改系统级配置域（例如 `channels.*` / `gateway.*` / `traffic.*`）。

常见 `reason_code`：

- `privilege_escalation`

### 3.2 subset 约束（Subset constraint）

工作区允许“更严格”，不允许“更宽松”。

最常见的是 allowlist 的子集约束：

- 当系统 `channels.<name>.allow_from` 非空时，工作区 `workspace.channels.<name>.allow_from` 必须是它的子集

常见 `reason_code`：

- `subset_constraint`

---

## 4) 租户配置文件的边界（运维需要知道）

租户配置持久化的根键以工作区维度为主：`agents/tools/providers/workspace`。

- 租户配置是 **schema-strict**：出现未知/拼错的 key 会被拒绝（`reason_code=tenant_config_unknown_keys`）。
- 系统级根键（如 `channels/gateway/traffic/session`）出现在租户配置里会触发 `privilege_escalation`（不会静默忽略）。
- 租户配置加载不会读取宿主机进程环境变量（避免 `NANOBOT_*` 泄漏进租户）。

---

## 5) 常见错误/拒绝：运维排障索引

### 配置保存/并发相关

- `tenant_config_busy`：配置文件正被其他写入占用；重试即可
- `tenant_config_conflict`：配置在你加载后被别人改过；需要 reload 后再保存

### 配置内容相关

- `tenant_config_unknown_keys`：租户配置包含不支持的 key（常见于手工编辑拼写错误/版本不匹配）

### 权限/运行模式相关

- `insufficient_permissions`：角色不足（例如非 Owner 改系统 channels）
- `single_tenant_runtime_mode`：单租户运行模式下禁止写入工作区级配置

### “为什么没收到消息？”

Workspace Routing 相关的 reason_code 表与排障流程详见：

- `docs/howto/workspace-routing-and-binding.md`
