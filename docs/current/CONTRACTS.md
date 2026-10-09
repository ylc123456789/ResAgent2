# 模块接口与契约

当前公共契约为 **schema 24.0**。三个 Agent 共用 `invoke(AgentRequest) -> AgentResult`：业务输入是 `instruction + input_artifacts`，业务输出是 `report + artifacts`。身份、权限、预算、状态、恢复和控制信号保持结构化。每个 Agent 只有一种调用和业务模式。

本页说明调用边界、字段和接收规则。职责看 [架构](ARCHITECTURE.md)，模型可见内容看 [上下文](CONTEXT.md)，公共模型以 [models.py](../../packages/contracts/src/resagent2_contracts/models.py) 为准。当前入口为进程内 Python 方法。

## 先认识接口中的对象

一次研究从用户提交需求开始。Controller 调用 Scientific 判断下一步；Scientific 可以直接使用工具，也可以通过 `request_work` 提交工作需求。后者由 Compiler 转成任务图、Scheduler 调用 Coding/Experiment 执行，结果再交回 Scientific。工件登记和状态保存贯穿这些调用。

| 层次 | 对象 | 解决什么问题 |
|---|---|---|
| 用户入口 | `ResearchRequest`、`ResearchRun`、问答 | 要完成什么，以及整次研究的授权、预算和当前状态 |
| 统一 Agent 调用 | `AgentRequest` → `AgentResult` | 给一个 Agent 指令和材料，接收报告、产物或控制结果 |
| 工具与材料 | `ToolObservation`、`ArtifactCandidate` → `ArtifactRef` | 一次工具动作得到什么；哪些内容已冻结登记、可按 ID 读取 |
| 工作编译与执行 | `WorkRequest`、`Workflow`、`Task`、`Attempt` | 将科学工作需求转为可执行任务，并记录每次真实尝试 |
| 返回与持久状态 | `WorkRecord`、`WorkFeedback`、`SessionRef` | 向 Scientific 交付执行事实；恢复时沿用正确的任务和会话 |

几个容易混淆的名称：`WorkRequest` 是 Scientific 请求工作，`AgentRequest` 是系统实际调用某个 Agent；`ToolObservation` 是 Agent 内部的一次工具回执，`AgentResult` 是整个 Agent 调用的对外结果。`ArtifactCandidate` 尚未登记，`ArtifactRef` 指向已冻结的原件，索引只负责导航。下面先查调用边界，再查对应字段和接收规则。

<a id="boundaries"></a>

## 按调用边界查找

| 调用方向 | 入口 | 输入 → 输出 | 位置 |
|---|---|---|---|
| 用户入口 → Controller | create_run / import_literature / answer_question / run_until_stable | ResearchRequest / PreparedLiteratureImport / UserAnswer → ResearchRun | [用户与控制](#entry) |
| Controller → Scientific | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module)、[科学决策](#scientific) |
| Controller → Interpreter | WorkInterpreter.interpret | 已配对 WorkRecord → 固定组织的原报告正文 | [反向交接](#interpreter) |
| Controller → Compiler | WorkflowCompiler.compile | WorkRequest + 图/路由/执行限制 → CompilationResult | [工作编译](#compiler) |
| Scheduler → Coding / Experiment | ModulePort.invoke | AgentRequest → AgentResult | [统一调用](#module) |
| AgentLoop → ToolRegistry → Tool | dispatch / execute | arguments → ToolObservation | [工具与运行](#tools) |
| Tool / Agent / 组合根 → Components | 普通 Python 调用 | 授权、资源、命令、事件 → 操作结果或内容投影 | [普通组件](#components) |
| AgentLoop → 领域完成检查 | CompletionCheck.evaluate | 真实记录 + 完成提议 → CompletionDecision | [完成验收](#completion) |
| 生产方 → Registry；Agent → reader | register / read_text | Candidate → Ref；授权 Ref → 内容 | [工件](#artifacts) |

<a id="conventions"></a>

公共模型继承 `ContractModel`，拒绝未知字段并校验版本。方法签名只规定调用形状；接收端还须校验身份、归属、状态和实际消费。机器状态不从报告文本推断，类型合法也不证明科学结论正确。

`NonEmptyStr` 在校验时去掉首尾空白，并拒绝空值；例如 `report` 和 `ModuleError.message` 不能只包含空白。Controller/Scheduler 将异常转为公共错误时，使用去空白后的异常消息；没有消息时使用异常类名，保证失败本身也能被合法保存。

<a id="entry"></a>

## 1. 用户入口与问答

`ResearchController` 是唯一 Run 业务入口：

- `create_run(run_id, request, *, literature=())` 创建 Run、登记可选导入论文，并推进到完成、失败或等待用户。
- `import_literature(run_id, literature)` 只给已暂停 Run 增加论文材料。
- `answer_question(run_id, answer)` 校验当前问题、记录回答，再继续同一个 Run。
- `run_until_stable(run_id)` 推进可恢复执行，不制造答案或绕过暂停。

Scheduler 只执行 Controller 接受的任务图，不创建第二条 Run 控制链。CLI 与 E2E 分别装配依赖，业务行为共用 Controller。

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

Agent 返回 `needs_user_input`、paused Session 和指向 `question` 工件的 ControlSignal。Orchestrator 分配并保存 PendingQuestion；用户只提交 `UserAnswer(question_id, values, answered_at)`。values 的键集合必须等于保存的问题字段；options 是供用户选择的提示，不是额外的字符串枚举校验。

Controller 从 PendingQuestion 配对原题，生成 `RecordedAnswer`：question_id、question_text、requested_fields、options、values、answered_at、run_id，以及 Task/Attempt 或 Scientific Session 作用域。它被冻结为 `answer` 工件，通过 input_artifacts 交回对应 Agent；`resume_artifact_ids` 标识本次恢复实际要消费的材料。完整问答也作为本 Run 的材料进入科研目录，Scientific 可以按原 ID 读取。阅读历史答案不消费批准、不恢复原任务，也不改变答案的作用域。

操作确认复用同一问答入口。`QuestionDraft / PendingQuestion / RecordedAnswer.action` 可携带 `ActionSnapshot`：action_id、工具、已校验参数、实际目录/环境/目标，以及 Run/Task/Attempt/Session 身份。Session 保存当前 pending_action，恢复时只消费本次 answer 工件。执行前重验权限、预算及目标，并先持久消费批准；下次相同命令仍需新的批准。批准不扩大授权，不再是当前待答问题的答案或字段不匹配的回答在状态修改前拒绝。消费后即使前置审计失败或进程中断也不恢复批准；缺少执行回执时不自动重放。

确认暂停时原操作尚未执行，提交同意答案也不自动执行。恢复后的 Agent 通过同一 invoke 重发准确工具和参数，权限层才匹配答案并执行。Runtime 从既有 pending_action 投影必需的 pending_operation 段；它不新建批准状态、不重复判定授权。拒绝、快照变化和消费后的再次操作仍走原检查；一次副作用可以对应确认前后两次工具调用。

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
| permissions | 必填 AgentPermissions：execute_commands、prepare_environment、request_work |
| workspace、workspace_id、workspace_spec | 物理授权、逻辑工作区身份和来源声明 |
| environment_spec、output_dir | 环境约束和系统指定的输出位置 |
| confirm_commands | 继承 Run 的逐次外部操作确认要求 |
| parent_session_id、resume_artifact_ids | 恢复所属 Session，以及此次 answer 或 work_feedback 工件 ID |

permissions 是操作授权，WorkspaceGrant.access 是文件访问上限，都不构成业务模式。当前 Controller 只给 Scientific `request_work=True`，不授予命令执行、环境准备或源工作区；Scheduler 给 Coding/Experiment 继承 Run 的两项操作授权和确认开关，并从已保存的工作区记录派生文件授权。任务图不携带自行扩权字段。可写工作区不强制修改，只读源目录也不禁止通过受控工件通道交付分析。工作区声明与授权必须一致，解析后的范围不能大于声明。

resume_artifact_ids 必须唯一、属于 input_artifacts 且指定 parent_session_id；仅允许 answer 或 work_feedback，同次不能混用。接收端另外校验工件内容中的 Run、Task/Attempt、Session 与当前调用一致。

### AgentRequest 怎样进入模型输入

请求字段不是完整模型 prompt。三个 Agent 的 builder 和 Runtime 将它投影为统一四部分：

| 部分 | 输入边界 |
|---|---|
| 固定契约 | Agent 职责、系统规则和工具 schema，由系统提供 |
| 任务需求 | `instruction` + 本 Session / Task / Attempt 作用域内的累计问答；回答先经归属与冻结内容校验 |
| 原生协议历史 | Session 的 assistant/tool 成对回合；`request_work` 仍是普通控制工具调用 |
| 当前任务上下文与完整索引 | 当前状态、必要恢复材料、近期读取片段，以及授权输入 ∪ Session 工具输出形成的唯一完整 `artifact_index` |

目录不携带正文、URI、hash 或权限，不能授予读取权；正文仍通过读取工具取得。`answer` 只在任务需求中展开，不重复进入 `material_<artifact_id>`；验收要求和当前工作反馈使用各自经过验证的材料投影。Scientific 的 `ResearchIndex` 是代码侧派生导航，模型只收到一个 `artifact_index`，其 groups 仅引用 `artifact_ids`。`Run.artifacts`/Registry 仍是登记权威。

重构、材料窗口、历史压缩和整份请求计量统一见 [CONTEXT](CONTEXT.md)。必需内容不能容纳时明确抛 `ContextBudgetExceeded`，不静默删减目录。

<a id="module-result"></a>

### AgentResult

| 字段 | 用途 |
|---|---|
| status | ModuleStatus，确定性机器状态 |
| report | 非空说明，表达发现、结果和局限 |
| artifacts | ArtifactCandidate 或本次所属且已登记的 ArtifactRef |
| session | 子模块拥有的 SessionRef；暂停控制结果必填，其他结果可为空；提供时其状态必须与结果对应 |
| control | 只含 action 和工件定位，不复制业务正文 |
| error、warnings | 结构化错误和非致命警告 |
| llm_calls | 本次调用用量的诊断投影；严格非负整数，拒绝 bool；Run 以发送前持久占用为准 |

`ControlSignal.action` 是 ask_user 或 request_work；`artifact_id` 与 `candidate_index` 必须且只能填写一个，分别指向返回列表中的 Ref 或 Candidate。ask_user 必须指向 question，request_work 必须指向 work_request。只有 Scientific 可以返回 request_work。

| status | error / control | Session（提供时） | warnings |
|---|---|---|---|
| `completed` | 均为空 | completed | 必须为空 |
| `completed_with_warnings` | 均为空 | completed | 必须非空 |
| `failed` / `blocked` | 必须有 error，control 为空 | failed / blocked | 可保留 |
| `needs_user_input` | 无 error；ask_user control | 必须 paused | 可保留 |
| `request_work` | 无 error；request_work control | 必须 Scientific paused Session | 可保留 |

接收端重新校验整个结果、Session 所有权、工件归属和恢复绑定，并登记本次已交付的答案。交付登记不证明自定义 Port 实际阅读或理解了材料；原生 Agent 通过必需上下文读取本次恢复工件。错误返回不能抹掉已发生调用或已登记工件；report 不能自行决定状态。

<a id="payloads"></a>

### 业务材料的唯一入口

答案、数据集目录、验收要求、工作反馈和科学意见均是工件内容，不再增加独立的领域请求/结果外壳。通用信封只提供相同的输入输出位置；不同领域仍可用明确的内容模型校验 JSON。

<a id="artifacts"></a>
<a id="artifact-models"></a>

## 3. 工件、来源与可信边界

### 候选、登记与读取

| 对象或操作 | 表示什么 | 不代表什么 |
|---|---|---|
| `ArtifactCandidate` | 生产方提议保存的内容或文件 | 尚未登记，不可用自造 ID 引用 |
| Registry 登记 | 解析合法来源、冻结内容并保存可信归属 | 不判断材料相关性或论断是否成立 |
| `ArtifactRef` | 已登记原件的 ID、hash、归属和描述 | 引用本身不能绕过授权与磁盘复验 |
| reader | 按授权 ID 核对整份 hash，再读取指定范围 | 核对完整性不等于记录已阅读；分页不等于流式 IO |

ArtifactCandidate 含 `kind`、相对 `path`、`media_type`、`summary`、可选 `output_name`、`metadata` 和 UTF-8 `content`。有 content 时 path 是存储文件名；无 content 时须在授权工作区或本次 output_dir 解析唯一实际文件，同一路径在两个根目录都存在时拒绝。绝对路径、`..` 和越界软链拒绝。任务工件的 TaskId 在写入前校验；所有登记入口通过同一个函数生成 artifact_ 加完整 64 位 SHA256 的 ID。随后冻结内容、计算文件 sha256 并记录来源后生成 ArtifactRef。用于路径验收的 source_path 由实际源文件解析，忽略候选 metadata 中的自报值。

登记的工件 ID 统一为 `artifact_<64位小写十六进制SHA256>`，只要求在同一 Run 内唯一、稳定。生成函数对规范 JSON `[登记类别, 稳定身份]` 计算 hash；类别仅参与内部计算，不出现在 ID 外观中。Task/Attempt/来源关系由已有 Ref 字段保存，文件名和 output_name 不由 ID 推断。

| 登记入口 | 沿用的稳定身份 |
|---|---|
| Task 产物 | task_id、attempt_number、产物序号 |
| 导入材料 | 文件内容 hash、kind、来源 metadata；不含调用者文件名 |
| Scientific 材料 | session_id、内容 hash、候选描述 |
| 系统材料 | kind、Run/Session/Task/Attempt 作用域、内容 hash |
| 最终报告 | 同 Run 的固定最终报告槽位 |

统一编码不改变原有复用语义：Task/最终报告槽位中的内容变化仍拒绝覆盖；导入改名复用原件；材料快照保留内容与来源差异。存储绝对路径不参与身份计算。ID 中的 hash 编码登记身份，Ref.sha256 校验文件字节，两者用途不同。决定与恢复边界见 [ADR-0024](../history/decisions/0024-unified-artifact-identifiers.md)。

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
| work_record | WorkRecord | Scientific Session / controller_work_record |
| research_index | ResearchIndex | Run / research_index |

以上来源由 Orchestrator 登记；Agent 不得把普通候选工件伪装成系统材料。question/work_request 的候选由控制工具产生，接收端按对应控制语义登记。系统材料的 producer、归属形状和 source_type 在契约层与 Registry 使用同一规则。

Scientific 的普通产物归属 Session，包括 literature_search、literature_paper、literature_pdf、literature_fulltext、web_search、web_page、scientific_opinion、scientific_assessment、observation_trace 和 module_report；Coding/Experiment 产物归属 Task+Attempt。导入材料和最终报告归属 Run，由 Orchestrator 记录 import/final_report 来源。

### 执行记录的信任范围

注入的进程内 ModulePort 与原生 Agent 的确定性 finalizer 是可信实现。模型 finish 候选不能提交 `execution_record`、`verification_result` 或 `observation_trace`；原生代码从真实命令回执、验证状态和读取观察中生成它们。Coding 的 patch 同样从实际差异生成。

任务是否完成由 Coding/Experiment 通过统一 finish 的 status 声明，不从命令 argv 或历史退出码推断。系统生成的 execution_record 保留 run_shell、run_setup 实际命令结果及其事件顺序；未执行的 blocked 回执不是命令事实；一次操作失败不自动等于任务失败，后续成功也不抹去旧记录。任务报告解释失败与交付的关系，Scientific 判断科研充分性。

Controller/Scheduler 只消费公共 AgentResult 和登记工件，核对来源、hash、状态及内容，不读取下游私有 Session。这一边界防止模型叙述冒充执行事实；它不隔离具有任意 Python 执行权限的恶意自定义 Port。替换 Port 必须遵守相同的可信生产约定。

Coding 每次实际验证使用独立日志目录（UTC 时间戳加唯一标识）。同一代码版本下重跑验证或恢复 Session 后再次验证，都保留各次 stdout/stderr；执行记录中的路径始终对应那次执行，不覆盖旧日志。绑定环境下，验证使用裸名 python/python3 或 pytest，由工具解析为绑定环境的 bin/python（pytest 通过 -m pytest），显式解释器路径在审批前拒绝。

命令已返回的真实回执不因后置 Git 检查失败而丢失；诊断失败记录原因并标为新鲜度未确认。批量验证中断保留已记录结果，剩余命令列入 unrecorded_commands，不虚构退出码或声称它们一定未开始；该批次不能被当作完整通过。Run 期限继续生效，超时后 Session 仍保留已执行回执，不为了保存回执延长预算。

报告用于解释；`module_report` 可保存需要下游分页读取的长说明。报告不替代原始测量、代码或文献。拿到 Ref 不代表已读正文，读过一次也不表示全文始终在模型上下文中。

<a id="scientific"></a>

## 4. 科学控制与工作交付

Scientific 同样接收 AgentRequest 并返回 AgentResult，使用同一 prompt、动作集合和 finish 协议；判断、提问、请求工作和完成是该流程的控制结果。

<a id="work-request"></a>

### request_work：提出下一轮工作需求

`WorkRequestDraft` 表达 objective、expected_evidence、constraints、input_artifact_ids，不含 Task 身份、物理路径或调度字段。Scientific 的 request_work 工具同时接收 assessment，将 assessment 与 work_request 配对在一个 work_request 候选中；控制信号定位该工件。request_work 在发出控制信号前复用授权 reader 检查 input_artifact_ids 的同 Run 登记与冻结 hash；未知、跨 Run、缺失或损坏输入返回 ok=False，由原 AgentLoop 反馈纠正，不把完整性检查记为阅读。Controller 保留接收端复验，负责分配 WorkRequest 身份并编译。

`ScientificAssessment` 包含 statement、evidence_artifact_ids、limitations、unresolved_questions。ask_user 同样带当前 assessment，便于保留暂停时的科学判断。

<a id="work-outcome"></a>

### WorkOutcome / WorkRecord / WorkFeedback：接收执行事实

`WorkOutcome` 记录 work_request_id、workflow_revision、summary 和每项 WorkTaskOutcome；后者保留任务状态、解释、工件 ID、错误和 warnings。失败/阻塞项必须有错误，成功项不含错误。

Controller 将原 WorkRequestDraft、WorkOutcome、未解决任务结果和本轮历次 Attempt 配对为 `WorkRecord`，冻结为 work_record。`WorkFeedback` 保存这些材料的反向交付：run_id、work_request_id、session_id、work_record_artifact_id、index_artifact_id 和 report。它不再复制完整执行记录。反馈保存在 feedback_refs[work_request_id]，Scientific 仍通过原 invoke 和 resume_artifact_ids 接收。

<a id="interpreter"></a>

### 反向交接与科研目录

`WorkInterpreter.interpret(record: WorkRecord) -> str` 是注入 Controller 的普通 Python 接口，不是第四个 Agent。生产与测试共用 DeterministicWorkInterpreter，不调用模型。

| 内容模型 | 保存的字段 | 用途 |
|---|---|---|
| `WorkRecord` | run_id、work_request_id、session_id、previous_work_request、work_outcome、unresolved_task_outcomes、attempts | 冻结一轮工作的配对事实；attempts 保留任务/尝试身份、状态、报告、工件 ID 和错误 |
| `WorkFeedback` | run_id、work_request_id、session_id、work_record_artifact_id、index_artifact_id、report | 返回给 Scientific 的正文及原件入口；不再复制整份执行记录 |
| `ResearchIndex` | run_id、groups | 从授权材料与执行事实重建科研导航 |
| `ResearchIndexGroup` | key、title、artifacts | 按初始材料、Scientific 材料或原 WorkRequest 分组 |
| `ResearchArtifactEntry` | artifact_id、kind、summary、output_name、attempt_number、execution_status、source_artifact_id | 原件描述与直接来源关系；不保存路径、hash 或权限 |

`ResearchIndex` 为科研材料分组，排除控制性系统工件和 observation_trace，但保留 answer 与 work_record。模型的完整 `artifact_index` 另包含本次全部授权输入和 Session 工具输出；其 groups 引用上述分组中的 ID，控制材料可在目录中但不进入科研材料组。这是同一批登记原件的不同导航用途，不是第二套工件存储。

**报告组织规则。** Interpreter 按既有任务顺序和真实 `attempt_number` 选择最新已记录报告，逐字保留；空报告与未执行明确区分，不把 instruction 当结果。历史尝试在正文中只列序号及状态，报告原文留在 WorkRecord。`attempts[*].summary` 来自当前保存的 `attempt.report`：同一次尝试问答恢复会更新它，不代表每次调用的完整历史。

原目标、期望证据与约束取自 WorkRequest；任务、尝试、产物沿用原 `task_id`、`task_id + attempt_number`、`artifact_id`。`output_name` 保留精确值，不把同名当同一产物，不从报告解析或创建身份别名。状态、错误、累计警告和工件 ID 取结构字段；报告不是新的测量或状态权威。

**交付与恢复规则。** Controller 冻结并验证 WorkRecord 后组织报告，保存到 `feedback_refs` 后复用。恢复只刷新完整目录，不重生成已保存报告；Scientific 有效返回后才消费 WorkRequest。结构配对、授权或 hash 错误明确失败。Interpreter 不读私有 Session、不判断科学有效性、不消费或重置模型用量，仍受 Run 时间边界约束。

**模型输入与原件。** Scientific 通过统一 `artifact_index` 获得完整目录；本轮反馈另有必需事实框和可伸缩报告。事实框来自同源 WorkRecord，报告省略与截断明确标记，最小必需内容不能容纳则显式超限。完整 WorkRecord / WorkFeedback 可通过 `read_artifact` 展开，窗口规则见[文本读取](#text-io)，投影与配额见 [CONTEXT](CONTEXT.md)。

目录、报告呈现和完整性校验不冒充工具访问记录。`observed` 只用于追溯，不阻塞提问、委托、引用或完成；实际引用仍须属于本 Run 授权登记表并通过完整性校验。成对问答按原作用域入目录，阅读不等于批准或恢复。

<a id="opinion"></a>

### scientific_opinion：最终科学观点与明确交付

Scientific finish 使用统一的 status/report/artifacts，完成意见只接受 status=completed，并提交一个 `scientific_opinion` JSON 工件。证据不足可表达 inconclusive、请求工作或提问，不能以 status=failed 绕过意见与明确交付检查。ScientificOpinion 含 verdict、statement、evidence_artifact_ids、limitations、unresolved_questions、recommended_next_steps；supports/refutes 至少引用一个工件。required_evidence_kinds 来自 conclusion_requirements，当前支持 literature_paper 或 literature_fulltext；按精确 kind 检查，literature_search 搜索回执不能满足论文证据要求。

Controller 在 Run 创建时将 `required_evidence_kinds` 与 `required_artifacts` 冻结为 `ConclusionRequirements`。后者仅按本 Run 已登记 `ArtifactRef.output_name` 精确、区分大小写地匹配，不按 path、文件名、kind 或 metadata 匹配，不从自然语言补全。`OutputName` 为 1–128 个 ASCII 字母、数字、下划线、点或连字符，以字母开头，不接受目录分隔符、空白或 glob。重复要求按存在语义去重；不同登记产物可同名，所有匹配的冻结文件都须通过 hash 校验。单次 finish 内原有输出名唯一性规则仍有效。

`required_artifacts` 不要求 Scientific 观察或引用该产物，也不评价内容含义；`required_evidence_kinds` 仍独立要求引用对应种类的授权工件，不要求预先进入 observed。两者是 Run 最终要求，不新增 WorkRequest/Task 字段；TaskAcceptanceSpec 继续检查其所属 Attempt 的明确交付。

原生 finalizer 从真实工具访问生成 observation_trace；Controller 保留合法访问记录供追溯。模型不能自行填报访问历史，但是否访问过不再作为 ask_user、request_work 或 finish 的前置门槛。引用的身份、归属与冻结 hash 仍由代码检查；正文是否足够、能否支持论断由 Scientific 判断。访问记录不证明读完全文或论断成立。

<a id="literature"></a>

### 文献材料：搜索、获取全文、读取分别调用

```python
literature_search(query, source='auto', scope='topic', page=1,
                  max_results=5, start_year=None, end_year=None)
fetch_literature_fulltext(paper_artifact_id)
```

| literature_search 参数 | 规则 |
|---|---|
| `query` | 普通关键词或双引号短语；字面双引号和反斜杠须转义，不输入供应商字段前缀或 Boolean 操作符 |
| `source` | auto / arxiv / openalex；auto 仅第一页，优先最近成功来源，仅不可用时有界切源；合法空结果不继续扫源，显式来源不回退 |
| `scope` | topic 查询标题/摘要关键词或短语；title 将完整查询规范化为一个标题短语，外层引号可省略，不保证精确或唯一匹配 |
| `page` | 正整数；续页必须显式指定来源 |
| `max_results` | 1–20，默认5 |
| `start_year` / `end_year` | 可选，1900–2100，起年不得晚于止年 |

搜索回执和反馈保存实际 `source`、`executed_query`、`page`、`total_results`、`source_attempts`、`next_request`、`error_type`、`retry_after`，状态区分 `results / empty / failed`。`next_request` 保留查询、scope、年份与数量，并绑定实际来源。总数缺失不证明耗尽；OpenAlex 页号分页最多覆盖前10000条，触及边界也不代表穷尽结果。

| 产生的工件 | 保存什么 | 后续怎么用 |
|---|---|---|
| `literature_search` | 一次查询、来源/分页/失败事实与论文引用 | 回顾搜索动作；不是论文证据 |
| 每篇 `literature_paper` | 来源元信息和来源提供的完整摘要；`metadata.paper` 是规范化记录 | 工具预览摘要前500字符并给出 abstract_truncated；完整摘要用 read_artifact 读取 |
| `literature_pdf` | 注册来源提供的 PDF 原件 | parse 失败仍保留；不作为 UTF-8 正文读取 |
| `literature_fulltext` | 解析后的文本、页码及解析限制 | 用 read_artifact 按需读取；获取全文不等于读过全文 |

论文按明确的规范化 key 识别，保留 arXiv 版本。同 Run 中只有 key 和规范化元信息快照一致才复用，反馈标记 `reused`；同 key 内容变化会生成新不可变快照。不按标题或摘要相似度猜同一论文。

全文工具只接收授权 `literature_paper` 的 ID，不接受模型任意填写的 PDF URL。衍生工件的 `metadata.paper_artifact_id` 指向所属论文，`metadata.source_artifact_id` 表示直接来源：PDF → paper、解析文本 → PDF。目录只投影直接关系，不复制原件、权限或完整来源图。

同 Run 按 paper_artifact_id 复用已冻结 PDF/全文，解析失败后的重试复用 PDF；所有复用仍校验登记与 hash。已导入本地 PDF 优先离线解析，导入不伪装成在线搜索回执。工具回执区分 `ready`、`unavailable`、`download_failed`、`parse_failed`；失败不生成伪造全文。解析用 pymupdf4llm，关闭 OCR；图、公式和表格可能不完整。

PDF 解析默认上限300秒，同时受 Run 剩余时间约束。CLI 正整数环境变量 `RESAGENT2_PDF_PARSE_TIMEOUT_SECONDS` 由组合根绑定解析器；Components 不自行读部署环境变量，程序化调用可注入解析器或超时。这不是 LLM 可改参数，也不随 Run 超时自动扩大，不能保证任何长度的论文都成功。超时终止受控解析进程，保留已冻结原件。

<a id="web"></a>

### 通用网页工具

```python
web_search(query, max_results=5)
web_fetch(url)
```

**启用与调用。** 只有组合根配置搜索 provider 时才注册 `web_search`；CLI 在已有 DeepSeek key 时默认使用托管搜索，也可显式选 Tavily 或关闭。无 provider 时不暴露搜索工具。`web_fetch` 独立抓取网页，不依赖搜索 provider。

`max_results` 为1–10，控制预览数量，不控制 provider 实际返回总量、托管搜索次数或费用。单次请求仍受字节与时间上限约束，不保证分页或穷尽搜索；工具不按关键词或域名自动决定相关性。

**搜索存储与回执。** 每次搜索保存一个 `web_search` 工件，包括 provider、query、provider_query 和本次收到的全部规范化结果。每条结果包含标题、URL、snippet 及可取得的发布时间；snippet 最多2000字符，不是页面全文。省略的结果用 `read_artifact` 查看。

| 回执字段 | 精确含义 |
|---|---|
| `query` / `provider_query` | 工具输入 / 实际提交供应商的查询；不保证等于供应商内部改写词，内部查询只在可取得的私有 full trace 中核对 |
| `result_count` / `omitted_count` | 保存的结果总数 / 未进入工具预览的数量 |
| `truncated` | 仅表示预览省略，不表示搜索工件已截断 |
| `incomplete_reason` | provider 提前停止的原因，独立于预览裁剪 |
| `error_type` / `retry_after` | 搜索失败类别 / 可取得的等待提示 |

| status | ok | 触发条件与处理 |
|---|---|---|
| `results` | True | 收到有效规范化结果 |
| `empty` | True | 明确空的原生结果列表且无工具错误；不证明资料不存在 |
| `partial` | True | DeepSeek `max_uses_exceeded` 且已取得有效来源；保留来源，incomplete_reason 记录该限制 |
| `failed` | False | 到达上述限制但无来源：search_limit_exceeded；其他供应商错误、畸形条目、未完成响应或缺少原生结果块仍整体失败 |

失败也保存搜索回执，供 Scientific 自主决定改词、换来源、重试或停止；不登记论文或页面正文。`partial` 表示取得部分可用线索，不表示搜索完整或研究任务完成。

**DeepSeek 后端。** 另发一次独立模型请求，提示为 `Perform a web search for the query: {query}`，无 Agent/Session 或主对话历史，也无自动重试。只取原生 `web_search_result` 中的标题和链接，按供应商批次顺序保存并按精确 URL 去重；snippet 只取 URL 匹配 citation 的 `cited_text`，缺少时留空。生成回答不替代 Scientific 的判断，不作为证据；不透明 `encrypted_content` 不解释为摘要。调用计入同一个 Run 预算，见[计量](#trace)。

**页面抓取。** `web_fetch` 成功登记一个 `web_page`，保存 source/final URL、title、提取文本、content type、parser 和 fetched_at；正文通过 `read_artifact` 分段读取。失败返回 error_type / retry_after，不生成页面工件。

| 抓取与提取边界 | 规则 |
|---|---|
| URL 与连接 | 仅无凭据 http/https；最多5次重定向，每次连接仅使用核对过的公网地址；DNS、HTTP、读入都受 Run 截止时间约束 |
| 内容类型 | 仅 HTML/XHTML/text；严格 UTF-8，拒绝 NUL、PDF 和其他二进制 |
| 链接 | 保留可见文字与合法 http(s) 地址；相对地址按重定向后的 final URL 解析；空/非法/非 http(s)/带凭据地址不保留，但保留可见文字 |
| 代码块 | pre 保留换行、缩进、空白和嵌套 code/span 连续文本；普通说明文字规范化空白 |
| 浏览器能力 | 不执行 JavaScript、不提供浏览器会话、不模拟布局、不解释 HTML base，不自动抓取链接或下载文件 |

网页搜索和文献搜索都向统一工件目录提供材料。网页结果不自动转换为 `literature_paper`，论文的精确 kind 证据规则仍独立成立。Scientific 依据用户目标选择搜索、抓取、阅读或委托，没有固定的“先网页后论文”流程。

<a id="compiler"></a>

## 5. 工作编译、路由与输出绑定

### compile：从工作需求到任务图

```python
compile(request: WorkRequest, *, current: Workflow | None,
        registry: WorkflowAgentRegistry, limits: ExecutionLimits,
        workspaces: list[WorkspaceDescriptor] | None = None) -> CompilationResult
```

成功返回 CompilationResult(output, llm_calls)，output 为 WorkflowProposal 或 WorkflowPatch；编译错误抛 CompilationError，并保留实际 llm_calls。预算耗尽和超时分别抛 BudgetExhaustedError、DeadlineExceededError，由 Controller 保存对应终止原因。Compiler 不读下游 Session，不直接修改 Run 状态。LLM 编译必须处于可信调用方绑定的共享 execution_budget 中，直接使用该余额和期限。

LLMWorkflowCompiler 请求一个 CompilationDraft，由确定性代码物化正式身份、解析工作区并校验。正文解析或结构校验失败时最多纠正一次，无额外语义复审调用。Compiler 使用 PromptLLMClient 的 JSON 输出路径，无 AgentLoop、工具或 Session；客户端根据 action_type 提供输出 schema，Compiler 提示不重复嵌入；上下文和调用消费仍受共同预算约束。

<a id="workflow"></a>

### 图与任务字段

TaskProposal 包含 id、work_request_id、workflow_agent_kind、instruction、depends_on、workspace_id、已有 input_artifacts ID、未来 input_artifact_bindings、output_names，以及可选 acceptance_spec。它不携带单独的扩权或确认开关。

LLM 草图只给逻辑 key、路由、instruction、依赖、逻辑工作区和工件交接，不编造指标键、文件路径、验收策略、操作权限或运行身份。外部确定性调用方可以提供明确的 TaskAcceptanceSpec；自然语言证据要求仍须保留在 instruction 中。

Scheduler 接受时将 output_names 合入 required_output_names，将非空验收要求冻结为 acceptance_requirements。WorkflowTask 与后续每个 Attempt 绑定同一个 acceptance_ref；请求把该 Ref 作为输入材料，不再复制一份可漂移的验收要求。

`depends_on` 表示上游成功，是执行顺序约束。失败修复须在观察真实失败后提出新的 WorkRequest，追加修复与重跑任务。WorkflowPatch 只追加任务，绑定 based_on_revision，不改写历史 Attempt。Proposal/Patch 共用同一候选图校验，依赖只限本轮任务；依赖失败按拓扑顺序传播，当前工作轮任务全部终态后才生成稳定 WorkOutcome。

### FutureArtifactBinding：引用还未产生的输出

`FutureArtifactBinding(source_task, output_selector)` 必须选择直接依赖声明的逻辑 output_name。上游成功后，Scheduler 从其最新成功 Attempt 查找唯一匹配 Ref，再加入下游 input_artifacts。缺失、重复、跨归属或尚未完成的来源均拒绝；执行前解析失败会保留不可重试的 failed Attempt，不调用 Agent。模型不能预猜未来 ArtifactId。

<a id="capabilities"></a>

### 执行 Agent 路由

`WorkflowAgentKind` 只有 coding、experiment；WorkflowAgentRegistry 每种路由只登记一次。Scientific 由 Controller 调用，不是执行图节点。模块路由与 capabilities 包里的工具能力是不同概念。

<a id="tools"></a>

## 6. AgentLoop → 工具与运行机制

### 工具参数与 ToolObservation

模型工具协议归 Runtime；通用模型入口由 Capabilities 提供，其公开导出只有 Tool 和对应输入模型。[Coding 验证工具](../../packages/agents/coding/src/resagent2_coding/verification.py)、Experiment 执行工具与各模块控制工具仍由所属 Agent/Runtime 提供。普通操作改从 `resagent2_components` 导入，不通过 Capabilities 转发。Tool 仍按下列协议运行，Python 文件移动不改变模型动作名、参数或公共契约。

ToolRegistry 按动作名找 Tool，以 input_model 完整校验 arguments，再调用 `Tool.execute(state, parsed_arguments) -> ToolObservation`。工具不直接写 AgentState，返回 memory_updates / 候选工件 / 控制结果，由 Loop 应用；但可实际写文件、运行命令或改变 EnvironmentBinding，并非纯函数。

| ToolObservation 字段 | 含义 |
|---|---|
| `summary`、`value` | 非空简短说明和可选 JSON 内容 |
| `ok` | 机器可读成功标志；非零退出、参数拒绝、缺文件等可恢复失败为 False，不能解析 summary 猜状态 |
| `memory_updates` | 由 Loop 合入 Session 的确定性更新，不是第二份外部结果信封 |
| `question` / `request_work` / `finish_candidate` | 至多一个非空；普通工具可全为空，Loop 负责形成对应控制或完成结果 |

所有 AgentLoop 客户端通过 `next_tool_call` 返回原生工具回合。每个 `Tool.input_model.model_json_schema()` 与说明共同生成 `tools`；实际注册表决定工具可用性并在执行前完整校验参数。三个 Agent 不维护自己的工具名 Literal，也不解析模型正文作为备用动作。仅有 `next_action` 的客户端不能运行 AgentLoop；Compiler 的结构化输出使用独立接口。

| 可注入入口 | 约定 |
|---|---|
| AgentLoop.run(definition, request, *, session_id, initial_memory=None) | 循环、观测、反馈、Session，不调度 Workflow |
| ContextBuilder(request, state, max_context_tokens) | 返回固定ContextSection及按需渲染的ContextMaterial；Runtime补反馈与原生历史容量，builder不预占独立硬额度 |
| ContextComposer.compose(...) | 先保留固定段与材料导航框，再按权重分配、按优先级借用；完整请求统一计量，最小required仍装不下明确失败 |
| LLMClient.next_tool_call(context, schemas, turns, *, max_input_tokens) | Agent 客户端必需方法；返回 ToolCallTurn，不执行工具；须提供稳定非空 tool_session_key |
| StructuredLLMClient.next_action(context, action_type) | 无状态结构化输出接口，供 PromptLLMClient / Compiler 使用 |
| OpenAICompatibleClient.next_tool_call(context, schemas, turns, ...) | AgentLoop 原生工具调用；返回一轮 assistant/tool-call 协议数据，不自行执行 Tool |
| OpenAICompatibleClient.summarize_history(prompt, max_input_tokens=...) | 可选纯文本历史交接；共用传输/trace/attempts，不执行工具，输出配置仍来自 ModelProfile |
| PromptLLMClient.next_action(prompt, action_type) | 普通提示复用 Composer/计量，无 Tool/Session/Loop |
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

原生每轮接受1–8个 tool calls，整批先校验参数/权限，再逐项复核权限与超时、串行执行。`finish / ask_user / request_work` 必须独占一轮；零个、超量或混合控制工具的批次拒绝。中途失败保留已完成结果、取消余下项，不回滚副作用；assistant content 不作为备用动作解析。

Loop 保存已发生 HTTP 尝试，把简短拒绝原因和“未执行工具”放入 required runtime_feedback，在同 Session/Attempt 内继续；不把坏正文或 reasoning 放入反馈。JSON、原生 framing、schema 拒绝、工具失败和 finish 拒绝共用连续失败上限5、调用预算与期限。成功非 finish 工具重置计数；工具正常返回观察后清除 tool_error 来源旧反馈，完成检查反馈沿原检查流程更新，完成时清除。达到停止边界则返回已有失败出口，不保证模型会纠正。

Compiler 不运行 AgentLoop，也不使用原生工具：编译草图经 `PromptLLMClient.next_action` 从正文 JSON 获取结构。其 JSONDecodeError 在已有“最多两版 draft”内携带解析原因重编，所有消耗保留；没有新一层重试。PromptLLMClient 只透传异常并记录 last_attempts，不自行纠错。JSON 能解析但字段不符仍走原有 schema 校验。响应封装缺失/非字符串 content 等协议错误不伪装成模型正文解析错误。

HTTP 解码失败在请求所属边界转为现有领域错误：搜索保存失败回执工件并更新目录，网页抓取返回失败回执、不登记正文。Run 截止和预算异常继续交给运行时处理，不冒充网页超时；ModelRequestClient 保持单次请求计量。

### 原生 Session 身份与恢复

创建 Session 时写入 `AgentState.tool_protocol_key`。OpenAICompatibleClient 的 `tool_session_key` 是协议、endpoint、model 的稳定 hash，不含 API key；其他原生客户端也须提供非空、稳定且能标识其协议配置的身份。恢复时身份须完全相同；旧正文 JSON Session、endpoint/model 变化均拒绝，不自动迁移。

`AgentState.tool_turns` 按轮保存 assistant `content`、`reasoning_content`、原始 `tool_calls` 和按 call ID 配对的 `tool_results`。Loop 先保存整批 call，每次派发前保存 executing_call_id，随后按 observation/event 路径配对该调用的 receipt 并清除执行标记。重启保留已完成回执；正在执行且缺回执的项记为 unknown outcome，其余缺回执项记为未开始；不自动重放。它防止把未知结果误判为成功，但只保证进程重启 checkpoint，不保证掉电持久化，也不是外部副作用的 exactly-once 事务。

下一轮原生请求由系统指令、已配对的历史 assistant/tool 消息和最新一次重建的业务 Context 组成；不保存或重发此前每轮完整业务 prompt。输入压力下使用可选 summarize_history 生成旧完整交互摘要；history_checkpoint 同时保存摘要与绝对历史边界，近期完整回合继续原样发送。原始历史不删，摘要不替代当前领域状态、回答、证据或完成门禁；失败不推进边界。具体比例和失败边界见[上下文](CONTEXT.md#compaction)。`reasoning_content` 仅用于同一 Session 的供应商协议续传，不进入业务 memory、ToolObservation 或完成证据。

<a id="text-io"></a>

### 文本读取、写入与搜索

读取通常可重复；写入和外部命令不承诺 exactly-once。

| 边界 | 规则 |
|---|---|
| `read_file` / `read_artifact` | 严格 UTF-8，保留原换行；先选物理行，再选 start_char/end_char 零基、末端不含字符窗口；默认返回上限128000字符 |
| 无效文本 | 含 NUL 或无效 UTF-8 返回可恢复错误，不新增成功阅读记录 |
| 工作区文本大小 | 读、搜、创建、替换共用10 MiB上限；读取核对声明大小与实际读入长度，写入前验证编码和最终字节数 |
| 冻结工件 | 不受工作区大小上限约束，读取前校验整份 hash；存在/完整性验证不要求文本可解码 |
| 分段读取回执 | start/end 行与字符字段回显请求范围；truncated 只表示请求窗口被返回上限裁剪；next_start_char 是实际返回末端，相对于完整所选行范围，无余文时为 null |
| IO 与分段读取 | 字符窗口不等于流式读取，不能绕过整份检查或改变冻结内容；连续读取保持行范围不变，不把请求 end_char 当作实际返回终点 |
| `search_text` | 大小写不敏感字面子串，不是正则；a\|b 仍匹配原文字面串 |
| 搜索覆盖 | skipped_count、skipped_files（最多50条）、skipped_files_truncated 描述授权候选中跳过文件及原因；incomplete 表示跳过或触及结果上限，零匹配不能因此当成完整无匹配 |

**模型输入容量。** ModelProfile 声明窗口、输出预留和安全余量，模块声明输入上限，有效额度取两者较小值。Agent 计量完整原生 messages + tools，含历史、schema 与 JSON 转义；Compiler 的结构化请求计量正文及其输出 schema。估算与分配规则见 [CONTEXT](CONTEXT.md#budgets)。输入压缩不另开模型调用额度；step 仅记录时序，不形成第二份预算。

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
| [RegisteredArtifactReader / build_module_report](../../packages/components/src/resagent2_components/artifacts.py) | 授权 ID + 行范围 → 正文；模块说明 → ArtifactCandidate | 读取先校验 Run 与整份 hash；报告生成纯函数，不自行登记或生成科学证据 |
| [LiteratureSearchBackend](../../packages/components/src/resagent2_components/literature/backends.py) | 查询、来源、范围、页号、数量和年份 → LiteratureSearchResult（论文页与来源/分页事实） | auto 首页面向平级来源有界切换；显式来源不回退；失败保留类型与来源尝试，不伪造全文 |
| [WebSearchBackend / WebPageFetcher](../../packages/components/src/resagent2_components/web.py) | provider 搜索回执或单页 HTML/text → 规范化网页结果 | 搜索 provider 可选；响应有界、严格 UTF-8、无 JavaScript/PDF/二进制解析；不决定科学相关性 |
| [load_literature_manifest](../../packages/components/src/resagent2_components/literature/imports.py) | 本地 JSON 清单 → PreparedLiteratureImport 列表 | 验证元信息及可选 PDF，路径相对清单目录；不联网、不登记或恢复 Run |
| [workspace_context](../../packages/components/src/resagent2_components/context.py) | 现有事件、绑定、授权及材料额度 → 段 / 材料 | 不启动 LLM、不写第二份状态；预算分配仍归 Runtime，详见 [CONTEXT](CONTEXT.md#budgets) |
| [文本读写与窗口](../../packages/components/src/resagent2_components/text.py) | 工作区路径 / 文本 → 有界严格读入 / 写入前编码验证；文本 + 行字符范围 → 窗口 | 工作区默认10 MiB，返回默认128000字符；保留原换行，不决定授权或模型预算，工件仍由 reader 验证 |

文献 Tool 通过工件组件中的 `ArtifactRegistrationPort` 接受组合根注入的登记/解析对象；实现仍是 Orchestrator 的 ScientificArtifactRegistration，Components 不反向 import Orchestrator。错误如何转为 ToolObservation、反馈、ModuleError 仍由已有 Tool/Loop/Agent 边界处理，组件不另建恢复机制。

<a id="trace"></a>

### LLM 计量与 trace

**诊断与权威状态。**

trace 是可选诊断输出。两个模型客户端共用私有 JSONL 写入函数；该函数中的 JSON 序列化或文件系统写入失败时发出仅含异常类型的告警，保留模型响应、原始请求错误和 action 校验反馈，不自动重试或再次扣量。Run 用量、Session 等权威状态保存错误仍按原流程处理，不能因 trace 的容错而被忽略。

最小客户端只有 next_action，由 invoke_model 在调用前占用一次。提供 manages_usage 的传输客户端负责通过当前共享预算逐次登记 HTTP 请求和重试；OpenAICompatibleClient 与 PromptLLMClient 遵循这一约定。托管搜索的 `ModelRequestClient` 每次发送前也占用一次共享模型调用，不自动重试；Tavily 和网页抓取只占 Run 时间。last_attempts、trace 等仍用于诊断，不再是 Run 扣费依据。自定义客户端隐藏的重试无法从单次方法调用推断，须接入同一用量接口。

**一次逻辑调用与 HTTP 尝试。**

OpenAICompatibleClient 的 trace 按 call_id 关联逻辑调用和后续校验记录。attempts 保留各次 finish_reason/usage/错误，顶层响应对应最后一次；retry_number+1 是 HTTP 尝试数，不能再加 attempts 长度。request_max_tokens 是实际输出上限，null 表示未指定。

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

这里保留调用方必须遵守的边界；段名、观察窗口、分配权重和压缩算法集中在 [CONTEXT](CONTEXT.md)，不在两份规范重复维护。

| 调用约定 | 必须成立的规则 |
|---|---|
| 失败反馈 | Loop 的动作拒绝、工具异常与 finish 拒绝可形成持久 required runtime_feedback；普通 ok=False 观察不自动全部变成该反馈。工具正常返回后清除 tool_error 旧反馈，完成反馈按检查流程更新/清除 |
| 用户问答 | request_task_context 校验作用域后注入当前作用域累计原题和 values；request_materials_context 跳过 answer。ask_user 成功只代表已发问，不代表已答复或资源已准备；续跑保留原作用域，失败重试的新 Attempt 不自动继承旧问答 |
| 原生历史 | 重放检查点后的完整配对 assistant/tool 回合，末尾加入最新业务上下文；不重复保存每轮完整 prompt |
| 读取材料 | 文件/工件片段只构成本轮工作集，有来源、范围、观察序号、截断/省略标识；旧文件片段可能过时。已读 ID 或短预览不证明全文在上下文中，也不证明论断成立 |
| 执行诊断 | command_results 使用真实命令观察与日志尾部，不依赖短历史预览，不替代当前状态或完成验收 |
| 完整目录 | 当前输入与 Session 工具输出按真实 artifact_id 合并去重，required artifact_index 不选条目或静默截断；目录不是访问授权 |
| 容量 | 三个 Agent 和 Compiler 默认输入上限同源为256000 tokens，各模块可配置；必需段/schema/历史/材料框与正文共同计量，不能容纳则 ContextBudgetExceeded，不自动扩容、暂停或追加摘要重试 |
| 持久原件 | 工作集淘汰或历史摘要不删除冻结工件、Session 原始观测和完整工具回合；Compiler 没有 Session 或协议历史压缩 |

工具返回的128000字符上限是 IO 边界，不是模型 token 容量。详细计量和 required/priority 选择见[构造流程](CONTEXT.md#pipeline)及[预算](CONTEXT.md#budgets)，原生压缩边界见[历史检查点](CONTEXT.md#compaction)。

工具派发、HTTP 重试和批量命令每项都重新检查剩余时间；到期取消请求或终止受控进程树。已经发生的副作用不能撤销，供应商是否停止计算可能未知。连续失败计数是有界纠错的停止条件，不是新步骤预算；具体拒绝和重置规则见[格式纠错](#tools)。

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

Coding/Experiment 和 ArtifactRegistry 共用 Components 的 `resolve_artifact_source`：候选文件必须在授权 workspace 或本 Attempt 的 output_dir 中唯一定位。缺失或歧义文件、重复 output_name 返回现有 `runtime_feedback`，原 Task/Attempt/Session 继续修改提交；三个 Agent 共用输出名唯一性检查。反馈带稳定 code 和具体文件/名称，未新增对外诊断 schema。连续失败上限、预算和超时继续生效。

越权、IO 异常和登记时的证据损坏不转成可接受结果；接收端错误沿现有失败路径处理。Experiment 的 execution_record 排在候选前面，使后续某候选登记失败时仍保留执行事实。声明 failed 也要先通过候选事实检查，不自动修改文件名或忽略非法候选。

统一的 finish 提交 `status/report/artifacts`；status 复用 ModuleStatus 的 completed/failed（缺省 completed），不增加另一套业务结果或生命周期。CompletionCheck 接受候选后，AgentLoop 将其映射到唯一的 AgentResult.status；模型声明失败标为 AGENT_REPORTED_FAILURE，报告保留模型的原因，不能冒充系统验证的 TOOL_FAILED。failed 仍沿现有 Session/Attempt/Task 失败与依赖阻断路径返回，预算、权限及真实技术错误不受模型声明覆盖。

- Coding 观察本 Attempt 的实际差异，生成 patch、变更文件与已有验证记录；covers_current_workspace 表示版本、环境与工作区匹配，passed 单独表示该验证批次的退出结果。当前版本测试失败可以覆盖当前代码，旧版本测试通过也不证明当前版本通过。分析任务可以无修改完成，未执行验证不能被写成已通过。
- Experiment 可分析已有结果；执行后由代码生成完整 execution_record。完成检查只验证候选事实，任务是否完成由 Agent 根据目标与证据声明；它无需制造失败命令来表达无法完成。
- Scientific 校验意见、引用工件的登记授权和冻结 hash，并生成 observation_trace。完成检查复用共享种类集合，模型只能创建 SCIENTIFIC_ARTIFACT_KINDS 中排除系统/工具生成种类后的工件；已有输入证据必须引用其 ID，不能重新作为输出交付。输入/外来/未经登记或被改写的 Ref、重复 Ref 和重复 output_name 在原 AgentLoop 中反馈纠正；本 Session 新登记的合法工具 Ref 仍可原样交付。注册层的身份/hash/磁盘复验继续保留，未知故障不会被无限重试。

Scientific 的 CompletionCheck 通过 Components 的 `missing_required_artifacts` 查询授权登记 Ref 并验证整份冻结 hash。缺少明确输出时返回 `required_artifact_missing` 与名称，经原 `runtime_feedback` 继续同一 Session，可 request_work 补交或 ask_user；预算、超时和连续拒绝上限继续生效。登记前，Scientific 自己的合法命名 finish 候选可作为本次拟交付；它们必须经接收端实际登记后才能满足最终 gate，候选提议本身不保证通过。

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

最终 Run gate 校验科学意见的结构、证据归属及冻结 hash、所需证据种类及明确输出名；访问记录只用于审计，不是完成或引用门禁；存在失败或阻塞工作时，检查 limitations 非空，局限是否充分解释其科研影响由 Scientific 判断。它通过 ArtifactRegistry 查询同一 Run 的实际登记表，共用授权 reader 和冻结 hash 规则；跨 Run 产物、仅磁盘存在的文件或未登记候选不满足要求。缺失输出返回 `code=required_artifact_missing`、`message="required artifact was not produced"`、`subject=名称`，阻止完成并保留证据。损坏或不可读的登记文件仍沿原错误路径拒绝，不伪装成普通缺失。通过后由确定性报告渲染器登记最终报告，再将 Run 标为 completed。inconclusive 可以是合法完成；Run completed 不保证假设成立或科学结论正确。

<a id="identities"></a>
<a id="attempt-session"></a>

## 8. 身份、状态、资源与版本

Run 表示完整研究请求，WorkRequest 是其中一轮需求，Task 是图节点，Attempt 是一次执行尝试。Scientific Session 属于 Run、跨工作回合复用；Coding/Experiment Session 属于 Run+Task+Attempt。SessionRef 只暴露 id、module、state_uri、status、created_at、updated_at，不把私有状态变成上游接口。

### Attempt 与状态所有权

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

`dataset_root`（`ResourceLayout.dataset_root`）是所有数据集的共享根，不是某个数据集目录。部署者在根下 `catalog.json` 维护 `dataset_id → relative_path`；CLI/E2E 各自把 DatasetCatalog 经现有 DatasetRefSource Port 注入 Controller。调用方不提交目录路径或完整表。Controller 在推进回合及回答后的恢复入口读取目录，将新增引用保存到 `ResearchRun.dataset_refs`，不改写 ResearchRequest。

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

Python 包版本与 wire schema 独立演进。公共模型当前仅接受 24.0；本版统一三个 Agent 的任务问答与 artifact_index 投影，删除旧文献/网页输出目录字段，并为 Session 明确保存版本。旧 schema 23 及更早的 Run/Session 不支持恢复，原记录保留不迁移。工件 ID、外部论文导入、按篇材料、全文来源和访问日志边界保持不变。字段删除、含义或必填性变化需要不兼容版本，并覆盖 round-trip、非法组合和恢复边界测试。metadata 不长期承担本应成为正式字段的机器状态。

ResearchRun 顶层没有 schema_version，但必填 request 等公共模型带版本；JsonRunStore.load 重新校验整个 Run，旧版本 Run 拒绝恢复。读取失败不改写原文件，应创建新 Run。已有 state/session/trace 保留，不迁移、不重写、不自动清理。

AgentState 顶层保存 schema_version=24.0。JsonSessionStore.load 在模型解析前检查原始 JSON 的版本；缺版本、23.0 和其他版本明确拒绝，不能用默认值接受旧记录。原生 Session 还必须匹配创建时的协议、endpoint 和 model 身份。不读取旧 memory 目录字段，也不提供迁移或兼容回退。

<a id="exports"></a>

### 公共导出

完整导出见 [contracts 包入口](../../packages/contracts/src/resagent2_contracts/__init__.py)。主要分组是 AgentRequest/AgentResult/AgentPermissions/ControlSignal、RunBudget/ExecutionLimits/RunPermissions、WorkspaceAccess/ActionSnapshot、工件与要求内容模型、工作流与绑定、身份状态、资源授权，以及 WorkRequest/WorkOutcome/ScientificOpinion。Runtime 的 AgentState、ToolObservation、FinishCandidate、CompletionDecision 和 ContextSection 仍由 runtime 定义；内部工具观察不等于跨 Agent 结果信封。
