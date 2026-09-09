# Packages

本目录是可安装逻辑包，源码在各包 src 下。三个原生 Agent 均已实现，不依赖旧项目 adapter。

| 包 | 职责 |
|---|---|
| contracts | 跨模块类型与纯组合规则 |
| runtime | AgentLoop、上下文、LLM、Tool、Session |
| capabilities | 文件、Git、进程、环境、数据集与证据等共享能力 |
| agents/scientific | 科学判断、工作需求与最终意见 |
| agents/coding | 代码理解、修改与验证 |
| agents/experiment | 实验执行与证据交付 |
| orchestrator | Controller、Compiler、Scheduler、Run 与最终验收 |

contracts 不依赖其他项目包；runtime 依赖 contracts；capabilities 依赖 runtime/contracts；Agent 按需使用它们但不互相依赖。orchestrator 通过 Port 调用 Agent，由 CLI/E2E 组合根注入具体实现。

完整说明见 [架构](../docs/current/ARCHITECTURE.md#modules)、[接口与契约](../docs/current/CONTRACTS.md)；开发看 [入门与实践](../docs/guides/README.md)。包内 README 只作定位入口，不维护第二套规范。
