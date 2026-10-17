# Capabilities：模型可调用的工具

这里放 **Tool、它的输入 schema 和少量工具专用逻辑**。普通 Python 操作与资源实现放在 [Components](../components/README.md)，运行循环与 Tool 协议放在 [Runtime](../runtime/README.md)。

| 目录 | 模型工具（每个 Tool 一个实现文件） |
|---|---|
| [workspace/](src/resagent2_capabilities/workspace/__init__.py) | [list_files](src/resagent2_capabilities/workspace/list_files.py)、[read_file](src/resagent2_capabilities/workspace/read_file.py)、[search_text](src/resagent2_capabilities/workspace/search_text.py)、[create_file](src/resagent2_capabilities/workspace/create_file.py)、[replace_text](src/resagent2_capabilities/workspace/replace_text.py)、[delete_path](src/resagent2_capabilities/workspace/delete_path.py)、[git_diff](src/resagent2_capabilities/workspace/git_diff.py) |
| [artifacts/](src/resagent2_capabilities/artifacts/__init__.py) | [read_artifact](src/resagent2_capabilities/artifacts/read_artifact.py) |
| [execution/](src/resagent2_capabilities/execution/__init__.py) | [run_shell](src/resagent2_capabilities/execution/run_shell.py) |
| `web.py` | `web_search`（注入搜索 provider）、`web_fetch`（可独立注入网页组件） |

每组按模型可调用的 Tool 拆分实现文件，例如 `read_file.py`、`replace_text.py`；`__init__.py` 只显式导出公开的 Tool 与输入模型。顶层包同样只负责导出；不要求与 Components 的文件一一对应。

## 调用与边界

- `resagent2_capabilities` 公开导出 Tool 和对应输入模型；Tool Profile 决定具体 Agent 装配哪些工具，import 包不等于授予能力。
- Tool 按需调用 Components 和 Runtime，没有一一对应或强制调用顺序。简单的、只属于该 Tool 的代码可直接留在工具实现里。
- Tool 从 AgentState 取身份和现有状态，返回 ToolObservation；Loop 应用 memory_updates 和控制信号。实际文件/命令副作用仍在原边界执行。
- [OperationPermissionPolicy](src/resagent2_capabilities/permissions.py) 是共享 Tool 权限适配器，组合 Run 授权与 Components 的工作区和进程边界，返回 allow/ask/deny；批准只用于当前动作，不替代底层组件的执行前检查。
- 组件直接从 `resagent2_components` 导入；不保留旧 Capabilities 服务类的转发入口。业务流程、prompt、完成规则留在 Agent。
- Runtime 自有 finish/ask_user 和 Scientific 控制工具仍由其所属模块提供，不为归类把领域控制搬入这里。

## 工具行为

read_file / read_artifact 只读取 UTF-8 文本，含 NUL 或无效 UTF-8 的文件返回错误，不替换乱码或更新成功读取记录；不自动解析 ZIP、图片等格式。两者先选物理行，再在选中范围内使用零基 `start_char` / `end_char` 字符窗口（不含 end_char），最后应用128000字符返回上限；原换行和物理行号保持不变。分段读取控制返回片段，整份文本仍需检查，不是流式读取；回执的 total_lines 为整份物理行数，selected_chars 为所选行段在字符切片前的字符数，空文件均为0，两字段保留在原工具回执；工件先校验授权与整份 hash，不受工作区大小上限约束。

工作区 read_file / search_text / create_file / replace_text 共用默认10 MiB文本处理上限，写入前校验严格编码、NUL和最终字节数；拒绝时不写入。replace_text 的 old_text 须在**本次实际文件中**唯一匹配，不是每个任务只能编辑一次。工作区现在保留CRLF等原换行，精确替换时使用当前原文；旧 Session 回执不改写。

search_text 是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按字面匹配。skipped_count 和 skipped_files 报告已授权候选中实际跳过的文件及 too_large / not_utf8_text / read_error 原因；详细条目最多50条，超出用 skipped_files_truncated 标明。incomplete 表示发生跳过或结果上限导致提前停止，truncated 表示达到匹配上限后停止，后续匹配未检查、总匹配数未知，不保证还有更多匹配；不报告尚未访问或未授权文件，零匹配不自动代表完整搜索。

delete_path 接受准确的相对 path 和 recursive=False；已授权文件、链接或空目录可直接删除，非空目录须明确 recursive=True 并通过目标快照确认。执行前重验路径集合、类型和版本，变化不能复用旧批准。链接只删除自身；根目录、受保护元数据和越界目标拒绝。删除与部分完成保留实际已删除/剩余路径，不提供原子回滚；不维护旧验证所用的 edit_revision 或读取过期状态。首版只装配到 Coding；删除文件内容仍使用 replace_text(new_text="")。


网页工具是通用材料入口，不是文献工具的别名：

- `web_fetch(url)` 可独立装配，抓取一个无凭据的 http(s) HTML/XHTML/text URL，登记一个 `web_page` 工件。工具回执返回简要状态、工件引用、final URL、title 和 content_type；source/final URL、parser 等完整页面事实存入登记元数据，提取正文存入冻结工件，回执不内联正文或 parser。HTML/XHTML 正文为 Markdown（parser=markdownify/html.parser），text/plain 保留原文（parser=plain）；链接文字及合法http(s)地址保留，相对地址按final URL解析，代码块正文保留换行和缩进，不自动获取链接指向的页面/PDF。未闭合的危险隐藏/标题标签、空提取或转换失败返回 parse_failed，不登记页面工件。Scientific 用 `read_artifact` 分段读取；私有/非公网地址、重定向到非公网地址、PDF、二进制、NUL、无效 UTF-8、超时和超大响应返回结构化失败且不生成页面工件。网页正文和摘要是外部不可信材料，不得当作指令执行。



测试入口：[Tool 行为](../../tests/capabilities/)、[工具 schema/说明指纹](../../tests/e2e/test_tool_surface.py)、[依赖与导出边界](../../tests/capabilities/test_capabilities_boundary.py)。当前公共数据 schema 为 29.0，旧权限和 confirm_commands 字段不保留兼容解释。文献和网页工具登记的工件共同并入 Session 的 artifact_index；工具回执仍按原生协议保存，不把目录当成授权或正文。

`run_shell` 是 Coding/Experiment 共用的唯一命令执行 Tool。环境检查、创建、安装、验证、训练和评测都通过普通脚本完成；旧的安装、环境准备/审计和 Coding 验证 Tool 已删除，没有兼容别名。
执行能力与日志/超时/中断在 Components，Tool 负责模型参数和唯一回执，不新增运行循环。
它需要 execute_commands 和完整可信工作区；默认 approval_mode=manual，每段脚本匹配准确操作的单次人工批准。auto_review 先经过相同固定授权检查，再由 Runtime 在派发前审阅一次；只有明确 allow 才执行，ask 或审核故障交人工。工具预检不触发审核，人工恢复不重复审核；无需系统绑定环境、认证或自动探针。
每次执行采用独立非登录 Bash（pipefail，无隐式 errexit），不维护终端或后台任务；activate/export/cd 不跨调用保留，模型应在同一脚本里激活或明确指定目标解释器。
环境由模型依据上级要求自行管理，多任务协作通过任务输入和公开结果传递环境信息，不固定共享路径。系统不自动接管、清理或重建用户环境，环境删除需要明确命令和正常批准。
脚本原文保持不变；批准绑定真实 cwd、Shell flags 和过滤后启动环境指纹，实际启动前变化则返回 not_executed。结果及 execution_record 只说明实际执行。Coding 保留实际 changed_paths 和执行前后代码身份，失败也保留已发生变化；命令完成后 Git 诊断中断时，唯一回执同时保留 completed_command 和诊断停止事实，执行工件记录两者。完成检查不再依赖专用验证或环境认证状态。
当前后端是受信宿主执行，人工批准和模型审核都不是 OS 沙箱。固定代码不通过命令关键词白名单猜脚本含义；无状态 reviewer 完整核对原始用户要求、当前任务、历史拒绝说明和准确操作，脚本效果未知或授权/风险不明则交人工。审核模型没有执行或读取文件的工具，审核请求共享 Run 总预算与取消链路。审核来源、理由和结果留在第三部分审计/原调用回执，不伪装成人工回答，也不进入第二部分需求或第四部分工件。Shell 输出不冒充工件正文访问记录，阅读记录不作为语义完成门槛。

当前接入规则：文献和网页搜索只返回临时线索；fetch_literature(source)/web_fetch 显式保存材料，read_artifact 按需读取。工具回执只进原生历史，实际 Ref 增量由共享 Loop 摄入完整目录。工作结果按 WorkRequest → Task → 原报告和实际产物展开，不生成总体报告。业务 ask_user 统一问直接父，根 Scientific 才问真实用户；子向上答复进入子第二部分、父收到下级问题进入父第三部分，不产生工件。操作批准续执行原调用，不进入需求/目录。工具通过共用取消上下文响应显式打断：未完成受管操作停止，准确回执保存；完成文件/结果保留，无自动回滚。规则以 [CONTEXT](../../docs/current/CONTEXT.md) 和 [CONTRACTS](../../docs/current/CONTRACTS.md) 为准。
