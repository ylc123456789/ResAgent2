# Agent 输入输出统一与多模式清理方案（V2）

> 日期：2026-09-20。状态：新版实施方案，产品代码尚未按本方案修改。
>
> 基线：`ae33790`，schema `11.0`。继续在 **`refactor/unified-agent-entry`** 分支开发。
>
> 本文完整替代 [旧版方案](UNIFIED_AGENT_IO_PLAN_2026-09-20.md)。**旧版及其追加勘误均不实施，也不作为补充要求。开发只按本文执行。**

## 1. 目标和范围

本轮要真正减少任务信息的传递方式，取消同一个 Agent 的多种业务调用模式。不能只给旧 request/result 包一层新对象，也不能把旧领域字段整体搬到一个任意字典里。

最终规则只有三条：

1. **任务信息只能通过 `instruction` 或 `artifacts` 传递。** instruction 是唯一的自然语言任务字段；artifacts 是唯一的结构化任务材料入口。
2. **控制信息通过明确的控制字段传递。** 预算、授权路径、操作权限、确认状态、调用身份和恢复定位不能依赖自然语言解析。
3. **业务输出只有 `report + artifacts`。** 机器状态、控制动作、错误和执行计量另外保留，但不得借这些字段继续塞领域 payload。

保持现有中央编排结构：Scientific 提出研究判断与工作请求，Compiler 生成执行图，Scheduler 执行 Coding/Experiment 任务，Controller 管理研究过程。此次不增加 Agent 层级，不改成自主接力，不更换框架、LLM 客户端或 Runtime 循环。

本轮同时整理与新契约直接相关的 validation，只处理结构、权限、来源、生命周期及执行事实的正确性。语义完成度和结果质量评估留待后续独立设计。

## 2. 唯一的 Agent 输入协议

```text
AgentRequest
  instruction: string
  input_artifacts: ArtifactRef[]
  controls: ExecutionControls
  runtime: InvocationContext
```

版本字段沿用项目的契约版本机制。controls/runtime 是否采用嵌套对象只是代码组织问题；有价值的改变是删除第二套任务语义入口，而不是增加嵌套层数。

### 2.1 instruction 与 input_artifacts

instruction 表达这次要做什么、目的是什么以及自然语言要求，例如“解释训练入口的数据流”或“修复梯度累积并运行相关验证”。模型结合材料选择工具，不需要调用方选择 understand/modify 模式。

input_artifacts 提供精确参数、已有结果、论文、用户回答、数据资源说明、工作反馈等结构化材料。不得再增加 goal、constraints、research、CapabilityInput、dataset_refs、answers、facts 或 continuation 等公共任务信息字段。

| 信息 | 新版传递方式 |
|---|---|
| 目标、说明性约束、任务要求 | instruction |
| 精确实验参数、配置 | 配置 artifact |
| 论文、源码证据、实验结果、验证日志 | 对应 ArtifactRef |
| 数据集位置和版本等材料 | 数据资源说明 artifact |
| 用户回答 | 系统登记的回答 artifact |
| 精确交付要求、最终结论所需证据种类 | 要求 artifact；系统绑定检查范围与执行时点 |
| 上一项工作请求及其执行结果 | 系统生成的完整工作反馈 artifact |
| Scientific 当前判断、最终结论 | 判断或结论 artifact |

同一份配置或结果不再同时完整复制到 instruction、额外输入字段和 Session 中供下游任选。instruction 可以解释怎样使用材料，但结构化值以对应 artifact 为准。

把数据集说明变成 artifact，不等于复制整个数据集。说明文件可以记录数据集标识、版本和位置；实际访问仍通过系统资源解析和权限检查。文件中写着某个路径，不意味着调用者因此获得该路径的读写权限。

小任务不必人为制造文件；只有需要结构化传递、精确复用或形成证据的材料才登记为 artifact。Agent 内部临时变量和 Runtime 内部状态无需全部文件化。

### 2.2 controls 只负责控制

| 控制项 | 用途 |
|---|---|
| budget | 本次调用的额度和时间限制，继续受 Run 总预算约束 |
| workspace / output_dir | 系统解析的工作区、授权路径和输出位置 |
| permissions | 实际允许的读写、进程执行、环境准备等操作，以及确认要求 |
| environment | 系统实际执行的环境限制；普通实验配置仍属于材料 |

控制字段必须有实际程序消费者；不新建任意 policy 字典、不增加没有执行方的配置。复用现有 WorkspaceGrant、预算和命令策略，按真实需要调整字段。

acceptance 和 required_evidence_kinds 不作为 AgentRequest 的额外控制字段。具体“要交付什么、要引用哪类证据”属于任务要求，Agent 通过 instruction 或要求 artifact 获取；系统将该正式 artifact 绑定到 Task/Run，按第 6 节实施客观检查。它们不是模型可改写的策略，也不在 controls 中复制一份。

有效权限由系统计算，不能由 Compiler、Agent、材料中的文字或 metadata 自行提升。只隐藏模型可见的工具不等于实施授权：执行入口和底层组件仍要检查权限。

写权限表示可以写，不表示必须改文件。源码只读时仍应允许在受控输出通道提交报告。WorkspaceMode 的 read_only/read_write 是权限描述，可以保留，但不能再据此选择两套业务 prompt、action 或完成协议。

现有实验确认改成对“正式实验执行”操作的控制，不新增默认确认步骤。用户回答的内容进入回答 artifact；确认是否有效、对应哪个待确认操作由系统记录和验证。

### 2.3 runtime 只负责身份和恢复定位

保留 Run、Task、Attempt、Session 的 ID、调用归属和既有幂等恢复所需标识。ID 和权限均由系统绑定，不由模型自行填写。

- Coding/Experiment 的任务调用保留 task_id 与 attempt_number 的关系。
- Scientific 保留 Run/Session 归属，不伪造一个 task 来统一字段。
- 同一入口处理首次调用与恢复，不新增业务 resume 模式。
- Session 内部恢复状态仍由原持有方维护；传给 Agent 的领域反馈走 artifact。

模块身份和图节点类型严格区分：

```text
WorkflowAgentKind = coding | experiment
AgentOwner = coding | experiment | scientific | orchestrator
```

Compiler 草案、TaskProposal、WorkflowTask 的路由字段只能使用 WorkflowAgentKind。AgentOwner 用于模块和产物来源记录；AgentRequest 的实际被调用对象限定为三个 Agent，orchestrator 只作为系统生产者等身份使用。Scientific 继续由 Controller 调用，在图节点 schema 层就被排除。

Scientific 是使用相同协议的被调用模块，Controller 是它的调用方；协议相同不改变二者职责，也不把 Scientific 加入执行图。

## 3. 唯一的 Agent 输出协议

```text
AgentResult
  status
  report: string
  artifacts: ArtifactOutput[]
  control: ControlSignal | null
  session: SessionRef | null
  error: ModuleError | null
  warnings: WarningRecord[]
  usage
```

report 说明已做的工作、得到的结果、未完成事项及限制。artifacts 保存需要持久化或由机器继续读取的结果。status/control/session/error/usage 是共同运行信息，不能成为隐藏的领域结果通道。

下游不得从 report 提取指标、测试结论或科研裁决等领域事实作为机器检查依据。需要机器消费的业务结果仅读取正式 ArtifactRef；report 用于解释和展示。

删除公共 `payload` 和按 code_understand/code_modify/experiment_run 区分的结果联合。代码变更、验证结果、实验指标、Scientific opinion 都通过有定义格式的 artifact 提交。

领域 JSON 可以继续有 schema：`metrics.json` 与 `scientific_opinion.json` 的内容结构不同是正常的。这些类型用于读取和检查文件内容，不能反过来选择不同的 Agent 调用模式。旧公共结果类及导出在迁移后删除；确有需要的内容 schema 在对应产物位置定义，不保留旧类型别名。

### 3.1 控制动作也不传第二份任务正文

ask_user 和 request_work 保留为机器可识别的动作。自然语言 report 不能代替暂停或派工信号。

- 问题的正文、选项等任务内容进入 question artifact；control 只引用它并声明 ask_user。
- 待派发任务的 instruction、所需材料等进入 work_request artifact；control 只引用它并声明 request_work。
- 用户回答由系统登记为 answer artifact，关联 question ID；恢复时通过 input_artifacts 交给 Agent。
- 控制动作引用同次尚未登记的候选时，使用该结果 artifacts 列表的明确候选定位符；系统登记后解析为正式 artifact ID。定位符仅在本次结果接收期间有效，不是新的全局 ID。

这样 control 负责“下一步做什么系统动作”，artifact 负责“该动作涉及什么内容”。问题、工作请求不得再复制一份完整领域对象到 control.data。

### 3.2 状态不变量

保留现有状态 vocabulary，不另外创建一套生命周期。

| status | 必须满足的条件 |
|---|---|
| needs_user_input | ask_user 控制信号、有效问题引用、可恢复的暂停会话 |
| request_work | request_work 控制信号、有效工作请求引用、可恢复会话及派工权限 |
| completed / completed_with_warnings | 无待处理 control 和 error；warning 组合符合既有规则 |
| failed / blocked | 有明确 error，不伪装为完成 |

失败或暂停允许返回真实产生的部分产物。usage 来自实际调用记录，不能从 report 或模型自报数字获得。

Agent 的 completed 声明表示它结束了本次工作；本轮系统只验证协议、执行事实和明确的客观条件，不把它解释成已经证明任务语义完全正确。

## 4. artifacts 的生命周期和所有权

```text
Agent / 工具产生文件或文本
       → ArtifactCandidate
       → 系统 Registry 校验、保存、hash、登记
       → ArtifactRef
       → 后续 Agent、Compiler、Controller 或检查函数读取
```

继续使用当前 ArtifactCandidate、ArtifactRegistry 和 ArtifactRef 机制，不更换存储系统。

- Agent 生成候选内容，系统生成正式 ID、hash、URI 与来源记录。
- 文件候选使用授权 workspace 内的相对路径；短文本可以通过现有 UTF-8 content 提交。
- output_dir 与 workspace 不同根目录时，使用明确受控的来源处理，不能简单允许任意绝对路径。
- 日志、图像和权重等大文件走文件方式；资源说明 artifact 不自动复制大型资源本体。
- Registry 保存的是内容快照；工作区以后变化不应改变已登记结果。
- 当前存储 URI 机制继续使用，不引入 artifact:// 协议或远程对象存储。

Scientific 工具目前可以中途登记 artifact 并同轮读取，这个时序保留。为避免重复登记，模块到系统的接收边界允许：

```text
ArtifactOutput = ArtifactCandidate | ArtifactRef
```

Candidate 走登记；已登记 Ref 必须从本次任务/Session 的可信登记记录核实，不能信任模型自行拼出的 ID、URI 或 hash。二者是同一产物不同登记阶段，所有 Agent 使用同一规则。

**系统处理完成后，下游只能收到 ArtifactRef。** 同次候选定位符必须被解析，候选路径不能流入后续任务。已有 Ref 不重新编号，不把上游输入冒认为本次新产物。

MANIFEST 是可选的可读交付文件，不是另一份权威索引。权威身份与访问控制仍属于 Registry 和 Run/Session 记录。

登记失败不能发布成功或消费控制信号；已登记的部分结果保留真实来源并支持原有恢复。report 的保存不能依赖 artifact 登记成功，外层应在 Agent 未返回文本时根据错误信息生成失败说明。

## 5. 恢复上下文怎样走 artifact

不再给 Scientific 的公共请求增加 work_outcome、previous_work_request 或 typed continuation 字段。

Controller 从同一个 active WorkRequest 生成一份完整的 `work_feedback` artifact，内容包括配套的 previous_work_request 与 work_outcome。两者在这个文件的 schema 中都必填；没有工作反馈时不传这个 artifact，不制造只有半份内容的记录。

unresolved_task_outcomes 等相关执行事实可保存在同一反馈文件的明确字段中；其他轮次的事实另用清楚的材料引用，不通过任意 facts 列表混入当前恢复状态。

校验分工明确：

1. **生成时**：Controller 确认请求、outcome、Run/Session 和 active WorkRequest 一致，再登记文件。
2. **接收时**：检查函数从授权 ArtifactRef 读取文件并校验内容 schema；缺少任一必填部分立即拒绝。
3. **恢复时**：Controller 将反馈引用或回答引用与实际待恢复原因配对；首次调用不能携带声称用于当前会话恢复的反馈/回答，工作反馈恢复和回答恢复不能混用，已消费反馈不能再次应用。

预先登记的用户说明或历史回答可以作为普通材料，不因此成为恢复事件。是否用于本次恢复由系统的 pending question/active WorkRequest 关联决定，不能仅凭文件内容或 kind 触发状态迁移。

这里的成对关系和恢复检查依然存在，但它们检查的是 artifact 内容与系统状态，不是在公共请求上保留旧领域字段。对新入口、持久化载入和结果接收使用同一规则，不能只验证 prompt 展示时的副本。

## 6. validation 本轮做什么

### 6.1 保留正确性检查，统一事实来源

“基于 artifacts”适用于材料和业务结果。权限、预算、状态转换仍然需要系统真实状态，不能改成相信文件里自报的授权或成功声明。

| 检查 | 数据来源 | 执行位置 |
|---|---|---|
| 请求/结果形状、必填字段、允许状态 | 共同契约 | contracts 与调用接收边界 |
| 路径、操作权限、预算 | 系统 grant、命令策略、计量 | 工具/组件执行入口及调度层 |
| 文件存在、来源、快照、hash、读权限 | Registry 和系统授权 | 登记/读取路径 |
| JSON 内容格式、必填配对、引用归属 | 正式 ArtifactRef 读取的内容 | 对应内容 schema 与共享检查函数 |
| Task/Attempt/Session 恢复、消费一次 | 持久化系统状态与反馈 Ref | 原 Scheduler/Controller 状态所有者 |
| 交付条件及结论引用来源 | 正式 artifact 与可信事件记录 | 下述两个独立门槛 |

模型可以返回待登记内容；内容经系统保存后，检查和后续消费使用同一份正式快照。登记只证明内容被保存，不证明所有模型填报的事实真实。退出码、超时、实际读取记录和调用次数仍来自工具执行事件；相关结果 artifact 应由确定性代码据此生成。

保留可复用的底层正确性检查；删除旧位置的重复调用、模式选择和 payload 检查。不是先删除所有检查、留下无授权的执行通道，再等下一轮修复。

### 6.2 两个独立的客观门槛

| 门槛 | 问题 | 范围与执行者 |
|---|---|---|
| task acceptance | 本 task 是否交付显式要求的文件/类型/JSON 数值键？ | 当前 task 的正式产物；Scheduler 接收路径 |
| conclusion evidence | 最终结论是否引用了它实际观察过的指定类别证据？ | 整个 Run 的结论与证据；Scientific/Controller 完成边界 |

task acceptance 只接收明确、可机器检查的条件，不从 instruction 猜测“应该通过哪些测试”或自动补出指标阈值。路径条件按 Registry 保存的可信源路径元信息检查；数值键从对应正式 JSON 内容检查，不从 report 抽取。

明确条件保存在已登记的要求 artifact 中，由系统在任务接收时固定对应引用；Scheduler 从该引用读取条件，再检查当前 task 的正式产物。Agent 如需知道这些条件，同样通过 input_artifacts 读取。任务自己产出一份更宽松的要求文件，不能覆盖系统已绑定的要求。

`required_evidence_kinds` 保留为独立的最终结论要求，权威来源仍是 Run。Controller 将其登记为要求 artifact 并固定 Run 关联，Scientific 通过 input_artifacts 读取，不能修改该关联。它不进入 task acceptance，也不成为 AgentRequest 的第二个任务字段。检查有效集合是：

```text
同 Run 已登记证据
  ∩ Scientific 返回边界认可的观察记录
  ∩ Run 持久化的观察记录
  ∩ 最终 opinion 的引用集合
```

在这个集合上检查所需 kind。Scientific 工具生成的 session-bound literature_search artifact 可以参与，无需虚构一个生产它的 DAG task。“存在某类文件”和“最终结论引用了已读证据”不是同一条件。

观察来源沿用工具/Session 的真实事件及 Run 的持久化记录；这是对现有调用记录和累计记录的一致性核对，不增加两套独立观察系统，也不要求同一证据重复读取两次。

这两个门槛可以共用 Registry、schema 读取和基础谓词，但策略、范围与触发时点独立。它们不判断文献是否足以支持科学结论，也不判断某个准确率是否合理。

### 6.3 删除或延期的语义门槛

本轮不实现“代码已满足全部意图”“研究论证成立”“实验设计合理”“摘要表达准确”等语义验收，也不把这些检查挪到控制字段、领域 artifact validator 或另一个隐藏 prompt 中继续执行。

既有 finalizer 中负责生成 patch、整理日志、提取指标和记录来源的确定性工作保留；按业务模式强制“必须发生编辑”“必须输出旧 payload”等门槛删除。编译器中用于判断任务语义覆盖度的 review gate 退出本轮强制接收链，相关专用 prompt、调用和不可达代码一并删除；DAG 合法性、引用和权限需求等确定性检查保留。

验证命令的失败仍是失败事实，应保存退出码、日志和相应错误，不能改写为成功；但某条命令成功也不等于系统证明了任务语义完成。语义 evaluator 如以后需要，另定方案，直接消费正式 artifact 和任务 instruction。

## 7. 各模块改动

| 模块 | 本轮修改 | 保留的机制 |
|---|---|---|
| contracts | 唯一 request/result；清除旧领域字段和公开 payload；受限图类型；定义必要 artifact 内容格式 | 基础 ID、版本、来源与状态不变量 |
| Coding | 删除 understand/modify 模式入口、双 action/finish 和模式 prompt；结果产物化 | 读写工具、Git、快照、验证执行和真实事件记录 |
| Experiment | 使用共同 instruction/artifacts；参数和数据说明从材料读取；结果产物化 | 资源解析、环境操作、执行确认、命令策略、指标提取 |
| Scientific | 判断、问题、工作请求走产物；恢复反馈读取 artifact；使用共同输出 | 研究控制职责、文献工具、中途登记、观察记录 |
| Registry/组件 | 补齐实际需要的候选来源处理、引用验证和内容读取 | 现有目录、hash、复制、原子发布、恢复机制 |
| Scheduler/Controller | 共同请求/结果接收；artifact 反馈与检查；清除旧 payload 消费 | 原生命周期、预算、依赖、暂停恢复与幂等 |

单入口不等于每个 Agent 拥有所有权限。Scientific 的派工职责继续属于 Scientific；Coding/Experiment 不因共享类型就获得 Controller 的控制权。

## 8. 注册、Compiler 和执行图

注册表按专业执行模块注册 coding、experiment，不再按 understand/modify/run 等业务动作注册。可复用注册容器，删除旧 capability key 和 mode 映射，不引入新插件框架。

Compiler 草案及图节点的任务内容统一为：

```text
workflow_agent_kind: WorkflowAgentKind
instruction
input_artifact_bindings
workspace_id
depends_on
明确的控制需求
```

同时保留图身份、revision、状态、attempt 历史等既有系统字段。删除 goal + constraints + CapabilityInput 的第二套任务表示。

输入绑定可以引用已有 artifact，或引用本图依赖任务的未来产物。后者由 Scheduler 在依赖完成后解析为已登记 Ref，不能让 Compiler 预造 artifact ID 或把候选路径传给 Agent。绑定表达数据依赖，不包含另一份任务正文。

Compiler 只看到系统提供的逻辑资源、受限模块类型和材料说明。实际物理路径、权限 grant、身份绑定和预算扣减仍由系统决定。

Compiler 保留非空图、依赖存在、无环、允许模块、revision 和预算等结构性检查；语义 review 的处理按第 6.3 节执行。Compiler 自身不是新增 Agent，不强制把所有内部编译对象和 Runtime 数据都 artifact 化。

## 9. 删除纪律和版本

这是一次替换，不做兼容层。完成后从生产代码、导出、配置、提示词、测试 fixture 和当前文档中删除：

- 旧业务 capability 枚举、模式路由和模式专用分支；
- CapabilityInput 及 CodeUnderstandInput、CodeModifyInput、ExperimentRunInput；
- 公共请求上的旧领域字段、任意 facts 和领域 continuation；
- 公共 payload、模式结果类型及按 capability 选择的 validator；
- 旧 Compiler 草案字段及其转换/渲染逻辑；
- 兼容解析器、deprecated alias、新旧入口切换开关和迁移 adapter；
- 已移出范围的语义 gate 及只为其存在的代码。

可以重用仍有作用的函数、工具和内容校验；用户要求删除的是被替换和失去消费者的旧实现，并不是把仍然正确工作的组件全部重写。

下一 schema 采用 `12.0`；如果实施前版本已被其他工作占用，按项目规则分配下一主版本。旧 11.0 Run/Session 不直接续跑，不静默转换，不为它保留旧执行实现。原始历史记录保留为历史资料，新读取入口先检查版本，明确拒绝不兼容续跑且不写回旧数据。

后续可以分批修改，但产品代码不引入兼容 adapter；阶段内必要的联动消费者一起切换。提交前清除死分支、旧导出、双份 schema、旧 fixture 和失效说明，不把清理留到下一轮。

## 10. 实施顺序与验收

1. 列出所有 Agent 模式、旧输入/输出字段消费者和 validation，逐项标记保留、迁移到 artifact、删除。
2. 确定共同契约、图路由类型和最小 artifact 内容格式；连同直接消费者一起替换，不先发布一个套壳版本。
3. 完成各 Agent、Registry 接收、Compiler 图、Scheduler/Controller 和恢复流程的联动切换。
4. 将必要正确性检查接到正式 artifact 和系统状态上，删除旧分散路径及延期的语义 gate。
5. 更新测试、CLI/e2e 装配、trace 断言、当前文档和示例；全项目检查旧实现是否仍有可执行入口。

必须覆盖的验收：

- 新 request 只从 instruction/artifacts 读取任务信息；旧字段因 extra=forbid 被拒绝。
- 同一个 Coding 入口完成解释、修改等任务；只读授权拒绝编辑，可写解释任务不被强制产生变更。
- Scientific 进入图的草案在 schema 边界被拒绝；经不可信 model_copy/载入得到的对象仍在接收时重验。
- artifact 越界、符号链接逃逸、伪造 Ref、外 Run 引用、损坏 hash、缺失内容均拒绝。
- 中途文献登记可在同轮读取；最终返回不重复登记；control 候选引用正确解析。
- 工作反馈缺失一半、关联错 WorkRequest、首次调用带恢复材料、回答/工作反馈混用、重复消费均拒绝。
- task acceptance 与 conclusion evidence 分别验收：只存在但没读、只读但没引用，都不能满足最终引用要求；合法的 session 证据无需 task_id。
- 状态、预算、真实调用计量、失败日志和恢复不回归；登记失败仍保留报告和真实部分结果。
- 成功、失败和暂停结果均不依赖旧公共 payload；无兼容开关、adapter、旧枚举或旧模式执行路径。

按项目既有规程执行相关定向测试、全量确定性测试、mock E2E 和格式检查。真实场景覆盖代码解释、修改与实验串联、修复重跑、问答恢复、文献与研究结论；记录最终确切提交、模型、trace 与实际结果。历史测试数字不能作为新版通过证据。

以上测试证明的是接口、控制和证据链正确运行，不宣称已经建立完整的任务语义正确性评估体系。


## 11. 四个边界问题的最终定案

本节是新版方案的明确实施约束，用来消除前文可能的歧义。它不是兼容说明，也不恢复旧字段。

### 11.1 验收要求仍然存在，但由任务控制面创建和绑定

删除 `expected_metrics`、`expected_artifacts` 作为 Agent 输入字段是有意的；删除“验收要求”本身不是有意的。验收要求是 Scheduler 的机器检查策略，不能让 Agent 从 instruction 猜，也不能让 Agent 自己产出一份要求来覆盖它。

任务图在任务输入材料之外保留一个明确的控制面槽位：

```text
TaskProposal / WorkflowTask
  acceptance_spec: TaskTaskAcceptanceSpec | null
```

`TaskTaskAcceptanceSpec` 只允许直接调用方、Controller 或 Compiler 的系统入口创建。直接调用方如果要求“必须有 accuracy 指标、必须有 results.json”，就在创建任务时填写这个槽位；Compiler 只有在上游已经给出明确、可机器检查的要求时才能转交，不能自行猜测。Compiler 的 LLM 草案即使包含验收文字，也必须由 Orchestrator 接收边界按显式来源和 capability registry 的允许谓词确定性归一化；没有明确来源就丢弃，不能让模型自证验收规则。没有验收要求时槽位为空，表示该 task 没有额外的 task-level acceptance 门槛。

任务接收时，系统把这个槽位物化成不可变的 `acceptance_requirements` artifact，并将正式 `acceptance_ref` 绑定到该 Task。Proposal、WorkflowTask、ModuleTaskRequest 和 Attempt 持有同一份快照；Scheduler 只读取这份系统绑定的 artifact 检查本 task 的正式产物。Agent 如需知道交付要求，也只能通过只读的要求 artifact 读取；Agent 的结果、报告或新 artifact 不能回写或放宽它。这样“谁填”的答案是明确的：直接调用方提出要求，Orchestrator 负责登记、归一化和绑定，Scheduler 负责执行检查。

`acceptance_spec` 是任务控制面策略，不是 `AgentRequest.controls`，也不是第二份任务正文；它与 `input_artifacts` 分开保存。`required_evidence_kinds` 仍然是 Run 级的结论接地要求，由 Controller 创建并绑定 Run-owned requirement artifact，继续执行“已观察且被最终结论引用”的独立检查，不能并入 task acceptance。

### 11.2 未来产物绑定必须同时声明执行依赖

图节点允许两类输入绑定：已经存在的 `ArtifactRef`，以及带逻辑来源的未来产物引用：

```text
FutureArtifactBinding {
  source_task: TaskKey
  output_selector: LogicalOutputName
}
```

`output_selector` 的语义固定为**逻辑输出名**，不是物理路径、artifact ID，也不是模糊的 kind 匹配。例如任务可以声明 `metrics`、`results`、`run_log` 三个输出槽位；Compiler 填写其中一个逻辑名，Scheduler 在源任务成功 Attempt 登记的产物中按该名字精确解析。逻辑输出名由任务的输出声明或系统登记时写入的 `output_name` 确定，不能由下游根据文件名猜测。kind 只描述内容类型，不能代替唯一的输出选择器。

一个逻辑输出名在一次成功 Attempt 中默认只能解析为一个正式 `ArtifactRef`；需要多个文件时，生产方必须声明多个逻辑输出名，或先登记一个包含文件清单的 manifest artifact。相同 selector 被多个输入位置引用时按 artifact ID 去重；selector 缺失、解析出多个结果或解析到未登记候选都直接拒绝，不使用“全都要”的隐式规则。

任何 `FutureArtifactBinding.source_task` 都必须同时出现在当前任务的直接 `depends_on` 中。编译接收和图物化阶段都要检查这条不变量；缺失依赖、未知任务、自引用和形成环的绑定直接拒绝。Scheduler 只有在所有声明的依赖成功、源产物已经登记为正式 `ArtifactRef` 后，才解析这个绑定并调用下游 Agent。绑定本身只说明要读哪项材料，`depends_on` 才决定执行顺序，不能把二者混为一件事。纯顺序依赖可以没有 artifact 绑定；依赖边不要求一一对应产物。

### 11.3 Registry 必须支持系统生成的全部 artifact kind

现有 `register_scientific` 中只允许 `literature_search` 的硬编码分支必须删除；不能把它当作新版登记路径继续复用。实现可以把它改为通用的 session/run artifact 登记入口，也可以拆出 `register_system_artifact`，但都必须经过同一个 hash、来源、归属和幂等检查。

至少要覆盖 `literature_search`、`work_feedback`、`question`、`answer`、`work_request`、`acceptance_requirements`、`scientific_opinion` 以及本项目定义的其他系统产物。未知 kind 仍然拒绝；允许列表或 kind 注册表应是集中定义，不能再写死“不是 literature_search 就报错”。系统生成产物必须写入可信 producer、session/run 归属和必要的父关联；Agent 不能伪造这些字段。为这些 kind 增加逐类登记、重复登记和错误归属测试。

Registry 放开后，`ArtifactRef.validate_provenance` 也必须同步改成按集中 provenance policy 校验，不能继续只接受旧的三种形状。实现至少要把以下矩阵固化为契约（实际 kind 可扩展，但不能绕过注册表）：

| kind | producer | 允许的归属 | `metadata.source_type` |
|---|---|---|---|
| `work_feedback` | orchestrator | `session_id`，无 task/attempt | `controller_feedback` |
| `acceptance_requirements` | orchestrator | `task_id`，无 attempt/session | `task_requirement` |
| `answer` | orchestrator | Scientific 为 `session_id`；Coding/Experiment 为 `task_id + attempt_number` | `controller_answer` |
| `question` | orchestrator | 提问所属的 session 或 task 作用域 | `controller_question` |
| `work_request` | orchestrator | Controller 所属 session/run 作用域 | `controller_work_request` |

旧的 task 执行产物、Scientific 工具 session 产物、run-only `import`/`final_report` 形状继续保留。kind、producer、scope 和 `metadata.source_type` 必须交叉匹配，禁止通过随意填写 metadata 绕过检查。未知组合仍拒绝；Registry 与 `ArtifactRef` 使用同一集中 provenance policy，不能一处放行、另一处拒绝。

### 11.4 answer artifact 必须保存问题快照和作用域

`answer` artifact 不能只保存 `question_id`。因为回答登记后 pending question 会被消费并清除，恢复和审计时必须能独立重建“当时问了什么、回答了什么”。本方案选择把 task 归属也写入 answer artifact；系统的 pending-question 状态仍是恢复路由的权威来源，artifact 中的归属用于自洽校验和独立审计。

最低内容为：

```text
{
  question_id,
  question_text,
  requested_fields_or_schema,
  options_if_any,
  answer,
  answered_at,
  run_id,
  session_id: SessionId | null,
  task_id: TaskId | null,
  attempt_number: int | null
}
```

作用域不允许混用：

- Scientific 问题必须有 `session_id`，`task_id` 和 `attempt_number` 为空；
- Coding/Experiment 问题必须有 `task_id` 和对应的 `attempt_number`，`session_id` 为空或仅作为非路由关联；这里记录的是被暂停并等待回答的 Attempt；
- `run_id` 始终存在。

`question_text`、字段要求和选项必须从系统持久化的 `QuestionDraft` 快照复制，不能信任用户回答或模型回传的同名字段。若问题没有选项，相关字段可以为 null，但不省略问题正文。`answer_task_ids[question_id]` 等 pending-question 状态仍决定回答实际续哪个 task；接收时必须检查它与 answer artifact 的 `task_id/attempt_number` 一致。普通预先登记的历史回答可以作为首次调用的材料，只有声称用于当前待恢复问题的 answer artifact 才受首次调用恢复限制。

### 11.5 对应验收

新版验收必须额外覆盖：

- 直接调用方填写 `TaskTaskAcceptanceSpec` 后，系统确实生成并绑定 `acceptance_requirements` artifact；缺少绑定时 Scheduler 拒绝执行 task-level acceptance，而不是静默通过。
- Agent 不能通过输出新的要求 artifact、修改 instruction 或修改 input_artifacts 覆盖已绑定验收要求；无验收要求的 task 不被强行猜测出要求。
- 未来产物绑定但未列入 `depends_on`、列入但任务未知、源任务失败或产物未登记时均拒绝或保持不可运行；合法绑定按依赖完成后再解析。
- 每一种系统 artifact kind 都能登记、读取、做归属检查和幂等重放；`literature_search` 不再是唯一允许值，`ArtifactRef` provenance 校验与 Registry 使用同一集中策略。
- `output_selector` 按逻辑输出名精确解析；多个产物、重复绑定、缺失或歧义 selector 都有确定结果，不依赖物理文件名或 kind 猜测。
- answer artifact 在 pending question 被清除后仍包含问题正文、原始请求结构和 task/session 作用域；重启恢复不依赖已删除的 QuestionDraft，且 task answer 的 task/attempt 归属与系统 pending 状态一致。
- task acceptance 仍只检查当前 task 的交付物；conclusion evidence 仍只在 Run 完成边界检查 observed ∩ cited，二者不能互相替代。
