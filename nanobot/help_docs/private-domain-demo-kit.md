# Private-Domain Demo Kit

这份说明面向 **Owner / Admin / 运维**，帮助你用最小步骤启动一个偏“私有域运维试点”的演示工作区。

## 适用场景

- 你要演示多渠道接入后的运营与排障路径
- 你需要先给小范围试点用户一个受控环境
- 你希望把“路由、权限、运维说明”作为第一优先级

## 初始化

```bash
nanobot onboard --demo-kit private-domain-ops
```

这个 demo kit 只会在**新建且空白**的 workspace 上叠加内容；已有内容的 workspace 会跳过 overlay，避免静默混合。

## 你会得到什么

- `workspace/demo/private-domain-ops/README.md`
- `.nanobot-demo-kit` 标记文件
- bundled help 文档与配套 skill，可供产品面与运维面引用

## 建议下一步

1. 先完成 channel / routing 基线配置
2. 再核对 `docs/howto/workspace-routing-and-binding.md`
3. 最后再邀请试点用户进入 workspace
