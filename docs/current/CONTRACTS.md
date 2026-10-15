# 模块接口与契约

当前公共契约为 **schema 27.0**。三个 Agent 共用 `invoke(AgentRequest) -> AgentResult`：业务输入是 `instruction + input_artifacts`，业务输出是 `report + artifacts`。身份、权限、预算、状态、恢复和控制信号保持结构化。每个 Agent 只有一种调用和业务模式。

本页说明调用边界、字段和接收规则。职责看 [架构](ARCHITECTURE.md)，模型可见内容看 [上下文](CONTEXT.md)，公共模型以 [models.py](../../packages/contracts/src/resagent2_contracts/models.py) 为准。当前入口为进程内 Python 方法。

## 先认识接口中的对象

一次研究从用户提交需求开始。Controller 调用 Scientific 判断下一步；Scientific 可以直接使用工具，也可以通过 `request_work` 提交工作需求。后者含 Scientific 明确生成的 Tasks；Compiler 只匹配 Agent，固定代码组装图，Scheduler 调用 Coding/Experiment 执行，结果再交回 Scientific。工件登记和状态保存贯穿这些调用。

| 层次 | 对象 | 解决什么问题 |
|---|---|---|
| 用户入口 | `ResearchRequest`、`ResearchRun`、问答 | 要完成什么，以及整次研究的授权、预算和当前状态 |
| 统一 Agent 调用 | `AgentRequest` → `AgentResult` | 给一个 Agent 指令和材料，接收报告、产物或控制结果 |
| 工具与材料 | `ToolObservation`、`ArtifactCandidate` → `ArtifactRef` | 一次工具动作得到什么；哪些内容已冻结登记、可按 ID 读取 |
| 工作编译与执行 | `WorkRequest`、`Workflow`、`Task`、`Attempt` | 将科学工作需求转为可执行任务，并记录每次真实尝试 |
| 返回与持久状态 | `WorkResult`、`ControlSignal`、`SessionRef` | 向 Scientific 交付执行事实；恢复时沿用正确的任务和会话 |

几个容易混淆的名称：`WorkRequest` 是 Scientific 请求工作，`AgentRequest` 是系统实际调用某个 Agent；`ToolObservation` 是 Agent 内部的一次工具回执，`AgentResult` 是整个 Agent 调用的对外结果。`ArtifactCandidate` 尚未登记，`ArtifactRef` 指向已冻结的原件，索引只负责导航。下面先查调用边界，再查对应字段和接收规则。

<a id="boundaries"></a>

## 按调用边界查找

| 调用方向 | 入口 | 输入 → 输出 | 位置 |
|---|---|---|---|
| 用户入口 → Controller | create_run / import_literature / answer_question / resume_run / run_until_stable；控制 inbox | ResearchRequest / PreparedLiteratureImport / UserAnswer → ResearchRun | [用户与控制](#entry) |
| Controller → Scientific | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module)、[科学决策](#scientific) |
| Controller → Interpreter | WorkInterpreter.interpret | WorkRequest + 实际 Task/Attempt/Ref → WorkResult | [反向交接](#interpreter) |
| Controller → Compiler | WorkflowCompiler.compile | WorkRequest + 图/路由/执行限制 → CompilationResult | [工作编译](#compiler) |
| Scheduler → Coding / Experiment | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module) |
| AgentLoop → ToolRegistry → Tool | dispatch / execute | arguments → ToolObservation | [工具与运行](#tools) |
| Tool / Agent / 组合根 → Components | 普通 Python 调用 | 授权、资源、命令、事件 → 操作结果或内容投影 | [普通组件](#components) |
| AgentLoop → 领域完成检查 | CompletionCheck.evaluate | 真实记录 + 完成提议 → CompletionDecision | [完成验收](#completion) |
| 生产方 → Registry；Agent → reader | register / read_text | Candidate → Ref；授权 Ref → 内容 | [工件](#artifacts) |

<a id="conventions"></a>

公共模型继承 `ContractModel`，拒绝未知字段并校验版本。方法签名只规定调用形状；接收端还须校验身份、归属、状态和实际消费。机器状态不从报告文本推断，类型合法也不证明科学结论正确。

`NonEmptyStr` 在校验时去掉首尾空白，并拒绝空值；例如 `ModuleError.message` 不能只包含空白。原始需求字段（WorkRequest 的 objective/constraints、Task instruction、shared_goal/shared_constraints、业务补充和 AgentRequest instruction）以及其结果导航使用 VerbatimText：拒绝纯空白，保留原文全部首尾空白与换行。原始 report 使用独立的非空字符串约束，保留首尾空白。Controller/Scheduler 将异常转为公共错误时，使用去空白后的异常消息；没有消息时使用异常类名，保证失败本身也能被合法保存。

<a id="entry"></a>

## 1. 用户入口与问答

`ResearchController` 是唯一 Run 业务入口：

- `create_run(run_id, request, *, literature=())` 创建 Run、登记可选导入论文，并推进到完成、失败或等待用户。
- `import_literature(run_id, literature)` 只给已暂停 Run 增加论文材料。
- `answer_question(run_id, answer)` 校验当前问题、记录回答，再继续同一个 Run。
- `run_until_stable(run_id)` 由单一 owner 推进可恢复执行，不制造答案或绕过用户主动暂停。
- `resume_run(run_id)` 明确继续用户主动暂停的 Run；不能代答仍有效的待答问题。
- `RunControlInbox.submit` 追加本 Run 的 input/interrupt 小记录。CLI 的 input、interrupt 和 Shell 共用此入口，不直接改整份 Run/Session。

Scheduler 只执行 Controller 接受的任务图，不创建第二条 Run 控制链。CLI 与真实 E2E 共用 `build_application` 装配依赖；完整研究场景使用 Controller，定向 Agent 场景使用应用中已装配的 binding。

<a id="research-request"></a>

### ResearchRequest：研究目标与 Run 边界

`ResearchRequest` 包含 `goal`、可选 `hypothesis`、`context`、`constraints`、`input_artifacts: list[ArtifactImport]`、`required_evidence_kinds`、`required_artifacts: list[OutputName]`（默认空），以及下列 Run 控制字段。创建 Run 时保存授权与执行限制，内部调用只能继承或收紧。

| 字段 | 含义 |
|---|---|
| `budget: RunBudget` | 必填；`max_llm_calls`、`timeout_seconds` 均为正整数，控制模型请求次数和有效运行时长 |
| `execution_limits: ExecutionLimits` | `max_tasks=8` 限制累计图节点数；`max_attempts_per_task=2` 包含首次执行，不是另加两次重试；均为正整数 |
| `permissions: RunPermissions` | 必填；`execute_commands`、`prepare_environment` 默认均为 False，可信入口负责明确授权 |
| `confirm_commands: bool` | 默认 False；启用后，对权限允许的 Agent 顶层外部操作逐次询问 |

明确等待用户的暂停时间不计入超时；安装、下载、命令、模型等待和普通进程停机仍计入。CLI 默认明确授权执行命令和环境准备，可分别关闭；这不改变公共契约的默认拒绝语义。

### 共享预算与调用计量

`ResearchRun.usage.requests` 按 `call_id:retry_index` 保存模型请求占用及 `succeeded / failed / unknown` 结果，`llm_calls_used` 从中计算。发送前先原子保存占用；保存失败不发送，登记后中断不退款。HTTP 重试、格式纠正、摘要、Compiler 和三个 Agent 共用此用量。结果中的 `llm_calls` 用于诊断，不再扣费。单个 Run 只支持一个执行者。

Controller/Scheduler 为调用绑定 `runtime.budget.execution_budget`，嵌套调用共享用量并取更早截止时间。TaskBudget 是当前余额的调用上限快照，不是第二份钱包。模型及文献 HTTP、退避、环境准备和受控子进程都受剩余时间约束；到期取消请求或终止进程树。本地取消不保证供应商停止计费，未知结果保留占用。

当前 Scheduler 将 Run 剩余调用数和时间传给下一次 Agent 调用，没有预先给各 Task 分钱包；等待回答、重试或追加任务都不重置 Run 用量。脱离 Controller 单独调用原生 Agent 时，TaskBudget 只限制该次调用；需要跨调用累计时，由可信调用方绑定共享执行预算。

调用者用 ArtifactImport 提交本地输入的 URI、kind、media_type、summary、metadata 和可选 expected_sha256。Controller 校验并冻结为已登记工件。数据集目录由部署环境提供，不是 ResearchRequest 的逐次路径参数。

### 外部论文导入

CLI 的 `load_literature_manifest(path)` 将本地清单规范化为 `PreparedLiteratureImport(paper, pdf_path)` 列表。新 Run 通过 `create_run(..., literature=...)` 导入；已存在的 Run 通过 `import_literature(run_id, literature)` 导入，状态必须为 `paused`。

Controller 以 `source_type=import` 登记 `literature_paper` 和可选 `literature_pdf`。对已存在的 Run，导入后刷新科研目录，仍返回暂停状态：不调用 Agent、不回答问题、不恢复执行、不重置预算。`pdf_path` 只由用户清单提供，由 Components 相对清单目录解析，不暴露给 LLM。

相同导入身份复用原冻结文件和 URI，不受来源文件改名影响。新材料先在独立临时目录复制并核对 hash，再发布正式目录；失败只清理本次临时目录，不删除既有证据。

<a id="questions"></a>

### 提问、回答与恢复

`QuestionDraft` 的 `text` 必须包含回答所需背景，`requested_fields` 非空；`options` 可选。字段键使用 AnswerFieldName：1–64 个 ASCII 字母、数字或下划线，以字母开头。正文和选项放在值中，不放进机器键。工具 schema 在暂停前校验这些规则。

Agent 返回 `needs_user_input`、paused Session 和直接 `ControlSignal`。Runtime 保存 PendingQuestion，包含原 Run、Session、Task/Attempt、完整题目、字段/选项和 `recipient_session_id`。所有 Agent 使用同名 ask_user；有直接父关系时问题送父 Agent，只有根 Scientific 问真实用户。权限 action 继续单独交真实用户，不允许父 Agent 以业务答复批准扩权。

父的 `reply_to_task` 绑定它的有效直接子 Task 和准确问题 ID，并给出完整字段答案；也可给尚未启动或原会话中的子任务定向澄清。父判断标明 source=parent。转交用户补充使用真实 `user_input_event_id`，固定代码复制原文、来源和原时间，不能把模型写的解释伪装为用户要求。父可以向自己的上级 ask_user，再回答原子问题。`reply_to_task.resume_work=false` 可先逐个任务传递澄清而不恢复子执行；最后一次答复使用 `true`（默认值）再继续工作。未答业务问题和待批准操作仍保持等待。

用户提交 `UserAnswer(question_id, values, answered_at, comment=None)`；字段键必须与当前真实用户问题完全一致，options 是提示而非枚举限制。业务自由回答写入 values，不接受 comment；操作批准/拒绝可附可选说明，原文保留，不解析为授权。普通 input 不自动充当 ask_user 答案。

Controller 保存完整 `RecordedAnswer`（含 source、source_session_id、原题目与作用域），不生成问答工件。子向上问答与收到的上级澄清进入子第二部分；父收到的下级问题和自己答子过程进入父第三部分，不改变父目标。未答下级问题由持久 pending 状态完整重投影，历史压缩不能使它丢失。父自己向上问答才进入父第二部分。操作批准、拒绝及说明不进入第二/第四部分。


操作批准的 `ActionSnapshot` 绑定工具、校验参数、真实目录/环境/目标、Run/Task/Attempt/Session。原操作调用在批准前保持未完成回执；恢复时权限策略检查本次答案与准确快照，批准在副作用前持久消费，然后为原调用写唯一执行/拒绝回执。匹配答案的 comment 经 `PermissionDecision.user_comment` 传入 `ToolObservation.user_comment`，输出在原回执的顶层 `user_comment`；业务 value 保持原形，不要求是字典。拒绝回执 `ok=False`，value 含 `execution_status=not_executed`，批准说明随执行成功或失败回执保留。批准后派发期间期限中断也保存原调用的说明与失败回执，value 的 `execution_status=unknown` 保守表示不能保证没有部分执行，不重放。整个流程不另造反馈回执。说明不扩大许可、不替代命令，不增加继续/停止决策字段；下一步由模型结合任务及反馈判断。快照变化重新询问，旧说明不借给变化后的动作；拒绝、缺少权限或已消费批准不能执行。批次已完成项保留，后续未开始项 not_executed，只恢复当前待批准项。结果未知的副作用不重放。

业务等待与普通补充继续原 Attempt/Session，真正 retry 新建尝试并保持问答隔离；用户显式打断终止活动旧委派，继续不复活旧子 Session。控制来源身份是 Session、原始 turn 与 native call ID；不能只用供应商 call ID 作为全局幂等键。业务问答原 accepted 回执不在回答后改写，实际回答完整进入第二部分。

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
| permissions | 必填 AgentPermissions：execute_commands、prepare_environment、request_work |
| workspace、workspace_id、workspace_spec | 物理授权、逻辑工作区身份和来源声明 |
| environment_spec、output_dir | 环境约束和系统指定的输出位置 |
| confirm_commands | 继承 Run 的逐次外部操作确认要求 |
| parent_session_id | 继续哪个本 Agent Session；不是直接父关系 |
| upstream_session_id | 真实直接父 Agent Session；根 Agent 为空 |
| upstream_inputs | 有序完整上级补充；含原 event_id、原文、source、原时间 |
| child_questions、runtime_notices | 第三部分的待答下级问题和真实运行通知，不入需求/工件 |
| release_control | owner 临时唤醒父处理补充或子问题，不结束原工作关系 |
| task_context | 明确验收、数据集、原共同目标/限制、有效子任务关系；不是工具正文工作集 |
| user_answers、resume_answers | 累计业务问答、本次恢复实际答案（可含操作批准） |
| work_results、control_ack | 已接收 WorkResult 导航、此次确认的原控制调用身份 |

permissions 是操作授权，WorkspaceGrant.access 是文件访问上限，都不构成业务模式。当前 Controller 只给 Scientific `request_work=True`，不授予命令执行、环境准备或源工作区；Scheduler 给 Coding/Experiment 继承 Run 的两项操作授权和确认开关，并从已保存的工作区记录派生文件授权。任务图不携带自行扩权字段。可写工作区不强制修改，只读源目录也不禁止通过受控工件通道交付分析。工作区声明与授权必须一致，解析后的范围不能大于声明。

恢复控制数据必须有 parent_session_id，并校验 Run、Task/Attempt 和 Session。业务问答不能带操作 action；工作结果只给 Scientific，并要求其实际 Ref 已显式授权为 input_artifacts。没有旧 resume_artifact_ids 别名。

### AgentRequest 怎样进入模型输入

固定契约在同一 Session 保持稳定；第二部分完整保留初始指令、共同限制、有序上级补充和向上业务问答；第三部分保存原生配对调用、工具内容、下级问题与运行通知；第四部分只有完整授权目录。来源、原消息身份和原时间保留，晚转发旧话不成为新要求；同范围后续明确修订替代对应旧选择，其余要求仍有效，含义冲突向直接上级澄清。只有第三部分可按完整回合压缩。上下文不重复投影近期正文、命令诊断或总体工作报告，具体结构见 [CONTEXT](CONTEXT.md)。

### AgentResult

| 字段 | 用途 |
|---|---|
| status | ModuleStatus，确定性机器状态 |
| report | 非空原始说明；不 strip、选摘或自动折行，表达发现、结果和局限 |
| artifacts | ArtifactCandidate 或本次所属且已登记的 ArtifactRef |
| session | 子模块拥有的 SessionRef；暂停控制结果必填，其他结果可为空；提供时其状态必须与结果对应 |
| control | action、原 Session/turn/call 身份，以及对应问题、WorkRequest、定向答复或结束委派载荷；可携科学 assessment |
| error、warnings | 结构化错误和非致命警告 |
| llm_calls | 本次调用用量的诊断投影；严格非负整数，拒绝 bool；Run 以发送前持久占用为准 |

`ControlSignal` 直接携 `action`、`source_session_id/source_turn/source_call_id` 及对应 question、work_request、task_reply 或 abandon_work_id，载荷互斥；可携 ScientificAssessment。来源必须匹配 paused Session。question/work_request 身份由 Runtime 在交接前分配并保存，不用工件定位，也不在接收端重新生成身份。只有 Scientific 可 request_work。

| status | error / control | Session（提供时） | warnings |
|---|---|---|---|
| `completed` | 均为空 | completed | 必须为空 |
| `completed_with_warnings` | 均为空 | completed | 必须非空 |
| `failed` / `blocked` | 必须有 error，control 为空 | failed / blocked | 可保留 |
| `needs_user_input` | 无 error；ask_user control | 必须 paused | 可保留 |
| `request_work` | 无 error；request_work control | 必须 Scientific paused Session | 可保留 |
| `reply_to_task` / `abandon_work` | 无 error；准确父级控制 | 必须 paused | 可保留 |
| `yielded` / `interrupted` | 无 error/control；边界让出或用户中断 | 必须 paused | 可保留 |

接收端重新校验整个结果、Session 所有权、工件归属和恢复绑定，确认本次交付后记录答案已投递。投递记录不证明自定义 Port 实际阅读或理解；原生 Agent 将累计业务回答完整放进任务需求，操作批准由策略消费。错误返回不能抹掉已发生调用或已登记工件；report 不能自行决定状态。

<a id="payloads"></a>

### 材料与控制各走自己的入口

持久文件由 input_artifacts/artifacts 交接，科学意见和明确验收要求仍使用内容模型校验。答案、工作结果和恢复身份通过统一 AgentRequest 的直接控制字段传递，不为流程控制创建工件。task_context 保存必要初始事实；它不用于追加工具正文或绕过正式工件授权。

<a id="artifacts"></a>
<a id="artifact-models"></a>

## 3. 工件、来源与可信边界

### 候选、登记与读取

| 对象或操作 | 表示什么 | 不代表什么 |
|---|---|---|
| `ArtifactCandidate` | 生产方提议保存的内容或文件 | 尚未登记，不可用自造 ID 引用 |
| Registry 登记 | 解析合法来源、冻结内容并保存可信归属 | 不判断材料相关性或论断是否成立 |
| `ArtifactRef` | 已登记原件的 ID、hash、归属和描述 | 引用本身不能绕过授权与磁盘复验 |
| reader | 按授权 ID 核对整份 hash，再读取指定范围 | 核对完整性不等于记录已阅读；分段读取不等于流式 IO |

ArtifactCandidate 含 `kind`、相对 `path`、`media_type`、`summary`、可选 `output_name`、`metadata` 和 UTF-8 `content`。有 content 时 path 是存储文件名；无 content 时须在授权工作区或本次 output_dir 解析唯一实际文件，同一路径在两个根目录都存在时拒绝。绝对路径、`..` 和越界软链拒绝。任务工件的 TaskId 在写入前校验；路径验收使用实际源文件，忽略候选 metadata 的自报 source_path。

所有工件只用 contracts.identifiers.new_artifact_id() 分配 `artifact_<uuid4.hex>`。UUID 是身份，Ref.sha256 是冻结字节校验，来源和内容快照决定是否复用。Run/Task/Attempt/Session 保存在正式字段，不塞进 ID。既有 Session 命名机制不因本次工件 ID 修改而改变。

登记前以原操作与输出位置保存已分配 ID、预期 hash、描述和必要源路径，再冻结、提交 Ref。Run 中未完成登记记录用于恢复，不是第二层目录；回到同操作必须复用原 ID/冻结字节，描述或预期内容变化明确拒绝。Scientific Session 保存实际授权 ID 和配对回执后再确认登记。Loop 将终态与原始 AgentResult 一起保存；接收端再保存待接收的完整原结果，登记中断可以续接原交付，不重跑模型或产生另一份报告。Compiler 原 output 在冻结任务要求前保存，初始化声明和导入进度也在第一次冻结前保存，恢复分别接收原图和原导入。RunStore 与 SessionStore 分别原子保存，不承诺跨文件事务或外部命令 exactly-once。


ArtifactRef 含 id、kind、producer、run_id、Task/Attempt 或 Session 归属、uri、sha256、media_type、summary、可选 output_name 和 metadata。Ref 不是自证：返回已有 Ref 时必须与可信登记记录完全一致，hash 正确且属于当前 Attempt 或 Scientific Session。输入材料及其他调用的产物不能冒充本次新输出。

### 系统材料与专业报告

| kind | 来源和作用域 |
| --- | --- |
| acceptance_requirements | Task / task_requirement，冻结明确验收要求 |
| conclusion_requirements | Run / conclusion_requirement，冻结最终交付要求 |
| dataset_catalog | Run / dataset_catalog，系统提供目录引用 |
| module_report | Orchestrator Task+Attempt / task_report，原报告或明确标识的预执行失败报告 |

Scientific 的材料归属其 Session，支持 literature_paper/PDF/fulltext、web_page、scientific_opinion、专业 assessment、observation_trace 和按实际需求提交的 module_report。Coding/Experiment 实际交付归属 Task+Attempt。导入和最终报告归属 Run，由 Orchestrator 记录 import/final_report 来源。不再支持 question、answer、work_request、work_record、work_feedback 或 research_index 控制工件链。

### 执行记录的信任范围

注入的进程内 ModulePort 与原生 Agent 的确定性 finalizer 是可信实现。模型 finish 候选不能提交 `execution_record`、`verification_result` 或 `observation_trace`；原生代码从真实命令回执、验证状态和读取观察中生成它们。Coding 的 patch 同样从实际差异生成。

任务是否完成由 Coding/Experiment 通过统一 finish 的 status 声明，不从命令 argv 或历史退出码推断。系统生成的 execution_record 按原生工具回合与回执顺序保留 run_shell、run_setup、run_verification 的实际命令结果，以及环境工具中断时已经取得的进程事实；未执行的 blocked 回执不是命令事实；一次操作失败不自动等于任务失败，后续成功也不抹去旧记录。任务报告解释失败与交付的关系，Scientific 判断科研充分性。

Controller/Scheduler 只消费公共 AgentResult 和登记工件，核对来源、hash、状态及内容，不读取下游私有 Session。这一边界防止模型叙述冒充执行事实；它不隔离具有任意 Python 执行权限的恶意自定义 Port。替换 Port 必须遵守相同的可信生产约定。

Coding 每次实际验证使用独立日志目录（UTC 时间戳加唯一标识）。同一代码版本下重跑验证或恢复 Session 后再次验证，都保留各次 stdout/stderr；执行记录中的路径始终对应那次执行，不覆盖旧日志。绑定环境下，验证使用裸名 python/python3 或 pytest，由工具解析为绑定环境的 bin/python（pytest 通过 -m pytest），显式解释器路径在审批前拒绝。

命令已返回的真实回执不因后置 Git 检查失败而丢失；诊断失败记录原因并标为新鲜度未确认。批量验证中断保留已记录结果，剩余命令列入 unrecorded_commands，不虚构退出码或声称它们一定未开始；该批次不能被当作完整通过。Run 期限继续生效，超时后 Session 仍保留已执行回执，不为了保存回执延长预算。

报告用于解释；`module_report` 可保存需要下游分段读取的长说明。报告不替代原始测量、代码或文献。拿到 Ref 不代表已读正文，读过一次也不表示全文始终在模型上下文中。

<a id="scientific"></a>

## 4. 科学控制与工作交付

Scientific 同样接收 AgentRequest 并返回 AgentResult，使用同一 prompt、动作集合和 finish 协议；判断、提问、请求工作和完成是该流程的控制结果。

<a id="work-request"></a>

### request_work：提出下一轮工作需求

`WorkRequestDraft` 包含整体 objective、共同 constraints 和非空 tasks；交付语义写在相应 Task，而非另一套 expected_evidence。`WorkTaskDraft` 包含局部 key、原自然 instruction、depends_on、input_artifact_ids、future input_artifact_bindings、output_names 和可选逻辑 workspace_id；不选择 Agent、不填写运行身份、物理路径或权限。固定代码校验 key 唯一、DAG、直接依赖及已声明输出选择器。工具校验实际授权及冻结输入，保存 WorkRequest 和源调用身份，直接返回 accepted + work_request_id 后暂停。Controller 按此真实身份接收、编译与调度，不再为控制动作登记工件。ScientificAssessment 保留当时科学判断，不自动生成报告工件。

<a id="work-outcome"></a>

<a id="interpreter"></a>

### 反向交接：实际结果和原报告

`WorkInterpreter.interpret(work, tasks, artifacts) -> WorkResult` 是无状态固定代码。返回 Run/Session/WorkRequest 身份、原 objective、终态、可选 workflow_revision/error、任务导航和实际 ArtifactRef。它不调用模型，不拼总体报告，不生成 research_index/work_record/work_feedback。

`WorkTaskResult` 保存 task_id、workflow_agent_kind、instruction、TaskStatus、错误/警告及各次 `WorkAttemptResult`。后者保存 number、AttemptStatus、report_artifact_id、实际 artifact_ids、错误/警告与可选接收失败。它不复制 report 正文。失败旧 Attempt 报告仍交回；依赖阻塞而未执行的任务没有假报告。编译失败可以是零 Task 的 WorkResult，不伪造 workflow_revision。

每个真实终态 Attempt 的报告在输出接收与验收结论确定后按原字符冻结为 module_report，生产者为 orchestrator、Task/Attempt 作用域、source_type=task_report。元数据说明 agent_started、原 Agent 错误与接收失败；运行时预执行失败报告明确标示来源。暂停提问不是终态，不冻结终态报告。Scientific 可按实际需求提交自己的 Session 作用域 module_report，它不等于自动生成的 WorkRequest 总报告。

Controller 把实际 Ref 纳入 Scientific 授权；完整目录按 WorkRequest → Task → 原报告与交付物直接展开。原 request_work 回执只有 accepted + work_request_id，之后不回写、不补无匹配的 tool 消息。

<a id="compiler"></a>

## 5. 工作编译、路由与输出绑定

### compile：从工作需求到任务图

```python
compile(request: WorkRequest, *, current: Workflow | None,
        registry: WorkflowAgentRegistry, limits: ExecutionLimits,
        workspaces: list[WorkspaceDescriptor] | None = None) -> CompilationResult
```

成功返回 CompilationResult(output, llm_calls)，output 为 WorkflowProposal 或 WorkflowPatch；编译错误抛 CompilationError，并保留实际 llm_calls。预算耗尽和超时分别抛 BudgetExhaustedError、DeadlineExceededError，由 Controller 保存对应终止原因。Compiler 不读下游 Session，不直接修改 Run 状态。LLM 编译必须处于可信调用方绑定的共享 execution_budget 中，直接使用该余额和期限。

LLMWorkflowCompiler 请求 RouteTasks；原生 tools 只有 route_tasks。每项只能给 task_key 匹配注册 workflow_agent_kind，或说明不可路由原因，没有 instruction/依赖/资源改写字段。固定代码要求恰好覆盖全部原任务，原文复制 instruction、shared_goal/shared_constraints 及控制字段，分配正式身份并校验工作区、授权和图。有效不可路由判断直接交回 Scientific；只有输出格式/结构错误最多纠正一次，无语义复审调用。Compiler 不运行 AgentLoop、没有 Session；复用 PromptLLMClient(output_tool_name="route_tasks")、共享预算及原生传输。用户取消透传 OperationCancelled，不作为错误重试。

<a id="workflow"></a>

### 图与任务字段

TaskProposal 包含 id、work_request_id、workflow_agent_kind、原 instruction、原 shared_goal/shared_constraints、depends_on、workspace_id、已有 input_artifacts ID、未来 input_artifact_bindings、output_names，以及可选 acceptance_spec。它不携带单独的扩权或确认开关。

Scientific 给出原任务正文和必要逻辑控制；路由 LLM 仅匹配 Agent，不生成任务草图，不增删、拆合或重写原任务。外部确定性调用方可以提供明确的 TaskAcceptanceSpec；自然语言证据要求仍须保留在 instruction 中。

Scheduler 接受时将 output_names 合入 required_output_names，将非空验收要求冻结为 acceptance_requirements。WorkflowTask 与后续每个 Attempt 绑定同一个 acceptance_ref；请求将该 Ref 作为输入材料，并从同一冻结原件投影 task_context.acceptance_requirements，保证明确要求完整进入第二部分。模型投影不是另一份可独立修改的要求记录。

`depends_on` 表示上游成功，是执行顺序约束。失败修复须在观察真实失败后提出新的 WorkRequest，追加修复与重跑任务。WorkflowPatch 只追加任务，绑定 based_on_revision，不改写历史 Attempt。Proposal/Patch 共用同一候选图校验，依赖只限本轮任务；依赖失败按拓扑顺序传播，当前工作轮任务全部终态后才生成稳定 WorkResult。

### FutureArtifactBinding：引用还未产生的输出

`FutureArtifactBinding(source_task, output_selector)` 必须选择直接依赖声明的逻辑 output_name。上游成功后，Scheduler 从其最新成功 Attempt 查找唯一匹配 Ref，再加入下游 input_artifacts。缺失、重复、跨归属或尚未完成的来源均拒绝；执行前解析失败会保留不可重试的 failed Attempt，不调用 Agent。模型不能预猜未来 ArtifactId。

<a id="capabilities"></a>

### 执行 Agent 路由

`WorkflowAgentKind` 只有 coding、experiment；WorkflowAgentRegistry 每种路由只登记一次。Scientific 由 Controller 调用，不是执行图节点。模块路由与 capabilities 包里的工具能力是不同概念。

<a id="literature"></a>
<a id="web"></a>

### 文献与网页：搜索、获取、读取各有边界

`literature_search` 和 `web_search` 返回临时线索，只进入工具历史；成功、部分成功、空或失败都不生成工件。搜索内部按准确资源身份去掉本次重复项，并保留来源尝试、分页、限制和失败事实。工具说明告知搜索信息可能随历史压缩消失，需要持久保存时显式 fetch。

`fetch_literature(source)` 替代旧全文工具，无兼容名。source 明确采用 `arxiv:<id及可选版本>`、`doi:<doi>`、`openalex:<W…>` 或 `artifact:<授权论文ID>`。既有 provider 增加精确单条解析，不从模糊标题猜论文。metadata → PDF → 提取全文分别登记，直接来源链为 PDF → paper、fulltext → PDF。部分失败保留实际成功工件和准确错误。解析使用 PyMuPDF4LLM，OCR 关闭，不宣称完整还原图和公式。

`web_fetch(url)` 获取 HTML/文本并冻结 web_page，正文由 markdownify/BeautifulSoup/html.parser 转 Markdown，保留代码空白、链接和标题。保守处理无法可靠解析的坏 HTML，真实报 parse_failed。获取时间保留 metadata/回执，不写进稳定正文。provider 仅 DeepSeek 或 off，不保留 Tavily 配置与适配器。

复用只检查当前 Agent 显式授权输入与已接收 Session 产物，核对准确来源、解析方式和冻结字节。相同快照可复用；正文、版本或必要来源变化保留新工件。原来源、身份和 hash 不改，复用不扩权。搜索去重、资源复用、目录 ID 唯一分别处理。

`read_artifact` 只读取已授权冻结 UTF-8 文本，校验 hash，不 fetch、不创建工件；二进制 PDF/检查点仍列目录，使用指定工具消费。读取正文只在原生回执，不生成第四部分工作集。

<a id="tools"></a>

## 6. AgentLoop → 工具与运行机制

### 工具参数与 ToolObservation

`ToolObservation.artifacts` 是本次实际登记/复用的 Ref 增量，由共享 Loop 验证作用域并接入 Session 授权。工具不自行维护一份 memory.artifact_index 元数据副本；导入输入仍按显式授权保留，不能作为伪造新输出返回。

模型工具协议归 Runtime；通用模型入口由 Capabilities 提供，其公开导出只有 Tool 和对应输入模型。[Coding 验证工具](../../packages/agents/coding/src/resagent2_coding/verification.py)、Experiment 执行工具与各模块控制工具仍由所属 Agent/Runtime 提供。普通操作改从 `resagent2_components` 导入，不通过 Capabilities 转发。Tool 仍按下列协议运行，Python 文件移动不改变模型动作名、参数或公共契约。

ToolRegistry 按动作名找 Tool，以 input_model 完整校验 arguments，再调用 `Tool.execute(state, parsed_arguments) -> ToolObservation`。工具不直接写 AgentState，返回 memory_updates、已登记 Ref 增量及控制/完成提议，由 Loop 应用；但可实际写文件、运行命令或改变 EnvironmentBinding，并非纯函数。

| ToolObservation 字段 | 含义 |
|---|---|
| `summary`、`value` | 非空简短说明和可选 JSON 内容 |
| `ok` | 机器可读成功标志；非零退出、参数拒绝、缺文件等可恢复失败为 False，不能解析 summary 猜状态 |
| `user_comment` | 可选操作批准说明，由运行时从匹配回答透传到原回执顶层；不改业务 value |
| `memory_updates` | 由 Loop 合入 Session 的确定性更新，不是第二份外部结果信封 |
| `question` / `request_work` / `task_reply` / `abandon_work_id` / `finish_candidate` | 至多一个控制/完成结果；普通工具可全为空，Loop 负责形成对应外部结果 |

所有 AgentLoop 客户端通过 `next_tool_call` 返回原生工具回合。每个 `Tool.input_model.model_json_schema()` 与说明共同生成 `tools`；实际注册表决定工具可用性并在执行前完整校验参数。三个 Agent 不维护自己的工具名 Literal，也不解析模型正文作为备用动作。仅有 `next_action` 的客户端不能运行 AgentLoop；Compiler 复用无状态 prompt 适配接口，其生产传输同样用原生工具。

| 可注入入口 | 约定 |
|---|---|
| AgentLoop.run(definition, request, *, session_id, initial_memory=None) | 循环、观测、反馈、Session，不调度 Workflow |
| ContextBuilder(request, state, max_context_tokens) | 返回完整任务与工件目录 ContextSection；Runtime 接入配对历史和摘要，builder 不另分正文额度 |
| ContextComposer.compose(...) | 完整组织固定输入段；Loop 按原生 messages + tools 计量并决定旧完整回合压缩，仍超限明确失败 |
| LLMClient.next_tool_call(context, schemas, turns, *, max_input_tokens) | Agent 客户端必需方法；返回 ToolCallTurn，不执行工具；须提供稳定非空 tool_session_key |
| StructuredLLMClient.next_action(context, action_type) | 可选 JSON 结构化输出接口，不作为生产 Compiler 路径 |
| OpenAICompatibleClient.next_tool_call(context, schemas, turns, ...) | AgentLoop 原生工具调用；返回一轮 assistant/tool-call 协议数据，不自行执行 Tool |
| OpenAICompatibleClient.summarize_history(prompt, max_input_tokens=...) | 可选纯文本历史交接；共用传输/trace/attempts，不执行工具，输出配置仍来自 ModelProfile |
| PromptLLMClient.next_action(prompt, action_type) | 复用 Composer/计量；配置 output_tool_name 时仅提供该原生输出工具；无工具执行、Session 或 Loop |
| ModelRequestClient.request(body) | 注入的单次模型 JSON 请求；共享用量/截止时间/trace，无重试、Tool 执行或 Agent/Session；供应商语义由调用组件解析 |
| PermissionPolicy.check(action, state, request) | 派发前返回 allow / ask / deny；共享操作规则位于 Capabilities，组合 Components 提供的基础边界；不是 OS 沙箱 |
| SessionStore | 内部状态/事件持久化；上层仅持有引用 |

LoopRequest 只要求身份、预算、父 Session 等运行信息；Scientific 的 task/attempt 可为空。领域指令、工件和授权由注入的 builder、工具、finalizer 使用。EnvironmentBinding、GitBaseline 留在 components，不变成 wire 消息。

### 授权、确认与执行

参数错误、ok=False、PermissionPolicy 的 deny 和执行时 PermissionError 等可恢复错误进入反馈，允许在剩余额度内改用合法操作；连续失败仍受统一上限约束。ask 保存结构化待确认动作并暂停，allow 才派发。未知工具与参数错误一样进入有界纠错，并为对应调用保存未执行回执；整批预检发现未知工具时不执行批内任何项。Action 不忽略其他未知字段。

`OperationPermissionPolicy` 先检查模块 Tool 集、Run 操作权限和工作区范围。共享 `run_shell` 替代 Experiment 的旧 `run_command`，每次都要求精确脚本、起始目录、绑定环境和 Bash 配置的单次确认，即使 confirm_commands=False；批准不能扩大 Run 授权。它执行 Linux `/bin/bash --noprofile --norc -o pipefail -c`，保留脚本首尾空白，不隐式开启 errexit，不提供持久终端或后台任务管理。通用脚本不套用单条 argv 分类或语义猜测；安装和角色职责由工具指引及用户审核约束，不宣称脚本内部被沙箱隔离。`run_verification` 和 `run_setup` 保留自己的命令范围与固定规则；confirm_commands 可为它们增加确认，需确认的验证一次只提交一条命令。环境创建/安装仍使用受控环境 Tool。

| 操作 | 所需操作授权 | 额外边界 |
|---|---|---|
| `prepare_environment` | `prepare_environment=True` | 可调用受控环境创建子进程，不要求 `execute_commands=True` |
| `run_setup` | 两项权限均为 True | 完整可读写工作区及安装命令策略 |
| `run_shell` | `execute_commands=True` | 已有绑定环境、完整可读写工作区；逐次批准；使用已有环境不要求准备权限 |
| `run_verification` | `execute_commands=True` | 已有绑定环境、完整可读写工作区及验证命令策略 |
| 显式 `audit_env` | `execute_commands=True` | 已有绑定环境；执行固定诊断，不获得通用脚本权限 |
| 文件读取、创建、修改及 Coding 的 `delete_path` | 对应 WorkspaceAccess 范围 | 模块必须提供该工具；不依赖命令或环境准备权限 |

`confirm_commands=True` 对进程和环境顶层工具逐次询问，包括显式 `audit_env`。命令执行内部的自动环境核验属于已批准动作的前置检查，不另发问题。`confirm_commands=False` 仍保留固定规则要求的确认，例如所有 Shell 调用、非空目录递归删除；它不等于自动批准所有操作。

Coding 的 `delete_path(path, recursive=False)` 删除单个文件、链接或空目录；非空目录须 recursive=True，并确认包含路径、类型和版本信息的目标快照。执行前重验目标，变化使旧批准失效；删除链接只 unlink 自身，不跟随目标。部分删除保留已完成/未完成记录，更新编辑 revision 及验证新鲜度，不承诺原子回滚。删除文件中的内容仍用 replace_text。

### 原生调用批次与格式纠错

OpenAICompatibleClient 区分传输/响应封装失败和模型输出拒绝：前者至多三次 HTTP 尝试，仍受剩余调用数和时间限制；原生 framing 或 arguments JSON 被拒绝时，完成本次 trace 后直接交回 Loop，不在客户端原样重试。损坏的 HTTP 压缩正文按响应失败记录，不新增自动重试。

原生每轮接受1–8个 tool calls，整批先校验参数/权限，再逐项复核权限与超时、串行执行。`finish / ask_user / request_work / reply_to_task / abandon_work` 必须独占一轮；零个、超量或混合控制工具的批次拒绝。中途失败保留已完成结果、取消余下项，不回滚副作用；assistant content 不作为备用动作解析。

Loop 保存已发生 HTTP 尝试。可匹配原生 call 的错误、拒绝与完成检查进入对应唯一回执；没有可解析调用身份的协议失败记录真实事件，不能添加孤立 tool 消息。错误纠正仍受连续失败上限、共享调用预算和期限约束，不保证模型会纠正。不再用 required runtime_feedback 段复制原回执。


Compiler 不运行 AgentLoop；PromptLLMClient 把 RouteTasks schema 放进唯一 route_tasks 原生工具，并要求恰好一次匹配调用，不解析正文作为备用结构。格式/结构拒绝最多纠正一次，所有请求消耗保留；不可路由判断和用户取消不原样重试。适配器只透传异常与 last_attempts，不增加第二层纠错。

HTTP 解码失败在请求所属边界转为现有领域错误：搜索直接返回失败回执而不登记工件；网页抓取返回失败回执、不登记正文。Run 截止和预算异常继续交给运行时处理，不冒充网页超时；ModelRequestClient 保持单次请求计量。

### 原生 Session 身份与恢复

创建 Session 时写入 `AgentState.tool_protocol_key` 和实际 system 内容（身份 prompt 与通用原生协议规则）/实际工具 schema 的 `contract_fingerprint`。OpenAICompatibleClient 的 `tool_session_key` 是协议、endpoint、model 的稳定 hash，不含 API key；其他原生客户端也须提供非空、稳定且能标识其协议配置的身份。恢复时身份须完全相同；旧正文 JSON Session、endpoint/model 或固定规则变化均拒绝，不自动迁移。动态预算不属于固定契约指纹。

`AgentState.tool_turns` 按轮保存 assistant `content`、`reasoning_content`、原始 `tool_calls` 和按 call ID 配对的 `tool_results`。Loop 先保存整批 call，每次派发前保存 executing_call_id；批准操作的匹配说明同时保存在 `ToolCallTurn.executing_user_comment`，随后按 observation/event 路径配对该调用的 receipt 并清除执行标记和临时说明。该字段仅是运行期恢复元数据，不形成另一套批准流程。重启保留已完成回执；正在执行且缺回执的项记为 unknown outcome，其说明附回原调用的未知回执，其余缺回执项记为未开始。未知恢复不重放、不重新授予许可或把说明借给后续动作。它防止把未知结果误判为成功，但只保证进程重启 checkpoint，不保证掉电持久化，也不是外部副作用的 exactly-once 事务。

下一轮原生请求按固定 system、完整任务需求、摘要/近期成对历史、完整目录排列；不保存或重发此前每轮完整业务 prompt。输入压力下使用可选 summarize_history 生成旧完整交互摘要；history_checkpoint 同时保存摘要与绝对历史边界，近期完整回合继续原样发送。原始历史不删，摘要不替代当前领域状态、回答、证据或完成门禁；失败不推进边界。具体比例和失败边界见[上下文](CONTEXT.md#compaction)。`reasoning_content` 仅用于同一 Session 的供应商协议续传，不进入业务 memory、ToolObservation 或完成证据。

<a id="text-io"></a>

### 文本读取、写入与搜索

读取通常可重复；写入和外部命令不承诺 exactly-once。

| 边界 | 规则 |
|---|---|
| `read_file` / `read_artifact` | 严格 UTF-8，保留原换行；先选物理行，再选 start_char/end_char 零基、末端不含字符窗口；默认返回上限128000字符 |
| 无效文本 | 含 NUL 或无效 UTF-8 返回可恢复错误，不新增成功阅读记录 |
| 工作区文本大小 | 读、搜、创建、替换共用10 MiB上限；读取核对声明大小与实际读入长度，写入前验证编码和最终字节数 |
| 冻结工件 | 不受工作区大小上限约束，读取前校验整份 hash；存在/完整性验证不要求文本可解码 |
| 分段读取回执 | start/end 行与字符字段回显请求范围；total_lines 为整份文本物理行数，selected_chars 为所选行段在字符切片前的字符数，空文件均为0；truncated 只表示请求窗口被返回上限裁剪；next_start_char 是实际返回末端，相对于完整所选行范围，无余文时为 null |
| IO 与分段读取 | 字符窗口不等于流式读取，不能绕过整份检查或改变冻结内容；连续读取保持行范围不变，不把请求 end_char 当作实际返回终点 |
| `search_text` | 大小写不敏感字面子串，不是正则；a\|b 仍匹配原文字面串 |
| 搜索覆盖 | skipped_count、skipped_files（最多50条）、skipped_files_truncated 描述授权候选中跳过文件及原因；truncated 表示达到结果上限后停止，后续匹配未检查、匹配总数未知；incomplete 表示跳过或触及结果上限，零匹配不能因此当成完整无匹配 |

**模型输入容量。** ModelProfile 声明窗口、输出预留和安全余量，模块声明输入上限，有效额度取两者较小值。Agent 计量完整原生 messages + tools，含历史、schema 与 JSON 转义；Compiler 也计量原生 messages + 唯一路由 tools。估算与分配规则见 [CONTEXT](CONTEXT.md#budgets)。输入压缩不另开模型调用额度；step 仅记录时序，不形成第二份预算。

<a id="components"></a>

### 普通调用方 → Components

这一层是进程内 Python 调用，不是原生 function call，也不新增远程协议。Tool、Agent 准备/完成检查、CLI/E2E 可直接使用；不强制一个 Tool 对应一个组件。Runtime 不依赖这里。

| 入口 | 输入 → 输出 / 副作用 | 失败与边界 |
|---|---|---|
| [WorkspaceBoundary](../../packages/components/src/resagent2_components/workspace.py) | 工作区授权 + 相对路径 → 已检查 Path / 文件清单 | 路径与软链越界拒绝；不代替 OS 沙箱 |
| [GitWorkspace / GitBaseline](../../packages/components/src/resagent2_components/git.py) | 工作区、Attempt 基线 → 变化路径 / patch / snapshot | 保持原基线与归属规则；不以模型说明推断修改 |
| [RepoMaterializer](../../packages/components/src/resagent2_components/repo.py) | 仓库来源、目标工作区 → MaterializedRepo，可准备仓库文件 | 来源/目录校验失败抛明确错误；不决定任务图 |
| [ProcessRunner.run](../../packages/components/src/resagent2_components/process.py) | 命令、目录、超时和环境 → VerificationResult，落 stdout/stderr | shell 组合拒绝，超时终止进程树；结果是执行事实，不是科学结论。历史类型名不在本次调整 |
| [EnvironmentManager / Binding](../../packages/components/src/resagent2_components/environment.py) | Run/workspace、Python 版本 → 环境及认证状态 | 环境失效/代次更新规则不变；SetupCommandPolicy 限制安装入口，Coding 验证策略不在这里 |
| [DatasetCatalog / resolve_dataset_refs](../../packages/components/src/resagent2_components/dataset.py) | 部署目录 / Run 引用 → 登记引用 / DatasetAvailability | 不下载、不猜准备状态；资源缺失怎样询问仍由 Agent 决定 |
| [ResourceLayout](../../packages/components/src/resagent2_components/resources.py) | 部署配置 → 数据集与环境根目录 | 不管理 pip/conda 下载缓存，不成为 ResearchRequest 字段 |
| [RegisteredArtifactReader](../../packages/components/src/resagent2_components/artifacts.py) | 授权 ID + 行/字符范围 → 正文 | 读取先校验 Run 与整份 hash；不登记材料或生成科学证据 |
| [LiteratureSearchBackend](../../packages/components/src/resagent2_components/literature/backends.py) | 查询、来源、范围、页号、数量和年份 → LiteratureSearchResult（论文页与来源/分页事实） | auto 首页面向平级来源有界切换；显式来源不回退；失败保留类型与来源尝试，不伪造全文 |
| [WebSearchBackend / WebPageFetcher](../../packages/components/src/resagent2_components/web.py) | provider 搜索回执或单页 HTML/text → 规范化网页结果 | 搜索 provider 可选；响应有界、严格 UTF-8、无 JavaScript/PDF/二进制解析；不决定科学相关性 |
| [load_literature_manifest](../../packages/components/src/resagent2_components/literature/imports.py) | 本地 JSON 清单 → PreparedLiteratureImport 列表 | 验证元信息及可选 PDF，路径相对清单目录；不联网、不登记或恢复 Run |
| [request_task_context / artifact_index_context](../../packages/components/src/resagent2_components/materials.py) | 初始事实、完整业务问答及授权 Ref → 任务段和完整目录 | 不复制正文工作集、不增加授权；完整消息计量归 Runtime，详见 [CONTEXT](CONTEXT.md#budgets) |
| [文本读写与窗口](../../packages/components/src/resagent2_components/text.py) | 工作区路径 / 文本 → 有界严格读入 / 写入前编码验证；文本 + 行字符范围 → 窗口 | 工作区默认10 MiB，返回默认128000字符；保留原换行，不决定授权或模型预算，工件仍由 reader 验证 |

文献 Tool 通过工件组件中的 `ArtifactRegistrationPort` 接受组合根注入的登记/解析对象；实现仍是 Orchestrator 的 ScientificArtifactRegistration，Components 不反向 import Orchestrator。错误如何转为 ToolObservation、反馈、ModuleError 仍由已有 Tool/Loop/Agent 边界处理，组件不另建恢复机制。

<a id="trace"></a>

### LLM 计量与 trace

**诊断与权威状态。**

trace 是可选诊断输出。两个模型客户端共用私有 JSONL 写入函数；该函数中的 JSON 序列化或文件系统写入失败时发出仅含异常类型的告警，保留模型响应、原始请求错误和 action 校验反馈，不自动重试或再次扣量。Run 用量、Session 等权威状态保存错误仍按原流程处理，不能因 trace 的容错而被忽略。

不管理用量的客户端由 invoke_model 在调用前占用一次；原生 Agent 使用 next_tool_call，Compiler 的 prompt 适配入口为 next_action，生产底层请求 next_tool_call 和唯一 route_tasks。提供 manages_usage 的传输客户端负责通过当前共享预算逐次登记 HTTP 请求和重试；OpenAICompatibleClient 与 PromptLLMClient 遵循这一约定。托管搜索的 `ModelRequestClient` 每次发送前也占用一次共享模型调用，不自动重试；网页抓取只占 Run 时间。last_attempts、trace 等仍用于诊断，不再是 Run 扣费依据。自定义客户端隐藏的重试无法从单次方法调用推断，须接入同一用量接口。

**一次逻辑调用与 HTTP 尝试。**

OpenAICompatibleClient 的 trace 按 call_id 关联逻辑调用和后续校验记录。attempts 保留各次 finish_reason/usage/错误，以及该次重新计量后的请求（full 为原文，metadata 为 hash）；顶层请求和响应对应最后一次已发送尝试。每次发送或重试前重新从 Run 账本投影共享总预算到第三部分，不复用旧额度或另行扣量。retry_number+1 是 HTTP 尝试数，不能再加 attempts 长度。request_max_tokens 是实际输出上限，null 表示未指定。

收到格式反馈后的请求是新逻辑调用、新 call_id，不是上一调用内的 HTTP retry。正文/原生参数解析和调用数量错误写在主记录/attempts 的 validation_error；schema_validation_error 补充行记录已解析候选的外层 schema 错误，以及原生调用身份/回执校验拒绝。主记录按带 model 的行识别，不把补充行计成另一次调用。统计解析失败、候选校验、HTTP retry、Task Attempt retry 时分开计数。

**托管搜索。** 单次请求关联当前 Run、Session、工具和步骤，复用同一 trace 档位：full 保存请求及可取得的JSON响应；HTTP失败只保留状态和Retry-After，不保留错误正文。metadata 保存相应 hash，不记录认证 header，不因 trace 增加另一份科学证据。此传输的`response_valid`和`succeeded`表示有效JSON对象，检索结果是否可用以搜索回执及Session观察为准。

| trace 档位 | 保存范围 |
|---|---|
| off | 不记录 |
| metadata | 请求/响应/源码正文只保存 hash；原生 calls 使用 tool_calls_sha256 |
| full | 原始 request/response，以及 provider 可取得的 raw_tool_calls / raw_reasoning_text，含可取得的失败响应 |

trace 与 Session 是独立边界：metadata 只留内容 hash，不表示 Session 不保存 tool_turns；reasoning 仅用于同 Session 协议续传，不是业务证据。目录/文件分别按0700/0600管理，Session 私有权限不依赖 trace 档位。full 可能包含源码和用户输入，不是可公开上传的日志。

空 JSON 只是一种现象，先查 finish_reason、usage、输出额度，不直接定性模型漂移；未返回的信息为未知。单工具 parsed_action 为对象，多工具为对象数组，名称另有 tools 列表；压缩行 included_sections 含 compaction，action_valid/tool/parsed_action 为 null，不是无效工具动作，但仍计调用。action_valid 不证明参数、执行或结论正确，应结合校验补充记录与 Session 观测。部署配置见 [CLI README](../../apps/cli/README.md#6-模型与上下文预算)。

**源码与测试**：[ToolRegistry](../../packages/runtime/src/resagent2_runtime/tools.py)、[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[Context](../../packages/runtime/src/resagent2_runtime/context.py)、[工具契约](../../tests/runtime/test_tool_contracts.py)、[guards](../../tests/runtime/test_guards.py)、[恢复](../../tests/runtime/test_resume.py)。

<a id="runtime-context"></a>

### 运行时反馈与上下文

失败、拒绝、执行诊断和 finish 检查通过原调用的唯一回执反馈。ContextComposer 完整组织固定输入，Loop 按实际原生 messages + tools 计量，只有较早完整原生回合可摘要。Session 保存 accepted 授权 ID，不保存独立元数据目录。固定契约指纹覆盖 prompt 与实际 tools schema，恢复拒绝改变它们。详细规则见 [CONTEXT](CONTEXT.md)。

<a id="completion"></a>

## 7. 完成与验收

固定完成检查只验证协议、归属和可观察事实；不证明任务语义或科研结论正确。原生 Agent 仍在既有 `CompletionCheck.evaluate` 中完成检查，接收端继续独立校验公开结果和登记状态。

| 检查层 | 接收什么 | 负责确认什么 |
|---|---|---|
| Agent 的 CompletionCheck | 模型 finish 候选 + 本 Agent 的执行事实 | 候选合法、领域完成规则成立；可纠正拒绝留在同一 Session |
| Registry / 接收端 | AgentResult 及输出 | 原件能冻结、归属/hash/控制结果正确；不能把候选当已登记事实 |
| Scheduler 的 Task 验收 | 本 Attempt 的冻结交付 + TaskAcceptanceSpec | 明确要求的名称、路径、种类、指标是否实际交付 |
| Controller 的最终 gate | 科学意见 + 同 Run 登记证据 + ConclusionRequirements | 最终引用、种类和明确输出条件成立，再生成最终报告 |

这些检查针对不同事实，不能因使用同一种工件类型就省略接收端复验。

### Agent finish 与可纠正候选

Coding/Experiment 和 ArtifactRegistry 共用 Components 的 `resolve_artifact_source`：候选文件必须在授权 workspace 或本 Attempt 的 output_dir 中唯一定位。缺失或歧义文件、重复 output_name 进入本次 finish 的拒绝回执，原 Task/Attempt/Session 继续修改提交；三个 Agent 共用输出名唯一性检查。回执带稳定 code 和具体文件/名称，未新增对外诊断 schema。连续失败上限、预算和超时继续生效。

越权、IO 异常和登记时的证据损坏不转成可接受结果；接收端错误沿现有失败路径处理。Coding/Experiment 的 execution_record 排在候选前面，使后续某候选登记失败时仍保留执行事实。正常、失败和中断共用 Components 的提取逻辑，从本 Agent 完整原生工具回执生成既有 results 结构；没有实际执行结果则不生成。中断保存实际 command、停止确认、耗时、日志和已知退出码，不补造字段或验证半成品。记录随 AgentResult.artifacts 经 Scheduler/Registry 接收，Task 报告关联 output_artifact_ids，父方中断通知提供公开工件 ID，不读取子 Session 或另造交接格式。声明 failed 也要先通过候选事实检查，不自动修改文件名或忽略非法候选。

统一的 finish 提交 `status/report/artifacts`；status 复用 ModuleStatus 的 completed/failed（缺省 completed），不增加另一套业务结果或生命周期。CompletionCheck 接受候选后，AgentLoop 将其映射到唯一的 AgentResult.status；模型声明失败标为 AGENT_REPORTED_FAILURE，报告保留模型的原因，不能冒充系统验证的 TOOL_FAILED。failed 仍沿现有 Session/Attempt/Task 失败与依赖阻断路径返回，预算、权限及真实技术错误不受模型声明覆盖。

- Coding 观察本 Attempt 的实际差异，生成 patch、变更文件与已有验证记录；covers_current_workspace 表示版本、环境与工作区匹配，passed 单独表示该验证批次的退出结果。当前版本测试失败可以覆盖当前代码，旧版本测试通过也不证明当前版本通过。分析任务可以无修改完成，未执行验证不能被写成已通过。
- Experiment 可分析已有结果；执行后由代码生成完整 execution_record。完成检查只验证候选事实，任务是否完成由 Agent 根据目标与证据声明；它无需制造失败命令来表达无法完成。
- Scientific 校验意见、引用工件的登记授权和冻结 hash，并生成 observation_trace。完成检查复用共享种类集合，模型只能创建 SCIENTIFIC_ARTIFACT_KINDS 中排除系统/工具生成种类后的工件；已有输入证据必须引用其 ID，不能重新作为输出交付。输入/外来/未经登记或被改写的 Ref、重复 Ref 和重复 output_name 在原 AgentLoop 中反馈纠正；本 Session 新登记的合法工具 Ref 仍可原样交付。注册层的身份/hash/磁盘复验继续保留，未知故障不会被无限重试。

Scientific 的 CompletionCheck 通过 Components 的 `missing_required_artifacts` 查询授权登记 Ref 并验证整份冻结 hash。缺少明确输出时，本次 finish 的拒绝回执包含 `required_artifact_missing` 与名称，继续同一 Session，可 request_work 补交或 ask_user；预算、超时和连续拒绝上限继续生效。登记前，Scientific 自己的合法命名 finish 候选可作为本次拟交付；它们必须经接收端实际登记后才能满足最终 gate，候选提议本身不保证通过。

### TaskAcceptanceSpec：当前 Attempt 的明确交付

| 字段 | 验收规则 |
|---|---|
| required_metric_keys | JSON 顶层有限数值，排除 bool |
| required_artifact_paths | 实际登记来源路径满足明确要求 |
| required_artifact_kinds | 本 Attempt 实际交付对应 kind |
| required_output_names | 本 Attempt 实际交付对应精确逻辑名称 |

Scheduler 使用冻结的同一 TaskAcceptanceSpec。未明确要求的条件不从 Agent 名称或任务文本猜出；不以不同命令的退出码推断任务语义，也不接受已删除的 require_successful_execution 字段。

<a id="final-report"></a>

### ConclusionRequirements：Run 最终交付

最终 Run gate 校验科学意见的结构、证据归属及冻结 hash、所需证据种类及明确输出名；访问记录只用于审计，不是完成或引用门禁；存在失败或阻塞工作时（包括编译失败、未创建 Task），检查 limitations 非空，局限是否充分解释其科研影响由 Scientific 判断。它通过 ArtifactRegistry 查询同一 Run 的实际登记表，共用授权 reader 和冻结 hash 规则；跨 Run 产物、仅磁盘存在的文件或未登记候选不满足要求。缺失输出返回 `code=required_artifact_missing`、`message="required artifact was not produced"`、`subject=名称`，阻止完成并保留证据。损坏或不可读的登记文件仍沿原错误路径拒绝，不伪装成普通缺失。通过后由确定性报告渲染器登记最终报告，再将 Run 标为 completed。inconclusive 可以是合法完成；Run completed 不保证假设成立或科学结论正确。

<a id="identities"></a>
<a id="attempt-session"></a>

## 8. 身份、状态、资源与版本

Run 表示完整研究请求，WorkRequest 是其中一轮需求，Task 是图节点，Attempt 是一次执行尝试。Scientific Session 属于 Run、跨工作回合复用；Coding/Experiment Session 属于 Run+Task+Attempt。SessionRef 只暴露 id、module、state_uri、status、created_at、updated_at，不把私有状态变成上游接口。

### Attempt 与状态所有权

暂停回答沿用 Attempt；retry 新建 Attempt。Attempt 保存 report、artifact_ids、SessionRef、error、acceptance_ref 及起止时间。running/needs_user_input 不含终止时间或错误；终态必须有 finished_at，failed/blocked/cancelled 必须有 error。编号连续且从 1 开始。

<a id="states"></a>
<a id="state-mapping"></a>

| 对象 | 状态 | 所有者 |
|---|---|---|
| Run | pending / running / paused / completed / failed | Controller |
| WorkRequest | requested / compiling / executing / stable / consumed / failed / cancelled | Controller |
| Task | pending / running / completed / failed / blocked / needs_user_input / cancelled | Scheduler |
| Attempt | running / completed / completed_with_warnings / failed / blocked / needs_user_input / cancelled | Scheduler |
| AgentResult | completed / completed_with_warnings / failed / blocked / needs_user_input / request_work / reply_to_task / abandon_work / yielded / interrupted | Agent，接收端校验 |
| Session | active / completed / failed / blocked / paused / cancelled | Agent/runtime |

completed_with_warnings 作为成功 Task 参与依赖，但保留 Attempt 状态和 warnings。业务 needs_user_input 先唤醒直接父，根问题和操作批准才暂停等待用户；request_work 仅用于 Scientific，将控制交回 Controller。Run 是否完成由最终 gate 决定。

### 运行中输入、打断与继续

普通输入立即追加到控制 inbox，不取消当前模型请求，也不丢弃有效回复。已采纳工具批次正常完成后、下一模型判断前让原 Scientific 处理补充；活动子任务在此边界让出，Scientific 定向澄清后恢复同一 Attempt/Session。长命令不会因普通输入终止，需立即停止时使用 interrupt。用户主动暂停时补充可以保存，仍需明确 resume。

显式 interrupt 关闭本 Run 派发，通过统一 CancellationToken 停止本地模型/HTTP等待及受管进程组和后代，保存真实回执后才确认 paused；不是只让主 Agent 暂停而留下子训练。模型未采纳回复失效，迟到回复不进有效历史或执行工具。已完成调用保留真实结果，未派发调用为 not_executed，运行中操作记录实际取消范围。远端已经发生的行为或供应商计算不保证撤销，不退还已派发调用预算。

普通工具未返回时唯一原回执报告中断；ask_user/request_work 已返回 accepted+ID 的，原回执保持不变，后续状态用第三部分真实运行通知关联该 ID。模型取消不伪造 tool call。有效根问题保留，普通输入不代答。活动 WorkRequest 及未完成子 Task/Attempt 确认停止后进入 cancelled；完成任务、文件、日志、已冻结工件、预算与旧子记录全部保留，无自动回滚或重放，部分文件不自动算成功工件。相关旧问题/未消费批准撤销。

继续只恢复 Scientific 原 Session。它收到旧委派完成/取消事实和可复用 workspace/工件，再按需提出新 WorkRequest（新身份）；旧未完成子 Session 不复活。普通补充使原工作不再适用时，Scientific 可用受限 abandon_work 结束自己的有效委派，不自动创建替代任务。短原子文件写入/登记结算为一致的完整状态再响应取消；安装取消使旧环境认证失效，复用前须核验。Git 回退不属于此接口。

JSON RunStore 适合单进程、单写入者；单个快照可原子替换，但 Run、Session、命令和文件不是共同事务。中断不保证外部命令未发生，失败不自动回滚代码或环境。原生 Scientific 按控制来源身份复用已完成交付的结果，终态 Session 返回保存的原结果，重复返回的新增 llm_calls 为零；这不推广为所有 Port 或外部工具的 exactly-once 保证。

<a id="workspace"></a>

### 工作区

```python
class WorkspaceAccess:
    read_paths: list[str] = []
    write_paths: list[str] = []
    denied_paths: list[str] = []

class WorkspaceGrant:
    root: NonEmptyStr
    access: WorkspaceAccess
    source: WorkspaceSourceKind

class WorkspaceSpec:
    workspace_id: WorkspaceId
    source_kind: WorkspaceSourceKind
    location: str | None = None
    environment: EnvironmentSpec | None = None
    access: WorkspaceAccess

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

`WorkspaceSourceKind`：GIT（clone 到受管目录）/ LOCAL（原地绑定，managed=False）/ COPY（复制已有本地 Git 工作树）/ GENERATED（创建空受管工作区）。LOCAL/COPY 必须指定真实 Git 仓库根，不能指定子目录；LOCAL 可绑定 linked worktree，COPY 暂不支持 .git 指针文件或符号链接，避免副本共享源仓库的 Git 状态。Git 快照遇到可读范围内的 submodule 明确拒绝，不将遗漏其内容的快照认证为工作区未变化；被拒绝或无关范围中的 submodule 不阻塞其他范围。

`WorkspaceSpec` 是逻辑来源声明，`location` 可包含仓库 URL 或本地来源路径，但不是 Attempt 的物理授权；`environment` 是 workspace 级的环境约束（上游指定 Python 版本时为硬约束）。`WorkspaceRecord` 是解析后的记录，`managed` 由 source_kind 派生（非 LOCAL 为 True）。`WorkspaceDescriptor` 是 Compiler 可见的最小工作区摘要，不含物理路径。

WorkspaceSpec/WorkspaceGrant 共用必填 access。路径是工作区相对前缀，不是 glob；允许列表 `[]` 表示无权限，`["."]` 表示全工作区。write_paths 必须包含于 read_paths，denied_paths 对读写优先拒绝。子授权只能收紧，不能清空父级排除项获得访问。工作区由可信组合根配置，在 create_run 时解析物理根并保存来源及权限；不是 ResearchRequest 的模型可写字段。后续调度使用 Run 中的记录，即使组合根配置改变也不扩大已有 Run 授权。仓库 materialize 仍在 Agent 调用准备阶段进行；模型只能引用已授权逻辑 ID，不能自填物理根路径。

WorkspaceBoundary 每次检查真实路径、软链逃逸与授权；`.git`、`.resagent2` 受保护，`__pycache__`、`.pytest_cache` 等只是可忽略的普通缓存，可按写权限清理。系统输出目录、冻结工件、数据集和环境缓存由各自组件管理，不因此授予源目录写权限。

当前进程使用宿主账户，Shell 审批和固定命令规则不是 OS 沙箱。execute_commands 授权程序执行，prepare_environment 管受控环境工具；后一开关不构成任意脚本的环境写入隔离。没有隔离后端时，仅完整可读写、无用户 denied_paths 的工作区允许通用脚本/验证/安装执行；只读或局部授权即使打开执行权限并批准也不能绕过。完整授权仅适合可信代码，不保证脚本无法访问宿主其他路径或元数据。

Coding 失败后在原期限内尽力收集诊断 patch；诊断失败保留原结果的错误、Session、计量和已有工件，以 error.details.diagnostic_patch_error 说明原因，并禁止自动重试。

Coding 使用 components 的内部 `GitBaseline.tree_hash` 表达 Attempt 起点；恢复时从 Session memory 恢复同一基线，不重新扫描为新起点。差异与验证新鲜度检查沿用此基线；环境或代码变动不能由旧验证冒充当前状态。Experiment 不生成启动快照；Coding/Experiment 完成检查与登记共用文件来源解析，workspace 文件仍由 WorkspaceBoundary 验证授权。

<a id="resources"></a>

### 数据集与环境

```python
class DatasetRef:
    dataset_id: NonEmptyStr
    relative_path: str

class EnvironmentSpec:
    python_version: str | None = None
```

#### 数据集目录与当前可用性

`dataset_root`（`ResourceLayout.dataset_root`）是所有数据集的共享根，不是某个数据集目录。部署者在根下 `catalog.json` 维护 `dataset_id → relative_path`；CLI 与真实 E2E 共用的 build_application 把 DatasetCatalog 经现有 DatasetRefSource Port 注入 Controller。调用方不提交目录路径或完整表。Controller 在推进回合及回答后的恢复入口读取目录，将新增引用保存到 `ResearchRun.dataset_refs`，不改写 ResearchRequest。

Run 中保存的是本 Run 累计发现的目录引用，不是实际使用清单、完整数据内容或数据版本快照。已有同名引用不允许改路径，不因后来删掉 catalog 条目而撤销 Run 中已有引用；但实际目录的存在性会重新检查。数据内容不复制、不做整库 hash，不承诺目录内部数据未被外部修改。

Controller 把目录引用冻结为 Run 级 dataset_catalog 工件；Controller/Scheduler 经 AgentRequest.input_artifacts 传递当前目录，三个 Agent 注入同一 ResourceLayout，各次调用通过共享 `resolve_dataset_refs` 检查。它返回一个轻量 DatasetAvailability：available 为 `{dataset_id, path, access="read_only"}` 列表，unavailable_ids 为目录暂不存在的 ID。上下文与命令环境映射使用同一个检查结果，不维护第二份可用性状态。

- catalog 缺失意味着当前没有新登记；已登记目录缺失标为不可用，不阻塞无关工作。
- 共享上下文明确区分 `available_dataset_ids`（已登记且目录存在）与 `unavailable_dataset_ids`（已登记但目录不存在）；两边都没有的 ID 在当前视图中未登记，不表示可用。当前任务需要的 ID 不在 available 列表时，应先 ask_user 再做依赖该数据的工作，不能只检查 unavailable 列表。
- 非法 JSON/登记格式、重复 ID 引用、绝对或越界路径（含软链逃逸）仍明确报错。
- 只有可用目录进入 `RESAGENT2_DATASETS_JSON` 的 ID→路径映射；该变量是 JSON 内容，不是 catalog 文件路径；`catalog.json` 固定在 dataset_root 下。Coding 验证与 Experiment 正式命令均获得该映射及 `RESAGENT2_DATASET_ROOT`。
- Agent 在运行中判断需要什么；缺少所需数据时用已有 ask_user，用户放置并登记后回答。恢复时重新检查，口头“已准备”不使目录自动变为可用；所需数据仍不在 available 列表时应再次询问，曾经问过不等于可以继续依赖该数据的工作。旧命令结果描述当时的资源状态，不覆盖本轮重新检查的视图。
- 目录存在只证明可定位；内部文件缺失或内容错误仍需从实际读取诊断。prompt 要求请求用户处理，不下载、不猜路径、不替代数据；这是行为指引，不是 OS 沙箱或强制资源选择器。

数据集通过精简 ID 目录呈现，不自动扫描或下载，也不保证大规模资源检索或实时热更新。

#### 环境绑定、认证与安装

环境能力由 Coding 与 Experiment 共用：

依赖需求可由代码和运行时反馈发现，需要时 prepare_environment / run_setup。run_verification / run_shell 在操作获准后、实际命令执行前自动核验尚未认证的绑定；run_shell 随后在执行前使旧认证及关联验证失效（即使只读脚本也如此），避免任意脚本修改依赖后沿用旧成功；Coding 记录 Shell 的实际文件变化，非零退出和超时也保留变化。audit_env 仍可显式调用以诊断环境。镜像与 pip/conda 包缓存属于部署/包管理器配置，不新增到 ResearchRequest，也不与数据集登记表合并；同名依赖或缓存命中不代替环境审计。

新 Run/Workspace 绑定可能需要创建环境并重新安装依赖；包管理器缓存不等于可直接复用的已认证环境，也不保证无需网络或安装开销。安装计入 Run 执行时间。

- `EnvironmentSpec.python_version` 有值表示硬约束，Agent 不得静默覆盖；为空表示 Agent 依据项目自行判断；
- 环境归属 `run_id + workspace_id`：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用），不同 Workspace/Run 隔离；`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`；
- 三个共享 Tool（capabilities 的公开 Python API）：`prepare_environment` / `run_setup` / `audit_env`。新绑定或真正开始 prepare/setup/shell 时，`EnvironmentBinding.generation` 更新且 `certified=False`，清除旧环境信息快照；执行成功、失败或抛异常都不能保留旧认证，参数/策略拒绝则不改变代次；
- 问答恢复不信任旧认证。获准命令执行前的自动核验使用同一 Run 截止时间，失败则不运行命令；这是该命令的固定前置检查，不新增模型调用或另一轮命令批准。实际自动核验结果保存在该命令的 ToolObservation.value.env_audit 和 Session memory.env_audit；已有认证时不重复执行探针；
- Coding 的成功验证还须属于最新 edit revision、当前已审计的 generation。setup/shell 后或新进程恢复后，只重新 audit 不会让旧验证复活，必须再验证；
- run_setup 接受裸名 python/python3 -m pip install、pip/pip3 install 及 conda env update；pip 实际运行绑定环境的绝对 Python。拒绝调用者指定其他解释器、目标目录或用户安装位置（含参数缩写）。确认继续绑定原工具参数和环境前缀，执行记录保存实际构造命令；部署层 pip 配置、镜像与缓存仍为可信输入，不改变或禁用。安装构建脚本仍属于可信进程，不构成系统沙箱。
- prepare 成功、setup 返回（包括非零退出）和显式/自动 audit 的工具回执附 environment_information，来自同次受控只读查询。平台、设备、驱动与绑定 Python 的包版本（含 PyTorch version.py 静态构建信息）供 Agent 判断，不纳入基础审计 success；查询缺失、失败或局部超时只标明未知，不选择依赖、不导入框架、不证明 GPU 可运行。查询遵守 Run 截止时间；若已完成安装后预算耗尽，保留安装回执并标明诊断未完成，后续操作仍由原预算检查限制。环境信息不是新增公共请求字段或资源授权。
- Python 版本优先级、硬约束不可覆盖、每 Attempt 最多两次版本切换：见 ADR-0009。

<a id="schema"></a>

### schema 版本

Python 包版本与 wire schema 独立演进。当前公共模型和 AgentState 只接受 **27.0**，旧 26.0 及更早 Run/Session 明确拒绝恢复，不迁移、不重写。此版显式 Tasks、纯路由、直接父问答与运行中 input/interrupt 改变控制状态；固定 prompt/schema 同 Session 必须保持稳定。本轮执行记录与动态预算不新增持久字段或状态，公共 schema 仍为 27.0；固定原生规则和控制工具说明已经修正，旧固定契约的 Session 会因指纹不符而拒绝恢复，证据保持原样。原有准确操作批准及拒绝说明、UUID 工件身份和完整目录继续沿用，不恢复旧控制/搜索/index 工件或重复正文工作集。

Run 顶层的嵌套公共 request 等带 schema；RunStore 重校验整个状态。SessionStore 先检查顶层 schema，再校验模型；缺版本不能靠默认值接受。恢复还检查协议、endpoint/model 和固定 prompt/tools 指纹。读取、解码、解析或状态校验失败返回明确 contract_error，不调用模型、不覆盖损坏记录，也不伪造 SessionRef。

<a id="exports"></a>

### 公共导出

完整导出见 [contracts 包入口](../../packages/contracts/src/resagent2_contracts/__init__.py)。主要分组是 AgentRequest/AgentResult/AgentPermissions/ControlSignal、RunBudget/ExecutionLimits/RunPermissions、WorkspaceAccess/ActionSnapshot、工件与要求内容模型、工作流与绑定、身份状态、资源授权，以及 WorkRequest/WorkResult/ScientificOpinion。Runtime 的 AgentState、ToolObservation、FinishCandidate、CompletionDecision 和 ContextSection 仍由 runtime 定义；内部工具观察不等于跨 Agent 结果信封。

业务升级问题的 `related_question_id` 绑定原子问题；委派取消只使其关联升级失效，不删除无关主问题。`reply_to_task.user_input_event_id/user_answer_question_id` 指向已授权真实记录，由代码复制原话/值和时间；下游答案的 `original_question_id` 保留原用户问题身份。模型输入只投影一份有序 `business_dialogue`。

输入邮箱的 `settlement` 是最终完成提交与发布的互斥边界；生产 UI 调用 `submit(validate_run=...)` 时必须在发布锁内重新检查 Run 可接收输入，不能依赖界面之前读到的状态。无此回调的直接 submit 仅是底层发布原语。Scientific 完成候选的登记操作键包含结果 Session 的不可变 updated_at 快照，使延后完成后生成的新候选不覆盖先前冻结结果；恢复同一待接收结果仍使用原键。
