# Agent 输入输出统一与调用模式简化：旧版方案（不实施）

> **状态：不实施，已被新版完整替代。**
>
> 原方案及后续追加的第 13、14 节仅作历史讨论记录，均不作为开发依据，也不补充新版要求。
> 请使用 [Agent 输入输出统一与多模式清理方案（V2）](UNIFIED_AGENT_IO_V2_PLAN_2026-09-20.md)。新版是一份独立、完整的实施方案。
>
> 原记录日期：2026-09-20；原核对基线：`ae33790`，schema `11.0`。本旧方案未实施。
> 后续开发继续在 `refactor/unified-agent-entry` 分支进行，设计要求以新版文档为准。

---

以下保留旧讨论原文，包含已经放弃或相互覆盖的方案表述；请勿据此实施。

## 1. 本次到底要改什么

保持现有中央编排结构：Scientific 提出研究判断和工作请求，Compiler 生成执行图，Scheduler 调用 Coding、Experiment 等模块，Controller 管理研究过程。此次不增加管理层、不引入 Agent 自主接力、不更换 Runtime 或 Agent 框架。

本轮统一的是 **Agent 的公共输入输出，以及决定如何调用这些 Agent 的接口**：

- 输入：一个自然语言任务字段 `instruction`，一组通用控制字段，一组统一的结构化材料字段，以及系统身份/恢复信息。
- 输出：一个自然语言报告字段 `report`，一个统一的 `artifacts` 字段，以及必要的状态、控制信号和执行记录。
- 同一个 Agent 只有一个公开调用入口。任务差异由 instruction 表达，可执行范围由权限和资源控制，交付要求由通用验收约束表达。
- 全项目清除由业务 mode/capability 选择专用输入、结果、prompt、工具套餐或执行循环的做法。工具仍可按显式权限和环境可用性过滤，内部循环可响应实际事件。Coding 的 understand/modify 是已确认实例，不是本轮唯一范围。
- 保留模块专业分工：Coding、Experiment、Scientific 可以有不同工具和内部算法，但不能要求调用方为同一个 Agent 选择工作模式。

“最少修改”指保留已有机制，只改统一边界及其必要消费者；不是只套一层新名字、让旧模式继续决定实际执行。

## 2. 已核对的现状与差距

| 位置 | 当前实现 | 本轮目标 |
|---|---|---|
| 执行输入 | `ModuleTaskRequest.instruction` 已统一任务文本，但仍有 `capability` 和 experiment 专属字段 | 通用 AgentRequest；去掉模式路由及专属顶层字段 |
| Scientific 输入 | 独立的 `ScientificTurnRequest`，有 instruction、材料、工作反馈和恢复信息 | 采用相同 envelope；差异由结构化材料和执行上下文表达 |
| 执行输出 | `ModuleResult.summary + payload + artifacts`，payload 随 capability 变化 | `report + artifacts`；移除公开模式专用 payload |
| Scientific 输出 | request_work/question/completed/failed 四类结果，携带 assessment/opinion | 相同结果 envelope；保留状态控制，判断内容作为结构化 artifact |
| Coding | capability 选择 understand/modify 的 action、工具和 completion 实现 | 单入口、单 action/finish 形状，按授权和实际行为检查 |
| 编译图 | `capability + goal + constraints + CapabilityInput` | `agent_kind + instruction + 通用任务控制/材料绑定` |
| 调度 | capability 推导写权限，并选择不同 payload 校验器 | 使用显式授权；统一结果接收和产物登记 |
| 产物 | 已有 Candidate → Registry → Ref；Scientific 工具还能在执行中登记 | 保留机制，统一返回约定和消费方式 |

基线中的 capability 枚举只有 `code_understand`、`code_modify`、`experiment_run`。Scientific 文献检索目前是工具，不应凭空描述成另一个已有 Agent 模式。开发时仍需全面检查其他入口、配置、提示词和状态恢复代码，不能仅删除这三个枚举值就宣告完成。

## 3. 统一输入契约

以下名称是本方案的目标公开名称；具体 Python 类型可复用现有类，不要求为了名字复制一套实现。

```text
AgentRequest
  schema_version
  instruction: string
  controls: AgentControls
  inputs: AgentInputs
  context: InvocationContext
```

### 3.1 instruction：唯一的任务语义入口

`instruction` 表达目标、任务要求、解释性约束和交付说明。例如：

- “说明训练循环的数据流，引用关键源码位置；不要修改源码。”
- “修复梯度累积错误，完成相关验证，并说明未覆盖的风险。”
- “按配置运行对照实验，收集准确率与训练耗时；缺少关键参数时询问用户。”

禁止另加 `goal`、`task_description`、`research`、`modify_instructions` 等并列字段重复承载同一次 Agent 调用的任务语义。

材料可以包含论文正文、以前的请求和实验配置，控制信号也可以携带一项待执行的新任务；它们不是本次任务的第二个语义入口。材料中的文字不能自动获得指令或授权地位。

### 3.2 controls：由系统执行的共同边界

| 字段 | 用途 | 约束 |
|---|---|---|
| `budget` | 本次允许的调用数、超时等 | 复用 TaskBudget；Run 总预算、累计计量仍由系统管理 |
| `workspace` | 已解析的逻辑工作区、物理根目录及读写授权 | 复用 WorkspaceGrant/WorkspaceSpec 等现有模型并消除重复；路径由系统绑定 |
| `output_dir` | 系统分配的日志和临时产物目录 | 不是最终 artifact 身份，也不授予任意路径写权限 |
| `permissions` | 允许的操作及需要确认的操作 | 明确控制读、写、进程执行、环境准备等已有操作；工具入口和组件执行层共同落实 |
| `acceptance` | 当前 task 的交付检查 | 检查该 task 产出的 metric 键、文件路径和产物类别；不承担整轮结论接地检查 |
| `conclusion_evidence` | Scientific 最终结论的接地检查 | 只在结论完成门槛使用，检查结论引用的证据是否属于本轮实际观察到的证据 |
| `environment` | 必要的运行环境约束 | 复用 EnvironmentSpec；没有环境操作的调用可为空 |

`permissions` 按实际操作表达，不定义 `analysis_mode`、`edit_mode`、`experiment_mode` 之类的套餐。对实现而言，可复用现有命令策略及工作区检查；不新建一套通用策略引擎。

有效授权取调用方/Run 授权、模块可实现范围、本次任务申请范围的交集。Compiler 只能提出需求，不能自己授予权限。模型文本、材料、artifact metadata 和用户问答文本均不能直接扩大权限。

现有 `WorkspaceMode.READ_ONLY/READ_WRITE` 表达文件访问权限，可以保留；它不是要删除的业务调用模式。代码不得据此重新选择一套 understand/modify prompt 或完成协议。

需要特别分清：

1. **有写权限不代表必须修改文件。** 可写工作区上的解释任务可以没有 patch。
2. **没有源码写权限不代表不能产生报告。** 报告可通过受控 output_dir 或短文本 content 提交，由系统保存。
3. **只把写工具从模型列表隐藏还不够。** 直接调用工具、进程或恢复会话也必须受同一授权约束。
4. 自然语言决定要做什么；程序控制决定能做什么。不要从 instruction 关键词自动推导授权。
5. 当前进程/命令策略不是通用操作系统沙箱。保持现有保护并补齐必要检查，不在本轮宣称获得全新的系统级隔离能力。

现有 `confirm_before_experiment` 迁到 `permissions` 下的操作确认策略：需要确认的是正式实验执行这一操作。提问、记录回答和恢复流程沿用已有实现；执行前由确定性逻辑检查，不能靠提示词自觉遵守，也不以新字段名引入额外默认确认。

`acceptance` 以现有 TaskAcceptanceSpec 扩展为共同结构，至少覆盖 `required_artifact_kinds`、`required_artifact_paths`、`required_metric_keys`；`required_evidence_kinds` 不并入 acceptance；它属于 Scientific 的 run-level conclusion grounding，仍由 Controller 在最终结论处检查 observed 与 cited 证据交集及 evidence kind。仅对已知的精确键/路径填写硬要求，未知要求放 instruction。类型/键要求不得成为选择另一套 Agent 模式的开关。

路径要求在候选来源上校验，指标键要求在符合约定的 JSON 证据中校验；没有适用证据时不能静默忽略。系统仍需检查产物是否属于该任务、内容是否符合要求。空 acceptance 不取消现有的真实性、证据有效性和完成门槛。

### 3.3 inputs：统一的结构化材料入口

```text
AgentInputs
  artifacts: ArtifactRef[]
  datasets: DatasetRef[]
  answers: RecordedAnswer[]
  continuation: ContinuationInput | None
  facts: InputFact[]

InputFact
  kind: 已定义的材料类型
  data: 与 kind 对应的已校验结构
```

| 材料 | 表达方式 |
|---|---|
| 已登记文件、论文、代码证据、实验结果 | `inputs.artifacts`；授权由系统独立校验 |
| 数据集目录 | `inputs.datasets`；保留现有 DatasetRef 解析逻辑 |
| 用户对当前问题的回答 | `inputs.answers`；保留问题 ID、时间及系统配对 |
| 工作恢复上下文 | `inputs.continuation` 中的强类型结构，保留 work request 与 outcome 的成对校验 |
| 精确实验配置或参数 | `inputs.facts` 中的配置对象，避免先转自然语言再猜回数字 |

ContinuationInput 不是任意 facts 列表。previous_work_request 与 work_outcome 必须是强类型成对字段，并在 model validator 中保证同时存在或同时为空；首次调用、恢复调用和 answers 的现有约束继续保留。facts 只承载独立的、已有 schema 的补充材料。

普通 JSON 参数用于提供精确数据；论文、文件及大型结果优先登记为 artifact。不要把所有小型控制事实强制落成文件。

### 3.4 context：系统身份和恢复

包含 `run_id`、`agent_owner`、`task_id`、`attempt_number`、`parent_session_id` 及已有幂等调用所需信息。`agent_owner` 表示模块身份；Compiler 图节点另用 WorkflowAgentKind，只允许 coding/experiment，因此 Scientific 在 schema 层不能进入执行图。

- Coding、Experiment 的执行调用必须携带成对的 task_id/attempt_number。
- Scientific 继续使用 Run/session 归属，不伪造一个 Scheduler task 或 attempt 来凑字段。
- 必需身份按调用来源验证；没有值的字段可省略，不能让一堆可选字段失去跨字段约束。
- 保留首次调用与续跑限制、问题配对、work_outcome 与 previous_work_request 配对、同一 Run 的 artifact 授权以及消费一次等不变量。
- 首次和恢复调用使用相同入口，通过系统会话信息恢复，不新增 `resume_mode`。

## 4. 统一输出契约

```text
AgentResult
  schema_version
  status
  report: string
  artifacts: ArtifactOutput[]
  control: ControlSignal | null
  session: SessionRef | null
  error: ModuleError | null
  warnings: WarningRecord[]
  usage: ExecutionUsage
```

`report` 解释做了什么、得到什么、未完成什么及已知局限。它是唯一的通用自然语言结果字段；不同时保留内容重复的 `summary`、`answer` 和公共 `payload`。

这里统一的**业务输出**是 report + artifacts；status、control、session、error、warnings 和 usage 属于所有模块共用的**运行控制信息**。两者职责不同，不把取消领域 payload 误解为删除必要的机器控制字段。

`status` 保留现有 completed、completed_with_warnings、failed、blocked、needs_user_input、request_work 等机器状态。失败可以有可用日志或部分产物，但不得因为存在产物就标为完成。

`control` 使用按 kind 区分的严格结构，只表达提问或请求工作等控制动作：

- `needs_user_input` 必须有对应 QuestionDraft 和可恢复的暂停会话。
- `request_work` 必须有结构化工作请求和可恢复会话；现有只有 Scientific 可以发起的规则继续执行。
- 成功状态不得同时携带错误、提问或工作请求。
- failed/blocked 必须有 ModuleError；completed_with_warnings 必须有 warning。

这些是统一协议的状态，不是业务模式。不能要求 Controller 从 report 猜测“是否应该暂停”“是否还要派任务”。

status 与 control 不是两个可独立决定的来源，按以下对应关系校验：

| status | control |
|---|---|
| needs_user_input | 必须且只能为 ask_user，含 QuestionDraft |
| request_work | 必须且只能为 request_work，含工作请求 |
| completed / completed_with_warnings | 必须为空 |
| failed / blocked | 必须为空，失败原因进入 error |

`usage` 初期复用实际已有的 `llm_calls` 等可信计量，不借此次改造建立新计费体系。调用数、观察过的 artifact、Session 状态来自 Runtime/工具记录，不采用模型自报值。

输入不可读、写文件失败或产物登记失败时，接收框架必须保留非空 report 和准确错误状态，说明已完成事实、缺失证据及阻碍。报告的保存不能依赖产物登记成功，也不能泄露无权读取的内容。不新造 partial 状态：已有 failed/blocked 和真实部分产物足以表达。Agent 崩溃或超时未生成文本时，Scheduler/Controller 从确定性错误信息生成简明失败报告；宿主或存储自身彻底不可用等情况沿用外层故障处理。

## 5. artifacts 的最终约定

### 5.1 责任划分

```text
Agent / 工具产生文件或短文本
        ↓
提交 ArtifactCandidate
        ↓
系统 ArtifactRegistry 校验、保存、计算 hash、赋予身份
        ↓
ArtifactRef 写入 Run / Session 的可信登记记录
        ↓
上层和后续 Agent 通过授权后的 ArtifactRef 消费
```

Agent 负责产生内容和候选清单；系统负责权威索引。Agent 不分配权威 artifact ID、来源身份或 hash，也不把自己编写的 MANIFEST 当成系统登记记录。

当前实际 Registry 使用系统 artifact 目录和 `file://` URI。本文不要求新增 `artifact://` 协议、远程对象存储、下载服务或一套 delivery 系统。

### 5.2 Candidate 与 Ref

| 类型 | 由谁产生 | 含义 |
|---|---|---|
| `ArtifactCandidate` | Agent 的工具/完成逻辑 | 声明某个文件或文本需要保存，尚未成为可信索引 |
| `ArtifactRef` | 系统 Registry | 系统已登记的身份、位置、hash、生产来源和描述 |

保留现有 Candidate 字段：`kind / path / media_type / summary / metadata / content`。

- `content is None`：path 是授权 workspace 内的相对文件路径，由 Registry 解析、检查并复制。
- `content` 有值：content 是 UTF-8 文本，path 作为保存文件名的相对名称；这适合需作为文件交付的 Markdown/JSON 和派生 patch。普通运行报告只放 report，不必再复制成文件。它不是任意二进制上传字段。
- 大型日志、图像、权重等用文件引用方式提交，不通过模型输出或巨大 JSON 搬运。
- output_dir 在 workspace 外时，现有 workspace-relative path 不能直接指向它。短文本可以用 content；大文件应由系统将候选文件物化到已授权、可登记的位置。确有必须跨根目录提交的调用点时，增加明确来源授权与校验，禁止放开任意绝对路径；不预先重做整个存储层。
- metadata 用于描述，不承载绕过权限、切换模式或跳过验证的控制命令。

### 5.3 执行中已经登记的产物

Scientific 的文献工具目前会在一次 Agent 调用中登记产物，随后同轮就可能 read_artifact。必须保留这种时序，不能一律推迟到 Agent 结束。

为兼容这一必要行为，统一 `AgentResult.artifacts` 使用同一个明确的联合类型：

```text
ArtifactOutput = ArtifactCandidate | ArtifactRef
```

这是模块实现到系统接收边界的受控联合，对下游统一归一成 Ref。两个成员复用现有严格类型，用各自必需字段与 extra=forbid 做确定性判别，拒绝混合字段的对象；这是登记阶段差异，不是不同 Agent 的结果类型。

联合类型不等于允许模型任意提交 Ref。已登记项必须由模块代码从 Registry/Session 的可信登记事件组装；模型生成的同形对象不产生读取权限或生产来源。复用现有记录，不为此引入全新 runtime collector。

系统接收时：

1. Candidate 按现有 Registry 路径登记。
2. Ref 必须与本次任务/session 的可信登记记录匹配，核对 Run、生产者、任务/会话归属、内容位置和 hash；生产者沿用受信 registrar 的实际记录，包括工具代所属 Agent 登记的情况，不能仅由模型填写的 agent_kind 认定。不能因对象长得像 ArtifactRef 就信任。
3. 已登记 Ref 不重复复制、重新编号或冒认成新的产物；重放时按已有 ID 去重。
4. 上游输入的 Ref 如仅被引用，应放在判断产物的 evidence_artifact_ids 等内容里，不伪装为本次新产物。
5. 上层收到的 artifacts 全部归一为 ArtifactRef；候选路径不能继续流入下游。

普通任务由 Scheduler 接收；Scientific 由 Controller 及已有 registrar 接收。二者共用登记/校验规则，不为了统一类型把 Scientific 塞进执行 DAG。

### 5.4 文件保存与不可变性

工作区文件可以继续变化，正式 artifact 表示登记时的内容快照。复用 Registry 的复制、staging、原子发布和恢复机制；hash 以保存后的内容为准。消费者按可信登记记录读文件并校验，不能把 Ref URI 当作任意文件读取许可。

需要消费原始日志或证据文件的派生产物，应保留可访问的正式来源引用；不要把 verification.json 中可能随临时目录清理而失效的绝对路径，宣称为已经持久保存的证据。

校验与复制期间的来源替换、符号链接变化属于登记边界要检查的情形；发现快照与用于验收的内容不一致时应拒绝或重做校验。保持已有恢复机制，不用更复杂的存储架构替代这个具体问题。

这里的“不可变”是应用层的快照与一致性契约；不是操作系统层面保证任何用户都无法修改文件。读取时的 hash 校验仍然必要。

登记失败不得把相关任务发布为成功。已有成功登记和恢复证据保留，避免重试导致冲突、重复提交或伪造完整产物集；本轮不引入跨目录分布式事务。

MANIFEST 可以是给人阅读的报告或额外 artifact，但正式索引由 Run/Session 持久化记录维护。不要新增第二份并行权威。

### 5.5 报告与证据的关系

- report 可以说“测试通过”；验证 artifact 保存执行命令、退出码、超时和相关日志引用。
- report 可以总结实验指标；数值来自确定性处理的证据文件，不从 report 重新抽取。
- report 可以说明代码修改；patch/变更记录承载实际变化及来源。
- report 可以提出研究判断；scientific_opinion artifact 保存 verdict、证据引用与局限。

不是每次任务都必须人为制造文件。简单解释可仅有 report；要求可追溯引用或后续机器消费的结果，应生成相应产物。失败/暂停也可携带真实产生的产物。

## 6. 各 Agent 具体怎么改

### 6.1 Coding

删除公开 `code_understand/code_modify` 路由、两套 action/finish 类型和按 capability 选择循环的分支，使用统一 Coding 入口与结果。

工具是否可调用由授权控制，源码读取、编辑、git diff、验证等底层组件复用。内部可以按实际事件执行校验，例如“本次有源文件变更”“本次运行过验证”，不能再引入隐藏的 `effective_mode=modify`。

完成检查保留必要保障：

- 没有修改的解释任务不要求伪造 changed_files、patch 或验证命令。
- 可写权限本身不触发“必须有变更”的硬门槛；任务要求尚未实现时不能据此宣告完成。
- 一旦产生代码修改，现有变更快照、验证记录、失败验证和未验证编辑检查继续生效。
- 代码证据仍应指向实际读过的源码及对应版本/快照，不能仅由模型填写 evidence_files。

`CodeUnderstandResult`、`CodeModifyResult` 不再作为公共调用结果。其必要数据分别转入说明/代码证据、patch、变更清单、验证报告等 artifact。类型可作为包内验证或 artifact 内容 schema 保留，不能继续决定公开输入/输出形状。

### 6.2 Experiment

只有 `experiment` 模块入口，任务语义来自 instruction，精确参数来自 inputs，环境/权限/预算/验收来自 controls。删除 `experiment_run` 作为业务 capability 的依赖，不另增 analyze/run/repair 模式。

保留环境准备、数据集访问、命令策略、正式执行确认和确定性指标提取。若一次调用仅需要分析已有实验结果，不应因模块名就强制执行命令或要求源码可写；使用到哪项操作就检查哪项权限。不能因支持这种请求取消真实实验任务的必要证据。

`ExperimentResult` 的指标、证据来源、环境和提交信息转入实验结果 artifact。指标继续由程序读取有效 JSON 证据得出；交付问题和局限在报告/warnings 及必要的结构化结果中保留。

### 6.3 Scientific

与其他 Agent 使用相同 envelope，但保留现有科研控制职责：阅读证据、提出判断、request_work、ask_user、finish。

- instruction 承载研究任务，所有已授权文件统一放 inputs.artifacts。
- work_outcome、previous_work_request、unresolved_task_outcomes 收敛进 `inputs.continuation` 的强类型结构，保留配对、归属和一次消费约束；不要降级为自由 facts。
- assessment/opinion 作为有 schema 的 artifact 内容；不能把二者继续放到公共 payload，或暗藏到任意 control.data。
- required_evidence_kinds 仍是独立的最终结论接地要求；Controller 必须验证 opinion 引用属于 Scientific 实际观察记录的 evidence kind，不能用 task acceptance 替代。
- `control` 只携带问题或待派发的 WorkRequest 等控制必需结构；Controller 如需要研究判断，应读已登记判断 artifact。
- 已观察证据、读过哪些产物以及判断引用是否成立，仍以工具/Session 记录检查。不能把 observed_artifact_ids 当成模型自报的可信事实。
- 目前文献检索和阅读工具不变成新的 Agent 模式；中途登记与同轮可读继续支持。
- Controller 最终完成门槛改为读取并验证正式 scientific_opinion artifact，不从 report 猜 verdict，同时保留 required_evidence_kinds 的 observed 与 cited 交集检查。

本次统一并不让 Coding/Experiment 自动获得 request_work 或科研裁决权限。保留各模块职责的授权边界。

## 7. 能力注册、Compiler 与 Scheduler 的最小必要修改

### 7.1 注册表按模块路由

目标注册项是模块身份与说明，例如 coding、experiment；Scientific 保持 Controller 的固定控制入口。描述写清模块擅长的工作和责任，不按“理解/修改/修复”等任务动作拆成多个注册项。

拆成两个类型：WorkflowAgentKind 只允许 coding/experiment，供 Compiler 图节点使用；AgentOwner 可以包含 coding、experiment、scientific、orchestrator，供执行上下文和来源记录使用。Scientific 只能由 Controller/ScientificPort 调用，不能进入 DAG。

`CapabilityRegistry` / `ModuleBinding` 可保留现有容器和装配方式，修改 key、描述和消费者。类名是否随改动统一为 AgentRegistry 是机械整理，不另起插件框架。原 capability 枚举和模式对应关系在最终收口时删除，不能长期保留别名。

### 7.2 Compiler 草案与图字段

如果图仍要求 Compiler 先选择 code_understand/code_modify，再包装成 AgentRequest，旧模式并未废弃。因此本轮必须调整图中的相关契约：

```text
CompilationTaskDraft / TaskProposal 的任务内容
  key 或 id（沿用原身份层级）
  workflow_agent_kind  # 只允许 coding | experiment
  instruction
  workspace_id
  depends_on
  通用权限需求、验收约束及材料绑定
```

`WorkflowTask` 同步使用这套任务内容，继续保留状态、attempts、warnings 等执行历史。

- `goal + constraints + CapabilityInput` 中的任务语义合入 instruction。
- 精确参数放材料，权限放申请/授权，验收放通用结构；不要把这些硬控制都转成散文。
- 删除 CodeUnderstandInput/CodeModifyInput/ExperimentRunInput 作为图的输入联合。
- Compiler 只见逻辑 workspace_id 和系统提供的资源描述，不输出真实根路径、artifact 存储路径、Session/Attempt 身份或任意授权。
- 保留 draft → 确定性绑定、DAG 校验、有界纠错、语义 review、预算检查和 append-only revision 规则。
- review 继续审任务职责、依赖与交付覆盖；不扩成另一层 Agent。

Scientific 发起的 `WorkRequestDraft` 仍是工作意图而非任务图：把重复的 objective/constraints 收敛为 instruction，已有 expected_evidence 的自由文本并入任务说明，精确可检查要求进入共同 acceptance。WorkRequest ID、状态和结果消费方式保持不变。不强求把 Run 顶层用户入口的所有领域数据也改成 AgentRequest。

### 7.3 Scheduler 与 Controller

Scheduler 对 workflow_agent_kind 图节点按模块路由；构造请求时写入 agent_owner，根据已批准权限生成 WorkspaceGrant；不再从 capability 推断可写或选择 payload 类型。

接收结果时统一执行状态约束、产物登记/引用验证、预算与来源检查，然后保存 report 和 artifact IDs。Attempt 等持久化消费者同步移除公开 payload 依赖，保留原状态和恢复语义。

Controller 负责 Scientific 的同一套结果接收与控制信号消费。两者共享必要的纯校验/归一化函数即可，不增加新的通用“超级调度器”。

不同 artifact 可以有不同内容 schema 和校验器。校验器依据产物类型与通用 acceptance 工作，不选择 Agent 模式、不替代权限、更不能仅靠模型声明 kind 就视为已满足交付。

## 8. 字段迁移表

| 旧字段或类型 | 新位置/处置 |
|---|---|
| `ModuleTaskRequest.instruction` / `ScientificTurnRequest.instruction` | `AgentRequest.instruction` |
| `capability` / `CapabilityInput` | 删除；图节点路由用 workflow_agent_kind，执行上下文用 agent_owner |
| 图级 `goal`、`constraints`、inputs 中 question/instructions | 合并为图级 instruction，再传入 AgentRequest |
| `budget` | `controls.budget` |
| workspace、workspace_id、workspace_spec | `controls.workspace` 内归一；避免多个来源互相覆盖 |
| environment_spec、output_dir | controls 中对应资源字段 |
| `acceptance` | `controls.acceptance`；只做单 task 交付检查 |
| `required_evidence_kinds` | `controls.conclusion_evidence` 或 Run-level Scientific completion policy；保留 observed 与 cited 接地检查 |
| confirm_before_experiment | `controls.permissions` 下的操作确认规则 |
| input_artifacts / authorized_artifacts | `inputs.artifacts` |
| dataset_refs / answers | `inputs.datasets` / `inputs.answers` |
| work_outcome / previous_work_request / unresolved_task_outcomes | `inputs.continuation` 的强类型结构；保留成对 validator |
| parameters | `inputs.facts` 中有 schema 的材料 |
| run/task/attempt/parent_session 身份 | `context`；保留来源约束 |
| summary | `report` |
| 公共 payload / CodeUnderstandResult / CodeModifyResult / ExperimentResult | 公开移除；必要内容转为 artifact，内部校验可复用 |
| Scientific assessment / opinion | 已登记的判断类 artifact |
| question / request_work | 严格 `control` 信号 |
| llm_calls | `usage` 中的真实计量 |
| observed_artifact_ids | Runtime/Session 可信记录，供完成验证使用 |
| ArtifactCandidate / ArtifactRef | 保留两阶段含义；共同 artifacts 字段，系统对外归一为 Ref |

## 9. 修改范围与不扩展事项

| 包/入口 | 预计涉及文件或位置 | 必须做的变化 |
|---|---|---|
| contracts | `models.py`、导出、必要 evidence 定义 | 共同契约、旧字段清理、schema 与不变量 |
| coding | `agent.py`、`models.py`、`context.py`、`completion.py` | 合并模式入口、共同上下文/finish、条件式完成校验 |
| experiment | 同名入口/上下文/完成文件 | 共同请求结果、权限驱动操作、结果产物化 |
| scientific | agent/context/models/tools/completion/interpreter | 共同 envelope、判断产物、控制信号和反馈适配 |
| orchestrator | compiler、workflow_validation、scheduler、controller、completion、ports、models | 模块路由、图字段、结果消费及持久化适配 |
| artifacts / components | 现有登记器与读取入口 | 只补共同接收、已登记 Ref 校验和实际必须的来源处理 |
| capabilities | 使用旧 request/result 的工具及装配 | 参数访问/授权适配，不重写具体工具能力 |
| CLI / e2e | 装配、输出、示例任务、fake ports | 去除模式暴露，适配共同契约 |
| tests / docs | 对应测试与当前参考 | 验收新契约，删除失效模式示例 |

不作为本轮目标：重写 LLM 客户端、compaction、Runtime 循环、进程执行器、Git/数据集/环境组件、引入新框架、增加远程存储、扩大默认权限、重做 UI、改为多层管理或自主接力。若某个底层调用确实阻碍共同授权，修改限于该检查/适配点并说明理由。

## 10. 实施顺序与版本处理

全程留在 `refactor/unified-agent-entry`。

1. **契约和消费者清点**：列出所有模式选择器、专用 payload 消费者、权限推导点和恢复持久化字段；定下共同类型与 artifact 内容 schema。
2. **共同契约与接收基础**：实现 AgentRequest/AgentResult、状态不变量、Candidate/Ref 归一与现有 finalizer 的内容序列化。临时适配器仅用于阶段开发。
3. **各 Agent 接入**：Coding 合并入口，Experiment/Scientific 使用同一 envelope；已有真实性检查随结果产物化迁移，不删除了以后再补。
4. **注册、Compiler、Scheduler、Controller 接通**：图/路由/权限和结果消费者一起迁移，恢复与幂等验证同时更新。
5. **全项目模式清理**：移除临时适配器、旧枚举、旧公共 payload 和双入口；检查 CLI、fake ports、trace 和提示词中是否仍要求选模式。
6. **文档与完整验收**：更新当前契约、架构、上下文和示例；基于最终确切代码提交做本地与真实场景验收。

建议下一契约版本为 `12.0`，实际开发前确认没有别的并行改动占用该版本。沿用项目已有跨 schema 策略：旧 11.0 Run/Session 原样保留，新代码明确拒绝直接续跑，不静默改写历史或长期保留旧模式。schema 检查要早于状态写入。阶段兼容不构成最终交付。

本文中的阶段是开发组织顺序，不是引入新运行层。代码实现时逐步保持可验证；不把“公共壳已统一、里面仍全是模式分支”认定为完成。

## 11. 验收标准

### 11.1 契约与全项目无模式

- 所有原生 Agent 与替代/脚本实现使用相同请求和结果 envelope，首次/恢复也一样。
- 新请求拒绝旧 mode/capability/专用输入字段；没有藏在 facts、metadata、profile 或权限套餐里的同义模式。
- Coding 解释/修改、Experiment 执行/已有结果分析、Scientific 文献阅读/研究判断/派工都通过模块单入口表达。
- Compiler、图、注册表、CLI 和真实 trace 不再要求选择 code_understand/code_modify/experiment_run。
- 允许文件权限 mode、模型 JSON 序列化 mode 和生命周期 status；扫描必须分类，不能用搜索单词 mode 的命中数冒充验收。

### 11.2 权限、任务语义与完成门槛

- 同一个 Coding 入口，在只读授权下能解释并交付报告；写操作和间接越权执行被拒绝。
- 可写授权下执行解释任务，不因为没有代码变更而失败，也不被强迫编辑。
- 真实修改任务有变更证据和必要验证；失败验证、未验证编辑、无依据的成功声明不能通过。
- Experiment 受操作权限、命令策略和执行确认约束，缺权限时不扩大授权。
- 改变 instruction 不改变已经批准的权限；Compiler 返回超额权限需求不能直接变成 grant。
- 预算耗尽、失败、重试、暂停、恢复维持原有计量与行为。

### 11.3 artifacts 与事实来源

- 路径越界、`..`、符号链接逃逸、不存在文件以及无权读取的 Ref 均拒绝。
- 短文本 Candidate 和授权文件 Candidate 均可登记；UTF-8 文本与二进制文件路径规则明确。
- 登记后修改源工作区文件，不改变已保存快照；正式文件损坏能被 hash 校验发现。
- Scientific 中途登记的文献产物同轮可读；最终汇总复用同一 Ref，恢复不重复登记。
- 伪造 Ref、其他 Run/Session 的 Ref、冒认输入为输出不能通过来源校验。
- 登记失败不能发布成功；现有崩溃恢复和幂等登记仍成立。
- 输入不可读、写入失败或登记失败能返回准确报告/错误，不把缺失证据伪装成完成；部分已登记产物有明确归属。
- 指标来自真实证据，验证结果来自执行记录，科研判断引用的证据通过授权/观察检查；不把 report 当作事实解析接口。

### 11.4 集成与真实场景

先运行相关契约、Agent、Compiler/Scheduler/Controller 定向测试，再按项目规程运行全量确定性测试、mock E2E 与 `git diff --check`。

真实场景至少覆盖：只读代码理解及报告交付、代码修改与实验串联、失败修复重跑、用户提问与恢复、文献检索及同轮读取、Scientific 基于正式证据完成判断。原有 Flash/Pro 覆盖保留；在最终确切提交上记录模型、退出码、产物、trace 和实际调用数。

新增重点是“同模块、同入口、不同任务语义和授权组合”的对照，不只是旧场景改名。trace 校验先解析外层及 request_text 中的 JSON，再检查实际上下文对象；不能用未经解析的子串匹配，把图字段、system prompt 或序列化 mode 误报为旧调用模式。

历史报告中的 `1058 passed / 1 skipped` 属于 ae33790 基线，不是本轮验收结果。新方案必须有自己的实际执行记录，不能复制旧数字宣称通过。

## 12. 实践参考与采用边界

- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) 提倡从简单、可组合的结构出发，也介绍中央编排的 orchestrator-workers。本方案沿用这一取舍，不据此升级项目的管理层数。
- [A2A Protocol specification](https://a2a-protocol.org/latest/specification/) 区分任务状态、消息和产物。本方案参考其职责分离；具体 Candidate/Registry/Ref 生命周期基于 ResAgent2 现有实现，本文不宣称它是 A2A 的强制存储模型，也不引入 A2A 服务。

最终交付判断：调用方只需说明任务、提供材料和明确控制边界，即可调用相应专业 Agent；系统通过统一状态及正式 artifact 消费结果，不再要求选择同一个 Agent 的业务工作模式。

## 13. 契约边界修订勘误（2026-09-20）

本节优先解释本文前面可能产生歧义的三处表述。

### 13.1 acceptance 与 required_evidence_kinds 必须分离

acceptance 是 Scheduler 在一个 task 结束时做的 task-local 交付检查，只检查该 task 自己产出的 metric 键、文件路径和产物类别。

required_evidence_kinds 是 Run-level 的 Scientific conclusion grounding 规则，在整轮最终结论处检查 Scientific opinion 引用的 artifact 是否已登记、被 Scientific session 实际观察过、被最终 opinion 实际引用并属于要求的 evidence kind。它继续使用 observed 与 cited 证据 ID 的交集，不能迁移成 acceptance.required_artifact_kinds，也不要求某个 workflow task 产生 literature_search 文件，因为该证据可以由 Scientific 工具在 session 中途登记且没有 task_id。

统一接口保留两个不同控制项：controls.acceptance 与 controls.conclusion_evidence。二者可以共享 ArtifactRef 和 kind 基础类型，但不能共享完成门槛、来源账本或判定函数。

### 13.2 图节点类型与模块身份必须分离

不能用一个扁平 agent_kind 同时表示 Compiler 图节点和所有模块身份。WorkflowAgentKind 只允许 coding、experiment，供 CompilationTaskDraft、TaskProposal、WorkflowTask 使用；AgentOwner 可包含 coding、experiment、scientific、orchestrator。

Compiler 图节点只能使用 WorkflowAgentKind，因此 Scientific 在 schema 层不能进入执行图。Scientific 继续由 Controller 或 ScientificPort 调用，可以使用统一 AgentRequest envelope，但不是 DAG task；执行上下文和 artifact provenance 使用 agent_owner。取消多模式不等于删除内部工具分组、action loop 或确定性 finalizer。

### 13.3 Scientific 续跑上下文必须保留强类型成对校验

work_outcome 和 previous_work_request 不能降级为 inputs.facts 列表中的两条任意记录，必须进入强类型 ContinuationInput：previous_work_request、work_outcome、unresolved_task_outcomes。

该结构继续保证：previous_work_request 与 work_outcome 要么同时存在、要么同时为空；首次调用不能携带 work_outcome 或 answers；恢复调用不能同时携带 work_outcome 和 answers；continuation 绑定当前 Run、Scientific session 和 active WorkRequest。校验放在 typed continuation model validator 或 Scientific 适配器中，先于 prompt facts 投影。inputs.facts 只承载独立的、已有 schema 的补充事实。

### 13.4 字段迁移修订

acceptance 进入 controls.acceptance，只检查单 task 交付；required_evidence_kinds 进入 controls.conclusion_evidence 或 Run-level Scientific completion policy，只检查最终结论的 observed 与 cited 接地关系；workflow_agent_kind 进入 Compiler 图节点，只能是 coding 或 experiment；agent_owner 进入执行上下文和 artifact provenance；三个 Scientific 续跑字段进入 typed continuation，不进入无类型 facts。

本勘误不改变一个 Agent 一个公开入口、instruction 表达任务语义、权限控制实际操作、report 加 artifacts 表达业务结果的总体目标；它保留三个不能被表面统一抹平的机器契约。

## 14. 方向再修订：删除旧语义边界，不再给旧接口套壳

本节是对前文设计的进一步收敛。凡是把旧的 capability-specific 输入、payload、facts 或续跑字段保留在公共 Agent 边界的表述，均以本节为准。

### 14.1 根本目标

当前 ModuleTaskRequest 已经同时有 instruction、预算、工作区、input_artifacts 和若干控制字段；ModuleResult 也已经有 summary、payload 和 artifacts。如果只是把这些字段外面再包成 AgentRequest，没有架构价值。统一必须改变任务语义的唯一来源，而不是改变对象名称。

新版公共 Agent 请求只允许四类内容：

- instruction：唯一的自然语言任务语义字段；不再并列 goal、constraints、research、question、modify_instructions 等任务语义字段。
- input_artifacts：唯一的结构化任务材料入口。参数、数据集说明、用户回答、实验上下文、WorkRequest、WorkOutcome、Scientific continuation 等需要机器消费的任务信息，都以已登记 ArtifactRef 传递。
- controls：预算、工作区、权限、输出目录、确认策略以及机器控制策略。controls 不承载任务正文，也不根据自然语言关键词授予权限。
- runtime：run、task、attempt、session 等调用身份和恢复定位。runtime 是系统控制信息，不是业务任务模式。

公共请求不再有 facts 列表、模式专用输入联合或 capability-specific task payload。Scientific 的续跑上下文如需结构化保存，生成一个完整的 continuation artifact，再通过 input_artifacts 传递；不在公共请求上保留 previous_work_request/work_outcome 两个平行领域字段。

新版公共 Agent 结果只允许四类内容：status、report、artifacts、control/runtime usage。删除公共 payload、CodeUnderstandResult、CodeModifyResult、ExperimentResult 这些按 capability 分叉的结果类型。领域结果作为有 schema 的 artifact 产生，report 只做自然语言说明。

### 14.2 validation 的第一阶段边界

问题的根源不只是 validation 写错，而是旧领域字段让 Scheduler、Controller、Agent completion 和 Pydantic model 各自拥有一部分事实。第一阶段删除这些分散的领域 validation，不再让 Scheduler 根据 capability 选择 payload validator 或 completion 分支。

保留的 validation 只证明系统正确运行，不证明任务语义正确：

1. 请求和结果的基本结构、状态与 control 的组合正确；
2. budget、run/task/attempt/session 归属和恢复关系正确；
3. 工具实际执行受 WorkspaceGrant、权限和操作策略约束；
4. ArtifactCandidate 的来源、相对路径、文件存在性、符号链接边界、登记、hash、provenance 正确；
5. input ArtifactRef 属于当前 Run/session 且读权限有效；
6. 已登记 artifact 的关系正确，例如 Scientific opinion 引用必须来自实际 observed 的 artifact。这个是来源关系正确性，不是判断结论内容是否正确。

删除或延期的是语义正确性 validation：代码是否真的修好了、实验指标是否合理、Scientific 结论是否有道理、report 是否说得准确。这些以后作为基于 ArtifactRef 的领域 evaluator 重新设计，不在本轮公共 Agent 边界中硬编码。

acceptance 也只保留为可选的、基于 artifact 的客观交付约束：检查登记后的 kind、path、metadata 或结构化文件是否存在，不再读取 capability-specific payload。required_evidence_kinds 继续是独立的最终结论接地检查，直接使用 artifact 登记记录和 observed/cited 关系。

validation 失败时不能让系统丢掉已经生成的 report。Agent 无法完成时返回 failed 或 blocked，并由 Scheduler 在 Agent 未能返回文本时生成确定性的失败报告；报告不得泄露无权读取的内容。

### 14.3 删除纪律

本轮不做兼容层。旧代码迁移完成后必须删除：旧 capability 枚举及其模式分支、CapabilityInput 联合、公共 payload 类型、按 capability 选择 completion/payload validator 的代码、旧 Compiler 模式字段、旧 facts/续跑公共字段、适配旧对象的 adapter 和 alias。

schema 升至 12.0。旧 11.0 Run/Session 不做静默转换，也不由新入口继续运行；旧记录只读保留，不能为了兼容继续保留旧执行代码。测试、fake port、trace 校验、提示词和文档中的旧字段一起删除。

实施时可以分多个提交，但最终每个提交的代码都不应留下与目标接口并行的旧公共路径。阶段性适配只允许作为本地一次性重构工具，不能进入产品代码。

### 14.4 新的实施顺序

先删除公共 payload 和模式输入，再建立 instruction/input_artifacts/controls/runtime；随后把各 Agent 的领域结果写成 artifact，最后重写一套以 ArtifactRef 为中心的最小 correctness validator。Compiler 只生成 workflow_agent_kind、instruction、输入 artifact 绑定和控制申请；Scientific 仍由 Controller 调用，不能进入 DAG。完成门槛在第一阶段只验证执行、来源和产物关系，不宣称语义正确。
