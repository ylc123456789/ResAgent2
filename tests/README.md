# 测试

测试按系统边界组织，不按历史 Phase 阅读。从仓库根执行 `python -m pytest tests apps/cli/tests -q`；无真实模型闭环是 `python -m e2e.mock_e2e`。隔离目录示例见 [开发与验证](../docs/guides/DEVELOPMENT.md#local-checks)。

| 目录 | 覆盖 |
|---|---|
| contracts/ | schema、ID、字段组合与状态不变量 |
| runtime/ | Loop、Tool、权限、上下文、LLM、持久化和恢复 |
| capabilities/ | 文件、Git、进程、环境、数据集、工件和投影 |
| orchestrator/ | 编译、图、Task/Attempt、问答、路由、接收与最终 gate |
| coding/、experiment/、scientific/ | 领域 Agent 行为 |
| e2e/ | 确定性端到端检查；真实模型入口另在仓库 e2e/ |
| ../apps/cli/tests/ | 产品组合根、命令和交互壳 |

普通单测不依赖真实 LLM、网络、GPU 或服务器。安全、状态和完成条件要有确定性负例，不能只检查 prompt。真实验收另记提交、配置、原始响应、观测与工件；一次通过不证明永远稳定。

边界见 [接口与契约](../docs/current/CONTRACTS.md)，历史验收见 [记录索引](../docs/history/README.md)。
