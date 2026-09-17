# Capabilities：模型可调用的工具

这里放 **Tool、它的输入 schema 和少量工具专用逻辑**。普通 Python 操作与资源实现放在 [Components](../components/README.md)，运行循环与 Tool 协议放在 [Runtime](../runtime/README.md)。

| 目录 | 模型工具 |
|---|---|
| [workspace/](src/resagent2_capabilities/workspace/__init__.py) | list_files、read_file、search_text、create_file、replace_text、git_diff |
| [artifacts/](src/resagent2_capabilities/artifacts/__init__.py) | read_artifact |
| [environment/](src/resagent2_capabilities/environment/__init__.py) | prepare_environment、run_setup、audit_env |
| [literature/](src/resagent2_capabilities/literature/__init__.py) | literature_search |

每组暂用一个 `__init__.py` 承载实现，不额外加只转发一次的 tools.py。确实长大后再拆，不要求每个 Tool 一个文件。

## 调用与边界

- `resagent2_capabilities` 公开导出 Tool 和对应输入模型；Tool Profile 决定具体 Agent 装配哪些工具，import 包不等于授予能力。
- Tool 按需调用 Components 和 Runtime，没有一一对应或强制调用顺序。简单的、只属于该 Tool 的代码可直接留在工具实现里。
- Tool 从 AgentState 取身份和现有状态，返回 ToolObservation；Loop 应用 memory_updates 和控制信号。实际文件/命令副作用仍在原边界执行。
- 组件直接从 `resagent2_components` 导入；不保留旧 Capabilities 服务类的转发入口。业务流程、prompt、完成规则留在 Agent。
- Runtime 自有 finish/ask_user、Scientific 控制工具、Experiment 的 run_command、[Coding 的 run_verification](../agents/coding/src/resagent2_coding/verification.py) 仍由其所属模块提供，不为归类把领域控制搬入这里。

## 不因目录整理改变的行为

read_file / read_artifact 都支持行范围；工件先校验授权与整份 hash 再切片。search_text 是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按字面匹配。replace_text 的 old_text 须在**本次实际文件中**唯一匹配，不是每个任务只能编辑一次。

文献 Tool 接收注入的后端与 ArtifactRegistrationPort，将真实记录交给 Registry 冻结，不自行生成 ArtifactId/hash。来源选择与 HTTP 规则在 [文献组件](../components/README.md#literature)，环境和数据集实现也在 Components。

上下文构造已迁到 Components；选择与预算仍由 Runtime 统一管理，详见 [CONTEXT](../../docs/current/CONTEXT.md#budgets)，这里不再维护另一份额度表。

测试入口：[Tool 行为](../../tests/capabilities/)、[工具 schema/说明指纹](../../tests/e2e/test_tool_surface.py)、[依赖与导出边界](../../tests/capabilities/test_capabilities_boundary.py)。本次移动不改变模型可见名称、参数、说明或公共数据 schema。
