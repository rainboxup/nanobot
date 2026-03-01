# nanobot 控制台（Web Dashboard / SaaS）产品介绍与页面功能清单

> 最后更新：2026-03-01  
> 范围：本文仅梳理 `nanobot` 的 **Web 控制台**（静态前端 + Dashboard API），用于产品/UI/UX 视角的功能对齐与后续重构规划。

## 0. 项目简介（nanobot 是什么）

`nanobot` 是一个超轻量的个人/团队 AI 助手与网关（Gateway）项目，核心能力包括：

- **Agent 对话**：以会话为单位与模型交互，支持工具调用与技能扩展
- **Providers**：对接多种模型服务（API Base / Key / 路由）
- **Channels**：对接 Telegram/Slack/Discord/飞书等渠道，把“对话能力”投递到不同入口
- **Skills**：以技能（Skill）形式沉淀可复用能力
- **Web 控制台（Closed Beta / SaaS）**：提供配置、用户/权限、安全审计、运维快照等后台能力（本文重点）

## 1. 这个产品是什么类型的 SaaS？

从 UI/UX 与产品定位看，当前 Web 控制台更像是一个 **B2B/运维后台（Admin Console）型 SaaS**：

- **核心对象**：LLM Provider（模型服务）、Channels（渠道接入）、Skills（技能）、Users/ Tenants（用户/租户）、Security & Audit（安全与审计）、Runtime Snapshot（运行态快照）
- **核心任务**：让 Owner/Admin 以“控制台”方式完成配置、启停、排障、审计与权限管理，而不是在服务器上手改配置文件
- **典型场景**：封闭 Beta / 多租户部署；运维与管理员日常使用；支持排障与用户治理

> 前端源码：`nanobot/web/dashboard-react/`（Vite + React + TS）  \n+> 构建产物：`nanobot/web/static/`（由后端通过 `/static` 挂载，根路径 `/` 返回 `index.html`）  \n+> 路由：HashRouter（`#/...`），避免后端做 SPA fallback

## 2. Web 控制台的信息架构（IA）与全局能力

### 全局导航

顶部栏固定存在（登录后显示主导航）：

- 主导航：`对话` / `设置` / `技能` / `运维`
- 主题：系统/亮色/暗色（持久化）
- 退出登录（会 revoke token，并清空本地 token）

### 全局状态提示（Ready / Warnings）

控制台会定期拉取 `/api/ready`，在右上角显示状态 Badge（`READY / WARNING / ERROR`），点击可展开摘要：

- Version（后端版本）
- Warnings 数量
- 入口：跳转到 `#/ops` 查看完整运维大盘与原始 JSON

### 通知（Toast）

全局 Toast 用于成功/失败反馈，支持 action、可更新同 ID toast、数量上限、hover/focus 暂停等（长期可维护的通知机制）。

## 3. 页面列表与功能（现状 + 想要的功能）

下面按路由列出当前每个页面的功能，并补充“想要的功能/规划”作为后续 UI/UX 重构与迭代参考。

**页面一览（快速定位）**

- `#/login`：登录（含邀请码/封闭 Beta）
- `#/chat`：对话（WS 连接、历史、发送）
- `#/settings/providers`：模型服务（Providers）配置
- `#/settings/channels`：渠道（Channels）启停与配置
- `#/settings/beta`：封闭 Beta（allowlist / invites）
- `#/settings/users`：用户与权限（用户增删改、角色、会话治理、改密）
- `#/settings/security`：安全与审计（Owner）（登录锁定、审计事件、导出、保留策略）
- `#/skills`：技能列表与详情
- `#/ops`：运维大盘（运行时快照、warnings、原始 JSON）

---

### 3.1 `#/login` 登录

**现有功能**

- 用户名/密码登录
- 可选邀请码 `invite_code`（封闭 Beta 登录一次加入白名单）
- 登录成功后保存 access/refresh token 并跳转 `#/chat`

**相关接口**

- `POST /api/auth/login`

**想要的功能（规划）**

- 登录态与错误提示更“产品化”：明确错误原因、失败次数提示、锁定提示（与安全模块联动）
- 密码可见性切换、CapsLock 提示、输入校验与 loading 状态
- 可扩展的登录方式：OAuth/SSO、2FA（若面向企业/团队化使用）

---

### 3.2 `#/chat` 对话

**现有功能**

- 通过 WebSocket 建立对话连接，展示连接状态与 session id
- 首包可返回 session 信息；获取历史消息并渲染
- 发送消息（按钮或 Ctrl/Cmd + Enter）
- 启动时检查“模型服务是否配置”，未配置则提示去设置页

**相关接口**

- `WS /ws/chat?token=...`
- `GET /api/chat/history?session_id=...`
- `GET /api/chat/status`

**想要的功能（规划）**

- 对话体验升级：流式输出、Markdown 渲染、代码块/复制按钮、错误消息的更清晰分层（系统/模型/网络）
- 会话管理：会话列表、搜索、重命名、删除、导出
- 多模态与附件（若产品路线包含）：图片/文件上传与展示
- 空状态与引导：首次使用的一键配置向导（直达“模型服务”）

---

### 3.3 `#/settings` 设置（模块化）

设置页是“后台型 SaaS”的核心页面，采用左侧导航 + 右侧面板结构，并记住上次打开的子模块。

#### 3.3.1 模型服务（Providers）

**现有功能**

- 列表展示 Provider：名称 / API Base / Masked Key
- 行内编辑 `api_base` 与 `api_key`（密钥不回填；可选择清空）

**相关接口**

- `GET /api/providers`
- `PUT /api/providers/:name`

**想要的功能（规划）**

- 配置向导与即时校验：API Base 格式、Key 是否可用、可用模型列表探测
- 多环境/多路由管理：默认 provider、fallback provider、按租户覆盖
- 变更影响提示：哪些功能会受影响（对话、渠道等）

#### 3.3.2 渠道（Channels）

**现有功能**

- 渠道列表展示：名称、config 摘要、启用开关、编辑按钮
- 启用/禁用渠道
- 编辑渠道 config：自动渲染配置表单、支持字段搜索；敏感字段不自动填充（留空保持不变）

**相关接口**

- `GET /api/channels`
- `POST /api/channels/:name/toggle`
- `GET /api/channels/:name`
- `PUT /api/channels/:name`

**想要的功能（规划）**

- 更强的配置体验：字段说明/示例、校验、测试连接/发送测试消息
- 渠道运行状态：最近心跳/最近错误/速率/消息吞吐（与运维快照联动）
- 变更审计：谁在什么时候改了什么

#### 3.3.3 封闭 Beta（邀请码与白名单）

**现有功能**

- 显示封闭 Beta 是否启用
- 白名单用户：添加/移除
- 邀请码：创建（TTL、最大次数、可指定目标用户）、撤销、列表展示状态

**相关接口**

- `GET /api/beta/allowlist`
- `POST /api/beta/allowlist`
- `DELETE /api/beta/allowlist/:username`
- `GET /api/beta/invites`
- `POST /api/beta/invites`
- `DELETE /api/beta/invites/:code`

**想要的功能（规划）**

- 邀请码管理增强：复制按钮、批量生成、按用户/过期筛选、导出
- 白名单与租户策略：按租户策略控制注册/邀请、配额、到期策略

#### 3.3.4 用户（角色/租户/会话）

**现有功能**

- 创建用户：用户名/密码/角色/租户（Admin 有权限限制）
- 用户列表：更新角色（Owner）、启用/禁用、删除、重置密码
- 会话管理（overlay）：查看 token 会话、撤销单个会话、强制全部退出（需要填写原因）
- 修改我的密码

**相关接口**

- `GET /api/auth/me`
- `GET /api/auth/users`
- `POST /api/auth/users`
- `PUT /api/auth/users/:username/role`
- `PUT /api/auth/users/:username/status`
- `DELETE /api/auth/users/:username`
- `POST /api/auth/users/:username/reset-password`
- `GET /api/auth/users/:username/sessions?...`
- `DELETE /api/auth/users/:username/sessions/:token_id?reason=...`
- `POST /api/auth/users/:username/sessions/revoke-all`
- `POST /api/auth/change-password`

**想要的功能（规划）**

- 权限与租户可视化：清晰解释 Owner/Admin/Member 能做什么
- 用户生命周期：邀请式创建、强制首次改密、密码策略、冻结原因
- 会话治理：设备信息、地理/IP 画像、批量撤销与导出

#### 3.3.5 安全（审计、登录防护与解锁；Owner 可见）

**现有功能**

- 登录锁定状态：按用户名/IP/范围筛选；查看锁定原因与时间；单条/批量解锁（原因必填）
- 安全事件（审计）：筛选、分页“加载更早”、导出 CSV
- 审计保留策略：查看状态、手动触发清理

**相关接口**

- `GET /api/security/login-locks?...`
- `POST /api/security/login-locks/unlock`
- `POST /api/security/login-locks/unlock-batch`
- `GET /api/audit/events?...`
- `GET /api/audit/events/export?...`
- `GET /api/audit/retention`
- `POST /api/audit/retention/run`

**想要的功能（规划）**

- 安全告警更可读：策略解释、推荐操作、风险等级
- 审计 UX：事件详情抽屉、可复制字段、固定常用筛选（presets）可保存
- 合规能力（若需要）：导出字段选择、签名/防篡改、保留策略可配置

---

### 3.4 `#/skills` 技能

**现有功能**

- 技能列表（卡片）：展示 name/source/description
- 查看技能详情：展示完整 content；可关闭详情

**相关接口**

- `GET /api/skills`
- `GET /api/skills/:name`

**想要的功能（规划）**

- 技能管理：搜索/筛选/标签、启用/禁用、版本与变更记录
- 安装/卸载（如果产品路线包含“技能市场/远程技能”）：从仓库安装、权限提示、风险提示
- 技能运行可观测：调用次数、失败率、耗时

---

### 3.5 `#/ops` 运维（运行时快照）

**现有功能**

- 拉取运行时快照，展示：
  - 状态（正常/异常/未知）与 warnings
  - 运行时间（started_at / uptime）
  - 队列深度与利用率
  - 渠道注册信息与状态 JSON
  - 原始 JSON（可复制）
- 刷新、复制 JSON
- 权限控制：不足角色会提示仅 Owner 可用

**相关接口**

- `GET /api/ops/runtime`

**想要的功能（规划）**

- 观测面板升级：历史趋势图（队列、吞吐、错误）、日志入口、下载诊断包
- 更强的排障路径：一键复制“支持信息”、关联最近审计/错误事件
- 只读与可写分离：避免误操作；危险动作二次确认

## 4. 已提出的 UI/UX 方向（需求汇总）

结合当前沟通记录，后续前端/体验迭代的目标可以先明确为：

- **先重构再重设 UI**：先把页面结构与组件化整理清晰，再进入视觉与交互的系统性重设计
- **主题切换能力要可扩展**：不只是临时实现，需有稳定的 design tokens / 主题系统（目前已有主题与配色切换，重构时要保证不回退，并为更多主题预留结构）
- **Toast 组件长期可用**：交互/样式/可访问性一致；支持 action、可更新、可批量收敛；避免全局散落的“临时提示”
- **从长期角度解决问题**：避免“打补丁式”改 UI；优先建立可复用的布局、表单、表格、弹窗、通知等基础组件与规范

--- 

如果你希望我把“每个页面对应的用户旅程（Journey）/关键场景/异常场景/信息架构重排建议”也补齐，我可以在这个 `PRODUCT.md` 基础上继续扩展成一份更完整的 PRD。
