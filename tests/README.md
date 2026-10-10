# 测试

测试按系统边界组织，不按历史 Phase 阅读。从仓库根执行 `python -m pytest tests apps/cli/tests -q`；无真实模型闭环是 `python -m e2e.mock_e2e`。隔离目录示例见 [开发与验证](../docs/guides/DEVELOPMENT.md#local-checks)。

| 目录 | 覆盖 |
|---|---|
| contracts/ | schema、ID、字段组合与状态不变量 |
| runtime/ | Loop、Tool、权限、上下文、LLM、持久化和恢复 |
| capabilities/ | 模型 Tool 参数、观测、副作用与集成行为 |
| components/ | 普通操作、文献后端、资源、工件与共享投影（部分混合工具测试仍在 capabilities/） |
| orchestrator/ | 编译、图、Task/Attempt、问答、路由、接收与最终 gate |
| coding/、experiment/、scientific/ | 领域 Agent 行为 |
| e2e/ | 确定性端到端检查；真实模型入口另在仓库 e2e/ |
| ../apps/cli/tests/ | 产品组合根、命令和交互壳 |

普通单测不依赖真实 LLM、网络、GPU 或服务器。安全、状态和完成条件要有确定性负例，不能只检查 prompt。真实验收另记提交、配置、原始响应、观测与工件；一次通过不证明永远稳定。

## 真实模型场景

入口为 `python -m e2e.real_e2e <stage>`，支持 `full` / `code-experiment`、`direct`、`repair`、`ask-start`、`ask-resume`、`literature`，以及定向 `code` / `experiment`。它们均调用 CLI 的 `build_application(data_root=workdir / "data")`，共用 [CLI 部署配置](../apps/cli/README.md#6-模型与上下文预算)、模型客户端、工具与资源注入，不再在 E2E 内维护另一套默认值。完整研究场景通过 Controller 执行；定向场景从应用的 binding 取得 Coding / Experiment Agent 并调用 `invoke`。E2E 只负责场景工作区、输入、预算和断言，定向场景通过不代表完整研究 Run 已完成。

使用 `REAL_E2E_WORKDIR` 指定独立验收目录；未设置时创建系统临时目录。新目录布局为 `data/state`、`data/sessions/{agent}` 和 `data/artifacts`；定向 `code` / `experiment` 的交付物仍在 `out`。`ask-start` 与 `ask-resume` 必须使用同一个 `REAL_E2E_WORKDIR` 及一致的部署配置，否则不能接续原现场。旧的 `state` / `sessions` / `artifacts` 布局不自动迁移或兼容；新验收使用新目录，保留旧现场。

这些场景会调用真实模型，可能执行命令或访问外部来源，按既有授权和验收规程运行。`e2e.mock_e2e` 继续使用确定性替身，不需要真实模型配置。

边界见 [接口与契约](../docs/current/CONTRACTS.md)，历史验收见 [记录索引](../docs/history/README.md)。

[设计原则](../docs/current/DESIGN_PRINCIPLES.md)的依赖方向由各包 AST 边界测试保护，三个 Agent 均有独立检查；接口形状之外的预算、授权、恢复与证据仍须行为测试。工具与组件边界由各包依赖测试约束；[test_tool_surface.py](e2e/test_tool_surface.py) 固定已审查的模型可见 schema、docstring 和 guidance 指纹，不应为使测试通过自动更新基线。变更说明须先核对实际差异；操作指引须核对原生工具 schema 与 guidance，提示词文字检查不代替行为验证。
