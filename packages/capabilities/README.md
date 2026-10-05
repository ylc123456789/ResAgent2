# Capabilities：模型可调用的工具

这里放 **Tool、它的输入 schema 和少量工具专用逻辑**。普通 Python 操作与资源实现放在 [Components](../components/README.md)，运行循环与 Tool 协议放在 [Runtime](../runtime/README.md)。

| 目录 | 模型工具（每个 Tool 一个实现文件） |
|---|---|
| [workspace/](src/resagent2_capabilities/workspace/__init__.py) | [list_files](src/resagent2_capabilities/workspace/list_files.py)、[read_file](src/resagent2_capabilities/workspace/read_file.py)、[search_text](src/resagent2_capabilities/workspace/search_text.py)、[create_file](src/resagent2_capabilities/workspace/create_file.py)、[replace_text](src/resagent2_capabilities/workspace/replace_text.py)、[delete_path](src/resagent2_capabilities/workspace/delete_path.py)、[git_diff](src/resagent2_capabilities/workspace/git_diff.py) |
| [artifacts/](src/resagent2_capabilities/artifacts/__init__.py) | [read_artifact](src/resagent2_capabilities/artifacts/read_artifact.py) |
| [execution/](src/resagent2_capabilities/execution/__init__.py) | [run_shell](src/resagent2_capabilities/execution/run_shell.py) |
| [environment/](src/resagent2_capabilities/environment/__init__.py) | [prepare_environment](src/resagent2_capabilities/environment/prepare_environment.py)、[run_setup](src/resagent2_capabilities/environment/run_setup.py)、[audit_env](src/resagent2_capabilities/environment/audit_env.py)、[guidance.py](src/resagent2_capabilities/environment/guidance.py) |
| [literature/](src/resagent2_capabilities/literature/__init__.py) | [literature_search](src/resagent2_capabilities/literature/literature_search.py)、fetch_literature_fulltext |

每组按模型可调用的 Tool 拆分实现文件，例如 `read_file.py`、`replace_text.py`；`__init__.py` 只显式导出公开的 Tool 与输入模型。顶层包同样只负责导出；不要求与 Components 的文件一一对应。

## 调用与边界

- `resagent2_capabilities` 公开导出 Tool 和对应输入模型；Tool Profile 决定具体 Agent 装配哪些工具，import 包不等于授予能力。
- Tool 按需调用 Components 和 Runtime，没有一一对应或强制调用顺序。简单的、只属于该 Tool 的代码可直接留在工具实现里。
- Tool 从 AgentState 取身份和现有状态，返回 ToolObservation；Loop 应用 memory_updates 和控制信号。实际文件/命令副作用仍在原边界执行。
- [OperationPermissionPolicy](src/resagent2_capabilities/permissions.py) 是共享 Tool 权限适配器，组合 Run 授权与 Components 的工作区、进程、环境边界，返回 allow/ask/deny；批准只用于当前动作，不替代底层组件的执行前检查。
- `environment/guidance.py` 提供 Coding/Experiment 共用的环境处理行为指引；硬件、驱动、依赖和认证事实仍由 Components 投影，角色职责仍留在各 Agent prompt。
- 组件直接从 `resagent2_components` 导入；不保留旧 Capabilities 服务类的转发入口。业务流程、prompt、完成规则留在 Agent。
- Runtime 自有 finish/ask_user、Scientific 控制工具、[Coding 的 run_verification](../agents/coding/src/resagent2_coding/verification.py) 仍由其所属模块提供，不为归类把领域控制搬入这里。

## 工具行为

read_file / read_artifact 只读取 UTF-8 文本，含 NUL 或无效 UTF-8 的文件返回错误，不替换乱码或更新成功读取记录；不自动解析 ZIP、图片等格式。两者先选物理行，再在选中范围内使用零基 `start_char` / `end_char` 字符窗口（不含 end_char），最后应用128000字符返回上限；原换行和物理行号保持不变。字符分页控制返回片段，整份文本仍需检查，不是流式读取；工件先校验授权与整份 hash，不受工作区大小上限约束。

工作区 read_file / search_text / create_file / replace_text 共用默认10 MiB文本处理上限，写入前校验严格编码、NUL和最终字节数；拒绝时不写入或增加编辑版本。replace_text 的 old_text 须在**本次实际文件中**唯一匹配，不是每个任务只能编辑一次。工作区现在保留CRLF等原换行，精确替换时使用当前原文；旧 Session 回执不改写。

search_text 是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按字面匹配。skipped_count 和 skipped_files 报告已授权候选中实际跳过的文件及 too_large / not_utf8_text / read_error 原因；详细条目最多50条，超出用 skipped_files_truncated 标明。incomplete 表示发生跳过或结果上限导致提前停止，truncated 仍表示触及匹配上限；不报告尚未访问或未授权文件，零匹配不自动代表完整搜索。

delete_path 接受准确的相对 path 和 recursive=False；已授权文件、链接或空目录可直接删除，非空目录须明确 recursive=True 并通过目标快照确认。执行前重验路径集合、类型和版本，变化不能复用旧批准。链接只删除自身；根目录、受保护元数据和越界目标拒绝。删除与部分完成都沿用 edit_revision、读取过期标记和验证失效机制，不提供原子回滚。首版只装配到 Coding；删除文件内容仍使用 replace_text(new_text="")。

文献 Tool 接收注入的来源组件与 ArtifactRegistrationPort，将真实材料交给 Registry 冻结，不自行生成 ArtifactId/hash。literature_search 为每篇结果登记 literature_paper，并保存查询与论文引用的搜索回执；fetch_literature_fulltext(paper_artifact_id) 按需登记原始 PDF 和解析文本，同 Run 复用已冻结材料，包括授权输入中的外部导入论文和 PDF；原件已存在时无需联网。工具不接受任意路径或 URL 代替论文工件 ID，也不自行增加 LLM 摘要。来源选择与 HTTP 规则在 [文献组件](../components/README.md#literature)，环境和数据集实现也在 Components。

通用材料读取与作用域校验在 Components；Scientific 的 WorkFeedback 事实框和报告呈现在 `agents/scientific/context.py`。选择与预算仍由 Runtime 统一管理，详见 [CONTEXT](../../docs/current/CONTEXT.md#budgets)，这里不再维护另一份额度表。

测试入口：[Tool 行为](../../tests/capabilities/)、[工具 schema/说明指纹](../../tests/e2e/test_tool_surface.py)、[依赖与导出边界](../../tests/capabilities/test_capabilities_boundary.py)。当前公共数据 schema 为 23.0，旧权限和确认字段不保留兼容解释。

`run_shell` 是 Coding/Experiment 共用的 Linux Bash 工具，取代 Experiment 的 `run_command`。
执行能力与日志/超时在 Components，Tool 负责模型参数和回执；不新增运行循环。
它需要 execute_commands、完整可信工作区和绑定环境，每段脚本无条件单次审批，
使用已有环境不要求 prepare_environment。环境创建和安装仍走原环境工具。
每次执行采用独立非登录 Bash（pipefail，无隐式 errexit），不维护终端或后台任务；
脚本原文保持不变，结果只说明实际执行。执行前使旧环境认证及验证失效，
Coding 的实际变化进入 edit_revision 和读取过期提示，失败也保留变化。
审批不是沙箱，不通过命令分类猜脚本含义，Shell 输出不冒充工件正文访问记录；阅读记录只用于追溯，不作为语义完成门槛。
