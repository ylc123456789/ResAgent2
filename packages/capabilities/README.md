# Capabilities：模型可调用的工具

这里放 **Tool、它的输入 schema 和少量工具专用逻辑**。普通 Python 操作与资源实现放在 [Components](../components/README.md)，运行循环与 Tool 协议放在 [Runtime](../runtime/README.md)。

| 目录 | 模型工具（每个 Tool 一个实现文件） |
|---|---|
| [workspace/](src/resagent2_capabilities/workspace/__init__.py) | [list_files](src/resagent2_capabilities/workspace/list_files.py)、[read_file](src/resagent2_capabilities/workspace/read_file.py)、[search_text](src/resagent2_capabilities/workspace/search_text.py)、[create_file](src/resagent2_capabilities/workspace/create_file.py)、[replace_text](src/resagent2_capabilities/workspace/replace_text.py)、[delete_path](src/resagent2_capabilities/workspace/delete_path.py)、[git_diff](src/resagent2_capabilities/workspace/git_diff.py) |
| [artifacts/](src/resagent2_capabilities/artifacts/__init__.py) | [read_artifact](src/resagent2_capabilities/artifacts/read_artifact.py) |
| [environment/](src/resagent2_capabilities/environment/__init__.py) | [prepare_environment](src/resagent2_capabilities/environment/prepare_environment.py)、[run_setup](src/resagent2_capabilities/environment/run_setup.py)、[audit_env](src/resagent2_capabilities/environment/audit_env.py) |
| [literature/](src/resagent2_capabilities/literature/__init__.py) | [literature_search](src/resagent2_capabilities/literature/literature_search.py) |

每组按模型可调用的 Tool 拆分实现文件，例如 `read_file.py`、`replace_text.py`；`__init__.py` 只显式导出公开的 Tool 与输入模型。顶层包同样只负责导出；不要求与 Components 的文件一一对应。

## 调用与边界

- `resagent2_capabilities` 公开导出 Tool 和对应输入模型；Tool Profile 决定具体 Agent 装配哪些工具，import 包不等于授予能力。
- Tool 按需调用 Components 和 Runtime，没有一一对应或强制调用顺序。简单的、只属于该 Tool 的代码可直接留在工具实现里。
- Tool 从 AgentState 取身份和现有状态，返回 ToolObservation；Loop 应用 memory_updates 和控制信号。实际文件/命令副作用仍在原边界执行。
- Run 授权与固定操作检查共用 Components 的 OperationPermissionPolicy，返回 allow/ask/deny；批准只用于当前动作，不替代文件/进程组件的执行前检查。
- 组件直接从 `resagent2_components` 导入；不保留旧 Capabilities 服务类的转发入口。业务流程、prompt、完成规则留在 Agent。
- Runtime 自有 finish/ask_user、Scientific 控制工具、Experiment 的 run_command、[Coding 的 run_verification](../agents/coding/src/resagent2_coding/verification.py) 仍由其所属模块提供，不为归类把领域控制搬入这里。

## 工具行为

read_file / read_artifact 都支持行范围；工件先校验授权与整份 hash 再切片。search_text 是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按字面匹配。replace_text 的 old_text 须在**本次实际文件中**唯一匹配，不是每个任务只能编辑一次。

delete_path 接受准确的相对 path 和 recursive=False；已授权文件、链接或空目录可直接删除，非空目录须明确 recursive=True 并通过目标快照确认。执行前重验路径集合、类型和版本，变化不能复用旧批准。链接只删除自身；根目录、受保护元数据和越界目标拒绝。删除与部分完成都沿用 edit_revision、读取过期标记和验证失效机制，不提供原子回滚。首版只装配到 Coding；删除文件内容仍使用 replace_text(new_text="")。

文献 Tool 接收注入的后端与 ArtifactRegistrationPort，将真实记录交给 Registry 冻结，不自行生成 ArtifactId/hash。来源选择与 HTTP 规则在 [文献组件](../components/README.md#literature)，环境和数据集实现也在 Components。

上下文构造已迁到 Components；选择与预算仍由 Runtime 统一管理，详见 [CONTEXT](../../docs/current/CONTEXT.md#budgets)，这里不再维护另一份额度表。

测试入口：[Tool 行为](../../tests/capabilities/)、[工具 schema/说明指纹](../../tests/e2e/test_tool_surface.py)、[依赖与导出边界](../../tests/capabilities/test_capabilities_boundary.py)。当前公共数据 schema 为 14.0，旧权限和确认字段不保留兼容解释。
