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

当前只建立文档骨架，尚无运行时代码。
