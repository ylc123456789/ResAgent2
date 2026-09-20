# 模块接口与契约

当前公共契约为 **schema 12.0**。三个 Agent 共用 `invoke(AgentRequest) -> AgentResult`：业务输入是 `instruction + input_artifacts`，业务输出是 `report + artifacts`。身份、权限、预算、状态、恢复和控制信号保持结构化。每个 Agent 只有一种调用和业务模式。

本页说明调用边界、字段和接收规则。职责看 [架构](ARCHITECTURE.md)，模型可见内容看 [上下文](CONTEXT.md)，公共模型以 [models.py](../../packages/contracts/src/resagent2_contracts/models.py) 为准。当前入口为进程内 Python 方法。

<a id="boundaries"></a>

## 按调用边界查找

| 调用方向 | 入口 | 输入 → 输出 | 位置 |
|---|---|---|---|
| 用户入口 → Controller | create_run / answer_question / run_until_stable | ResearchRequest / UserAnswer → ResearchRun | [用户与控制](#entry) |
| Controller → Scientific | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module)、[科学决策](#scientific) |
| Controller → Compiler | WorkflowCompiler.compile | WorkRequest + 图/路由/预算 → CompilationResult | [工作编译](#compiler) |
| Scheduler → Coding / Experiment | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module) |
| AgentLoop → ToolRegistry → Tool | dispatch / execute | arguments → ToolObservation | [工具与运行](#tools) |
| Tool / Agent / 组合根 → Components | 普通 Python 调用 | 授权、资源、命令、事件 → 操作结果或内容投影 | [普通组件](#components) |
| AgentLoop → 领域完成检查 | CompletionCheck.evaluate | 真实记录 + 完成提议 → CompletionDecision | [完成验收](#completion) |
| 生产方 → Registry；Agent → reader | register / read_text | Candidate → Ref；授权 Ref → 内容 | [工件](#artifacts) |

<a id="conventions"></a>

公共模型继承 `ContractModel`，拒绝未知字段并校验版本。方法签名只规定调用形状；接收端还须校验身份、归属、状态和实际消费。机器状态不从报告文本推断，类型合法也不证明科学结论正确。

<a id="entry"></a>

## 1. 用户入口与问答

`ResearchController` 是唯一 Run 业务入口：

- `create_run(run_id, request)` 创建 Run 并推进到完成、失败或等待用户。
- `answer_question(run_id, answer)` 校验当前问题、记录回答，再继续同一个 Run。
- `run_until_stable(run_id)` 推进可恢复执行，不制造答案或绕过暂停。

Scheduler 只执行 Controller 接受的任务图，不创建第二条 Run 控制链。CLI 与 E2E 分别装配依赖，业务行为共用 Controller。

<a id="research-request"></a>

`ResearchRequest` 包含 `goal`、可选 `hypothesis`、`context`、`constraints`、`input_artifacts: list[ArtifactImport]`、`required_evidence_kinds` 和 `budget: RunBudget`。RunBudget 限制任务数、每任务尝试数、LLM 调用数和总时间；明确等待用户的暂停时间不计入超时，其他耗时仍计入。

调用者用 ArtifactImport 提交本地输入的 URI、kind、media_type、summary 和可选 expected_sha256。Controller 校验并冻结为已登记工件。数据集目录由部署环境提供，不是 ResearchRequest 的逐次路径参数。

<a id="questions"></a>

### 提问、回答与恢复

`QuestionDraft` 的 `text` 必须包含回答所需背景，`requested_fields` 非空；`options` 可选。字段键使用 AnswerFieldName：1–64 个 ASCII 字母、数字或下划线，以字母开头。正文和选项放在值中，不放进机器键。工具 schema 在暂停前校验这些规则。

Agent 返回 `needs_user_input`、paused Session 和指向 `question` 工件的 ControlSignal。Controller 分配并保存 PendingQuestion；用户只提交 `UserAnswer(question_id, values, answered_at)`。values 必须匹配保存的问题字段。

Controller 从 PendingQuestion 配对原题，生成 `RecordedAnswer`：question_id、question_text、requested_fields、options、values、answered_at、run_id，以及 Task/Attempt 或 Scientific Session 作用域。它被冻结为 `answer` 工件，通过 input_artifacts 交回对应 Agent；`resume_artifact_ids` 标识本次恢复实际要消费的材料。

任务问答继续同一 Attempt、Session、输出目录与基线；retry 才产生新 Attempt。Scientific 工作交付与问答也通过同一个 invoke 恢复，答案和工作反馈不能混作同一次恢复材料。资源是否准备好仍须重新检查，口头回答不替代目录事实。

<a id="module"></a>

## 2. 三个 Agent 的统一调用

```python
class ModulePort(Protocol):
    def invoke(self, request: AgentRequest) -> AgentResult: ...
```

Controller 调用 Scientific，Scheduler 调用 Coding/Experiment。`ModuleBinding(owner, port)` 由组合根注入；Orchestrator 不 import 具体 Agent。业务任务可包含检查、准备、执行、分析等步骤，无需给同一 Agent 再设模式枚举。

<a id="module-request"></a>
<a id="scientific-turn"></a>

### AgentRequest

| 字段 | 用途 |
|---|---|
| run_id、agent | Run 身份和 scientific / coding / experiment 路由 |
| task_id、attempt_number | Coding/Experiment 必须成对提供；Scientific 必须为空 |
| instruction | 本次唯一自然语言任务指令 |
| input_artifacts | 已登记、同 Run 且 ID 唯一的输入材料 |
| budget | TaskBudget：本次调用的 max_llm_calls、timeout_seconds |
| permissions | AgentPermissions：execute_commands、prepare_environment、request_work |
| workspace、workspace_id、workspace_spec | 物理授权、逻辑工作区身份和来源声明 |
| environment_spec、output_dir | 环境约束和系统指定的输出位置 |
| confirm_before_experiment、experiment_confirmed | 执行确认控制 |
| parent_session_id、resume_artifact_ids | 恢复所属 Session，以及此次 answer 或 work_feedback 工件 ID |

permissions 是操作授权，WorkspaceGrant.mode 是文件访问上限，都不构成业务模式。`request_work=True` 只允许 Scientific；模型不能自行提高权限。可写工作区不强制修改，只读源目录也不禁止通过受控工件通道交付分析。

resume_artifact_ids 必须唯一、属于 input_artifacts 且指定 parent_session_id；仅允许 answer 或 work_feedback，同次不能混用。接收端另外校验工件内容中的 Run、Task/Attempt、Session 与当前调用一致。

<a id="module-result"></a>

### AgentResult

| 字段 | 用途 |
|---|---|
| status | ModuleStatus，确定性机器状态 |
| report | 非空说明，表达发现、结果和局限 |
| artifacts | ArtifactCandidate 或本次所属且已登记的 ArtifactRef |
| session | 子模块拥有的 SessionRef |
| control | 只含 action 和工件定位，不复制业务正文 |
| error、warnings | 结构化错误和非致命警告 |
| llm_calls | 本次实际调用消费；严格非负整数，拒绝 bool |

`ControlSignal.action` 是 ask_user 或 request_work；`artifact_id` 与 `candidate_index` 必须且只能填写一个，分别指向返回列表中的 Ref 或 Candidate。ask_user 必须指向 question，request_work 必须指向 work_request。只有 Scientific 可以返回 request_work。

完成状态不含 error/control；completed_with_warnings 必须有 warnings，completed 不能含 warnings。failed/blocked 必须有 error，不能含 control。needs_user_input/request_work 必须有对应 control 和 paused Session，不能含 error。

接收端重新校验整个结果、Session 所有权、工件归属和实际消费。错误返回不能抹掉已发生调用或已登记工件；report 不能自行决定状态。

<a id="payloads"></a>

### 业务材料的唯一入口

答案、数据集目录、验收要求、工作反馈和科学意见均是工件内容，不再增加独立的领域请求/结果外壳。通用信封只提供相同的输入输出位置；不同领域仍可用明确的内容模型校验 JSON。

<a id="artifacts"></a>
<a id="artifact-models"></a>

## 3. 工件、来源与可信边界

ArtifactCandidate 含 `kind`、相对 `path`、`media_type`、`summary`、可选 `output_name`、`metadata` 和 UTF-8 `content`。有 content 时 path 是存储文件名；无 content 时须在授权工作区或本次 output_dir 解析唯一实际文件，同一路径在两个根目录都存在时拒绝。绝对路径、`..` 和越界软链拒绝。Registry 冻结内容、计算 sha256 并记录来源后生成 ArtifactRef。用于路径验收的 source_path 由实际源文件解析，忽略候选 metadata 中的自报值。

ArtifactRef 含 id、kind、producer、run_id、Task/Attempt 或 Session 归属、uri、sha256、media_type、summary、可选 output_name 和 metadata。Ref 不是自证：返回已有 Ref 时必须与可信登记记录完全一致，hash 正确且属于当前 Attempt 或 Scientific Session。输入材料及其他调用的产物不能冒充本次新输出。

### 系统材料

| kind | 内容 | 归属 / metadata.source_type |
|---|---|---|
| acceptance_requirements | TaskAcceptanceSpec | Task、无 Attempt / task_requirement |
| conclusion_requirements | ConclusionRequirements | Run / conclusion_requirement |
| dataset_catalog | datasets: list[DatasetRef] | Run / dataset_catalog |
| question | 已保存问题对应的内容 | Task+Attempt 或 Scientific Session / controller_question |
| answer | RecordedAnswer | Task+Attempt 或 Scientific Session / controller_answer |
| work_request | assessment + work_request: WorkRequestDraft | Scientific Session / controller_work_request |
| work_feedback | WorkFeedback | Scientific Session / controller_feedback |

以上来源由 Orchestrator 登记；Agent 不得把普通候选工件伪装成系统材料。question/work_request 的候选由控制工具产生，接收端按对应控制语义登记。系统材料的 producer、归属形状和 source_type 在契约层与 Registry 使用同一规则。

Scientific 的普通产物归属 Session，支持 literature_search、scientific_opinion、scientific_assessment、observation_trace 和 module_report；Coding/Experiment 产物归属 Task+Attempt。导入材料和最终报告归属 Run，由 Orchestrator 记录 import/final_report 来源。

### 执行记录的信任范围

注入的进程内 ModulePort 与原生 Agent 的确定性 finalizer 是可信实现。模型 finish 候选不能提交 `execution_record`、`verification_result` 或 `observation_trace`；原生代码从真实命令回执、验证状态和读取观察中生成它们。Coding 的 patch 同样从实际差异生成。

同一 Attempt 的执行结果按命令 argv 归并：仅同一命令后来成功重跑才能解除它此前的失败或超时；不同命令的成功（包括诊断探针）不能覆盖未恢复失败。仅忽略 shell 引号和参数间空白，不推测不同参数或解释器是否等价。Experiment 完成判定和 `require_successful_execution` 验收共用该规则，原始 `execution_record` 保留全部执行。没有执行的纯分析任务仍可完成；要求实际执行时应同时声明所需指标与产物，成功命令本身不证明目标实验已完成。

Controller/Scheduler 只消费公共 AgentResult 和登记工件，核对来源、hash、状态及内容，不读取下游私有 Session。这一边界防止模型叙述冒充执行事实；它不隔离具有任意 Python 执行权限的恶意自定义 Port。替换 Port 必须遵守相同的可信生产约定。

报告用于解释；`module_report` 可保存需要下游分页读取的长说明。报告不替代原始测量、代码或文献。拿到 Ref 不代表已读正文，读过一次也不表示全文始终在模型上下文中。

<a id="scientific"></a>

## 4. 科学控制与工作交付

Scientific 同样接收 AgentRequest 并返回 AgentResult，使用同一 prompt、动作集合和 finish 协议；判断、提问、请求工作和完成是该流程的控制结果。

<a id="work-request"></a>

`WorkRequestDraft` 表达 objective、expected_evidence、constraints、input_artifact_ids，不含 Task 身份、物理路径或调度字段。Scientific 的 request_work 工具同时接收 assessment，将 assessment 与 work_request 配对在一个 work_request 候选中；控制信号定位该工件。Controller 负责分配 WorkRequest 身份并编译。

`ScientificAssessment` 包含 statement、evidence_artifact_ids、limitations、unresolved_questions。ask_user 同样带当前 assessment，便于保留暂停时的科学判断。

<a id="work-outcome"></a>

`WorkOutcome` 记录 work_request_id、workflow_revision、summary 和每项 WorkTaskOutcome；后者保留任务状态、解释、工件 ID、错误和 warnings。失败/阻塞项必须有错误，成功项不含错误。

Controller 将原 WorkRequestDraft、WorkOutcome 和尚未解决的任务结果配对为 `WorkFeedback`，绑定 run_id、work_request_id、session_id，冻结为 work_feedback 工件。Scientific 恢复时由 interpreter 生成 work_brief；它是无 LLM、无 IO 的投影，不拥有执行状态。工件 ID 清单只用于导航，不能当作已读证据。

<a id="opinion"></a>

Scientific finish 使用统一的 report/artifacts，并提交一个 `scientific_opinion` JSON 工件。ScientificOpinion 含 verdict、statement、evidence_artifact_ids、limitations、unresolved_questions、recommended_next_steps；supports/refutes 至少引用一个工件。required_evidence_kinds 来自 conclusion_requirements，当前支持 literature_search。

原生 finalizer 从成功 read_artifact/literature_search 观察生成 observation_trace；Controller 合并合法已观察 ID，最终验收据此检查引用。模型仅把某 ID 写进正文或候选意见不会使它成为“已读”。访问记录也不证明读完全文或论断成立。

<a id="compiler"></a>

## 5. 工作编译、路由与输出绑定

```python
compile(request: WorkRequest, *, current: Workflow | None,
        registry: WorkflowAgentRegistry, budget: RunBudget,
        workspaces: list[WorkspaceDescriptor] | None = None,
        remaining_calls: int | None = None) -> CompilationResult
```

成功返回 CompilationResult(output, llm_calls)，output 为 WorkflowProposal 或 WorkflowPatch；失败抛 CompilationError，并保留实际 llm_calls。Compiler 不读下游 Session，不直接修改 Run 状态。

LLMWorkflowCompiler 请求一个 CompilationDraft，由确定性代码物化正式身份、解析工作区并校验。正文解析或结构校验失败时最多纠正一次，无额外语义复审调用。Compiler 使用 PromptLLMClient 的 JSON 输出路径，无 AgentLoop、工具或 Session；上下文和调用消费仍受共同预算约束。

<a id="workflow"></a>

TaskProposal 包含 id、work_request_id、workflow_agent_kind、instruction、depends_on、workspace_id、已有 input_artifacts ID、未来 input_artifact_bindings、output_names，以及可选 acceptance_spec 和 confirm_before_experiment。

LLM 草图只给逻辑 key、路由、instruction、依赖、逻辑工作区和工件交接，不编造指标键、文件路径、验收策略、操作权限或运行身份。外部确定性调用方可以提供明确的 TaskAcceptanceSpec；自然语言证据要求仍须保留在 instruction 中。

Scheduler 接受时将 output_names 合入 required_output_names，将非空验收要求冻结为 acceptance_requirements。WorkflowTask 与后续每个 Attempt 绑定同一个 acceptance_ref；请求把该 Ref 作为输入材料，不再复制一份可漂移的验收要求。

`depends_on` 表示上游成功，是执行顺序约束。失败修复须在观察真实失败后提出新的 WorkRequest，追加修复与重跑任务。WorkflowPatch 只追加任务，绑定 based_on_revision，不改写历史 Attempt。

`FutureArtifactBinding(source_task, output_selector)` 必须选择直接依赖声明的逻辑 output_name。上游成功后，Scheduler 从其最新成功 Attempt 查找唯一匹配 Ref，再加入下游 input_artifacts。缺失、重复、跨归属或尚未完成的来源均拒绝；模型不能预猜未来 ArtifactId。

<a id="capabilities"></a>

`WorkflowAgentKind` 只有 coding、experiment；WorkflowAgentRegistry 每种路由只登记一次。Scientific 由 Controller 调用，不是执行图节点。模块路由与 capabilities 包里的工具能力是不同概念。

<a id="tools"></a>

## 6. AgentLoop → 工具与运行机制

模型工具协议归 Runtime；通用模型入口由 Capabilities 提供，其公开导出只有 Tool 和对应输入模型。[Coding 验证工具](../../packages/agents/coding/src/resagent2_coding/verification.py)、Experiment 执行工具与各模块控制工具仍由所属 Agent/Runtime 提供。普通操作改从 `resagent2_components` 导入，不通过 Capabilities 转发。Tool 仍按下列协议运行，Python 文件移动不改变模型动作名、参数或公共契约。

ToolRegistry 按动作名找 Tool，以 input_model 完整校验 arguments，再调用 `Tool.execute(state, parsed_arguments) -> ToolObservation`。工具不直接写 AgentState，返回 memory_updates / 候选工件 / 控制信号，由 Loop 应用；但可实际写文件、运行命令或改变 EnvironmentBinding，并非纯函数。

OpenAICompatibleClient 的 AgentLoop 通过 `next_tool_call` 把每个既有 `Tool.input_model.model_json_schema()` 作为原生 `tools` 参数的完整参数 schema；无 `next_tool_call` 的测试或注入客户端继续走 `next_action`，并收到从同一 input_model 派生的必填顶层参数与 guidance，作为 required `tool_contracts`。无论走哪条路径，供应商返回都不替代执行前的 ToolRegistry 完整校验。

| 可注入入口 | 约定 |
|---|---|
| AgentLoop.run(definition, request, *, session_id, initial_memory=None) | 循环、观测、反馈、Session，不调度 Workflow |
| ContextBuilder(request, state, max_context_tokens) | 返回固定ContextSection及按需渲染的ContextMaterial；Runtime补工具契约/反馈/历史，builder不预占独立硬额度 |
| ContextComposer.compose(...) | 先保留固定段与材料导航框，再按权重分配、按优先级借用；完整请求统一计量，最小required仍装不下明确失败 |
| LLMClient.next_action(context, action_type) | 最小客户端必需方法；供测试/注入客户端及正文 JSON 调用方返回候选 dict/模型 |
| OpenAICompatibleClient.next_tool_call(context, schemas, turns, ...) | AgentLoop 原生工具调用；返回一轮 assistant/tool-call 协议数据，不自行执行 Tool |
| OpenAICompatibleClient.summarize_history(prompt, max_input_tokens=...) | 可选纯文本历史交接；共用传输/trace/attempts，不执行工具，输出配置仍来自 ModelProfile |
| PromptLLMClient.next_action(prompt, action_type) | 普通提示复用 Composer/计量，无 Tool/Session/Loop |
| PermissionPolicy.check(action, state, request) | 派发前确定性允许/拒绝，不是 OS 沙箱或人工审批 UI |
| SessionStore | 内部状态/事件持久化；上层仅持有引用 |

LoopRequest 只要求身份、预算、父 Session 等运行信息；Scientific 的 task/attempt 可为空。领域指令、工件和授权由注入的 builder、工具、finalizer 使用。EnvironmentBinding、WorkspaceSnapshot 留在 components，不变成 wire 消息。

参数错误、ok=False 和执行时 PermissionError 等可恢复错误进反馈；PermissionPolicy 明确拒绝则立即 permission_denied。未知工具走既有拒绝策略，不放宽 schema。Action 校验前只移除旧 reasoning_summary 字段，不忽略其它未知字段。

**协议与格式纠错**：OpenAICompatibleClient 区分响应封装/传输失败与模型输出拒绝。前者保持现有至多三次尝试（受剩余额度限制）；正文 JSON 解析失败，或原生 tool arguments 不是 JSON object，则完成本次 trace 后直接交回 Loop，不在客户端原样重试。原生每轮接受 1–8 个 tool calls，整批先做参数/权限预检，再逐项复核权限/超时并串行执行。finish/ask_user/request_work 必须单独一轮；零个、超量或混合控制工具的批次拒绝。中途失败保留已完成结果并取消余下项，不做事务回滚，assistant `content` 不作为备用动作解析。AgentLoop 记录已发生的全部 HTTP 尝试，把简短原因和“未执行工具”送入 required `runtime_feedback`，同 Session/Attempt 继续；不把坏正文或 reasoning 复制到反馈。JSON、原生 framing 和 schema 错误共用连续失败上限 5、LLM 调用预算和超时；成功非 finish 工具清除该反馈并重置失败计数，完成时也清除反馈。耗尽后沿用已有失败出口，不保证模型一定纠正成功。

Compiler 不运行 AgentLoop，也不使用原生工具：编译草图经 `PromptLLMClient.next_action` 从正文 JSON 获取结构。其 JSONDecodeError 在已有“最多两版 draft”内携带解析原因重编，所有消耗保留；没有新一层重试。PromptLLMClient 只透传异常并记录 last_attempts，不自行纠错。JSON 能解析但字段不符仍走原有 schema 校验。响应封装缺失/非字符串 content 等协议错误不伪装成模型正文解析错误。

**原生 Session 与恢复**：创建 Session 时写入 `AgentState.tool_protocol_key`。正文 JSON 客户端固定为 `None`；OpenAICompatibleClient 的 `tool_session_key` 是协议、endpoint、model 的稳定 hash，不含 API key；其他声明 `next_tool_call` 的客户端必须同时提供非空、稳定且足以标识其协议配置的 `tool_session_key`。恢复时当前客户端身份必须与 Session 完全相同，拒绝旧 JSON→原生、原生→JSON及原生 endpoint/model 变化，不自动迁移。

`AgentState.tool_turns` 按轮保存 assistant `content`、`reasoning_content`、原始 `tool_calls` 和按 call ID 配对的 `tool_results`。Loop 先保存整批 call，每次派发前保存 executing_call_id，随后按 observation/event 路径配对该调用的 receipt 并清除执行标记。重启保留已完成回执；正在执行且缺回执的项记为 unknown outcome，其余缺回执项记为未开始；不自动重放。它防止把未知结果误判为成功，但只保证进程重启 checkpoint，不保证掉电持久化，也不是外部副作用的 exactly-once 事务。

下一轮原生请求由系统指令、已配对的历史 assistant/tool 消息和最新一次重建的业务 Context 组成；不保存或重发此前每轮完整业务 prompt。输入压力下使用可选 summarize_history 生成旧完整交互摘要；history_checkpoint 同时保存摘要与绝对历史边界，近期完整回合继续原样发送。原始历史不删，摘要不替代当前领域状态、回答、证据或完成门禁；失败不推进边界。具体比例和失败边界见[上下文](CONTEXT.md#compaction)。`reasoning_content` 仅用于同一 Session 的供应商协议续传，不进入业务 memory、ToolObservation 或完成证据。

读取通常可重复；写入和外部命令不承诺 exactly-once。read_file/read_artifact 共用行切片，Artifact 先核对整份 hash。search_text 是大小写不敏感字面子串，非正则，a|b 按原文匹配。

**容量**：ModelProfile 声明窗口、输出预留、安全余量，模块声明输入上限；有效额度取模块与剩余模型容量之小值。正文 JSON 路径计量渲染后的 Context，Action schema 另在有Profile时从模型容量预留，不计入Context的estimated_tokens；原生路径计量 `messages + tools` 完整 JSON 序列化，包括历史、schema 与转义开销。均使用字符/4近似；三个Agent及Compiler默认均为128000。不另加隐藏调用额度；压缩、动作和重试共用 Run 剩余 calls，step 仅记录时序。required 保持顺序，optional 按优先级稳定选入；大可选段放不下不阻挡后续小段。不查询或按模型名猜容量，不新增长期记忆系统；只对旧协议历史做共享的有损检查点。

<a id="components"></a>

### 普通调用方 → Components

这一层是进程内 Python 调用，不是原生 function call，也不新增远程协议。Tool、Agent 准备/完成检查、CLI/E2E 可直接使用；不强制一个 Tool 对应一个组件。Runtime 不依赖这里。

| 入口 | 输入 → 输出 / 副作用 | 失败与边界 |
|---|---|---|
| [WorkspaceBoundary](../../packages/components/src/resagent2_components/workspace.py) | 工作区授权 + 相对路径 → 已检查 Path / 文件清单 | 路径与软链越界拒绝；不代替 OS 沙箱 |
| [GitWorkspace](../../packages/components/src/resagent2_components/git.py)、[WorkspaceObserver](../../packages/components/src/resagent2_components/snapshot.py) | 工作区、Attempt 基线 → 变化路径 / patch / snapshot | 保持原基线与归属规则；不以模型说明推断修改 |
| [RepoMaterializer](../../packages/components/src/resagent2_components/repo.py) | 仓库来源、目标工作区 → MaterializedRepo，可准备仓库文件 | 来源/目录校验失败抛明确错误；不决定任务图 |
| [ProcessRunner.run](../../packages/components/src/resagent2_components/process.py) | 命令、目录、超时和环境 → VerificationResult，落 stdout/stderr | shell 组合拒绝，超时终止进程树；结果是执行事实，不是科学结论。历史类型名不在本次调整 |
| [EnvironmentManager / Binding](../../packages/components/src/resagent2_components/environment.py) | Run/workspace、Python 版本 → 环境及认证状态 | 环境失效/代次更新规则不变；SetupCommandPolicy 限制安装入口，Coding 验证策略不在这里 |
| [DatasetCatalog / resolve_dataset_refs](../../packages/components/src/resagent2_components/dataset.py) | 部署目录 / Run 引用 → 登记引用 / DatasetAvailability | 不下载、不猜准备状态；资源缺失怎样询问仍由 Agent 决定 |
| [ResourceLayout](../../packages/components/src/resagent2_components/resources.py) | 部署配置 → 数据集与环境根目录 | 不管理 pip/conda 下载缓存，不成为 ResearchRequest 字段 |
| [RegisteredArtifactReader / build_module_report](../../packages/components/src/resagent2_components/artifacts.py) | 授权 ID + 行范围 → 正文；模块说明 → ArtifactCandidate | 读取先校验 Run 与整份 hash；报告生成纯函数，不自行登记或生成科学证据 |
| [LiteratureSearchBackend](../../packages/components/src/resagent2_components/literature/backends.py) | 查询、数量和年份 → 规范化论文列表 | 两源保持平级切换；服务不可用与合法空结果区分，不伪造全文 |
| [workspace_context](../../packages/components/src/resagent2_components/context.py)、[文本切片](../../packages/components/src/resagent2_components/text.py) | 现有事件、绑定、授权及材料额度 → 段 / 材料 / 文本窗口 | 不启动 LLM、不写第二份状态；预算分配仍归 Runtime，详见 [CONTEXT](CONTEXT.md#budgets) |

文献 Tool 通过工件组件中的 `ArtifactRegistrationPort` 接受组合根注入的登记/解析对象；实现仍是 Orchestrator 的 ScientificArtifactRegistration，Components 不反向 import Orchestrator。错误如何转为 ToolObservation、反馈、ModuleError 仍由已有 Tool/Loop/Agent 边界处理，组件不另建恢复机制。

<a id="trace"></a>

### LLM 计量与 trace

最小客户端只有 next_action；context_budget、set_attempt_limit、last_attempts、set_trace_context、record_validation 等 hooks 按存在与否使用。内部有重试应提供计量与限制；无 hook 按每请求一次计数，不宣称获知隐藏重试。

OpenAICompatibleClient 的 trace 按 call_id 关联逻辑调用和后续校验记录。attempts 保留各次 finish_reason/usage/错误，顶层响应对应最后一次；retry_number+1 是 HTTP 尝试数，不能再加 attempts 长度。request_max_tokens 是实际输出上限，null 表示未指定。

收到格式反馈后的请求是新逻辑调用、新 call_id，不是上一调用内的 HTTP retry。正文/原生参数解析和调用数量错误写在主记录/attempts 的 validation_error；schema_validation_error 补充行记录已解析候选的外层 schema 错误，以及原生调用身份/回执校验拒绝。主记录按带 model 的行识别，不把补充行计成另一次调用。统计解析失败、候选校验、HTTP retry、Task Attempt retry 时分开计数。

off 不记录；metadata 不保存请求/响应/源码正文，对这些内容只保留相应 hash（原生 tool calls 为 `tool_calls_sha256`）；full 保存原始 request/response，并在 provider 提供时保存 `raw_tool_calls` 与 `raw_reasoning_text`，包括可取得的失败响应。trace 与 Session 是独立边界：metadata 对内容只留 hash 不表示 Session 不保存 `tool_turns`；其中 reasoning 只用于同 Session 协议续传，不是业务证据。Session 与 trace 目录/文件都按 `0700/0600` 管理，Session 私有权限不依赖 trace 档位；full 仍可能含源码和用户输入，不是可公开上传的日志。

空 JSON 只是一种现象，先查 finish_reason、usage、输出额度，不直接定性模型漂移；未返回的信息为未知。单工具 parsed_action 为对象，多工具为对象数组，名称另有 tools 列表；压缩行 included_sections 含 compaction，action_valid/tool/parsed_action 为 null，不是无效工具动作，但仍计调用。action_valid 不证明参数、执行或结论正确，应结合校验补充记录与 Session 观测。部署配置见 [CLI README](../../apps/cli/README.md#6-模型与上下文预算)。

**源码与测试**：[ToolRegistry](../../packages/runtime/src/resagent2_runtime/tools.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[Context](../../packages/runtime/src/resagent2_runtime/context.py)、[工具契约](../../tests/runtime/test_tool_contracts.py)、[guards](../../tests/runtime/test_guards.py)、[恢复](../../tests/runtime/test_resume.py)。

<a id="runtime-context"></a>

### 运行时反馈与上下文

本节说明调用约定；各模块哪些字段实际进入模型、如何呈现和刷新，以及工具结果与上下文片段的区别，见 [模型上下文](CONTEXT.md)。`required` 与 `priority` 的实际选择和排序规则见 [共同构造流程](CONTEXT.md#pipeline)。

`ToolObservation.ok` 是机器可读的成功标志：成功读取/命令为 True，失败命令（非零退出）、参数拒绝、路径缺失等可恢复失败为 False。下游不得靠解析 `summary` 文本判断失败。AgentLoop 的反馈语义：

- Loop 生成的动作拒绝、工具异常或完成检查拒绝可形成持久 `runtime_feedback`（`ok=False`），存在时插在其他领域段之前，作为 required 上下文注入。普通 Tool 返回的 `ok=False` 观察不自动全部转成此反馈；`tool_error` 来源的旧反馈在工具正常返回 observation 后清除，完成检查来源的反馈按完成检查流程更新/清除。不能据此假定所有失败命令的完整诊断都常驻；
- 已配对的 RecordedAnswer 冻结为 answer 工件；调用方用 resume_artifact_ids 交付本次恢复材料。三个 Agent 共用 request_materials_context，原题 question_text 与回答 values 一起进入必需材料段，仍由 ContextComposer 计量，装不下时沿用 ContextBudgetExceeded。`ask_user` 的成功观测只代表已发问，不代表已收到答案或前提已经满足；历史工具结果也可能早于当前回答与资源刷新；
- 正文 JSON 客户端使用 `recent_observations` 有界最近历史（默认 6 条），以原始事件编号从旧到新呈现；value 是约 400 字符的短预览，用 head+tail 截断序列化值，不保证所有字段完整。原生客户端改为重放 `tool_turns` 中检查点之后已配对的 assistant/tool 消息，并在末尾加入最新业务 Context；两者都不把历史 prompt 当第二套记忆；
- Agent 需要保留文件正文等领域观察时，统一使用 runtime 的 `recent_tool_snippets`（以 (path, start_line, end_line) 为片段身份、最新片段优先完整装入，仅截断装箱的最后一段；选入后按原始事件顺序从旧到新呈现），分别进入 `file_reads` / `artifact_reads` 材料。导航框required，正文弹性分配；各含snippets、previously_read及content_omitted。`recent_tool_listing` 保留最近有界目录清单，不截断单个路径；directory可选（priority=62）。这只是本轮模型输入，旧workspace_reads trace及Session原事件不改写；
- 片段 `observed_at` 复用 AgentEvent.sequence；`truncated` 表示呈现正文是否不完整，`context_truncated=true` 另标记工作集预算截断。components 仅对有后续同路径成功内置写入的文件片段附 `modified_after_read_at`，不清空旧片段、不标记冻结 Artifact、不把失败动作当修改。无标记不保证文件仍是磁盘当前版本。这些是上下文投影字段，不修改 ToolObservation、跨模块契约或 Session 原记录；
- 三个Agent及Compiler默认输入上限同源为128000 tokens，模块分别可配置；CLI与real E2E的Compiler复用同一默认常量，Compiler仍无Session或历史压缩。固定段、工具schema、完整历史与材料导航框先计量；剩余材料空间按文件/工件/诊断/目录16/16/4/1相对权重起步，再按priority借用空余，扩展至整包80%软水位。必需固定内容可超过软水位但不超过总硬上限。正文JSON请求计完整section，原生请求计完整messages+tools及转义；最终仍不足就报错，不自动扩容/暂停/追加摘要重试。默认工具返回上限128000字符是独立IO边界，不是tokens容量；详见[上下文预算](CONTEXT.md#budgets)；
- 共享command_results从原事件中选择run_verification/run_setup/run_command各自最近一次带命令结果的观察。先选失败命令及stdout/stderr尾部，再限长，标记事件号、裁剪和省略数量；有结果时为required，不依赖400字符历史预览。它是执行诊断，不替代当前状态或完成校验；原事件和日志不删除；
- directory附observed_at并明确是历史目录观察，创建文件不会自动重写旧清单。Coding控制投影用edited_since_verification表达编辑/验证版本差，不再把它叫workspace_changed；这些是模型可见投影，不增加业务schema字段；
- 按行读取的工件工作集从 Session 工具观测投影；要求和当前恢复材料则由共享读取函数直接校验并装入必需段。已读 ID、工件说明和检索短预览不是完整正文，也不是当前论断的支持证明；需要精确内容时按工件行范围读取。冻结工件、原始观测与 full trace 不因工作集淘汰而删除；
- 共享客户端的每次 HTTP 尝试（含重试）都计入 `llm_calls`；AgentLoop/Compiler 通过可选的 `set_attempt_limit`/`last_attempts` hooks 限制并计量实际尝试。最小 LLM 客户端只须有 `next_action`，无计数 hook 时一次调用按一次计；自带内部重试的实现应提供这两个 hooks；
- 工具派发前重新检查 wall-clock 余量；LLM 或权限检查已用尽时间时，不再派发工具，已发生调用仍入账。这不等于能撤销或抢占已经执行的外部操作；
- 一条 ToolObservation 的 `question`、`request_work`、`finish_candidate` 至多一个非空；普通观察可以全为空；
- 连续失败计数：成功的非 finish 工具重置；`ok=False` 累加；completion check 拒绝的 finish 也累加；连续 5 次失败返回 `TOOL_FAILED`。这是有界纠错的停止条件，不是第二套任务步骤预算。

LLM trace 的 `action_valid` 表示响应已解析出候选动作：单工具 parsed_action 为对象，合法原生多调用为对象数组并另记 tools 名称列表，不只挑其中一个标有效。外层 Action schema 错误另以同一 `call_id` 记录。它不证明整批预检通过、Tool 执行成功或科学结论有效；须结合 validation 记录和 Session 中的 observation/completion 结果阅读。摘要调用不产生动作，action_valid 为 null。

<a id="completion"></a>

## 7. 完成与验收

统一的 finish 只提交 `report` 和 `artifacts`。它是完成提议，不能自行设置最终 status 或提交另一套机器结果。

- Coding 观察本 Attempt 的实际差异，生成 patch、变更文件与已有验证记录，标明验证是否覆盖当前代码和环境。分析任务可以无修改完成，未执行验证不能被写成已通过。
- Experiment 可分析已有结果；执行后由代码生成 execution_record。最新实际命令失败会返回失败及诊断，后续成功命令可以取代该失败状态，旧记录仍保留。
- Scientific 校验意见、工件授权和已观察引用，并生成 observation_trace。

Scheduler 根据冻结的 TaskAcceptanceSpec 检查本 Attempt 的交付：required_metric_keys 必须是 JSON 顶层有限数值（排除 bool）；required_artifact_paths、required_artifact_kinds、required_output_names 必须实际存在。require_successful_execution 需要可信的成功 execution_record 或覆盖当前代码的 verification_result；报告文字不计作执行证据。未明确要求的检查不会从 Agent 名称或任务文本猜测出来。

<a id="final-report"></a>

最终 Run gate 校验科学意见、证据归属、观察记录、所需证据种类及未解决工作对应的局限。通过后由确定性报告渲染器登记最终报告，再将 Run 标为 completed。inconclusive 可以是合法完成；Run completed 不保证假设成立或科学结论正确。

<a id="identities"></a>
<a id="attempt-session"></a>

## 8. 身份、状态、资源与版本

Run 表示完整研究请求，WorkRequest 是其中一轮需求，Task 是图节点，Attempt 是一次执行尝试。Scientific Session 属于 Run、跨工作回合复用；Coding/Experiment Session 属于 Run+Task+Attempt。SessionRef 只暴露 id、module、state_uri、status、created_at、updated_at，不把私有状态变成上游接口。

暂停回答沿用 Attempt；retry 新建 Attempt。Attempt 保存 report、artifact_ids、SessionRef、error、acceptance_ref 及起止时间。running/needs_user_input 不含终止时间或错误；终态必须有 finished_at，failed/blocked 必须有 error。编号连续且从 1 开始。

<a id="states"></a>
<a id="state-mapping"></a>

| 对象 | 状态 | 所有者 |
|---|---|---|
| Run | pending / running / paused / completed / failed | Controller |
| WorkRequest | requested / compiling / executing / stable / consumed / failed | Controller |
| Task | pending / running / completed / failed / blocked / needs_user_input | Scheduler |
| Attempt | running / completed / completed_with_warnings / failed / blocked / needs_user_input | Scheduler |
| AgentResult | completed / completed_with_warnings / failed / blocked / needs_user_input / request_work | Agent，接收端校验 |
| Session | active / completed / failed / blocked / paused | Agent/runtime |

completed_with_warnings 作为成功 Task 参与依赖，但保留 Attempt 状态和 warnings。needs_user_input 暂停 Run；request_work 仅用于 Scientific，将控制交回 Controller。Run 是否完成由最终 gate 决定。

JSON RunStore 适合单进程、单写入者；单个快照可原子替换，但 Run、Session、命令和文件不是共同事务。中断不保证外部命令未发生，失败不自动回滚代码或环境。原生 Scientific 按恢复材料身份复用已完成交付的结果，重复返回的新增 llm_calls 为零；这不推广为所有 Port 或外部工具的 exactly-once 保证。

<a id="workspace"></a>

### 工作区

```python
class WorkspaceGrant:
    root: NonEmptyStr
    mode: WorkspaceMode
    allowed_paths: list[str] = []
    denied_paths: list[str] = []
    source: WorkspaceSourceKind

class WorkspaceSpec:
    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    location: str | None = None
    environment: EnvironmentSpec | None = None
    mode: WorkspaceMode = WorkspaceMode.READ_WRITE

class WorkspaceRecord:
    workspace_id: WorkspaceId
    root: NonEmptyStr
    source: WorkspaceSpec
    managed: bool = False

class WorkspaceDescriptor:
    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    description: str = ""
```

`WorkspaceSourceKind`：GIT（clone 到受管目录）/ LOCAL（原地绑定，managed=False）/ COPY（复制已有本地 Git 工作树）/ GENERATED（创建空受管工作区）。

`WorkspaceSpec` 是逻辑来源声明，`location` 可包含仓库 URL 或本地来源路径，但不是 Attempt 的物理授权；`environment` 是 workspace 级的环境约束（上游指定 Python 版本时为硬约束）。`WorkspaceRecord` 是解析后的记录，`managed` 由 source_kind 派生（非 LOCAL 为 True）。`WorkspaceDescriptor` 是 Compiler 可见的最小工作区摘要，不含物理路径。

components 提供一个内部 `WorkspaceSnapshot`（Git workspace 用 `GitBaseline` 的 tree hash，非 Git workspace 用有界 file-hash fallback）表达 Attempt 起点；Coding 的差异与验证新鲜度检查使用 Attempt 基线；环境或代码变动不能由旧验证冒充当前状态。

<a id="resources"></a>

### 数据集与环境

```python
class DatasetRef:
    dataset_id: NonEmptyStr
    relative_path: str

class EnvironmentSpec:
    python_version: str | None = None
```

`dataset_root`（`ResourceLayout.dataset_root`）是所有数据集的共享根，不是某个数据集目录。部署者在根下 `catalog.json` 维护 `dataset_id → relative_path`；CLI/E2E 各自把 DatasetCatalog 经现有 DatasetRefSource Port 注入 Controller。调用方不提交目录路径或完整表。Controller 在推进回合及回答后的恢复入口读取目录，将新增引用保存到 `ResearchRun.dataset_refs`，不改写 ResearchRequest。

Run 中保存的是本 Run 累计发现的目录引用，不是实际使用清单、完整数据内容或数据版本快照。已有同名引用不允许改路径，不因后来删掉 catalog 条目而撤销 Run 中已有引用；但实际目录的存在性会重新检查。数据内容不复制、不做整库 hash，不承诺目录内部数据未被外部修改。

Controller 把目录引用冻结为 Run 级 dataset_catalog 工件；Controller/Scheduler 经 AgentRequest.input_artifacts 传递当前目录，三个 Agent 注入同一 ResourceLayout，各次调用通过共享 `resolve_dataset_refs` 检查。它返回一个轻量 DatasetAvailability：available 为 `{dataset_id, path, access="read_only"}` 列表，unavailable_ids 为目录暂不存在的 ID。上下文与命令环境映射使用同一个检查结果，不维护第二份可用性状态。

- catalog 缺失意味着当前没有新登记；已登记目录缺失标为不可用，不阻塞无关工作。
- 共享上下文明确区分 `available_dataset_ids`（已登记且目录存在）与 `unavailable_dataset_ids`（已登记但目录不存在）；两边都没有的 ID 在当前视图中未登记，不表示可用。当前任务需要的 ID 不在 available 列表时，应先 ask_user 再做依赖该数据的工作，不能只检查 unavailable 列表。
- 非法 JSON/登记格式、重复 ID 引用、绝对或越界路径（含软链逃逸）仍明确报错。
- 只有可用目录进入 `RESAGENT2_DATASETS_JSON` 的 ID→路径映射；该变量是 JSON 内容，不是 catalog 文件路径；`catalog.json` 固定在 dataset_root 下。Coding 验证与 Experiment 正式命令均获得该映射及 `RESAGENT2_DATASET_ROOT`。
- Agent 在运行中判断需要什么；缺少所需数据时用已有 ask_user，用户放置并登记后回答。恢复时重新检查，口头“已准备”不使目录自动变为可用；所需数据仍不在 available 列表时应再次询问，曾经问过不等于可以继续依赖该数据的工作。旧命令结果描述当时的资源状态，不覆盖本轮重新检查的视图。
- 目录存在只证明可定位；内部文件缺失或内容错误仍需从实际读取诊断。prompt 要求请求用户处理，不下载、不猜路径、不替代数据；这是行为指引，不是 OS 沙箱或强制资源选择器。

不新增资源 Agent、通用 Resource 类、自动扫描或下载器。当前目录规模用精简 ID 上下文；不宣称大规模资源检索或实时热更新。实际恢复路径和验证见 [资源验收单](../history/reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。

环境能力由 Coding 与 Experiment 共用（ADR-0009）：

依赖需求可由代码和运行时反馈发现，沿用 prepare_environment → run_setup → audit_env。镜像与 pip/conda 包缓存属于部署/包管理器配置，不新增到 ResearchRequest，也不与数据集登记表合并；同名依赖或缓存命中不代替环境审计。

新 Run/Workspace 绑定可能需要创建环境并重新安装依赖；包管理器缓存不等于可直接复用的已认证环境，也不保证无需网络或安装开销。安装计入 Run 执行时间。长任务中的实际成本及待调查项见 [L3 待办 O2](../history/reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md#follow-ups)。

- `EnvironmentSpec.python_version` 有值表示硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断；
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用），不同 Workspace/Run 隔离；`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`；
- 三个共享 Tool（capabilities 的公开 Python API）：`prepare_environment` / `run_setup` / `audit_env`。新绑定或真正开始 prepare/setup 时，`EnvironmentBinding.generation` 更新且 `certified=False`；执行成功、失败或抛异常都不能保留旧认证，参数/策略拒绝则不改变代次；
- Coding 的成功验证还须属于最新 edit revision、当前已审计的 generation。setup 后或新进程恢复后，只重新 audit 不会让旧验证复活，必须再验证；
- Python 版本优先级、硬约束不可覆盖、每 Attempt 最多两次版本切换：见 ADR-0009。

<a id="schema"></a>

### schema 版本

Python 包版本与 wire schema 独立演进。公共模型当前仅接受 12.0，字段删除、含义或必填性变化需要不兼容版本，并覆盖 round-trip、非法组合和恢复边界测试。metadata 不长期承担本应成为正式字段的机器状态。

本版统一 AgentRequest/AgentResult，任务改用 instruction 和模块路由，移除模式专属输入输出与独立科学回合信封。答案、反馈、目录和验收要求通过工件传递；不保留旧版兼容运行分支。

ResearchRun 顶层没有 schema_version，但必填 request 等公共模型带版本；JsonRunStore.load 重新校验整个 Run，旧版本 Run 拒绝恢复。读取失败不改写原文件，应创建新 Run。已有 state/session/trace 保留，不迁移、不重写、不自动清理。

AgentState 继承不带公共版本字段的 RuntimeModel；某些旧 Session 仍能单独解析，不代表支持恢复旧 Run。若嵌套公共模型含旧 schema，会在相应校验处拒绝；原生 Session 还必须匹配创建时的协议、endpoint 和 model 身份。memory/events.data 的 JSON 可解析性不构成业务兼容承诺。

<a id="exports"></a>

### 公共导出

完整导出见 [contracts 包入口](../../packages/contracts/src/resagent2_contracts/__init__.py)。主要分组是 AgentRequest/AgentResult/AgentPermissions/ControlSignal、工件与要求内容模型、工作流与绑定、身份状态、资源授权，以及 WorkRequest/WorkOutcome/ScientificOpinion。Runtime 的 AgentState、ToolObservation、FinishCandidate、CompletionDecision 和 ContextSection 仍由 runtime 定义；内部工具观察不等于跨 Agent 结果信封。
