# 模型实际会看到什么：上下文说明

这份文档回答：**每个模块把哪些信息交给模型，信息从哪里来，什么时候刷新，放不下时怎样处理。** 它描述当前实现，不把候选改进写成已有能力。

- 系统分工看 [ARCHITECTURE](ARCHITECTURE.md)；公开方法和字段看 [CONTRACTS](CONTRACTS.md)。
- 本文解释字段如何进入模型输入，不重复定义接口类型。
- 原始问题与方案演变见 [上下文审查](../history/reviews/CONTEXT_REVIEW_2026-09-13.md)；本页描述已实现的语义、预算及文献呈现。测试要求和分阶段结果见 [验收记录](../history/reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout)。
- 当前共享材料分配的依据、改动与服务器补验见[统一分配记录](../history/reviews/CONTEXT_ALLOCATION_REVIEW.md)。

建议第一次先读 [基本区别](#basics)、自己关心的 [模块](#modules)，再看 [文献流程](#literature) 和 [预算](#budgets)。后面的源码、测试链接用于查证，不要求按顺序读代码。

<a id="basics"></a>

## 1. 先分清三件事

| 东西 | 保存什么 | 是否每轮完整交给模型 |
|---|---|---|
| 模块请求 | 本次 `instruction`、权限/预算/工作区等控制字段，以及授权输入工件 | 否；由各模块选取、组织；问答、要求和工作反馈属于工件内容 |
| Session 与冻结工件 | 工具事件、内部记忆、已获取的文件内容和结果，以及原生 assistant/tool 配对历史 | 否；领域材料仍由各模块选择，原生协议发送近期完整历史及可用检查点摘要，原始历史仍留在 Session；工具结果自身也可能已截断 |
| 本轮领域上下文 | 本次调用重新选中的职责、状态、材料片段和反馈 | 是；作为原生请求最后一条 `user` 消息的正文 |

例如：Experiment 已生成风险报告，Scientific 收到报告编号，Scientific 实际打开报告，Scientific 在结论中考虑风险，是四件不同的事。

`ResearchRequest.context` 只是用户提供的一段研究背景，不是本文所说的完整模型上下文。完整上下文还包含工具说明、控制状态、答案和读取结果。

当前 CLI/真实 E2E 为三个 Agent 注入原生工具客户端；下文主要描述这条路径。每次请求包含一条 API `system` 协议说明、Session 中检查点之后已经配对的 assistant/tool 消息、最后一条本轮重新组装的 `user` 领域上下文，以及独立的 `tools` 数组。领域上下文里的 `system` 仍是职责文本段名，位于最后这条 `user` 消息内；旧轮次的完整领域 prompt 不会累积进历史。单独注入仅提供 next_action 的客户端时，仍走正文 JSON 路径；这不是原生输出失败后的降级策略。

原生响应的 `reasoning_content` 会与该 assistant 回合、工具调用及 receipt 一起保存在 Session，近期回合在后续原生请求中原样续传；较早回合可以按下述边界进入有损摘要。它只是 Provider 协议连续性数据，不是科学证据，也不是新增的研究记忆组件；Session 的 `memory` 仍是代码维护的状态字典。

**对应接口与源码**：[研究输入](CONTRACTS.md#research-request)、[Session](CONTRACTS.md#attempt-session)、[LLM 客户端](../../packages/runtime/src/resagent2_runtime/llm.py)、[AgentState / ContextSection](../../packages/runtime/src/resagent2_runtime/models.py)。

<a id="pipeline"></a>

## 2. 一轮上下文怎样组成

三个原生 Agent 使用同一条主线：

1. **调用方交付请求。** Controller 或 Scheduler 决定这一回合/任务可见的输入、工件和回答作用域。
2. **先为原生开销预留额度。** Loop 取模块上限与模型可用输入容量的较小值，先扣除完整工具 schema 和当前续传历史的估算，再把剩余材料额度传给 `ContextBuilder(request, state, max_context_tokens)`。
3. **构造本轮领域段。** Agent 的 builder 从当前请求、Session 与实际绑定中构造固定 `ContextSection`；`workspace_context` 为读取、诊断和目录提供可按额度渲染的 `ContextMaterial`。要求、当前恢复的回答/反馈经 `request_materials_context` 校验后作为必需材料；数据集目录工件解析后生成实际可用性视图。
4. **AgentLoop 补运行反馈。** 尚待处理的动作、参数或完成检查拒绝作为 required 领域段加入；原生路径不再加入 `tool_contracts` 文本或 `recent_observations` 的 400 字符预览。
5. **ContextComposer 选择并计量。** 先保留必需固定段与材料的最小导航框，再按优先级选入可选段。剩余空间先按材料权重分配，未用完的空间再按优先级借用；扩展仅到完整输入的80%软水位。原生路径每次候选都按最终 `{messages, tools}` 序列化请求计量，并生成 included/omitted 清单。
6. **客户端发请求并保存配对回合。** 原生每轮可返回 1–8 个工具调用，整体参数/权限预检后串行执行并逐项保存回执；失败取消后续。finish/ask_user/request_work 必须独占一轮。输入压力达到阈值时可先做一次有界摘要，再发送当前领域上下文。

| 所在位置 | 负责什么 | 不负责什么 |
|---|---|---|
| Agent 的 context builder / Orchestrator Interpreter | 领域信息的选择、组织和用途说明 | 不替代上层调度，不变更执行事实 |
| components 的共享投影 | 环境、数据集、读取材料等可复用内容 | 不决定科学结论，不保管第二份 Run |
| runtime 的 Loop / Composer | 运行反馈、原生协议历史/检查点、工具 schema 与完整请求预算 | 不理解哪篇论文更重要，不自动总结或压缩研究发现 |
| 外层组合根 | 注入模型、容量及模块配置 | 不负责每步阅读内容的选择 |

**源码**：[Scientific context](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[interpreter](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py)、[workspace_context](../../packages/components/src/resagent2_components/context.py)、[AgentLoop](../../packages/runtime/src/resagent2_runtime/loop.py)、[ContextComposer](../../packages/runtime/src/resagent2_runtime/context.py)。

### 2.1 required、priority 与顺序不是一回事

- `ContextSection(required=True)`：固定正文必须原样放入；必需段累计超限时明确失败，不静默丢弃目标、约束或用户回答。
- `ContextMaterial(required=True)`：保留说明、来源/省略提示的最小导航框，正文可按剩余空间缩减。它是纯渲染描述，不写入 Run/Session，不是新业务契约。
- `priority`：决定**可选段**的尝试顺序，以及材料第二轮借用空余空间的顺序；数值大的先尝试。它不决定必需段显示顺序。
- `weight`：已选入材料的相对起始权重。第一轮给各材料一份，第二轮才分空余；不是独立拒绝上限，也不强制填满。
- 必需段按传入顺序排列，不按 priority 排序；所有必需段之后才是选中的可选段。
- Composer 不自行剪 JSON、代码或问答；调用材料的纯 `render(chars)` 投影，再按整包计量。具体如何保留片段、尾部或完整路径仍由共享能力决定。

所以“priority=1000”不自动意味着放在全文最前面。Loop 主动把反馈放在领域输入之前；Scientific 的 builder 首先加入 evidence_control_state。这来自插入顺序。原生工具 schema 与历史不属于这些领域段，而在 Composer 的完整请求计量中单独呈现。

### 2.2 三个 Agent 共用的原生协议与运行段

| 输入部分 | 来源和用途 | 保留与刷新方式 |
|---|---|---|
| API `system` 消息 | 原生工具使用、串行批次及控制工具独占、权限/完成门禁及新旧状态优先级说明 | 每次请求固定重建，不是 Agent 的领域职责段 |
| `tools` | 三个 Agent 各自实际注入的 Tool；名称、说明和完整 `input_model` JSON Schema | 每次从工具集合生成；`model_guidance` 合并进 description，不再复制成 `tool_contracts` 文本 |
| assistant/tool 历史 | Provider 原始 assistant 内容、reasoning、tool call，以及本地生成的配对 receipt | 按 Session 顺序完整保存；续传检查点后的完整回合，不切开调用/回执；不附加旧轮完整领域 prompt |
| 领域 `system` 段 | 当前 Agent 唯一 prompt 的职责与用法提示 | 始终必需，是最后一条 `user` 消息内的首段，不是 API `system` 消息 |
| `history_checkpoint` 段 | 压力下生成的旧交互交接摘要 | 有检查点才出现，必需；仅用于定位/续做，当前状态与回答优先，不是证据 |
| `runtime_feedback` 段 | 尚待处理的动作/参数/完成检查等拒绝信息 | 有反馈才出现，必需；每轮随当前领域上下文重建，解除规则由 Loop 管理 |
| `pending_operation` 段 | Session.pending_action 的工具、准确参数和 action_id，明确该操作尚未执行 | 有待确认动作才出现，必需；不另存状态，消费批准后消失；要求依据当前 answer 决定是否重发 |

原生 tool receipt 是 JSON，包含 `ok`、`summary`、`value`、可用时的 `observed_at`，以及询问用户、请求工作或提议完成时的控制说明；操作确认回执额外标记 `execution_status=not_executed`，避免把成功发出问题当作执行成功。不会把 `memory_updates` 发给模型。它保留工具本身已经施加的原始 IO 截断，但历史层不再额外做约 400 字符裁剪。`runtime_feedback` 的 value 明细仍有约 800 字符的预览限制。

批准本身不会执行工具。模型需按当前答案重发相同工具和参数，沿用原权限策略重验目标、消费批准后才执行；这是同一待执行操作的继续，不是第二次副作用。拒绝时不执行。`pending_operation` 只投影待处理快照，不自行把答案判成有效授权，也不对消费后执行结果未知的操作自动重放。

Tool 返回 `ok=False` 的普通观察与 Loop 生成的持久拒绝反馈仍不是同一机制；不能假定每个失败工具的完整 stderr 都会自动进入 required 反馈段。receipt 历史和 `file_reads`、`artifact_reads`、`verification_state`、`command_results` 等领域投影可能呈现同一事实，两份内容都会计入总预算。

Coding/Experiment 的命令结果仍由共享 `command_results` 段保留，见 [命令诊断](#commands)。领域投影负责当前语义与有界工作集，原生历史负责协议连续性；两者都不把已被工具截断的原始结果恢复成全文。

**源码与测试**：[原生协议投影](../../packages/runtime/src/resagent2_runtime/tool_calling.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[LLM 客户端](../../packages/runtime/src/resagent2_runtime/llm.py)、[原生工具调用测试](../../tests/runtime/test_native_tool_calls.py)、[上下文测试](../../tests/runtime/test_context.py)。

### 2.3 Session 持久化与恢复边界

CLI 在 data root 的 `sessions/coding`、`sessions/experiment`、`sessions/scientific` 分别保存完整 Session 快照；目录权限为 `0700`，文件权限为 `0600`。这与 `off` / `metadata` / `full` LLM trace 是两套独立存储：关闭 trace 不会阻止原生工具历史和 reasoning 写入 Session。

新 Session 固定记录协议身份。JSON-only 路径以 `tool_protocol_key=null` 标识；原生客户端的 key 由协议版本、API endpoint 和模型名哈希得到，不包含 API key。恢复时必须与创建时一致，因此 JSON-only Session、缺少该身份的旧记录，或换了模型/API endpoint 的 Session 都不会静默接到原生历史上；当前不迁移这类记录，需要新建 Run。

Loop 先保存整批 assistant/tool calls，每个工具派发前记录 executing_call_id，完成后保存对应 receipt 并清除执行标记。重启保留已完成回执；当时正在执行但缺回执的项记为结果未知，后续项记为未开始，不自动重放。这只是进程重启的 checkpoint 防护，不承诺掉电持久性，也不提供有副作用工具的 exactly-once 保证。

<a id="modules"></a>

## 3. 各模块实际看到什么

表中的“必需”指段出现之后不能被 Composer 省略，不代表模型一定正确使用其中的信息，也不代表原始材料全文都被保留。

<a id="scientific"></a>

### 3.1 Scientific：研究判断和证据

输入边界是统一的 [AgentRequest](CONTRACTS.md#module-request)。Controller 调用 Scientific.invoke，Scientific Session 在同一个 Run 内复用；本轮请求经 builder 重新构造成当前领域上下文，此前已配对的原生工具历史另按协议共同构成下一次输入。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `evidence_control_state` | 本 Session 已观察工件编号、尚待补读/撤回的引用 | 必需；每步根据请求和 memory 重算 |
| `research` | 当前调用的 `instruction`，由 Controller 组合目标、假设、背景和约束 | 必需；来自当前请求 |
| `dataset_catalog` | 同次数据集解析得到的可用/不可用 ID 与共享用法说明 | 必需；不是数据正文 |
| `research_materials` | 最新完整科研目录正文及原件读取入口；独立调用以同一结构组织传入材料 | 必需；累计保留历史条目，不裁成增量；原件按 ID 用 read_artifact 读取 |
| `material_<artifact_id>` | conclusion_requirements 及本次 answer 原题/回答全文 | 必需；校验 hash、内容类型与归属 |
| `material_<feedback_id>` | 本轮带引用简报，以及对应目录/执行记录引用 | 仅本轮交付反馈时必需；完整目录只在 research_materials 展示，不展开执行记录 |
| `artifact_reads` | 本 Session 的工件读取片段和已读来源提示 | 有读取/来源提示才出现；导航框必需，正文弹性分配 |

Scientific 不注入 execution environment，不提供代码编辑/实验执行工具。它的 builder 不输出 workspace_access、permissions 或剩余调用数/时间；request_work 是否允许仍由工具读取结构化权限执行硬校验。`literature_search` 只有在组合根同时提供 backend 和 registration port 时才加入工具集合。

finish 与另外两个 Agent 相同，只提交 report 和 artifacts；Scientific 其中必须包含 scientific_opinion JSON 工件。代码从真实工具观察另生成 observation_trace，模型不能提交该记录。ask_user 的 text 包含用户回答所需背景，复用共享问题字段约束并额外附带 assessment；request_work 则提交 assessment 和语义工作需求。公共结果的控制信号只引用相应 question/work_request 工件，见 [提问契约](CONTRACTS.md#questions)。

Scientific 的提示与完成检查从共享工件契约派生允许新建的种类；已有输入证据通过 opinion.evidence_artifact_ids 引用，不在 finish 里重新交付为新工件。不支持的 kind、输入/外来/伪造 Ref 和重复输出在现有 Loop 内收到 runtime_feedback，使用同一剩余预算纠正；不是 Controller 失败后另起重试。注册层仍检查身份、hash 和磁盘内容。

**科研目录与简报的分工：**

Orchestrator 的 Interpreter 从已登记材料生成科研目录，按初始材料、Scientific 材料和工作需求组织；来源、尝试序号和实际状态由代码填入。成对问答沿用 answer 原件，按保存的作用域归组。路径、hash、权限仍只在底层登记表，目录不重新管理文件。索引和原件读取共用登记来源，Scientific 能读取目录里的子任务答案；阅读不等于批准或恢复。

每轮稳定后，Controller 保存执行记录 work_record，再调用 Interpreter 的 LLM，根据本轮相关文本窗口（包括本轮成对问答）生成每条带原 Artifact ID 引用的简报。简报只解释本轮工作，和目录引用保存在冻结 work_feedback；Scientific 在本轮交付时接收简报，并在 research_materials 收到更新后的完整累计目录。工作事实与简报分开：未解决任务等机器要求从原记录读取，不相信摘要是否提到了它们。回答恢复继续展示完整目录，不重放旧简报或重新解释旧轮次。

Scientific 不再默认收到平铺 input_artifacts、完整 work_record 或旧 work_brief。AgentRequest 内的授权引用仍完整供读取和校验使用。同轮检索材料从文献工具回执发现，原 ID 可立即读取；下一次 Controller 刷新时收入累计目录。原文件、失败记录和模块报告可按需读取；默认简报不替代这些材料。

当前 observed 集合来自成功 `read_artifact` **或** `literature_search` 的记录；它表示访问历史，不证明读过全文或当前仍能看到全部正文。仅列在授权清单里的编号不自动进入 observed。读过也不代表观点必然正确。

**源码与测试**：[context](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[interpreter](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py)、[装配与回合](../../packages/agents/scientific/src/resagent2_scientific/agent.py)、[观察与完成检查](../../packages/agents/scientific/src/resagent2_scientific/completion.py)、[Interpreter 测试](../../tests/orchestrator/test_work_interpreter.py)、[证据控制测试](../../tests/scientific/test_evidence_control.py)。

<a id="coding"></a>

### 3.2 Coding：理解、修改和验证

输入边界是 [AgentRequest](CONTRACTS.md#module-request)。同一个 prompt、动作集合和 context builder 处理分析、修改和验证，工具行为受工作区与操作权限约束。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `task` | `instruction`、workspace_access、permissions、confirm_commands，以及 input_artifacts 的 id/kind/summary | 必需；来自本 Task/Attempt 的请求 |
| `dataset_catalog` | 当前 invoke 解析的数据集视图与共享说明 | 必需 |
| `material_<artifact_id>` | acceptance_requirements，以及本次 resume_artifact_ids 指定的 answer | 对已选材料必需；校验 Task/Attempt 归属 |
| `verification_state` | edit_revision、verification_revision、environment_certified、验证问题/是否过时及 suggested_next_action；代次比较在代码内完成，不直接展示 generation | 存在控制投影时必需；每步调用 derive_control_state |
| `environment` | 实际 EnvironmentBinding 的 prepared/certified、Python 要求及已有环境身份 | 有绑定时必需；与工具使用同一绑定 |
| `file_reads` / `artifact_reads` | 文件片段与工件片段，分别保留 | 各自导航框必需，正文共享空余额度 |
| `command_results` | 每个执行工具最近一次有记录的命令结果；优先保留失败诊断 | 有命令结果才出现，必需；共享投影，不新增缓存 |
| `directory` | 最近一次 list_files 观察的有界路径清单 | 有结果才出现，可选，priority=62；不是每轮重新扫磁盘 |

可写工作区允许修改，不要求修改；只读源目录仍可通过候选工件输出报告。任务基线和验证记录由代码保存，模型不能自行声明“代码已改、验证已过”作为机器事实。

**验证状态的含义：**`verification_state.edited_since_verification` 比较 edit_revision 与 verification_revision，表示记录的编辑版本是否晚于验证版本。false 不表示本 Attempt 没有修改，也不代表实时 Git diff 为空。verification_issue、verification_stale 和 suggested_next_action 提示当前验证状态；新编辑、环境变动或恢复不会让旧验证自动覆盖当前代码。

需要验证而绑定尚未认证时，建议动作是 run_verification，由获准执行的工具自动核验环境；不再要求模型先单独 audit_env。没有记录到编辑时 suggested_next_action 为 none，verification_stale 也可为 false，即使 verification_issue 是“未执行验证”；这些字段不强制纯分析任务运行命令。

这些字段是确定性事实与建议，不是另一种业务模式。finalizer 生成验证工件，Scheduler 根据明确的验收要求判断是否必须成功执行。读文件、search_text、git_diff 等工具结果保留在原生 receipt 历史中，但仍受工具原始 IO 截断和总输入预算约束；文件正文另进工作集，命令与验证信息继续使用各自投影。

**源码与测试**：[context](../../packages/agents/coding/src/resagent2_coding/context.py)、[Agent 装配](../../packages/agents/coding/src/resagent2_coding/agent.py)、[验证状态与完成检查](../../packages/agents/coding/src/resagent2_coding/completion.py)、[验证状态测试](../../tests/coding/test_control_state.py)、[验证有效性测试](../../tests/coding/test_verification_validity.py)。

<a id="experiment"></a>

### 3.3 Experiment：分析、执行与交付结果

同样使用 [AgentRequest](CONTRACTS.md#module-request)，不含 Coding 的编辑工具；已有结果分析和新实验执行共用一套 prompt 与动作。

| 段名 | 从哪里来、给模型看什么 | 保留方式 |
|---|---|---|
| `task` | `instruction`、workspace_access、output_dir、permissions、输入工件清单和 confirm_commands | 必需 |
| `dataset_catalog` | 与另两个 Agent 同源的 dataset_context | 必需 |
| `material_<artifact_id>` | acceptance_requirements，以及本次 resume_artifact_ids 指定的 answer | 对已选材料必需；与 Coding 共用函数 |
| `environment` | 实际环境绑定与认证状态 | 有绑定时必需；每次构造读取同一绑定 |
| `file_reads` / `artifact_reads` | 文件与工件两组正文 | 各自导航框必需，正文共享空余额度 |
| `command_results` | run_command/run_setup 最近的命令结果与有界失败诊断 | 有结果才出现，必需；与 Coding 共用机制 |
| `directory` | 最近一次有界目录观察 | 可选，priority=62 |

调用开始不强制探测硬件或执行命令；需要时通过工具观察。环境绑定是工具和上下文共用的实际对象，原生历史中的旧 audit receipt 不能代替当前绑定。

run_command 的回执包含实际命令、退出/超时状态、日志路径与有界 stdout_tail/stderr_tail；实际执行自动核验时还包含 env_audit。当前没有 evidence_files 自动发现清单，需通过 list_files/read_file 检查产物。`command_results` 再投影有界诊断，产物正文不会因此自动读入。execution_record 由代码从真实事件生成；数值交付与成功执行等精确要求在 Scheduler 按要求工件检查，报告自报数字不算测量证据。

**源码与测试**：[context](../../packages/agents/experiment/src/resagent2_experiment/context.py)、[初始记忆与装配](../../packages/agents/experiment/src/resagent2_experiment/agent.py)、[结果检查](../../packages/agents/experiment/src/resagent2_experiment/completion.py)、[Agent 测试](../../tests/experiment/test_experiment_agent.py)、[环境投影测试](../../tests/components/test_workspace_context.py)。

<a id="compiler"></a>

### 3.4 Compiler：同一预算机制，但不是第四个 AgentLoop

输入边界见 [WorkflowCompiler](CONTRACTS.md#compiler)。CLI / real E2E 仍用 `PromptLLMClient.next_action` 把编译 prompt 包成两个必需段：`system` 和 `compiler_request`，要求 JSON-only 输出；两入口的默认额度同源为128000，CLI仍可用 `RESAGENT2_COMPILER_CONTEXT_TOKENS` 单独覆盖。它复用客户端的旧分段注入路径，不使用 Agent 的原生工具历史或 `tools` 数组。

compiler_request 包含当前 WorkRequest 的目标、证据要求、约束，可用 coding/experiment 模块说明，CompilationDraft schema，剩余任务容量、逻辑工作区，以及存在时的结构纠错反馈。CLI 与 real E2E 使用各 Agent 类上的同一份 description，明确 Coding 可只解释代码，Experiment 可只分析已有结果、无需执行或准备环境。要求把同一 Agent 的提问、检查、准备和执行保留在一个任务中，并用声明的 output_name 连接确有需要的跨任务产物。任务容量明确是上限，不是应凑满的目标；单次操作的约束不能被改写成只准调用工具一次，确认后重发不等于重复执行。

草图顶层只有 tasks；节点使用 instruction，不另列 goal/constraints/inputs 或业务模式。代码物化正式身份并校验，不额外发起语义复审。当前图历史主要用于物化、校验和剩余预算，不把全部旧 Task 和 Run 历史倒给编译模型。

Compiler 没有 Session、工具读取工作集或 AgentLoop 的 runtime_feedback 段。编译拒绝进入下一版 compiler_request，最多两版草图。任务数来自 ExecutionLimits；请求次数和截止时间使用调用方绑定的同一 Run execution_budget，不另开余额。复用预算不要求复用工具循环，Agent 的坏原生输出也不会降级到这条 JSON-only 路径。

**源码与测试**：[编译草图与校验](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[PromptLLMClient](../../packages/runtime/src/resagent2_runtime/llm.py)、[CLI 组合根](../../apps/cli/src/resagent2_cli/composition.py)、[E2E 组合根](../../e2e/real_e2e.py)、[编译器测试](../../tests/orchestrator/test_compiler.py)、[适配器测试](../../tests/runtime/test_prompt_client.py)。

<a id="interpreter"></a>

### 3.5 Interpreter：反向翻译，无独立会话

输入和产物契约见[反向交接](CONTRACTS.md#interpreter)。生产入口注入独立 PromptLLMClient，使用 system/interpreter_request 必需段和 WorkBrief JSON 输出。默认输入上限同源为 128000 tokens，可用 RESAGENT2_INTERPRETER_CONTEXT_TOKENS 配置；调用、格式纠正和 HTTP 重试仍计入同一 Run。

输入只包含本轮完整 WorkRecord、本轮相关目录条目及原始文本窗口，包括按 Task/Attempt 归属找到的本轮成对问答，不把累计历史全文重复发送。每份文本最多读取 12000 字符，窗口保留 truncated 等信息；二进制内容不进入文本解释。窗口经过现有 Run 授权和整份 hash 校验，引用只允许当前真实提供的文本来源或执行记录。材料过多超过有效输入额度时明确失败，不默默丢弃必需结构，也不增加摘要循环。第一次 JSON/引用错误允许携带原因纠正一次，不作额外语义评审。

Interpreter 没有 Session、工具循环或私有工作区；产出保存后由 Controller 重用。解释过程中读取的材料不计入 Scientific 的 observed。

<a id="reads"></a>

## 4. 共享读取：工具结果与上下文工作集

### 4.1 读取工具先限制一次返回

`read_file` 与 `read_artifact` 共用 `slice_text_lines`：先取从 1 开始、两端包含的行范围，再保留最多128000字符的前缀（共享 `MAX_READ_CHARS`）。这是原始工具返回的IO边界，不是128K tokens；实际送入模型的部分还要按模块有效额度选择。范围超过文件末尾可得到短结果或空串，不自动寻找另一个范围。

start_line/end_line 记录请求边界，未指定时可以是 null；它们不是重新计算出的“返回正文的精确末行”。`truncated=False` 仅表示所选范围未被字符上限裁掉，不表示已读完整个文件。

文件读取还受授权和默认 1,000,000 字节文件大小限制；工件读取先查授权、来源和整份冻结 hash，再切片。后者不因只取几行而跳过完整性校验。`search_text` 是大小写不敏感的字面子串搜索，不是正则；结果给出行号，但当前没有独立的长期搜索正文段。

**源码与测试**：[workspace read_file](../../packages/capabilities/src/resagent2_capabilities/workspace/read_file.py)、[工件读取](../../packages/components/src/resagent2_components/artifacts.py)、[切片函数](../../packages/components/src/resagent2_components/text.py)、[行窗口测试](../../tests/capabilities/test_text_windows.py)。

### 4.2 下一轮再从历史里选择片段

`workspace_context` 调用 `recent_tool_snippets`，文件和工件各自选择：

1. 从新到旧寻找不同片段，直到内容额度用完，不再固定最多6个；身份为工具名 + 来源 + 请求行范围，同一来源的不同范围可以共存。
2. 文件和工件以相同起始权重进入 Composer；每次从实际剩余空间计算，而非各自占死25%。Scientific 不提供文件材料，所以工件可使用其空余。先装最新片段；装箱的最后一个片段放不下时保留头尾并标记，其后的旧片段不再选入。
3. 选中后按原始事件顺序从旧到新呈现，不修改原始 Session 事件或工件。

例如，扣除固定上下文后，若本轮材料可用空间为60K、只存在文件和工件材料，起始各得30K；工件只用了5K，文件可借用余下25K。两类都很大时先各保留一份，不让较新工件直接挤掉所有文件。这里的K指估算tokens，不是字符；每次扩展连同元数据与JSON转义一起重新计量，不追求字节级最优装箱，也不强制填满。

| 提示字段 | 当前含义 | 不能据此推出什么 |
|---|---|---|
| `observed_at` | 原始 Session 事件序号 | 不是文件版本、当前 step 或时钟时间 |
| `modified_after_read_at` | 记录中有同路径、较晚的内置编辑或已完成删除（含部分删除中的完成项） | 无标记不证明外部没改文件；不用于冻结工件 |
| `truncated` | 当前显示正文是否遭到工具或工作集裁剪 | 不代表整个原文件都读完了 |
| `context_truncated` | 工作集又裁剪了工具返回的正文 | 不会覆盖或修改原事件的截断标志 |
| `previously_read` | 所在文件/工件段中最多20个、合计600字符的来源提示，不切断单个标识 | 不是完整读史，没有已发现结论或语义定位目录 |
| `content_omitted` | 本次该段没有放入任何正文片段 | false不保证全部历史已放入；具体片段另看truncated |

当前工作集再裁剪时会同时置 `truncated=True` 和 `context_truncated=True`。`file_reads` / `artifact_reads` 都使用 `{snippets, previously_read, content_omitted}`；来源提示不是一个独立、永久可见的全文索引。旧 trace 中的 `workspace_reads` 保持原样，不迁移历史数据。

### 4.3 目录、环境、数据集的刷新频率不同

- **directory**：最近一次 list_files 的结果，附原始事件号 observed_at 和历史性说明；参与共享材料分配，最多2000条完整路径。创建文件不自动更新旧清单，旧清单未列出的文件不等于不存在；重建上下文不是重新列目录。
- **environment**：每次构造从同一 EnvironmentBinding 读取 prepared/certified、required_python；已有环境时再附 env_id、prefix、python_version。环境恢复不自动沿用旧认证。获准的验证/实验命令执行前会核验尚未认证的绑定，无需模型先单独 audit_env；这不代表每轮上下文构造都扫描依赖。完整 env_audit 保存在该次工具 value 和 Session memory，原生 receipt 可见；environment 段只投影当前绑定，不直接展开历史审计。
- **数据集视图**：Agent 的 invoke 开始时从 dataset_catalog 工件解析引用，供该次循环的上下文和脚本映射共同使用；用户回答后再次进入 Agent 会重查。不是后台监视 catalog，也不是每个 LLM step 都重新扫目录。
- **恢复材料**：Controller 配对原题或工作需求，将 answer/work_feedback 冻结并限定作用域；builder 展示 resume_artifact_ids 指定的本次材料；Scientific 的工作反馈展示本轮简报，完整累计目录在 research_materials 单独展示，历史工件仍保留。每个 material 段包含 artifact_id、kind、content；answer 的 content 保留原题、回答和可选动作快照，不是只展示一句 yes。要求工件不依赖 resume_artifact_ids，仍按类型自动装入。读取并注入材料不替代权限策略核对 pending_action 和单次批准；必需材料过大时明确超限，不静默截断结构化答案。

资源字段、路径授权等公开约定仍以 [资源契约](CONTRACTS.md#resources)、[问答契约](CONTRACTS.md#questions) 为准。

**源码与测试**：[共享投影](../../packages/components/src/resagent2_components/context.py)、[片段选择](../../packages/runtime/src/resagent2_runtime/context.py)、[数据集视图](../../packages/components/src/resagent2_components/dataset.py)、[读取时序](../../tests/e2e/test_workspace_read_history.py)、[资源恢复](../../tests/e2e/test_runtime_resources.py)。

<a id="commands"></a>

### 4.4 命令结果：先选失败原因，再限制长度

`command_context` 从原事件读取 run_verification、run_setup、run_command 各自最近一次带结果的观察，生成 required `command_results`：

- 先按 exit_code/timed_out 选择失败项，再尝试放入成功项；不把整批JSON剪成首尾。
- 失败项展示命令、退出/超时状态和已捕获的 stdout_tail/stderr_tail；没有捕获到输出就明确说明，不编造根因。
- 正文参与统一材料分配，空余空间优先借给诊断（priority=96），然后读取材料（80）、目录（62）。一个日志尾部仍最多2000字符；裁剪和未放入的结果数量明确标记。选中后按原始事件顺序呈现。额度很小时保留省略提示，不宣称全部根因始终可见。
- 同一工具较新的命令结果取代投影中的旧结果，但不删除Session事件；后续普通读文件不会把最近的验证失败挤出这个段。
- 该段只选择含合法 exit_code 的真实命令结果，不展开 env_audit。前置审计失败、无环境或等待批准均不是已执行命令，可能仍保留此前的命令诊断；本次阻断原因应结合最新 receipt、environment 和运行反馈读取。
- 这些是历史执行诊断，不是当前状态或科学测量。验证是否仍有效，由 verification_state 与确定性验证记录说明；是否满足任务的成功执行要求由验收决定。

此处没有IO、LLM摘要或第二份状态缓存。完整日志仍留原处，也不承诺有限摘录覆盖所有失败原因。[源码](../../packages/components/src/resagent2_components/context.py)与[共享投影测试](../../tests/components/test_workspace_context.py)。

<a id="literature"></a>

## 5. 文献的完整工作流及边界

1. **Scientific 发起检索。** CLI/E2E 将 arXiv、OpenAlex 作为平级来源装配，成功后继续用该源，限流/临时网络故障时再试其他源，每次最多遍历一轮。正常空结果、坏请求和损坏响应不触发切换。两个后端都输出原有 LiteraturePaper：编号、标题、作者、日期、真实来源链接和摘要；每篇摘要最多 2000 字符，缺摘要留空。不是下载、解析 PDF，也不是另一次 LLM 总结。切换原因在应用日志，工件里的 OpenAlex ID/链接不会伪装成 arXiv。
2. **按论文条目保存这一批结果。** LiteratureSearchTool 用确定性排版生成 `literature_search.md`，每篇一个标题，包含论文ID、来源、作者、日期和检索所得摘要；Registry 冻结并校验hash，metadata仍保留规范化记录。明确标为检索摘要而非全文或模型阅读结论。长物理行折到最多1000字符，未丢弃原字符。旧冻结JSON不改写。
3. **返回检索预览。** 每篇摘要在 ToolObservation 的 brief 中最多 200 字符；该已受限结果进入原生 tool receipt 后不再附加约 400 字符的历史裁剪。完整协议历史仍受总输入预算，模型也不一定在一次检索预览里得到论文全文。
4. **按需读本地工件。** Scientific 调用现有read_artifact；通常一读可看完这批条目，较大材料仍可按标题附近的行范围继续。工具默认返回最多128000字符，工件工作集由共享Composer按剩余空间分配。不需要再次访问论文检索服务。
5. **判断和继续。** Scientific 可继续读取、搜索、询问用户或提出结论；没有自动为每篇论文保存一份 LLM 阅读笔记的步骤。
6. **检查引用。** 工件身份、访问历史及要求的证据种类会被检查；这些不等于全文理解、结论正确或相关风险全部被考虑。

**当前局限：**文献内容按论文组织，但底层仍复用文本行读取，不新增按论文ID取片段的API。材料过大时仍可能裁剪一篇论文或淘汰较早条目；不会自动理解“哪篇最重要”或保存阅读发现。增大额度和改善排版不等于永久记忆，也不恢复backend未取得的全文。

外部 timeout/429 与本地上下文丢失是两种问题。前者可能确实需要外部帮助；如果所需证据已在冻结工件里，是否还需要重新联网，应依据已有内容判断，不能把当前片段缺失等同于从未检索到。相关实际轨迹和候选策略见 [审查 C5](../history/reviews/CONTEXT_REVIEW_2026-09-13.md#c5)。

**源码与测试**：[文献后端与呈现](../../packages/components/src/resagent2_components/literature/backends.py)、[文献 Tool](../../packages/capabilities/src/resagent2_capabilities/literature/literature_search.py)、[Registry](../../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)、[Scientific 提示](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[文献能力测试](../../tests/capabilities/test_literature.py)、[冻结工件范围读取测试](../../tests/e2e/test_literature_artifact_windows.py)。最后这类脚本化测试证明指定范围可达，不证明真实模型一定自己找到范围、也不证明翻页后不会遗忘。

<a id="budgets"></a>

## 6. 预算：不要把字符、输入、输出混为一谈

| 层次 | 当前默认或规则 | 由谁负责 |
|---|---|---|
| 模型可用输入容量 | 注入的 ModelProfile：窗口减预留输出和安全余量；Compiler/Interpreter 还扣 JSON 输出 schema 说明 | LLM client 的预算 hook |
| 模块输入上限 | Scientific / Coding / Experiment / Compiler / Interpreter 各128000 tokens；前三者覆盖完整序列化 `{messages, tools}`，Compiler/Interpreter保留JSON-only计量路径 | 共享DEFAULT_AGENT_CONTEXT_TOKENS，各模块参数可覆盖 |
| 材料软水位 | 固定正文与最小导航框先入场；可伸缩材料扩展到整包80%，与压缩触发阈值同源 | ContextComposer + CONTEXT_TARGET_SHARE |
| 材料起始份额 | 文件/工件/诊断/目录的相对权重16/16/4/1，只分配给已选入项；空余按96/80/62优先级借用 | ContextMaterial + ContextComposer |
| 阅读、诊断、目录的选择 | 阅读保留来源时序；命令先失败后成功；目录最多2000条完整路径 | workspace_context + 既有选择器 |
| 一次工具读取 | 默认所选行范围最多返回128000字符；不是输入tokens上限 | read_file / read_artifact 的共享IO常量 |
| 原生工具历史 | 近期完整配对回合 + 可用摘要检查点；原始全史留在 Session，不做400字符裁剪 | AgentLoop + SessionStore |
| 调用次数/时间 | RunBudget 仅含 max_llm_calls 和 timeout_seconds；发送前持久占用，内部共享余额和截止时间 | Controller / Scheduler / Runtime 的共享 execution_budget 与 RunUsage |
| 流程上限 | ExecutionLimits 的 max_tasks、max_attempts_per_task；step 仅记录动作时序 | Compiler / Scheduler，不另立消费预算 |

执行预算与模型可见信息是两个边界：当前三个 Agent 的 builder 及共同运行段都不自动输出 TaskBudget 数值、Run 用量或实时剩余时间。Coding/Experiment 的 task 段展示明确权限、工作区范围和确认开关；Scientific 的 research 段只有 instruction。上述控制仍在代码中执行，不依赖模型自行记账。Compiler 的 compiler_request 展示剩余任务槽位，但不接收一份可分配的模型调用钱包。

Loop在调用builder之前计算有效总额度：有ModelProfile时取“模块上限”和“模型可用输入容量”的较小值；没有hook时使用模块上限，不猜Provider容量。原生路径先为完整 tools schema 和当前续传历史预留空间，再把剩余材料额度交给builder；Composer 随后仍按完整请求复核。输入压力先尝试下述最小压缩；没有可用前缀、schema/单个巨大回合/required 领域段仍装不下时，明确返回 `budget_exhausted`。不删除半个 assistant/tool pair，也不暗改上限。CLI注入Profile；real E2E有自己的装配，但原生Agent默认值同源，不能假定它继承CLI的环境变量覆盖。

Composer 仍按 `ceil(字符数 / 4)` 估算，但原生路径计量的是序列化后的 messages、tools 和 JSON 转义，而不只是领域渲染文本；客户端发送前还会以同一完整请求边界复核。这个算法是确定性粗估，不是模型 tokenizer 的精确计数，也不保证对中文等所有内容都高估；不能把 `estimated_tokens` 当实际 usage。Compiler/Interpreter 的 JSON 输出 schema 说明仍在正文 JSON 路径中计量；无 Profile 时不提供同等模型容量保证。

**只有整包输入上限是容量硬门槛。** 先原样放入职责、任务、问答、反馈、已有摘要等固定必需段，再保留材料的最小导航框；可选段仍按原优先级选入。材料在80%水位内先按相对权重分配，再按优先级使用剩余空间，不能借走其他材料已分到的一份。80%不是必需输入的拒绝线：必需内容超过它但未超过100%仍可容纳，并沿用历史压缩判断。

例如128K模块的材料填充目标是整包102.4K，留下空间容纳后续工具返回；这不是对下次返回一定放得下的承诺。不能把所有空余都填到128K，否则材料扩展自身就可能反复触发压缩。输入参数可覆盖模块上限，但系统不会自动扩大它。协议历史与材料投影的重复内容仍会重复计量，不做隐式去重或删原始回执。

**真正不足就报错，不新增恢复状态机。** 沿用至多一次旧历史压缩后，schema、单个巨大近期完整回合或最小必需输入仍装不下，返回现有 `budget_exhausted` 并保留现场；不自动ask_user、不加二次摘要纠错、不反复扩容。空/截断摘要仍拒绝。当前业务信息不由历史摘要替代，Compiler没有材料或Session；其默认输入额度同为128000，但仍走原JSON-only路径，不新增压缩。

输出额度是另一项配置：思考与最终正文可能共享 Provider 的输出额度。它不能用来解释所有输入裁剪，也不因为输入还有空间就自动增大。环境变量及部署默认值统一查 [CLI 配置](../../apps/cli/README.md#6-模型与上下文预算)，本文不另设一套值。

**源码与测试**：[Composer / 材料分配 / 选择器](../../packages/runtime/src/resagent2_runtime/context.py)、[弹性分配测试](../../tests/runtime/test_context_materials.py)、[ModelProfile / 客户端](../../packages/runtime/src/resagent2_runtime/llm.py)、[执行Agent容量](../../tests/e2e/test_native_context_capacity.py)、[Scientific容量](../../tests/e2e/test_scientific_context_capacity.py)。128K是当前工程选择，不承诺所有任务都足够或成本/延迟不变。

<a id="compaction"></a>

### 6.1 Session 会一直累积吗？

**磁盘原始记录会累积；不再把全部旧历史无限塞回 128K。** 仅压缩旧协议历史，不删除原始 tool_turns/events，不改当前任务、回答、领域 memory、文件工件或完成状态。没有自动磁盘清理/归档，也不保证超长 Session 的存储或重写成本恒定。

- 完整请求超过有效输入的 80%，或组装必需段时实际超限，才考虑压缩。每轮最多做一次；没有更早完整前缀就不做。
- 至少保留最新一个完整 turn（包含全部 tool calls/receipts/reasoning），再尽量保留合计不超过有效输入 20% 的近期完整回合。20% 是选择目标，不是切断单个回合的刀。
- 较早完整回合连同已有摘要送给同一个客户端作纯文本交接；输入整包仍受相同容量限制。摘要的生成目标按有效输入的 5% 换算字符，目标值最多 16384 字符；这是写短的提示，不是返回长度的独立拒绝上限。Provider 输出额度仍沿用 ModelProfile，避免 thinking 挤空正文。
- 接受摘要后，由原 Composer 检查“完整摘要 + 近期完整回合 + 当前领域上下文 + 工具 schema”能否装下；略超生成目标但整包能容纳就完整保留，不截断摘要。成功后才把摘要和绝对边界一起写入 history_checkpoint，并追加 compaction 审计事件。失败、空摘要、截断响应、unfinished prefix、重建真正超限都不推进边界。
- 后续发送“摘要 + 边界后的完整回合 + 最新领域上下文”；重启从持久检查点继续，不重复总结已经覆盖的原始前缀。总结只提供做过什么、待办和来源线索，不具证据权威性。
- 摘要调用和 HTTP 重试计入同一 max_llm_calls，开始前至少留下两次额度（摘要与下一次动作）；不设置另一份摘要调用钱包。每次发送前持久占用，暂停恢复不重置余额，崩溃留下的 unknown 不退款；这不是供应商精确计费或 Run/Session/Provider 的跨系统事务。
- 没有可选 summarize_history 的注入客户端仍可运行；真正装不下时明确失败。摘要失败不会悄悄换模型、增大预算或自动无限重试。

这里新增的是一个共享检查点，不是分 Agent 的长期记忆、阅读笔记或向量检索。Compiler/Interpreter 没有 Session，不走压缩。压缩有损；它不保证避免所有循环、保留所有历史细节或节省每次调用费用。

**源码与测试**：[规划函数](../../packages/runtime/src/resagent2_runtime/compaction.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[检查点模型](../../packages/runtime/src/resagent2_runtime/models.py)、[规划/传输测试](../../tests/runtime/test_compaction.py)、[循环/重启测试](../../tests/runtime/test_history_checkpoint.py)。服务器要求见[本轮验收单](../history/reviews/RUNTIME_CONTINUATION_ACCEPTANCE.md)。

<a id="verification"></a>

## 7. 怎样判断信息真的交接成功

检查时分开看四层，不用最终 rc=0 代替全部结论：

1. **原记录是否正确**：请求、ToolObservation、报告和冻结内容有没有信息。
2. **本轮输入是否可见**：构造出的文本、included_sections、实际片段正文是什么；“段已 included”不等于段内全部材料都在。
3. **模型是否使用**：真实动作有没有读相关材料，是否按答案行动；不能只看测试驱动替模型挑好了范围。
4. **结果是否对应**：真实执行证据、最终结论与被读取的信息是否一致；读过也不等于相信或正确理解。

原生调用的 full trace `request_text` 是序列化的 `{messages, tools}` JSON，可核对最终请求；`raw_tool_calls` 保存 Provider 返回的调用数组，单工具的 `parsed_action` 为 `{tool, arguments}`，多工具为相同对象组成的数组，并用 `tools` 列表展示名称；摘要请求 included_sections 含 compaction、action_valid/tool/parsed_action 为 null，不能计为无效工具动作。工具调用响应的 `raw_response_text` 可以为 null，这不表示模型返回了空动作。完整 reasoning 可帮助分析，但不是控制状态或证据。trace 包含敏感源码/输入，应按现有访问边界处理，不把其原文复制到公开文档。详见 [trace 契约](CONTRACTS.md#trace)。

<a id="maintenance"></a>

## 8. 后续维护这份文档

- 改字段先查 CONTRACTS，再查这里的来源→上下文段→用途；区分“字段无用”与“还没有交给消费者”。
- 修改 builder、共享读取/预览、预算或刷新方式时，同步本页对应段表与确定性上下文测试。
- 文档区分代码保证、提示期望和模型实测；不把“提示要求阅读”写成“门禁已强制阅读”。
- 同一事实沿用原权威来源；纯展示不另存一份可漂移的业务状态。
- 当前实现与候选方案分开记录。优先复用已有能力，但不因为代码和文献都叫“文本”就宣称两者理解需求完全相同。

当前 schema 16.0 保持三个 Agent 的 invoke、instruction/input_artifacts 输入和 report/artifacts 输出；业务材料通过冻结工件交接。RunBudget/TaskBudget 只含请求次数与时间，任务数/尝试数由 ExecutionLimits 控制，step 仅记时序。Coding/Experiment 模型可见 workspace_access 与明确操作授权；自然语言及历史回答不能扩权，操作确认依靠结构化单次快照。旧 Run 不支持恢复，state/session/trace 原样保留不迁移。Compiler 保留 JSON 编译路径，默认装配的三个 Agent 使用原生工具协议且不会在坏输出时降级。此前上下文阶段的结果见[验收记录](../history/reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout)，原生调用与续传边界见[续传计划](../history/reviews/RUNTIME_CONTINUATION_PLAN.md)。历史验证不代表本次变更的真实模型表现；确定性测试也不保证模型消除重复动作或循环。
