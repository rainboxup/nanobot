# Effective Policy & Soul（Explainability 指南）

这份文档解释两个常见困惑：

1) “我在页面/命令里改了设置，**到底哪里生效**？”  
2) “为什么 UI 显示某能力是 off/denied？**原因是什么**？”

---

## 1) 先分清两件事：Policy vs Soul

- **Policy（硬约束）**：代码层面的强制规则（例如工具开关/白名单/路由 allowlist）
  - 特点：能阻止越权与资源滥用；会给出 `reason_code`
- **Soul（软提示）**：给模型的系统提示词/性格/工作方式（文本）
  - 特点：不能绕过 Policy；更多用于“说话方式”和“默认行为”

一句话总结：**Policy 决定“能不能做”，Soul 决定“怎么做”。**

---

## 2) Effective（实际生效）怎么来的？

Effective 通常来自三层合并：

1) **System baseline（系统基线）**：运维配置（可能来自 `config.json` + `NANOBOT_*`）
2) **Workspace overrides（工作区覆盖）**：租户保存的配置（立即生效）
3) **Session metadata（会话临时项）**：当前会话/请求携带的临时开关（不持久化）

并且：如果 Policy 判断“禁止”，Effective 会体现为 off/denied，并给出原因。

---

## 3) Dashboard：从哪里看 explainability？

### 3.1 Tools Policy（工具权限）

入口：**Settings → Tools Policy**

你会看到（按系统/租户/用户设置分层展示）：

- `system_cap`：系统总开关/系统白名单
- `tenant_policy`：租户自己的策略（更严格）
- `user_setting`：当前 workspace 的开关
- `effective`：最终是否启用（并附带 `reason_codes`）

常见 reason_code（示例）：

- `system_disabled`：系统总开关关闭
- `system_allowlist`：未命中系统白名单
- `tenant_disabled`：租户策略关闭
- `tenant_allowlist` / `tenant_policy`：租户限制导致禁止
- `user_disabled`：用户设置关闭

### 3.2 Soul（提示词层叠与预览）

入口：**Settings → Soul**

你会看到：

- **Workspace Soul**：当前 workspace 持久化的 Soul（保存后影响后续消息）
- **Effective Preview**：合并后的结果，用于解释“最终会给模型的 Soul 是什么”
  - `Layers` 会显示来源与优先级
  - `Overlay` 仅用于预览/解释（不持久化）

提示：Soul 里写“开启某工具/放开限制”不会绕过 Policy，Policy 仍会先判定。

### 3.3 Channels：System vs Workspace vs Effective

入口：**Settings → Channels**

- **Platform Admin**：系统级 channels（通常需重启生效）
- **Workspace Routing**：工作区级 routing（立即生效，只能“更严格”）
- **Binding（账号绑定）**：当前推荐走 dashboard challenge-first 流程；`!link` 仍保留为兼容路径

更多 routing + `!link` 的用户流/排障：`docs/howto/workspace-routing-and-binding.md`

支持/排障建议：

- 如果用户问“为什么消息没进 workspace”，优先看 routing explainability：
  - Dashboard 的 routing explain / 日志里的 `reason_code`
  - API：`POST /api/channels/{name}/routing/explain`
- 重点字段：
  - `reason_code`：稳定拒绝/放行原因
  - `reason_summary`：给支持/销售能直接复述的简明解释
  - `details`：用于定位 mention、allowlist、workspace enabled 等具体条件

### 3.4 Ops：怎么讲“这是支持/销售也能看懂的运行时快照”？

入口：**Settings → Ops**

- 这是 **Owner-only** 的事实型快照，不是另一个配置页
- 数据来自：`/api/ops/runtime`
- 当前适合在 pilot 演示中讲的内容：
  - 已注册 / 运行中渠道数
  - workspace runtime 数量与 inactive 数量
  - inbound / outbound queue pressure
  - active web connections
  - attention items（例如某个 workspace runtime 已配置但未激活）

讲法建议：

- 用它解释“系统现在是什么状态”
- 不要把它描述成完整 observability / SIEM / APM 平台
- 看到 attention item 时，继续联动 Channels / Security / Users 页面做解释

### 3.5 Security / Users：为什么有的页面你看得到但不能操作？

入口：**Settings → Security** / **Settings → Users**

- **Security**：偏 Owner 的运维/审计视角，主要看登录锁定、审计事件、清理策略
- **Users**：Owner / Admin 都能进入，但 Admin 的操作范围会被限制在**本租户的 member 用户**
- **Channels → Workspace Routing**：member 可以做 binding，但 routing / BYO credentials 仍要求 admin access
- Dashboard 现在会显示“权限边界”卡片；API 侧也可通过 `GET /api/security/boundaries` 查看机器可读的边界说明
- 如果 UI 显示按钮不可用，优先看：
  - 当前登录角色（owner / admin / member）
  - 当前资源是 system 还是 workspace scope
  - 返回的 `reason_code` / `Insufficient role`

---

## 4) 运维建议：怎么减少“改了但不生效”

- 先问清楚这是 **系统级** 还是 **工作区级**：
  - 系统级（channels/gateway/traffic）：改完通常要重启
  - 工作区级（providers/tools/agents/workspace routing）：改完立即影响新消息
- 排障优先看 Effective：
  - Tools：看 `effective + reason_codes`
  - Routing：看 system/workspace/effective 三态 + 日志 reason_code
