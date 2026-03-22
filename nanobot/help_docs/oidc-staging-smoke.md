# OIDC Staging Smoke

这份说明用于在 staging/预生产环境快速验证 Nanobot Web 的 OIDC 登录链路。

## 前置条件

1. Web 服务使用 OIDC provider：
   - `NANOBOT_WEB_AUTH_PROVIDER=oidc`
2. OIDC 基础配置可用（至少满足其一）：
   - `NANOBOT_WEB_OIDC_JWKS_URL`
   - `NANOBOT_WEB_OIDC_JWKS_JSON`
3. 建议具备一个可用 `id_token`（短时效测试令牌）。
   - 没有 token 时也可执行 `--oidc-preflight` 预检模式。

## 推荐环境变量

```bash
export NANOBOT_SMOKE_URL="https://your-staging-domain"
export NANOBOT_SMOKE_OIDC_ID_TOKEN="<paste-id-token>"

# 可选断言
export NANOBOT_SMOKE_EXPECT_USERNAME="user@example.com"
export NANOBOT_SMOKE_EXPECT_TENANT_ID="tenant-a"
export NANOBOT_SMOKE_EXPECT_ROLE="member"
```

## 执行 Smoke

```bash
python scripts/smoke_oidc_login.py --allow-ready-degraded
```

默认验证步骤：

1. `GET /api/health`
2. `GET /api/ready`
3. `POST /api/auth/login`（`id_token`）
4. `GET /api/auth/me`

无 token 预检模式（不验证真实用户身份，仅验证 OIDC provider/JWKS 链路）：

```bash
python scripts/smoke_oidc_login.py --allow-ready-degraded --oidc-preflight
```

## 失败排障（reason_code）

`/api/auth/login` 失败时会返回 `reason_code`。常见值：

- `auth_provider_unavailable`: provider 未注册或配置名错误
- `oidc_id_token_required`: 请求体未携带 `id_token`
- `oidc_token_invalid` / `oidc_token_expired`: token 本身无效或过期
- `oidc_token_algorithm_not_allowed`: token 算法不在允许列表
- `oidc_token_kid_unknown`: `kid` 不在 JWKS 中
- `oidc_jwks_not_configured`: 未配置 `JWKS_URL/JWKS_JSON`
- `oidc_jwks_fetch_failed` / `oidc_jwks_timeout`: 取 JWKS 失败或超时
- `oidc_jwks_invalid_json` / `oidc_jwks_no_keys`: JWKS 响应格式问题
- `oidc_username_claim_missing`: claim 映射后拿不到用户名

## CI / 自动化建议

1. 优先使用短期测试令牌执行完整 smoke，不要使用长期敏感令牌。
2. 没有 token 时使用 `--oidc-preflight`，至少保证 provider 选择和 OIDC 失败码行为正确。
3. 将脚本退出码纳入部署门禁：
   - `0`: 通过
   - `1`: 业务断言不匹配（如用户名/租户/角色）
   - `2`: 接口或登录链路失败
