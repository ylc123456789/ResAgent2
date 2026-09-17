# Components

普通 Python 调用方可复用的操作和内容呈现，不是模型工具目录，也不启动 AgentLoop。

- 工作区、Git、仓库准备与变化观察；
- 命令执行、日志和超时；
- 环境、硬件、数据集与资源路径；
- 工件授权读取、说明报告与共享文本处理；
- 从原事件/实际绑定生成共享上下文，不保存第二份业务状态。

Tool、Agent 的准备/完成检查，以及 CLI/E2E 装配都可以直接使用这里的实现。组件不与 Tool 一一对应，也不要求每个 Tool 都有对应组件。

基础操作只使用实际需要的依赖；共享上下文投影可使用 runtime 的类型和历史选择函数。不得依赖 capabilities、具体 Agent 或 orchestrator，不提供 Tool 入口，不自行调用模型。

稳定导入入口是 `resagent2_components`。包内小函数跟随其实现，不为每个函数建立文件或公共抽象。

上下文权重、时序和预算以 [CONTEXT](../../docs/current/CONTEXT.md) 为准；接口语义见 [CONTRACTS](../../docs/current/CONTRACTS.md)。这些操作的提取不改变 schema、权限、证据或恢复规则。
