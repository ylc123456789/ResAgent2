# Packages

本目录包含 ResAgent2 的可安装逻辑包。

依赖方向必须保持：

```text
contracts
   ↑
runtime
   ↑
agents/*        orchestrator
   ↑                ↑
   └──── adapters ──┘
```

更准确地说：

- `contracts` 不依赖其他本项目包；
- `runtime` 只依赖 contracts；
- 各 Agent 依赖 contracts/runtime，不互相依赖；
- orchestrator 依赖 contracts，通过 Port/Adapter 调用 Agent；
- orchestrator 不 import Agent 私有模型和内部 state。

当前 `contracts`、`runtime`、`orchestrator` 均有可安装实现；`agents/coding` 在 Phase 5 提供第一个原生专业 Agent。Scientific 与 Experiment 仍处于 legacy adapter 过渡期。
