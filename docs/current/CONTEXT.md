# 模型实际会看到什么：上下文说明

这份文档回答：**每个模块把哪些信息交给模型，信息从哪里来，什么时候刷新，放不下时怎样处理。** 它描述当前实现，不把候选改进写成已有能力。

- 系统分工看 [ARCHITECTURE](ARCHITECTURE.md)；公开方法和字段看 [CONTRACTS](CONTRACTS.md)。
- 本文解释字段如何进入模型输入，不重复定义接口类型。
- 原始问题与方案演变见 [上下文审查](../history/reviews/CONTEXT_REVIEW_2026-09-13.md)；本页已同步随后获批的语义、预算及文献呈现修正。真实验收要求见 [验收单](../history/reviews/CONTEXT_128K_ACCEPTANCE.md)。

建议第一次先读 [基本区别](#basics)、自己关心的 [模块](#modules)，再看 [文献流程](#literature) 和 [预算](#budgets)。后面的源码、测试链接用于查证，不要求按顺序读代码。

<a id="basics"></a>

## 1. 先分清三件事

| 东西 | 保存什么 | 是否每轮完整交给模型 |
|---|---|---|
| 模块请求 | 本次目标、输入、约束、授权工件、已配对回答等 | 否；由各模块选取、组织 |
| Session 与冻结工件 | 工具事件、内部记忆、已获取的文件内容和结果 | 否；保存不等于一直可见，工具结果自身也可能已截断 |
| 本轮上下文 | 本次调用实际选中的规则、状态、材料片段和反馈 | 是；原生客户端再追加动作输出格式说明 |

例如：Experiment 已生成风险报告，Scientific 收到报告编号，Scientific 实际打开报告，Scientific 在结论中考虑风险，是四件不同的事。

`ResearchRequest.context` 只是用户提供的一段研究背景，不是本文所说的完整模型上下文。完整上下文还包含工具说明、控制状态、答案和读取结果。

当前原生 OpenAI-compatible 客户端每次把重新组装的文本作为一条 `user` 消息发送；没有自动附加完整聊天历史。文中的 `system` 是文本段名，不是该客户端另发的一条 API `system` 消息。替换客户端可以有不同封装，不能把原生客户端的方式当成所有 Provider 的强制接口。

模型上轮的 reasoning 只在配置允许时写入 full trace，不自动进入下一轮上下文，也不是持久的研究笔记。Session 的 `memory` 是代码维护的状态字典，不等于模型的隐含记忆。

**对应接口与源码**：[研究输入](CONTRACTS.md#research-request)、[Session](CONTRACTS.md#attempt-session)、[LLM 客户端](../../packages/runtime/src/resagent2_runtime/llm.py)、[AgentState / ContextSection](../../packages/runtime/src/resagent2_runtime/models.py)。

<a id="pipeline"></a>

## 2. 一轮上下文怎样组成

三个原生 Agent 使用同一条主线：

1. **调用方交付请求。** Controller 或 Scheduler 决定这一回合/任务可见的输入、工件和回答作用域。
2. **先算额度，再构造领域段。** Loop 先取模块上限与模型可用容量的较小值，将这一有效值传给 `ContextBuilder(request, state, max_context_tokens)`。Agent 的 builder 从请求、Session 和实际绑定中选取信息，不输出全部 Run/memory。
3. **共享能力补充材料。** `workspace_context` 按有效额度的固定比例选择读取材料、命令诊断和目录观察，同时提供当前环境绑定；数据集、回答也使用已有共享投影。
4. **AgentLoop 补运行段。** 加入实际可用工具的参数契约、待处理反馈和最近工具预览。
5. **ContextComposer 选择并计量。** 先保留必需段，再尝试装入可选段，生成最终文本与 included/omitted 清单。
6. **客户端发请求。** 原生客户端追加动作 schema 说明，调用模型；动作通过校验和工具执行后，形成下一轮可使用的新事件。

| 所在位置 | 负责什么 | 不负责什么 |
|---|---|---|
| Agent 的 context builder / Scientific interpreter | 领域信息的选择、组织和用途说明 | 不替代上层调度，不变更执行事实 |
| capabilities 的共享投影 | 环境、数据集、读取材料等可复用内容 | 不决定科学结论，不保管第二份 Run |
| runtime 的 Loop / Composer | 运行反馈、工具契约、历史选择与统一预算 | 不理解哪篇论文更重要，不自动总结研究发现 |
| 外层组合根 | 注入模型、容量及模块配置 | 不负责每步阅读内容的选择 |

**源码**：[Scientific context](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[interpreter](../../packages/agents/scientific/src/resagent2_scientific/interpreter.py)、[workspace_context](../../packages/capabilities/src/resagent2_capabilities/workspace_context.py)、[AgentLoop](../../packages/runtime/src/resagent2_runtime/loop.py)、[ContextComposer](../../packages/runtime/src/resagent2_runtime/context.py)。

### 2.1 required、priority 与顺序不是一回事

- `required=True`：该段必须放入；必需段累计超限时明确失败，不静默丢弃。
- `priority`：决定**可选段**的尝试顺序，数值大的先尝试；装不下的可选段整段省略。
- 必需段按传入顺序排列，不按 priority 排序；所有必需段之后才是选中的可选段。
- Composer 不自动压缩正文。进入 Composer 前，某个段内部可能已经按自己的局部额度裁剪。

所以“priority=1000”不自动意味着放在全文最前面。当前 Loop 主动把反馈插到前面、Coding 主动把 control_state 插到领域段前面；这是插入顺序的效果。工具契约虽然 priority=990，但作为必需段，仍按构造顺序出现。

### 2.2 三个 Agent 都有的运行段

| 段名 | 来源和用途 | 保留与刷新方式 |
|---|---|---|
| `system` | 当前 Agent/profile 的职责与用法提示 | 始终必需，Composer 放在首段 |
| `tool_contracts` | 本次实际注入的 Tool 名、input_model JSON Schema，以及可选 model_guidance | 始终必需；不靠模型猜参数，也不是另一次 LLM 调用 |
| `runtime_feedback` | 尚待处理的动作/参数/完成检查等拒绝信息 | 有反馈才出现，必需；反馈解除规则由 Loop 管理 |
| `recent_observations` | 最近最多 6 条 observation 的事件号、工具名、summary、成功/失败与 value 预览 | 有历史才出现，可选，priority=950；每次从 events 重建 |

`recent_observations` 的 value 预览采用约 400 字符的头尾裁剪；summary 没有同样的局部字符限制，整个段仍受总预算控制。它不是完整执行日志。`runtime_feedback` 的 value 明细另有约 800 字符的预览限制。

Tool 返回 `ok=False` 的普通观察与 Loop 生成的持久拒绝反馈不是同一机制；不能假定每个失败工具的完整 stderr 都会自动进入 required 反馈段。`last_observation` 也没有被无条件作为一个完整正文段注入。

Coding/Experiment 的命令结果另由共享 `command_results` 段保留，不依赖这个400字符预览，见 [命令诊断](#commands)。普通历史预览仍是定位线索，不用它承载完整代码、论文或错误报告。

**源码与测试**：[工具契约生成](../../packages/runtime/src/resagent2_runtime/tools.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[预览测试](../../tests/runtime/test_observation_previews.py)、[工具契约测试](../../tests/runtime/test_tool_contracts.py)、[上下文测试](../../tests/runtime/test_context.py)。错误恢复协议另见 [CONTRACTS](CONTRACTS.md#runtime-context)，本文不重新设计 JSON 恢复。

<a id="modules"></a>

## 3. 各模块实际看到什么

表中的“必需”指段出现之后不能被 Composer 省略，不代表模型一定正确使用其中的信息，也不代表原始材料全文都被保留。

<a id="scientific"></a>

### 3.1 Scientific：研究判断和证据

输入边界是 [ScientificTurnRequest](CONTRACTS.md#scientific-turn)。Controller 交付科学回合，Scientific Session 在同一个 Run 内复用；新回合请求与此前工具历史共同构成下一次输入。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `evidence_control_state` | 本 Session 已观察工件编号、当前请求中未观察的授权编号、尚待补读/撤回的引用 | 必需；每步根据请求和 memory 重算 |
| `research` | research 的 goal、hypothesis、context、constraints、required_evidence_kinds | 必需；来自当前回合请求 |
| `dataset_catalog` | 同次数据集解析得到的可用/不可用 ID 与共享用法说明 | 必需；不是数据正文 |
| `authorized_artifacts` | 当前请求的工件 id、kind、summary | 必需；是入口清单，不是工件正文 |
| `work_brief` | interpreter 将上轮需求、当前交付结果、仍未解决的执行问题整理成简报 | 必需；不转存一份新状态 |
| `answers` | 当前回合交付的 RecordedAnswer，含原题 question_text 和回答 values | 必需；即便为空列表仍有此段 |
| `workspace_reads` | 本 Session 的工件读取片段和已读来源提示 | 有读取片段才出现，必需；正文受局部额度限制 |

Scientific 不注入 execution environment，不提供代码编辑/实验执行工具。`literature_search` 只有在组合根同时提供 backend 和 registration port 时才加入工具集合。

**work_brief 的用途分工：**

- `purpose` 是上一份工作需求的 objective / expected_evidence / constraints。
- 完成结果的 `narrative` 是模块解释；`caveats` 是交付警告，只投影 code/message。
- `evidence` 给出可授权读取的 artifact_id、kind 和用途，不展开所有结果 payload 或数字 metrics。
- 失败/阻塞项保留原因；仅白名单投影 stderr_tail 的末尾最多 1000 字符为 `diagnostic_excerpt`，它用于执行诊断，不是科学证据。
- `module_report` 用于代码答案、解释和残余风险。prompt 要求在依赖模块结果作结论前，阅读该结果相关的说明/局限报告（若存在）；“不是测量”不等于“可以忽略风险”。不要求遍读所有历史报告，也未添加确定性阅读门禁。

当前 observed 集合来自成功 `read_artifact` **或** `literature_search` 的记录；它表示访问历史，不证明读过全文或当前仍能看到全部正文。仅列在授权清单里的编号不自动进入 observed。读过也不代表观点必然正确。

**源码与测试**：[context](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[interpreter](../../packages/agents/scientific/src/resagent2_scientific/interpreter.py)、[装配与回合](../../packages/agents/scientific/src/resagent2_scientific/agent.py)、[观察与完成检查](../../packages/agents/scientific/src/resagent2_scientific/completion.py)、[interpreter 测试](../../tests/scientific/test_interpreter.py)、[证据控制测试](../../tests/scientific/test_evidence_control.py)。

<a id="coding"></a>

### 3.2 Coding：理解代码，或修改后验证

输入边界是 [ModuleTaskRequest](CONTRACTS.md#module-request)。两个 profile 共用 context builder；区别在工具、提示和控制状态。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `task` | goal、typed inputs、task constraints、workspace_mode，以及 input_artifacts 的 id/kind/summary | 必需；来自本 Task/Attempt 的请求 |
| `dataset_catalog` | 当前 invoke 解析的数据集视图与共享说明 | 必需 |
| `answers` | 调用方限定在本 Task 的已配对原题与回答 | 非空才出现，必需；共享 user_answers_section，不借别的 Task 的回答 |
| `control_state` | 修改、验证及环境相关的下一步指引 | 仅 code_modify，必需；每步调用 derive_control_state |
| `environment` | 实际 EnvironmentBinding 的 prepared/certified、Python 要求及已有环境身份 | 仅 code_modify，必需；与工具使用同一绑定 |
| `workspace_reads` | 文件片段与工件片段，分别保留 | 有片段才出现，必需 |
| `command_results` | 每个执行工具最近一次有记录的命令结果；优先保留失败诊断 | 有命令结果才出现，必需；共享投影，不新增缓存 |
| `directory` | 最近一次 list_files 观察的有界路径清单 | 有结果才出现，可选，priority=62；不是每轮重新扫磁盘 |

只读 code_understand 没有环境绑定或修改控制状态；code_modify 的任务基线和验证记录由代码保存，模型不自行声明“代码已改、验证已过”。

**控制状态的含义：**`control_state.edited_since_verification` 比较 edit_revision 与 verification_revision，表示“记录的编辑版本是否晚于验证版本”。false 不表示本 Attempt 没有修改，也不代表实时 Git diff 为空。`required_next_action` 与完成门禁共用验证有效性规则：较新的编辑或环境代次会要求重验，不会被旧验证失败记录误导成继续修旧问题。

`verification_required`、`verification_issue`、`required_next_action` 是确定性派生的指引；真正的完成门槛仍在 completion check。读文件、search_text、git_diff 等工具的全结果不会因为重要就自动全部常驻：文件正文另进工作集，其他结果主要依靠最近预览和各自记录。

**源码与测试**：[context](../../packages/agents/coding/src/resagent2_coding/context.py)、[两种 profile 装配](../../packages/agents/coding/src/resagent2_coding/agent.py)、[控制状态与完成检查](../../packages/agents/coding/src/resagent2_coding/completion.py)、[控制状态测试](../../tests/coding/test_control_state.py)、[验证有效性测试](../../tests/coding/test_verification_validity.py)。

<a id="experiment"></a>

### 3.3 Experiment：执行命令与交付真实结果

同样使用 [ModuleTaskRequest](CONTRACTS.md#module-request)，但不含 Coding 的修改控制状态和编辑工具。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `task` | goal、typed inputs、task constraints 和输入工件的 id/kind/summary | 必需 |
| `datasets` | 与另两个 Agent 同源的 dataset_context，只是段名不同 | 必需 |
| `answers` | 本 Task 的 RecordedAnswer | 非空才出现，必需；与 Coding 共用函数 |
| `environment` | 实际环境绑定与认证状态 | 必需；每次构造读取同一绑定 |
| `workspace_reads` | 文件与工件两组正文 | 有片段才出现，必需 |
| `command_results` | run_command/run_setup 最近的命令结果与有界失败诊断 | 有结果才出现，必需；与 Coding 共用机制 |
| `directory` | 最近一次有界目录观察 | 可选，priority=62 |
| `hardware` | Session memory 中的硬件审计文本 | 可选，priority=80 |
| `repo` | Session memory 中的 repo_url / commit | 可选，priority=70；不是磁盘最新变更清单 |

hardware/repo 在首次 Session 的 initial_memory 中初始化，恢复时从持久 Session 取得，不承诺每一步重新探测。环境绑定则是工具和上下文共用的实际对象；不能用一条已轮出预览的旧 audit 日志来代替它。

命令 stdout/stderr 和 evidence_files 的持久记录，不会自动全部成为模型输入。最终 metrics 由 completion check 从本次实际证据文件推导，不由模型在上下文里自报数字就算完成。

**源码与测试**：[context](../../packages/agents/experiment/src/resagent2_experiment/context.py)、[初始记忆与装配](../../packages/agents/experiment/src/resagent2_experiment/agent.py)、[结果检查](../../packages/agents/experiment/src/resagent2_experiment/completion.py)、[Agent 测试](../../tests/experiment/test_experiment_agent.py)、[环境投影测试](../../tests/capabilities/test_workspace_context.py)。

<a id="compiler"></a>

### 3.4 Compiler：同一预算机制，但不是第四个 AgentLoop

输入边界见 [WorkflowCompiler](CONTRACTS.md#compiler)。原生 CLI / real E2E 用 `PromptLLMClient` 把编译 prompt 包成两个必需段：`system` 和 `compiler_request`。

| 调用 | compiler_request 包含什么 |
|---|---|
| draft | 当前 WorkRequest 的目标、证据要求、约束；可用能力及职责/字段语义；草图格式；本轮可用任务数、可用工作区；存在时的纠错反馈 |
| review | 同一 WorkRequest 与能力/字段语义；草图任务的 key、capability、goal、依赖、约束以及按物化器同样规则规范化的 inputs |

两者共用 `_capability_context`，不是两份互相独立维护的字段解释。当前图历史主要用于物化、校验和剩余预算，不把全部旧 Task 和 Run 历史倒给编译模型。

Compiler 没有 Session、工具读取工作集或 AgentLoop 的 runtime_feedback 段。编译拒绝进入下一版 compiler_request，最多两版草图。预算限制能复用，并不要求复用工具循环。

**源码与测试**：[draft / review](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[PromptLLMClient](../../packages/runtime/src/resagent2_runtime/llm.py)、[CLI 组合根](../../apps/cli/src/resagent2_cli/composition.py)、[E2E 组合根](../../e2e/real_e2e.py)、[编译器测试](../../tests/orchestrator/test_compiler.py)、[适配器测试](../../tests/runtime/test_prompt_client.py)。

<a id="reads"></a>

## 4. 共享读取：工具结果与上下文工作集

### 4.1 读取工具先限制一次返回

`read_file` 与 `read_artifact` 共用 `slice_text_lines`：先取从 1 开始、两端包含的行范围，再保留最多128000字符的前缀（共享 `MAX_READ_CHARS`）。这是原始工具返回的IO边界，不是128K tokens；实际送入模型的部分还要按模块有效额度选择。范围超过文件末尾可得到短结果或空串，不自动寻找另一个范围。

start_line/end_line 记录请求边界，未指定时可以是 null；它们不是重新计算出的“返回正文的精确末行”。`truncated=False` 仅表示所选范围未被字符上限裁掉，不表示已读完整个文件。

文件读取还受授权和默认 1,000,000 字节文件大小限制；工件读取先查授权、来源和整份冻结 hash，再切片。后者不因只取几行而跳过完整性校验。`search_text` 是大小写不敏感的字面子串搜索，不是正则；结果给出行号，但当前没有独立的长期搜索正文段。

**源码与测试**：[workspace tools](../../packages/capabilities/src/resagent2_capabilities/workspace_tools.py)、[工件读取](../../packages/capabilities/src/resagent2_capabilities/artifacts.py)、[切片函数](../../packages/capabilities/src/resagent2_capabilities/text.py)、[行窗口测试](../../tests/capabilities/test_text_windows.py)。

### 4.2 下一轮再从历史里选择片段

`workspace_context` 调用 `recent_tool_snippets`，文件和工件各自选择：

1. 从新到旧寻找不同片段，直到内容额度用完，不再固定最多6个；身份为工具名 + 来源 + 请求行范围，同一来源的不同范围可以共存。
2. Coding/Experiment 的文件、工件各用有效输入额度的25%；Scientific 只读工件，使用50%。按4字符/token换算正文额度。先装最新片段；装箱的最后一个片段放不下时保留头尾并标记，其后的旧片段不再选入。
3. 选中后按原始事件顺序从旧到新呈现，不修改原始 Session 事件或工件。

以有效128000 tokens为例：Coding/Experiment 每组128000字符正文，Scientific 工件组256000字符正文（可包含多次读取）。上述比例只限制 content；段说明、编号、行号等仍计入 Composer 总预算。两组不相互借用，不自动扩容，也不强制填满。

| 提示字段 | 当前含义 | 不能据此推出什么 |
|---|---|---|
| `observed_at` | 原始 Session 事件序号 | 不是文件版本、当前 step 或时钟时间 |
| `modified_after_read_at` | 记录中有同路径、较晚的成功内置编辑 | 无标记不证明外部没改文件；不用于冻结工件 |
| `truncated` | 当前显示正文是否遭到工具或工作集裁剪 | 不代表整个原文件都读完了 |
| `context_truncated` | 工作集又裁剪了工具返回的正文 | 不会覆盖或修改原事件的截断标志 |
| `previously_read_files/artifacts` | 每组最多 20 个、合计 600 字符的来源提示，不切断单个标识 | 不是完整读史，没有已发现结论或语义定位目录 |

当前工作集再裁剪时会同时置 `truncated=True` 和 `context_truncated=True`。来源提示位于有 workspace_reads 时的段内，并不是一个独立、永久可见的全文索引。

### 4.3 目录、环境、数据集的刷新频率不同

- **directory**：最近一次 list_files 的结果，附原始事件号 observed_at 和历史性说明；路径正文额度按有效输入的1/64换算字符，最多2000条完整路径。创建文件不自动更新旧清单，旧清单未列出的文件不等于不存在；重建上下文不是重新列目录。
- **environment**：每次构造从同一 EnvironmentBinding 读取 prepared/certified 等当前绑定状态；环境恢复不自动沿用旧认证，不代表每轮扫描所有依赖。
- **数据集视图**：Agent 的 run/invoke 开始时从调用方引用解析，供该次循环的上下文和脚本映射共同使用；用户回答后再次进入 Agent 会重查。不是后台监视 catalog，也不是每个 LLM step 都重新扫目录。
- **answers**：由 Controller 配对原题、调用方限定作用域；builder 展示收到的回答。当前不额外创建答案缓存或偷偷删除早期回答，必需段过大时仍明确超限。

资源字段、路径授权等公开约定仍以 [资源契约](CONTRACTS.md#resources)、[问答契约](CONTRACTS.md#questions) 为准。

**源码与测试**：[共享投影](../../packages/capabilities/src/resagent2_capabilities/workspace_context.py)、[片段选择](../../packages/runtime/src/resagent2_runtime/context.py)、[数据集视图](../../packages/capabilities/src/resagent2_capabilities/dataset.py)、[读取时序](../../tests/e2e/test_workspace_read_history.py)、[资源恢复](../../tests/e2e/test_runtime_resources.py)。

<a id="commands"></a>

### 4.4 命令结果：先选失败原因，再限制长度

`command_context` 从原事件读取 run_verification、run_setup、run_command 各自最近一次带结果的观察，生成 required `command_results`：

- 先按 exit_code/timed_out 选择失败项，再尝试放入成功项；不把整批JSON剪成首尾。
- 失败项展示命令、退出/超时状态和已捕获的 stdout_tail/stderr_tail；没有捕获到输出就明确说明，不编造根因。
- 正文额度为有效输入的1/16（128K时32000字符）。一个日志尾部最多2000字符；裁剪和未放入的结果数量明确标记。选中后按原始事件顺序呈现。
- 同一工具较新的命令结果取代投影中的旧结果，但不删除Session事件；后续普通读文件不会把最近的验证失败挤出这个段。
- 这些是历史执行诊断，不是当前状态或科学测量。验证是否仍有效，由现有control_state/完成门禁决定。

此处没有IO、LLM摘要或第二份状态缓存。完整日志仍留原处，也不承诺有限摘录覆盖所有失败原因。[源码](../../packages/capabilities/src/resagent2_capabilities/command_context.py)与[共享投影测试](../../tests/capabilities/test_workspace_context.py)。

<a id="literature"></a>

## 5. 文献的完整工作流及边界

1. **Scientific 发起检索。** 当前 ArxivLiteratureBackend 查询 arXiv API，得到论文编号、标题、作者、日期、链接和摘要；每篇摘要最多保留 2000 字符。不是下载、解析论文 PDF，也不是另一次 LLM 总结。
2. **按论文条目保存这一批结果。** LiteratureSearchTool 用确定性排版生成 `literature_search.md`，每篇一个标题，包含论文ID、来源、作者、日期和检索所得摘要；Registry 冻结并校验hash，metadata仍保留规范化记录。明确标为检索摘要而非全文或模型阅读结论。长物理行折到最多1000字符，未丢弃原字符。旧冻结JSON不改写。
3. **返回检索预览。** 每篇摘要在 ToolObservation 的 brief 中最多 200 字符；进入最近工具历史时，整个 value 还可能遭到约 400 字符预览裁剪。模型不一定同时看到每篇的完整 200 字符。
4. **按需读本地工件。** Scientific 调用现有read_artifact；通常一读可看完这批条目，较大材料仍可按标题附近的行范围继续。工具默认返回最多128000字符，工件工作集按Scientific有效输入的50%派生。不需要再次访问arXiv。
5. **判断和继续。** Scientific 可继续读取、搜索、询问用户或提出结论；没有自动为每篇论文保存一份 LLM 阅读笔记的步骤。
6. **检查引用。** 工件身份、访问历史及要求的证据种类会被检查；这些不等于全文理解、结论正确或相关风险全部被考虑。

**当前局限：**文献内容按论文组织，但底层仍复用文本行读取，不新增按论文ID取片段的API。材料过大时仍可能裁剪一篇论文或淘汰较早条目；不会自动理解“哪篇最重要”或保存阅读发现。增大额度和改善排版不等于永久记忆，也不恢复backend未取得的全文。

外部 timeout/429 与本地上下文丢失是两种问题。前者可能确实需要外部帮助；如果所需证据已在冻结工件里，是否还需要重新联网，应依据已有内容判断，不能把当前片段缺失等同于从未检索到。相关实际轨迹和候选策略见 [审查 C5](../history/reviews/CONTEXT_REVIEW_2026-09-13.md#c5)。

**源码与测试**：[literature backend / Tool](../../packages/capabilities/src/resagent2_capabilities/literature.py)、[Registry](../../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)、[Scientific 提示](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[文献能力测试](../../tests/capabilities/test_literature.py)、[冻结工件范围读取测试](../../tests/e2e/test_literature_artifact_windows.py)。最后这类脚本化测试证明指定范围可达，不证明真实模型一定自己找到范围、也不证明翻页后不会遗忘。

<a id="budgets"></a>

## 6. 预算：不要把字符、输入、输出混为一谈

| 层次 | 当前默认或规则 | 由谁负责 |
|---|---|---|
| 模型可用输入容量 | 注入的 ModelProfile：窗口减预留输出、安全余量及动作格式说明估算 | 原生 LLM client 的 context_budget hook |
| 模块输入上限 | Scientific / Coding / Experiment 各128000 tokens；Compiler保持4096 tokens | 共享DEFAULT_AGENT_CONTEXT_TOKENS，各模块参数可覆盖 |
| 阅读正文 | 有效输入的50%；Coding/Experiment文件和工件各25%，Scientific工件50% | workspace_context + recent_tool_snippets |
| 命令诊断 | 有效输入的1/16换算字符；优先失败、再成功 | command_context |
| 目录路径 | 有效输入的1/64换算字符；最多2000条完整路径 | workspace_context + recent_tool_listing |
| 一次工具读取 | 默认所选行范围最多返回128000字符；不是输入tokens上限 | read_file / read_artifact 的共享IO常量 |
| 普通历史预览 | 最近 6 条，每条 value 约 400 字符 | AgentLoop |

Loop在调用builder之前计算一次有效总额度：有ModelProfile时取“模块上限”和“模型可用输入容量”的较小值，再将同一额度交给builder和Composer。没有hook时使用模块上限，不猜Provider容量。CLI注入Profile；real E2E有自己的装配，但原生Agent默认值同源，不能假定它继承CLI的环境变量覆盖。

Composer 按最终渲染文本（含标题、分隔符）计算 `ceil(字符数 / 4)`。这是确定性粗估，不是模型 tokenizer 的精确计数，也不保证对中文等所有内容都高估；不能把 estimated_tokens 当实际 usage。原生客户端追加的动作格式说明在 Profile 路径中另行预留，不属于 included_sections；无 Profile 时不提供同等模型容量保证。

**required装不下仍明确失败。** 三个Agent各自可配置128K、256K或更小额度，局部材料比例随有效额度变化；不会因一次超限自动加到256K。剩余空间用于职责、任务、工具契约、问答、元数据和运行反馈，不再做自适应借额度或LLM压缩。比例是简单的装箱上限，不是要求每段填满；固定的短预览/来源索引仍仅用于定位。

输出额度是另一项配置：思考与最终正文可能共享 Provider 的输出额度。它不能用来解释所有输入裁剪，也不因为输入还有空间就自动增大。环境变量及部署默认值统一查 [CLI 配置](../../apps/cli/README.md#6-模型与上下文预算)，本文不另设一套值。

**源码与测试**：[Composer / 额度换算 / 选择器](../../packages/runtime/src/resagent2_runtime/context.py)、[ModelProfile / 客户端](../../packages/runtime/src/resagent2_runtime/llm.py)、[执行Agent容量](../../tests/e2e/test_native_context_capacity.py)、[Scientific容量](../../tests/e2e/test_scientific_context_capacity.py)。128K是当前工程选择，不承诺所有任务都足够或成本/延迟不变。

<a id="verification"></a>

## 7. 怎样判断信息真的交接成功

检查时分开看四层，不用最终 rc=0 代替全部结论：

1. **原记录是否正确**：请求、ToolObservation、报告和冻结内容有没有信息。
2. **本轮输入是否可见**：构造出的文本、included_sections、实际片段正文是什么；“段已 included”不等于段内全部材料都在。
3. **模型是否使用**：真实动作有没有读相关材料，是否按答案行动；不能只看测试驱动替模型挑好了范围。
4. **结果是否对应**：真实执行证据、最终结论与被读取的信息是否一致；读过也不等于相信或正确理解。

查 full trace 的 request_text 能核对最终请求；完整 reasoning 可帮助分析，但不是控制状态或证据。trace 包含敏感源码/输入，应按现有访问边界处理，不把其原文复制到公开文档。详见 [trace 契约](CONTRACTS.md#trace)。

<a id="maintenance"></a>

## 8. 后续维护这份文档

- 改字段先查 CONTRACTS，再查这里的来源→上下文段→用途；区分“字段无用”与“还没有交给消费者”。
- 修改 builder、共享读取/预览、预算或刷新方式时，同步本页对应段表与确定性上下文测试。
- 文档区分代码保证、提示期望和模型实测；不把“提示要求阅读”写成“门禁已强制阅读”。
- 同一事实沿用原权威来源；纯展示不另存一份可漂移的业务状态。
- 当前实现与候选方案分开记录。优先复用已有能力，但不因为代码和文献都叫“文本”就宣称两者理解需求完全相同。

实现保持原状态机、公开业务schema、完成门禁和JSON恢复机制；本页记录获批的上下文语义/预算/排版变化。后续真实行为以[验收单](../history/reviews/CONTEXT_128K_ACCEPTANCE.md)检查，不能用确定性测试代替模型行为证据。
