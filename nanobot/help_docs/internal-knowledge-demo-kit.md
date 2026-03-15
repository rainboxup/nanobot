# Internal Knowledge Demo Kit

这份说明面向 **Owner / Admin / 业务试点负责人**，帮助你快速准备一个“内部知识问答演示”工作区。

## 适用场景

- 你要展示 FAQ / 文档问答的基础效果
- 你希望先在小范围内验证回答质量
- 你需要一个可重复初始化的演示入口

## 初始化

```bash
nanobot onboard --demo-kit internal-knowledge-demo
```

这个 demo kit 只会在**新建且空白**的 workspace 上叠加内容；已有内容的 workspace 会跳过 overlay，避免静默混合。

## 你会得到什么

- `workspace/demo/internal-knowledge-demo/README.md`
- `.nanobot-demo-kit` 标记文件
- bundled help 文档与配套 skill，可供演示与培训使用

## 建议下一步

1. 先准备一小批可信知识源
2. 再做几组固定问答验证
3. 把已知边界写清楚后再扩大试点范围
