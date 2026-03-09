# Workspace Routing & Binding（Dashboard + `!prove` / `!link`）（Feishu / DingTalk）

这是一份面向 **运维 / Owner / Admin / 试用用户** 的操作与排障指南，解决两类问题：

1) **如何配置 Workspace Routing**（让消息“能否进入当前 workspace”可控且立即生效）  
2) **如何安全绑定身份**（优先用 Dashboard + `!prove` 校验身份；`!link` 仅作兼容兜底）

另见：

- Effective Policy + Soul explainability：`docs/howto/effective-policy-and-soul.md`

---

## 0) 先理解：System / Workspace / Effective

对 Feishu / DingTalk 来说，消息能否进入某个 workspace，取决于两层：

- **System（系统级）**：`channels.feishu|dingtalk.*`
  - 控制：是否启用连接、凭证、系统 allow_from（全局）
  - 特点：通常 **需要重启** 才影响正在运行的连接
- **Workspace（工作区级）**：`workspace.channels.feishu|dingtalk.*`
  - 控制：workspace routing 开关、allowlist、群策略
  - 特点：**立即生效**

因此 UI 会显示三种状态：

- `system on/off`
- `workspace on/off`
- `effective on/off`（一般等价于 `system && workspace`）

---

## 1) 配置 Workspace Routing（Dashboard）

入口：**Settings → Channels → Workspace Routing**

你会看到每个 workspace-routing channel（当前为 Feishu / DingTalk）：

- `system on/off`：系统连接是否启用
- `workspace on/off`：当前 workspace 是否允许该 channel 的消息进入
- `effective on/off`：最终是否允许进入（system 与 workspace 的合取）

### 1.1 开关（Enable workspace routing）

- 关闭：该 channel 的消息不会进入当前 workspace（会被拒绝，通常不发通知）
- 开启：继续按 allowlist / 群策略判断

### 1.2 Sender allowlist（Allowed sender IDs）

用途：在 workspace 层进一步收紧可进入的 sender。

- 留空：不额外加限制（但 **系统 allow_from 仍可能拦截**）
- 非空：只允许列表里的 sender ID

重要约束（常见踩坑点）：

- 若系统 `channels.<name>.allow_from` 非空，则 workspace 的 `allow_from` 必须是它的子集，否则保存会失败（`reason_code=subset_constraint`）。

如何拿到 sender ID（自助方式）：

1. 在对应平台 **私聊/DM** 机器人发送 `!whoami`
2. 在输出的 `linked identities` 里找到形如 `feishu:<sender_id>` / `dingtalk:<sender_id>`
3. 把 `<sender_id>`（冒号后半段）填到 `Allowed sender IDs`（一行一个）

### 1.3 群策略（Group policy）

当 `message_type=group` 时生效：

- `open`：允许所有群消息进入（高风险，可能被刷屏）
- `mention`（默认推荐）：必须 @ 机器人（或平台判断为“被提及”）才允许进入
- `allowlist`：仅允许 `Allowed group IDs` 列表中的群

### 1.4 群 allowlist（Allowed group IDs）

仅当 `group policy=allowlist` 时启用。

当前获取 group_id 的方式偏运维向（因为 UI/命令暂不直接暴露）：

- Feishu：`group_id` 通常等于群聊的 `chat_id`
- DingTalk：`group_id` 通常等于群聊的 `conversation_id`
- 建议做法：让运维在服务日志里定位一次群消息，提取对应 ID 后写入 allowlist

---

## 2) 安全绑定身份（优先用 Dashboard + `!prove`）

绑定的意义：

- **多个身份 → 同一个 workspace**
- 绑定后共享：记忆/技能/工作区配置
- 仍隔离：会话历史（按身份/渠道隔离）

### 2.1 推荐方式：Dashboard 发起 challenge，目标身份用 `!prove` 完成校验

入口：**Settings → Channels → Workspace Routing → 目标 channel → Binding**

1) 在 Dashboard 中点击 **Start verification**
2) 系统会生成一个短时有效的 challenge code（当前默认约 **5 分钟**）
3) 在“要加入这个 workspace”的目标身份里，打开 **私聊/DM**，发送：

```text
!prove <CODE>
```

4) 回到 Dashboard，点击 **Refresh status**
5) 当你看到 `verified identity` 后，点击 **Confirm binding**

这个主流程的好处：

- 不要求用户手填 `sender_id`
- 绑定的是**实际发出 `!prove` 的身份**
- member/admin 都可以对自己的账号完成绑定管理

### 2.2 兼容兜底：`!link`

如果当前环境还在使用旧流程，或者你临时无法走 Dashboard challenge 流，可以退回兼容命令：

1) 在“已经绑定到目标 workspace”的任意身份中（私聊/DM）发送：

```text
!link
```

2) 在“要加入这个 workspace”的新身份中（私聊/DM）发送：

```text
!link <CODE>
```

### 2.3 安全注意事项（必须读）

- challenge code / `!link` code 都等同“临时密码”：**不要发到群里、不要截图外传**
- 群聊里执行 `!prove` / `!link` 会被拒绝（防止泄露）
- 如果你怀疑 code 泄漏：直接在 Dashboard 里重新生成 challenge，或重新执行 `!link`
- 绑定成功后，建议立刻执行 `!whoami` 确认新的 identity 已出现在 `linked identities`

---

## 3) `!whoami` 的用途（强烈建议作为排障第一步）

`!whoami` 会输出：

- `tenant_id`：当前身份所属 workspace 的标识
- `workspace`：工作区目录路径（运维排障用）
- `linked identities`：已经绑定到同一 workspace 的身份列表

常见用途：

- 确认“我现在在哪个 workspace”
- 拿到 `sender_id`（用于配置 workspace routing 的 allow_from）
- 验证绑定是否生效（是否出现新的 identity）

---

## 4) 为什么没收到消息？（排障清单 + reason_code）

先按这个顺序排查（最省时间）：

1) **System 连接是否启用且配置正确？**
   - `system off`：无论 workspace 怎么配，都不会进来
   - System 改动通常需要重启生效（Platform Admin 页面会提示）
2) **Workspace routing 是否开启？**
   - `workspace off` / `effective off`：消息会被拒绝
3) **是不是被 allowlist 拦了？**
   - 系统 allow_from（先拦截）
   - workspace allow_from（后拦截，只能更严格）
4) **群消息是否满足群策略？**
   - `mention`：必须 @ 机器人
   - `allowlist`：群 ID 必须在列表中
5) **你可能收到了“私聊回复”而不是群里回复**
   - Feishu/DingTalk 为安全起见，群消息默认倾向私聊回复（避免把 tenant 上下文泄漏到群里）

### 4.1 Workspace routing 常见 reason_code

这些 reason_code 通常出现在日志/指标里（以及部分 API 返回中）：

| reason_code | 含义 | 你该怎么做 |
|---|---|---|
| `workspace_channel_disabled` | 当前 workspace 禁止该 channel 进入 | 在 Workspace Routing 打开开关 |
| `sender_not_allowlisted` | sender 不在 workspace allow_from | 用 `!whoami` 拿到 sender_id，加入 allow_from（注意子集约束） |
| `bot_not_mentioned` | 群策略为 mention，但没有 @ 机器人 | 在群里 @ 机器人，或将群策略改为 open/allowlist |
| `group_not_allowlisted` | 群策略为 allowlist，但 group_id 不在列表 | 把正确 group_id 加入 allowlist |
| `unsupported_group_policy` | 配置了不支持的群策略值 | 改回 `open/mention/allowlist` |
| `missing_sender_id` | 平台消息里缺少 sender_id | 多为适配器/平台异常；联系运维看日志 |

> 如果你“完全没看到任何拒绝信息”，这通常是**刻意的静默丢弃**（避免泄露策略）。此时最有效的办法是让运维看一眼 ingress 侧日志。
