# ResAgent2 系统架构

**文档角色**：系统概念、职责边界、控制流和状态语义的最高级事实来源（semantic source of truth）。

**当前基线**：Stabilization 3.0（ADR-0011）之后的 wire schema `5.0`（能力注册表契约收敛）。`ResearchController` 是研究 Run 唯一入口与状态负责人；`WorkflowScheduler` 只执行任务图、不决定 Run 完成；pause/resume 走同一 Attempt；dataset / environment / input artifact / workspace 各有唯一权威来源；公共契约只保留有 production producer+consumer 的字段。schema 5.0 是不兼容升级，旧 4.0 及更早的 Run 不恢复；既有记录原样保留，Session 的解析边界见 [CONTRACTS §18](CONTRACTS.md#18-schema-版本规则)。

任何改变系统概念、模块职责、控制流或状态语义的变更，必须先修改本文件，再修改契约、开发计划、代码和测试。

## 1. 文档权威关系

| 问题 | 唯一权威来源 |
|---|---|
| 系统概念、模块职责、控制流、状态含义、架构约束 | `ARCHITECTURE.md` |
| 跨模块对象的字段、类型、组合约束和 wire 版本 | `CONTRACTS.md` |
| 开发顺序、阶段范围、完成状态和验收证据 | `DEVELOPMENT_PLAN.md` |
| 已经运行的代码到底做了什么 | 代码和自动化测试 |
| 难以逆转的架构决定及其理由 | `docs/decisions/` |

`README.md` 是派生摘要，不是另一个事实来源。发生冲突时：先由本文件裁定概念，再由 `CONTRACTS.md` 表达数据，由 `DEVELOPMENT_PLAN.md` 安排实现；代码尚未达到目标时必须明确写成缺口，不能把计划描述成现状。

[接口说明卡（INTERFACES.md）](INTERFACES.md) 是本文件与 CONTRACTS 的配套阅读说明，不增加一层契约权威：本文件 §8 按模块回答“谁负责什么、从哪里进出”，接口卡按六类调用边界回答“如何调用、如何返回、谁保存状态、出错和重入怎么办”。字段全集仍只在 CONTRACTS 维护。

### 1.1 接口设计规则

模块隐藏实现，但公开契约必须说明输入/输出、副作用、状态归属、失败、重复调用和预算语义。调用者不应读取实现私有属性或 Session 内容才能正确使用接口。现有未达项按 [接口优化计划](reviews/INTERFACE_OPTIMIZATION_PLAN.md) 收敛，不把设计目标当成已实现保证。

- 一个事实只有一个权威来源；允许派生投影，不增加独立缓存真值。summary 是解释，不覆盖状态、真实验证与工件证据。
- 每个公开字段须有明确语义、生产者和消费者（代码、模型、人或审计）；缺失、空值、单位和范围应清楚。不为将来预留无效必填声明。
- 同一规则只维护一个实现；Compiler 纠错和 Scheduler 接收等不同边界可以调用同一纯判据。内部已保证的条件不再靠虚构默认业务内容兜底。
- 成功、失败、暂停均须保持可对账的消费和身份；恢复不等于重试，超时检查不等于抢占取消，重复调用不默认保证 exactly-once。
- 类型定义、工具说明和模型实际看到的参数要求保持一致；不另写一套竞争的 schema，也不为此无差别注入完整 schema。
- CLI 与 E2E 保留独立组合根：模型、目录、测试场景和替身由各入口装配。只复用已证明相同的预算、登记等机制，允许少量构造代码重复；不新增总 bootstrap，不让 E2E 依赖 CLI。
- 公开边界用行为测试验证替代实现，而不只测试原生实现内部。删公开字段单列为契约变更；本轮不新增 MCP/A2A 服务、插件系统或历史兼容层。

这些规则借鉴信息隐藏、Design by Contract、MCP 的工具结构与错误语义、A2A 的黑盒和生命周期；只采用原则，不复制网络协议。原始依据和逐阶段范围见上述计划。

## 2. 一句话架构

**Scientific Agent 是科学大脑，Orchestrator 是执行神经系统。**

- Scientific Agent 负责回答：当前证据意味着什么，还缺什么证据，最终能形成什么科学观点；
- Orchestrator 负责回答：怎样把「还缺什么」转换成合法任务图，怎样调度、恢复、登记证据和结束 Run；
- Coding Agent 是程序员；Experiment Agent 是实验员；
- Workflow 是 Orchestrator 的内部执行表示，不是 Scientific Agent 的公开产物。

最重要的闭环是：

```text
自然语言目标
  → 科学判断
  → 若证据不足，提出语义化工作请求
  → Orchestrator 编译并执行任务图
  → 冻结新证据并恢复同一个科学会话
  → 更新科学判断
  → 直到形成最终科学意见或需要用户输入
```

## 3. 目标与非目标

### 3.1 系统目标

1. 用户可以直接用自然语言描述研究目标和约束；
2. Scientific Agent 以一套长期、可恢复的 Agentic Loop 持续形成科学判断，不需要外部选择 plan/analyze 模式；
3. 证据不足时，Scientific Agent 只说明需要什么工作和证据，不承担执行图字段；
4. Orchestrator 可以用 LLM 理解工作请求，但必须用确定性代码校验图、状态、预算、安全和 provenance；
5. Coding、Experiment、Scientific 复用同一个 runtime，不复制三套 Agent 框架；
6. 所有跨模块事实通过契约、Run state 和不可变 Artifact 传递；
7. 用户可以解释一次 Run 为什么发起任务、任务产生什么证据、最终观点依据什么。

### 3.2 当前不追求

- 多科学人格辩论、树搜索或 supervisor swarm；
- 给 Scientific Agent 设计 plan/analyze/review 等多套公开模式；
- 让子 Agent 彼此直接调用；
- 让 LLM 直接写 RunStatus、TaskStatus 或历史 Attempt；
- 把聊天记录当作唯一系统状态；
- 分布式调度、并行 worker、插件市场或通用聊天产品；
- 为可能出现的需求提前建设复杂抽象。

## 4. 核心概念与所有权

| 概念 | 准确含义 | 所有者 |
|---|---|---|
| ResearchRequest | 用户确认的自然语言目标、上下文、约束和总预算 | Orchestrator |
| ResearchRun | 一次研究从目标到最终意见的完整持久化状态 | Orchestrator |
| ScientificSession | Scientific Agent 对同一 ResearchRun 的长期可恢复推理状态 | Scientific Agent/runtime；Orchestrator 只持有引用 |
| ScientificAssessment | Scientific Agent 在某一时点对目标、证据、局限和未解问题的当前观点 | Scientific Agent |
| WorkRequestDraft | Scientific Agent 对「还需要得到什么证据」的语义请求，不含执行字段 | Scientific Agent |
| WorkRequest | Orchestrator 分配 ID 并持久化后的工作请求 | Orchestrator |
| WorkflowCompiler | 把 WorkRequest 翻译为 WorkflowProposal/Patch 的无状态有界组件 | Orchestrator |
| WorkflowProposal | 尚未被接受的初始执行图候选 | WorkflowCompiler 产生，Orchestrator 校验 |
| WorkflowPatch | 对已接受执行图的**只追加**修订候选 | WorkflowCompiler 产生，Orchestrator 校验 |
| Workflow | Orchestrator 接受并持久化的有版本任务图 | Orchestrator |
| WorkflowTask | 调度器交给一个专业执行能力的顶层工作单元，可有多个 Attempt | Orchestrator |
| Attempt | 某个 WorkflowTask 的一次执行尝试；因问答暂停时，可跨多次模块调用继续 | Orchestrator |
| WorkspaceSpec | 逻辑工作区的来源声明（source_kind + location + environment）；location 可为本地来源路径，但不等于本 Attempt 的物理授权 | Orchestrator 的 composition root 声明 |
| WorkspaceRecord | 一个工作区解析后的记录（root、source、managed） | Orchestrator |
| WorkspaceGrant | 某次 Attempt 的最大物理授权边界，由 WorkspaceRecord 派生 | Orchestrator |
| WorkOutcome | 一次 WorkRequest 执行稳定后，成功、失败、警告和 Artifact 的汇总 | Orchestrator |
| ScientificOpinion | Scientific Agent 对用户目标给出的最终自然语言观点及证据引用 | Scientific Agent |
| ScientificCompletionValidator | 验证科学闭环结构、证据 provenance 和控制状态；不判断科学观点真假 | Orchestrator |
| ArtifactCandidate | 模块声明的待登记文件 | 生产它的模块 |
| ArtifactRef | Orchestrator 验证、冻结并登记后的不可变证据引用 | Orchestrator |
| PendingQuestion | 已持久化、会暂停 Run 的用户问题 | Orchestrator |

`ScientificAssessment` 与 `ScientificOpinion` 的正文是自然语言；小型结构化外壳只服务于控制、证据引用和验证，不把科学推理固定成枚举流程。

必须始终区分执行身份：

```text
WorkRequest（为什么需要执行）
  └─ Workflow revision（Orchestrator 怎样安排执行）
       └─ WorkflowTask（一个顶层工作单元）
            └─ Attempt（一次执行尝试，可暂停并继续调用）
                 └─ Session（模块内部 Agentic Loop）
                      └─ AgentAction（单步 Tool 动作）
```

ScientificSession 不属于某个 WorkflowTask；它属于整个 ResearchRun，可以跨越多个 WorkRequest 和 Workflow revision。Coding/Experiment Session 仍属于具体 Attempt。

## 5. 系统架构

```mermaid
flowchart TB
    User([用户])
    subgraph Res[Orchestrator（研究编排模块）]
        Entry[CLI / API / Conversation Adapter]
        Controller[Research Controller]
        Compiler[Workflow Compiler]
        Validator[Workflow Validator]
        Scheduler[Workflow Scheduler]
        Outcome[WorkOutcome Builder]
        Gate[Scientific Completion Validator]
        RunStore[(Run Store)]
        Registry[(Artifact Registry)]
        QA[Question / Answer Coordinator]
    end
    subgraph Agents[专业 Agent]
        Sci[Scientific Agent\n一个长期 Agentic Loop]
        Code[Coding Agent]
        Exp[Experiment Agent]
    end
    subgraph Caps[可装配 Capabilities]
        Lit[Literature Search]
        ReadArt[Registered Artifact Reader]
        Exec[Workspace / Git / Process / Env / Dataset]
    end
    User <--> Entry
    Entry --> Controller
    Controller -->|goal + state + authorized evidence| Sci
    Sci -->|ScientificTurnResult| Controller
    Controller -->|completed opinion| Gate
    Gate --> Entry
    Controller -->|active WorkRequest| Compiler
    Compiler -->|WorkflowProposal / WorkflowPatch| Validator
    Validator --> Scheduler
    Scheduler -->|code tasks| Code
    Scheduler -->|experiment tasks| Exp
    Code --> Scheduler
    Exp --> Scheduler
    Scheduler --> Outcome
    Outcome -->|WorkOutcome + ArtifactRefs| Controller
    Controller -->|resume same ScientificSession| Sci
    Sci <--> Lit
    Sci <--> ReadArt
    Lit -->|normalized ArtifactCandidate| Registry
    Registry -->|ArtifactRef| Lit
    Code <--> Exec
    Exp <--> Exec
    Controller <--> QA
    Controller <--> RunStore
    Scheduler <--> RunStore
    Scheduler <--> Registry
```

顶层研究控制涉及两个 LLM 职责：Scientific Agent 形成科学判断；WorkflowCompiler 把工作请求编译为执行图。Coding、Experiment 内部同样会调用 LLM 决定领域动作；状态迁移、校验、调度、预算和 Artifact 登记由确定性代码负责。

图表示职责与数据流，不表示每个框都是一个独立类或服务。当前 Workflow Validator 分布在模型校验、Compiler 与 Scheduler 的接受检查中；Question/Answer Coordinator 位于 Controller/Scheduler；WorkOutcome Builder 是 Scheduler 内部汇总函数。实际入口见 §8.1。

## 6. 两种循环和一个编译步骤

### 6.1 科学控制循环

它跨越整个 ResearchRun：

```text
加载或创建 ScientificSession
  → 注入目标、当前 Run 摘要、授权 Artifact、上次 WorkOutcome/用户回答
  → Agentic Loop 自主读取 Artifact、检索文献和推理
  → request_work：保存当前 ScientificAssessment 和 WorkRequestDraft
     或 ask_user：保存问题并暂停
     或 finish：提交 ScientificOpinion 候选
  → 有新证据/回答后恢复同一 Session
```

Scientific Agent 没有显式 plan/analyze 模式。`request_work`、`ask_user` 和 `finish` 是控制信号，不是三种 Agent 实现。

每次 `request_work` 必须同时给出当前科学观点，禁止只派工作、不说明科学理由。它对 Scientific Agent 类似一个异步 Tool：调用后暂停，Orchestrator 完成工作后把 `WorkOutcome` 作为 observation 返回。

runtime SessionStore 持有完整 Tool event history。ScientificPort 的确定性 finalizer 只从成功的 `read_artifact` / `literature_search` Tool observation 中提取 `observed_artifact_ids`，LLM 不能直接填写这组 trace；ResearchController 在 Registry 复核后把并集持久化进 ResearchRun，不读取或复制 Session 私有内容。

### 6.2 Workflow 执行循环

它由 Orchestrator 的确定性代码驱动：

```text
读取已接受 Workflow
  → 稳定计算 ready Task
  → 创建并保存 running Attempt
  → 通过 capability 对应的 ModulePort 发出 ModuleTaskRequest
  → 接收并校验 ModuleResult
  → 登记 Artifact、更新 Task；结算完成/失败 Attempt，或保留等待答案的暂停 Attempt
  → 处理 retry / blocked / question / patch
  → 图稳定后生成 WorkOutcome
```

Scheduler 不选择 Agent 内部 Tool；Agentic Loop 不修改顶层 TaskStatus。

### 6.3 WorkflowCompiler

WorkflowCompiler 不是第三种循环。它把一次 `WorkRequest` 翻译成一张可执行图，采用「语义草图 + 确定性物化 + 一次语义审查」（ADR-0010）：

```text
WorkRequest + 能力注册表 + 当前 Workflow 摘要 + Run 约束
  → (LLM 只输出) CompilationDraft —— summary/rationale + 局部 task key + 局部依赖 + capability + inputs
  → (确定性 _materialize_draft) 结构校验 + 分配全局 TaskId、绑定 work_request_id、解析 workspace、转换局部依赖
  → (LLM 一次短审查) CompilationReview —— accepted / issues
  → WorkflowProposal（尚无图）或只追加的 WorkflowPatch（已有图）
```

LLM 只负责语义：做什么、任务之间有什么关系。所有运行时身份、作用域和状态由代码决定——LLM 不输出全局 TaskId、WorkRequestId、revision、status 或旧 Task 引用，也不输出代码细节（文件路径、函数位置、CLI 参数、验证命令）；`code_modify` 的 `suggested_paths` 在物化时被强制清空，Coding Agent 自己探索工作区决定「在哪里、怎么做」。允许它使用结构化 LLM 调用，因为自然语言证据需求到具体 capability 的映射需要语义理解；但它必须无长期 Session、不调用专业 Agent、不形成科学结论、不改持久化状态，并把结果交给确定性 validator。它只选择任务类型、目标、依赖和逻辑 `workspace_id`；不扫描源码、不指定文件、不生成验证命令、不决定物理目录、不执行 `git clone`。

结构校验通过后，Compiler 再做一次短小的**语义审查**（`CompilationReview`）：判断草图有没有漏掉当前已知前置任务，以及是否提前加入了“只有本轮失败后才需要”的诊断、修复或重跑。一个 WorkRequest 只物化当前可执行的一轮；失败事实经 WorkOutcome 返回 Scientific，再由新的 WorkRequest 进入修复轮。结构拒绝与语义拒绝共享一次带精确反馈的纠错重编，总计最多两版草图，每版最多一次 review；仍失败才把 WorkRequest/Run 置为 failed。物化检查与 Scheduler 接受检查各守自己的边界，不意味着二者完整重复每个语义判断。测试中可由 `DeterministicWorkflowCompiler` 替代。

下游任务的约束只来自 `WorkflowTask.constraints`——由 Compiler 从最新 WorkRequest 分配给每个 Task；Scheduler 只传 `task.constraints`，不再把 `ResearchRequest.constraints` 原样广播给每个子 Agent。

编译与审查共用注册能力说明及职责规则。审查读每个任务的完整目标、依赖、约束及物化后输入语义，而非只看标题；代码正确性验证不替代正式实验和指标交付，预算紧张也不能扩大 Coding 的职责。此处是有界语义审查，不是确定性自然语言理解保证。Controller 在新编译前检查剩余任务名额：零名额直接以 `budget_exhausted` 结束当前 WorkRequest/Run，不调用 Compiler；已接受图的恢复优先处理，不受新增任务额度影响。

LLM 调用可开启一个最小 JSONL trace（`OpenAICompatibleClient.trace_dir` + `trace_level`，off/metadata/full）：记录 run/session/task/agent/step、model、latency、retry、usage、call_id/created_at；full 档保留完整 request、最终 response，以及 provider 明确返回的 `reasoning_content`（若有），metadata 档只记 hash/tool/valid。reasoning 只用于调试，不进入 AgentState、Session 或下一轮上下文。目录 0700、文件 0600，永不记录 API key，trace 不进 ArtifactRegistry 也不进 Run JSON。

## 7. 完整工作流

### 7.1 新 Run

```mermaid
sequenceDiagram
    actor User
    participant Res as Orchestrator
    participant Sci as Scientific Agent
    participant Comp as WorkflowCompiler
    participant Sch as Scheduler
    participant Exec as Coding / Experiment
    User->>Res: natural-language ResearchRequest
    Res->>Sci: start(goal, state, authorized artifacts)
    alt evidence is already sufficient
        Sci-->>Res: finish(ScientificOpinion)
        Res-->>User: validated final opinion
    else user information is missing
        Sci-->>Res: ask_user(QuestionDraft)
        Res-->>User: PendingQuestion
        User->>Res: UserAnswer
        Res->>Sci: resume(answer)
    else more execution evidence is required
        Sci-->>Res: ScientificAssessment + WorkRequestDraft
        Res->>Comp: persisted WorkRequest + execution context
        Comp-->>Res: WorkflowProposal / WorkflowPatch
        Res->>Sch: validated Workflow
        loop until execution graph is stable
            Sch->>Exec: ModuleTaskRequest
            Exec-->>Sch: ModuleResult
        end
        Sch-->>Res: WorkOutcome + registered ArtifactRefs
        Res->>Sci: resume same session with outcome
    end
```

### 7.2 工作失败与修复

Task failure 是执行事实，不等于科学 Run 立即失败：

```text
Experiment Task failed/blocked
  → Scheduler 先按 retry policy 处理
  → 图稳定后 WorkOutcome 记录失败、警告、诊断 Artifact
  → Scientific Agent 根据失败原因更新判断
  → 可 request_work 请求代码修复、替代实验或更多诊断
  → Orchestrator 编译为 WorkflowPatch 后继续
```

### 7.3 用户问题

Scientific、Coding 或 Experiment 都只能产生 `QuestionDraft`。Orchestrator 分配 QuestionId，持久化 PendingQuestion 并把 Run 置为 paused。`answer_question(run_id, answer)` 先按 run_id 选 Run，再核对当前 question_id 和必答字段；任务与 Session 从已持久化的问题和 Attempt 推导，并不是让用户在答案里自报这些身份。恢复时复用对应 Session，普通 retry 不复用 Session。同 Attempt 连续两问也分配不同 QuestionId；第一问的答案不能用于回答第二问，见 [I3](INTERFACES.md#i3-执行任务)。

## 8. 模块职责

这一节把架构模块和真实接口对上。下面的方法签名是阅读索引，不是新增 API；标注“内部”的方法不应被 CLI 直接用来另起一套控制流。以下按当前实现说明；本轮本地契约回归与服务器验收分开记录，见 [修复计划](reviews/CONTRACT_FIXES.md)。历史服务器通过记录不代表本轮代码已完成真实验收。

### 8.1 Orchestrator（研究编排模块）

负责自然语言入口、ResearchRun/ScientificSession 引用、WorkRequest 生命周期、WorkflowCompiler、Proposal/Patch 校验、Task/Attempt 调度、retry、问题协调、Artifact、预算、WorkOutcome 和 ScientificCompletionValidator。它还管理 Run 中的逻辑工作区（`ResearchRun.workspaces`），把 `workspace_id` 解析为物理 `WorkspaceRecord` 并为每个 Attempt 派生 `WorkspaceGrant`，同时用 `RunLayout`/`ResourceLayout` 分开 Run 数据目录与共享资源目录。

它不形成科学观点，不修改代码或运行实验，不读取/篡改子 Agent 内部 Session，也不让 LLM 直接决定状态转换。

#### 调用入口与内部组件

| 组件 / 实际入口 | 谁调用、传入什么 | 返回什么、谁消费 | 状态与副作用 |
|---|---|---|---|
| `ResearchController.create_run(run_id, ResearchRequest)` | CLI/程序化调用方提供目标、资源声明与预算 | 返回推进到稳定状态的 ResearchRun | 创建并保存 Run，随后同步执行；不是“仅创建” |
| `run_until_stable(run_id)` | CLI resume 或 Controller 的继续执行路径 | completed / failed / paused 的 Run | 负责中断整理与后续推进；paused 不会因调用 resume 自动越过待答问题 |
| `answer_question(run_id, UserAnswer)` | UI 提交当前问题的答案 | 保存答案后继续推进并返回 Run | 校验 question_id/字段；只送对应 Scientific 或 Task；任务级续跑复用 Attempt |
| `WorkflowCompiler.compile(...)` | Controller 传 WorkRequest、现有图、registry、预算与逻辑工作区 | 成功 CompilationResult；失败 CompilationError；两者均报告本次 llm_calls | 不保存图、不调用执行 Agent；详见 [I2](INTERFACES.md#i2-工作编译) |
| 图校验与接受：`accept_proposal` / `apply_patch` | Controller 向 Scheduler 提交候选图 | 已接受图所在的 ResearchRun | 模型及接受检查验证 DAG、版本、能力绑定等；接受过程还会物化工作区，不是纯校验函数 |
| 任务执行：Scheduler `execute_task` / `run_until_stable` | Controller 驱动内部调度，按已接受图选择 ready Task | 更新后的 Run；图稳定后生成 WorkOutcome | 调用 ModulePort、保存 Attempt、登记工件、处理 retry；**不决定 Run completed** |
| 结果汇总：`_build_work_outcome` | Scheduler 内部读取当前 WorkRequest 的任务结果 | WorkOutcome，经 Controller 交给 Scientific | 区分完成、失败、警告与工件；全图 failed/blocked 另由 Controller `_unresolved_tasks` 汇总 |
| 最终 gate：`ScientificCompletionValidator.validate(run, result)` | Controller 提供 Run snapshot 与 ScientificCompletedResult | CompletionValidation：violations 或 FinalReportData | 不调用 LLM、不读取私有 Session、不修改 Run；检查闭环一致性而非科学真理 |
| `FinalReportRenderer.render(FinalReportData)` | Controller 传通过 gate 的报告数据 | RenderedFinalReport：正文及候选声明 | 纯渲染；文件登记和最终完成状态仍由 Controller 负责 |
| `RunStore.save` / `load` / `exists` | Controller/Scheduler 读写 ResearchRun | 完整快照或存在性 | Json 实现原子替换单个快照；不调度，不保存 Session 正文，不提供跨文件事务或多 worker 锁 |
| ArtifactRegistry | Scheduler、Controller、注入的 Scientific registration adapter | 冻结的 ArtifactRef | 校验和保存证据文件，不形成观点；见 [I6](INTERFACES.md#i6-工件登记与读取) |

“问题协调器”实际是上述 Controller 答案入口与 Scheduler 的问题生成/恢复辅助函数。`resume_task_in_place(run, task_id)` 只修改传入 Run，不自行 reload/save，由 Controller 将答案与恢复状态一次保存；不要另外创建 QuestionCoordinator 服务。

**错误与恢复**：业务 ask 是同 Attempt 暂停；失败 retry 是新 Attempt；进程中断由 Controller 将遗留 running Attempt 记为 interrupted 后再按预算处理。这三者不能混用。RunStore 的原子快照不保证外部命令恰好执行一次，也不授权两个进程同时 resume 同一个 Run。

**返回验收**：Scheduler 按 capability 校验成功 payload；Controller 对 Scientific 四种返回分支统一核对结构、Session 身份/状态和证据引用，再消费工作结果或答案。工件登记失败保留已有调用消费、Session、已登记工件和原根因；Scientific failure 与报告生成失败通过 `ResearchRun.terminal_error` 保留原因。详见 [I1](INTERFACES.md#i1-科学决策)、[I3](INTERFACES.md#i3-执行任务)、[I6](INTERFACES.md#i6-工件登记与读取)。这些验收不证明自然语言目标已经正确实现。

源码入口：[controller.py](../packages/orchestrator/src/resagent2_orchestrator/controller.py)、[scheduler.py](../packages/orchestrator/src/resagent2_orchestrator/scheduler.py)、[store.py](../packages/orchestrator/src/resagent2_orchestrator/store.py)、[completion.py](../packages/orchestrator/src/resagent2_orchestrator/completion.py)。

### 8.2 Scientific Agent

唯一业务职责是科学判断。输入是目标、当前 Run 摘要、授权证据、WorkOutcome 和用户回答；对外控制动作是 `request_work`、`ask_user` 和 `finish`。三者都会提前校验引用：未观察到的 artifact 返回 `ok=False` 的可恢复反馈，让 Agent 改正后再走闭环。

它可以直接使用只读 `read_artifact` 和 `literature_search` Tool。它不输出 WorkflowProposal/Patch，不选择 capability、workspace、环境或执行器，不修改 Task/Run 状态，也不直接调用 Coding/Experiment Agent。

Scientific 的 `build_context` 复用 capabilities 的 `workspace_context(state)`，但不传 EnvironmentBinding；它没有 read_file 等工作区工具，因此读取工作集只有工件正文，不因此获得文件或执行能力。正文从已有 Session events 按来源、行范围和事件顺序投影，最多 6000 字符，作为 required 上下文统一计量。不再维护 `read_artifact_summaries` 正文前缀缓存：正文前 2000 字符不是语义摘要，也不能代替选定行范围。已观察 ArtifactId 只证明过去访问过，不能证明全文仍在上下文或支持当前论断。

Prompt 明确文献检索、读取证据与科学判断是 Scientific 自有职责。timeout/HTTP 429 等服务故障经工具已有重试仍失败时，不应通过 request_work 派代码/实验任务绕路；需要用户提供材料或决定等待服务恢复时走现有 ask_user。此处是职责与恢复提示，不新增状态机、Compiler review 或确定性路由 gate；真实模型是否遵循仍须单独验收。

规划提示区分“已知前置工作”和“假设未来失败”：目标或已有证据明确说明代码缺失/损坏时，先请求必要修改，再获取实验结果；仅在没有已知故障、目标只是要求失败后修复时，先执行再根据真实失败请求修复。不能把后一条误用成“所有场景都必须先运行一次”。

#### 接口说明

| 项目 | 说明 |
|---|---|
| 入口 / 调用方 | `ScientificPort.run(ScientificTurnRequest) -> ScientificTurnResult`；由 Controller 调用，原生实现是 ScientificAgent |
| 输入 | 目标与要求、授权工件、上一轮工作目的与结果、未解决工作、用户答案、剩余预算及 Session 引用 |
| 模型所见 | context builder 组织科学上下文，interpreter 生成工作简报；共享 workspace_context 提供有界工件正文；narrative 是解释，不是证据自证 |
| 返回 | request_work：assessment + 语义工作请求；needs_user_input：assessment + 问题；completed：opinion；failed：ModuleError |
| 状态所有者 | Scientific/runtime 保存 Session；Controller 保存 assessment、WorkRequest、PendingQuestion、opinion 及 Run 状态 |
| 副作用与恢复 | 可检索并经 registration port 冻结文献；同 Run 的 Session 可跨多轮工作恢复，不创建执行 Task |

因此，“科学判断是唯一业务职责”不表示它没有工具或 IO；它有证据获取能力，但没有调度权。`completed` 必须经 Controller 最终 gate，不是模型说完成就结束整个研究。接口分支、幂等交付与失败信息保存见 [I1](INTERFACES.md#i1-科学决策)。

源码入口：[agent.py](../packages/agents/scientific/src/resagent2_scientific/agent.py)、[context.py](../packages/agents/scientific/src/resagent2_scientific/context.py)、[interpreter.py](../packages/agents/scientific/src/resagent2_scientific/interpreter.py)。

### 8.3 Coding Agent

负责准备或复用代码仓库、自己读项目结构（含 Python、依赖与已授权数据集）、在授权范围内修改代码、按需用共享环境工具（`prepare_environment`/`run_setup`/`audit_env`）准备并绑定环境、根据项目实际选择验证命令并在**绑定环境**中执行验证、按错误修复，最后交付 patch/变更文件/验证结果。验证命令获得与 Experiment 相同的只读数据集环境映射；Coding 不选择、下载或注册数据集。它不作科学结论，不直接调用其他 Agent，不扩大 workspace 授权。

#### 接口说明

入口为 `NativeCodingAgent.invoke(ModuleTaskRequest) -> ModuleResult`，由 Scheduler 通过 ModuleBinding 调用，而不是由 Scientific 直接调用。

| profile | 专属输入 → 成功 payload | 允许的领域行为与交付依据 |
|---|---|---|
| code_understand | CodeUnderstandInput → CodeUnderstandResult | 读/search/查看 diff 等；回答必须有确实观察的文件依据，不注入代码写入/进程工具 |
| code_modify | CodeModifyInput → CodeModifyResult | 读写、共享环境准备、执行验证；基于 Attempt Git baseline 的真实改动、patch 与验证记录交付 |

两者共同接收 task goal/constraints、WorkspaceGrant、输入工件、数据集、预算与 Session 上下文。工作区是最大操作授权，不是建议路径清单；模型决定项目内部操作，不能扩大授权。

成功返回的是对应 payload + ArtifactCandidate；失败/阻塞返回 ModuleError；缺用户信息可返回问题并暂停。代码修改在失败时不自动回滚，原生实现会尽量保存 failed_changes.patch。暂停恢复复用原 baseline，不能把已做修改重新当作初始状态。Session 由 Runtime 保存，Task/Attempt 与产物登记由 Scheduler 保存。

**验证有效性**：Session 身份包含 Run、Task 和 Attempt。代码验证必须有非空、合法、全部成功的命令记录，覆盖最新编辑、已审计环境的当前 generation；环境 prepare/setup 或进程重新绑定后必须重验，仅重新 audit 不够。控制提示与 finalizer 共用这个判据，finalizer 另以 Git diff 检查验证后代码是否改变。见 [I3](INTERFACES.md#i3-执行任务)、[I5](INTERFACES.md#i5-完成检查)。

源码入口：[agent.py](../packages/agents/coding/src/resagent2_coding/agent.py)、[completion.py](../packages/agents/coding/src/resagent2_coding/completion.py)；详细字段见 [CONTRACTS §10](CONTRACTS.md#10-领域-payload)。

### 8.4 Experiment Agent

负责在指定逻辑工作区运行实验（复用 Coding 已改过的代码）、通过共享环境工具（`prepare_environment`/`run_setup`/`audit_env`）准备环境、解析数据集引用、登记指标/日志和实验结果。它通过同一个 `workspace_id` 操作与 Coding 相同的 `WorkspaceRecord`，仓库来源来自统一工作区上下文而非 `ExperimentRunInput`。它不作最终科学结论，不直接调用 Coding Agent，也不自行创建 repair Task。

#### 接口说明

| 项目 | 说明 |
|---|---|
| 入口 / 调用方 | `NativeExperimentAgent.invoke(ModuleTaskRequest) -> ModuleResult`；Scheduler 绑定 experiment_run |
| 专属输入 | instructions 是实验/交付语义；expected_metrics / expected_artifacts 仅是可信调用方已知的精确键/路径；统一请求另带 workspace、数据集、约束、预算、输出目录与 Session |
| 成功返回 | ExperimentResult（metrics、evidence_files、环境/仓库身份、delivery_issues、风险）与 ArtifactCandidate；原始日志/JSON 通过登记保存 |
| 完成依据 | 实际命令结果、相对 Attempt WorkspaceSnapshot 新增/改变的证据；metrics 由代码从完整 JSON 证据集派生，不直接相信模型填的数字 |
| 不完整与失败 | 即使未指定精确名称，也必须有本 Attempt 新增/变化的证据；部分精确交付缺失可 completed_with_warnings；真实失败命令可产生确定性 failed；用户问题可暂停 |
| 恢复 / 所有权 | 同 Attempt 恢复 Session 与 workspace snapshot；重建 EnvironmentBinding 后需重审计；不自行改变 Task/Run 状态 |

summary 是解释性结果，Scientific 可以参考，但数值须追溯到证据文件。metrics 名称规范化后精确匹配，`accuracy` 不代替 `baseline_accuracy`；完整证据集内同规范名不同值会拒绝本次 finish，要求区分指标或提供一致证据。同值重复可接受，缺少部分交付仍走 warnings。Session 身份与 Coding 使用同一生成规则。见 [I3](INTERFACES.md#i3-执行任务)、[I5](INTERFACES.md#i5-完成检查)。

LLMCompiler 没有可信的精确输出名称输入，所以物化时不让它猜 `expected_*`。草图里错放的描述保留为当前 Task.instructions 中的语义说明；条件性错误证据不变成成功时的精确必需产物。直接/确定性调用方仍可使用精确字段，规则不变。成功命令 + 本次证据 + 文件派生数字是机器底线，不等于自动证明所有自然语言目标达成；Scientific 必须读取证据后判断。`run_command` 保存的 stdout/stderr 才是原始命令日志，根据 metrics 编写的说明属于派生报告，不是独立佐证。

源码入口：[agent.py](../packages/agents/experiment/src/resagent2_experiment/agent.py)、[tools.py](../packages/agents/experiment/src/resagent2_experiment/tools.py)、[completion.py](../packages/agents/experiment/src/resagent2_experiment/completion.py)。

### 8.5 runtime：共享运行机制

`runtime` 只回答「Agent 怎样运行」：Agentic Loop、LLM client、Context Composer、Tool 协议/分发、PermissionPolicy、Session/event 持久化和统一错误映射。Loop 用 `ToolObservation.ok` 区分成功与可恢复失败，把拒绝落为持久 `runtime_feedback`（`ok=False`、最高优先级 required 注入），维护有界 `recent_observations`（head+tail 截断，保留末尾错误字段），并对连续失败计数（成功的非 finish 工具重置、completion check 拒绝的 finish 累加；连续 5 次返回 `TOOL_FAILED`）。每轮还从 Tool 的 `input_model` 自动派生必填顶层参数与 guidance，作为 required `tool_contracts` 经 Composer 计入预算；ToolRegistry 仍在执行前做完整类型校验。

共享 `recent_tool_snippets` 按工具、来源和行范围去重：优先选入最近读取，装箱的最后一段可能截断，更旧内容省略；选入后按事件顺序从旧到新展示，`observed_at` 是原始 `AgentEvent.sequence`，不是新建的文件版本。三个 Agent 经 capabilities 的 `workspace_context` 复用它：Coding/Experiment 的文件正文与工件正文**各限 6000 字符**，在 `workspace_reads.file_snippets` / `artifact_snippets` 中分组；Scientific 只使用总共 6000 字符的工件正文组。读取工件不会挤掉文件片段，反之亦然。同类多个来源仍共享该类额度，不是每个文件都给 6000。有界已读来源索引提醒“内容省略不等于从未读过”，目录清单使用 `recent_tool_listing`。这是已有观测的纯投影，不是永久记忆或第二套缓存。

旧片段不因修改就整批清除：`workspace_context` 从成功的 `create_file` / `replace_text` 观测推导同路径最近修改；读取早于修改时，附 `modified_after_read_at` 指向该修改事件，说明正文是修改前的观察。修改失败或其他文件的修改不触发此标记；冻结 Artifact 不套用文件修改标记。没有标记只说明未记录后续内置写入，不保证外部进程没有改文件；不自动重读磁盘、不推导最新全文，不新增持久状态。原始 Session/trace 保留不变，同一范围的重复读取仍只选最近一次进入工作集。

工作集的 `truncated` 描述实际展示的片段；`context_truncated=true` 表示上下文预算又裁剪了原始工具结果。`recent_observations` 则明确标为短历史预览，使用相同原始事件编号，预览省略不改变工具原始 `truncated` 含义。模型需要缺失正文时仍按目标行范围读取。`search_text` 是大小写不敏感的字面子串搜索，不支持正则或 `a|b` 这种“二选一”语法，工具契约明确提示分别搜索。

读取内容仍计入 ContextComposer 的 Agent 总输入预算。Scientific/Coding/Experiment 的 Native 与 CLI 默认上限均为 **8192 tokens**，容纳对应正文及任务、工具说明、反馈；Compiler 保持 4096。CLI 的对应模块配置和 ModelProfile 的模型可用容量上限仍有效。这里没有动态分配、自动扩容或借用另一类闲置额度：显式配置过小且 required 内容装不下时，仍明确返回预算错误，不会静默丢掉整份读取内容。共享 LLM client 的 provider retry 每次计入总账，并受剩余预算限制。

上下文容量采用显式、配置驱动的 `ModelProfile`，不查询供应商元数据：组合根声明模型总窗口、输出预留和安全余量，每个 Scientific/Coding/Experiment/Compiler 再声明自己的输入上限；实际输入预算取「模块上限」与「模型窗口扣除输出、Action schema 和安全余量后的容量」两者较小值。三个领域 Agent 继续通过 Agentic Loop 使用 Context Composer；Workflow Compiler 经组合根适配复用同一个 Composer 和预算计算，但没有 Session、Tool 或 Agentic Loop，只有有界的草图/review/纠错调用。这样未来可给不同模块注入不同 LLM client/ModelProfile，而不改变领域 Agent 或 orchestrator 契约。

#### 可注入接口

| 接口 | 输入 → 输出 | 责任与边界 |
|---|---|---|
| `AgentLoop.run(definition, request, *, session_id, initial_memory=None)` | AgentDefinition + LoopRequest → ModuleResult | 通用循环、观测、反馈、Session 持久化；不解释 capability inputs 或调度 Workflow |
| `AgentDefinition` | prompt、tools、LLM、context builder、permission policy、completion check、模型类型等配置 | 是 Agent 的装配配置，不是跨模块消息；三个 Agent 通过注入差异复用 Loop |
| `ContextBuilder(request, state)` | 领域请求和 AgentState → list[ContextSection] | Agent 选择需要什么内容；Runtime 统一补工具契约/反馈等，不各自拼第二套完整 prompt |
| `ContextComposer.compose(...)` | sections + 输入预算 → ComposedContext | 统一装入和省略；必需段装不下则显式失败，不静默丢掉必需条件；Compiler 可经组合根适配直接复用，无需使用 Loop |
| `LLMClient.next_action(context, action_type)` | 已组合上下文与 action 类型 → action 对象或 dict | 只提供候选动作；Loop/ToolRegistry 才做后续验收。协议不意味着输出天然有效 |
| `PromptLLMClient.next_action(prompt, action_type)` | 普通 prompt 与结果类型 → 底层客户端返回值 | 共享 Composer/模型预算及计量转发，不运行 Loop；system prompt 和模块上限由组合根注入 |
| `PermissionPolicy.check(action, state, request)` | 动作及当前范围 → PermissionDecision | 在分发前允许或拒绝；不是操作系统沙箱或人工审批 UI |
| `Tool.execute(state, parsed_arguments)` | 已验证参数 → ToolObservation | 操作能力并返回观测/状态更新建议；见 [I4](INTERFACES.md#i4-单步工具) |
| `CompletionCheck.evaluate(state, candidate)` | 真实记录与完成提议 → CompletionDecision | 继续 / 成功 / 确定性失败；见 [I5](INTERFACES.md#i5-完成检查) |
| SessionStore | 保存/加载 AgentState 和事件 | 独占 Agent 内部会话；上层仅持有 SessionRef，不读它来调度任务 |

LoopRequest 只要求 run/task/attempt、预算和父 Session；Scientific 的 task/attempt 可为空。领域 inputs、工作区和研究语义由注入的 context builder、工具及 finalizer 使用，因此共享 Runtime 不依赖具体 Agent。

CLI 与 E2E 是独立组合根：各自选择模型、资源根、会话和预算，但都通过 runtime 的 `PromptLLMClient` 适配普通编译提示，通过 orchestrator 的 `ScientificArtifactRegistration` 完成 Scientific 工件冻结、Run 登记及同轮查找。前者不认识 Compiler，后者不 import 具体 Agent。E2E Compiler 现在同样执行 4096 输入上限，不再用 estimated_tokens=0 的裸包装绕过预算；这项变化需要真实 E2E 验收。

**LLM 客户端的最小约定**：必需方法只有 `next_action(context, action_type)`。Runtime 通过 `getattr` 使用可选的 `context_budget`、`set_attempt_limit`、`last_attempts`、`set_trace_context`、`record_validation`，没有这些 hooks 的客户端仍可运行。缺少计量 hook 时按一次请求计一次调用；若客户端内部会重试，应提供真实 attempts 和限制 hook。换客户端不需要新增 Provider 层，但应测试预算、计量与错误约定。

**上下文计量**：Composer 对最终渲染文本估算，包含 section 标题与段间分隔符；`estimated_tokens == estimate_tokens(text)`，不超过输入上限。required 保持顺序，optional 按优先级稳定选入，装不下的大段不会阻止后面较小的可选段。仍使用字符数/4 的近似值，不宣称等于供应商 tokenizer；Action schema、输出预留及安全余量由 ModelProfile 另外扣除。

**trace 的含义**：`action_valid` 只表示 provider 初步解析得到了候选动作；后续外层 Action schema 错误以同一 `call_id` 的补充记录关联。它不证明工具参数通过 `input_model` 校验、工具执行成功、finalizer 通过或科学结论正确。调试时结合原始响应、关联校验记录和 Session 的 action/observation/error，不以“全部 action_valid=True”代替验收。

Runtime 恢复检查 run/task/attempt/owner/agent 与可恢复状态。循环在工具派发前重新检查 deadline：LLM 或权限检查耗尽时间后不再启动工具，已发生的调用仍计账；这不是对运行中工具的强制抢占。`ToolObservation` 的 question、request_work、finish_candidate 至多携带一种，防止相互矛盾的控制信号。

源码入口：[loop.py](../packages/runtime/src/resagent2_runtime/loop.py)、[context.py](../packages/runtime/src/resagent2_runtime/context.py)、[llm.py](../packages/runtime/src/resagent2_runtime/llm.py)、[tools.py](../packages/runtime/src/resagent2_runtime/tools.py)、[store.py](../packages/runtime/src/resagent2_runtime/store.py)。

### 8.6 capabilities：可复用的真实能力

`capabilities` 只回答「Agent 可以调用什么能力」：workspace、process、Artifact 读取、Git、repo materialization、environment（`EnvironmentManager` + `prepare_environment`/`run_setup`/`audit_env` 三个共享 Tool）、dataset、hardware、literature，以及内部的 `WorkspaceSnapshot`。dataset 能力以 `DatasetCatalog` 从共享根的 `catalog.json` 读取唯一的 `dataset_id → relative_path` 注册表，并复用同一组解析、上下文和环境映射函数服务 Scientific/Coding/Experiment；它不选择数据集，也不下载资源。`ResourceLayout` 提供共享 dataset/env 路径约定；`RunLayout`（Run 数据目录约定）归 orchestrator。它们提供物理边界和可审计执行，不包含科学决策或 Workflow 调度。

#### 能力入口与消费者

| 能力 / Python 入口 | 输入 → 输出 | 谁复用、有哪些副作用 |
|---|---|---|
| WorkspaceBoundary | WorkspaceGrant + 相对路径 → 已核验可读/可写路径 | Coding/Experiment 工具复用访问范围规则；不选择任务工作区 |
| RepoMaterializer.materialize | 授权工作区 + repo 来源 → MaterializedRepo | 两个执行 Agent 共用准备/复用逻辑；可 clone、建立本地工作区，不调度任务 |
| GitWorkspace / WorkspaceObserver | 授权目录 → Git 操作、WorkspaceSnapshot 及相对基线变化 | Agent 验证与产物判定共用；Git 用 baseline，非 Git 用有界 file-hash 回退 |
| ProcessRunner.run | 命令 + 日志目录/超时/环境映射 → VerificationResult | Coding 验证与 Experiment 执行共用；启动真实进程并写 stdout/stderr，不判断科学成功 |
| EnvironmentManager.inspect / prepare / audit | run/workspace 身份、Python 要求 → 基础环境信息或健康结果 | 管理 run+workspace 环境；prepare 可创建/重建基础 Python，不替 Agent 决定项目依赖 |
| EnvironmentBinding + 三个 environment tools | 已授权操作 → 当前环境、认证状态、工具观测 | Coding/Experiment 共用绑定规则；setup 可改依赖，开始执行即使旧 audit 失效；基础 audit 不是测试通过证明 |
| DatasetCatalog.references / resolve_dataset_refs | catalog 与 DatasetRefs → 注册引用/可读目录；辅助函数生成上下文和环境映射 | 三个 Agent 复用；不下载、不选择默认数据集；资源缺失走已有 ask_user |
| HardwareAudit | 当前机器 → 硬件信息 | 为实验选择提供事实，不决定实验方案 |
| LiteratureSearchBackend.search | query、条数与年份条件 → list[LiteraturePaper] | Scientific Tool 使用；可访问网络；Tool 将规范化结果交 registration port 冻结 |
| RegisteredArtifactReader.read_text | 当前 Run + 授权 ArtifactRefs + artifact_id + 可选行范围 → 有界内容 | 先核对 Run、artifact_id、整份文件 SHA256，再切片；不允许直接传任意文件路径 |
| workspace_context | AgentState + 可选 EnvironmentBinding → 共享 ContextSections | 三个 Agent 复用；Coding/Experiment 投影绑定及文件/工件，Scientific 只投影已有工件读取、不传绑定；每类正文 6000 字符，不拥有新状态，均纳入总输入预算 |

这些是普通 Python 组件和部分 Tool，不要求每个能力都有自己的 Agent、Session 或“服务管理器”。例如环境准备是一项能力，决定该装什么依赖是 Agent 策略；文件内容访问属于能力，决定把哪些片段保留在模型上下文属于 Runtime 的共享上下文机制。

EnvironmentBinding 是 capabilities 的公开 API，而非跨模块 wire contract。恢复已有 prefix 会重建 binding，但 certified 为 false，需要重新 audit；marker 只表示基础环境事实。绑定的 generation 在 prepare/setup 开始执行或进程重新绑定时改变，依赖旧 generation 的验证不能复用。Coding 的控制提示与完成 gate 共用同一验证规则：当前代码和环境都必须被成功验证。ProcessRunner 的 shell-free、凭据清理和路径检查有明确用途，但不是 OS 级隔离，也不能防止被授权程序做出全部不当行为。

模型每轮看到的 environment 从工具实际使用的同一个 binding 生成，不从 `memory.environment` 或历史 `env_audit` 猜当前认证状态。文件与工件可按行范围重读；`search_text` 支持单文件或目录并沿用 WorkspaceBoundary 检查。分段和环境投影都属于共享能力，不在两个 Agent 各实现一套。

源码入口：[capabilities 包](../packages/capabilities/src/resagent2_capabilities/)、[environment_tools.py](../packages/capabilities/src/resagent2_capabilities/environment_tools.py)、[process.py](../packages/capabilities/src/resagent2_capabilities/process.py)、[literature.py](../packages/capabilities/src/resagent2_capabilities/literature.py)。工具与工件的详细交互见 [I4](INTERFACES.md#i4-单步工具)、[I6](INTERFACES.md#i6-工件登记与读取)。

### 8.7 contracts：跨模块词典与结构规则

contracts 不是运行中的 Actor，没有 run/invoke 方法，不持有 Session 或调度任务。各调用方通过 `models.py` 中的模型构造、`model_validate` 和序列化操作来交换数据。

| 输入 | 输出 / 作用 | 不负责什么 |
|---|---|---|
| Python 数据或持久化 JSON | ResearchRequest、WorkRequest、Workflow、ModuleTaskRequest/Result、ScientificTurnResult 等 typed 对象，或 ValidationError | 不操作工作区、不运行 LLM、不判断自然语言目标是否实现 |
| 合法模型 | JSON/schema，用于持久化与调用约束 | schema 声明不能代替接收方实际调用校验 |
| CapabilityRegistry 与专属 inputs | registry 声明 capability、owner、description；专属 inputs 约束输入结构 | registry 不持有具体 Agent 或执行策略；实际绑定由组合根提供 |

结构校验只能证明检查过的规则。`ModuleResult` 外壳合法，并不自动证明 payload 与 capability 相配、Session 属于当前 Run、证据已读或依赖变更后测试仍有效；这些需要接收方与领域完成检查共同落实。因此可替换实现要通过同一组接收边界测试，不能仅凭返回类型名称宣称守约。

字段说明见 [CONTRACTS](CONTRACTS.md)；源码见 [models.py](../packages/contracts/src/resagent2_contracts/models.py)。通用运行机制的小对象（AgentState、ToolObservation、ContextSection）留在 runtime；EnvironmentBinding 等能力对象留在 capabilities，不为了“统一”全部搬入跨模块 contracts。

### 8.8 组合根与用户入口

当前产品组合根是 `apps/cli` 的 `build_application(*, data_root, workspaces=None) -> CliApplication`，提供 controller 和 run_store。它创建真实模型客户端、三个 Agent、各自 SessionStore、共享 ResourceLayout、DatasetCatalog、ArtifactRegistry、Compiler 和 Scheduler，并通过 Port 装配到 Controller。`e2e/real_e2e.py` 是验收用的另一组合根，不是生产 CLI 的依赖。

| 使用面 | 调用或读取 | 明确不做 |
|---|---|---|
| CLI run / answer / resume | Controller 的三个业务入口；run 时把 flag 转成 ResearchRequest | 不自己改 Task/Run 状态、不直接调用 Agent |
| show / artifacts 与 shell attach | RunStore 中的快照及已登记产物 | 读取不启动执行；attach 不暗中 resume |
| shell 的后台 Runner | 后台调用既有同步入口；前台轮询快照和 trace 渲染进度 | 不成为第二套 Scheduler；停止监看不等于取消 Run |
| LLM / Artifact adapter | 把具体客户端与登记能力接到约定接口 | 不把密钥传给实验进程；不替 Agent 作科学决策 |

数据根是存储布局配置，不是会话身份的替代品；同一个应用会服务多个 Run，所以任务 Session 标识由 Run、Task、Attempt 共同生成，动态 Artifact resolver 也按 Run 隔离。暂停恢复复用同一 Attempt；同一 Attempt 连续提问则使用不同 QuestionId。不要用“每次换全新目录”代替接口隔离测试。当前没有独立的聊天 API 服务，架构图中的 API/Conversation Adapter 是可替换入口位置，不是已交付功能。

源码入口：[composition.py](../apps/cli/src/resagent2_cli/composition.py)、[main.py](../apps/cli/src/resagent2_cli/main.py)、[shell.py](../apps/cli/src/resagent2_cli/shell.py)；用户操作见 [CLI README](../apps/cli/README.md)。

代码依赖为两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`。composition root 同时依赖 orchestrator 与具体 Agent，并通过 Port 注入。orchestrator 不 import 具体 Agent；runtime 不依赖 capabilities；capabilities 不依赖具体 Agent。

## 9. 执行边界（Coding / Experiment）

### 9.1 Coding

原生 Coding Agent 的 `code_understand` 与 `code_modify` 复用同一 AgentLoop，并在 loop 前确定性复用 `RepoMaterializer` 准备/复用仓库。`code_understand` 不注入写/进程 Tool 并在结束时验证 Git 未改变；`code_modify` 的写入受 WorkspaceGrant 限制，Agent 先读项目的 Python/依赖要求、按需用共享环境工具准备并绑定环境，验证命令由 Agent 根据项目实际自行选择（shell-free，经 ProcessRunner 的结构化解析与权限检查，在绑定环境执行）。两个 profile 都看到共享数据集目录，`code_modify` 的验证命令额外获得 `RESAGENT2_DATASET_ROOT`/`RESAGENT2_DATASETS_JSON`，但不能下载或替换数据集。finalizer 以真实 Git diff 和命令结果生成 payload/ArtifactCandidate。

工作区允许共享且可含未提交改动：Attempt provenance 由 `WorkspaceSnapshot` 建立——Git workspace 用 `GitBaseline`（临时 index + read-tree + add -u + write-tree），非 Git workspace 用有界 file-hash fallback——按 Attempt 隔离变更，不要求干净工作区。ProcessRunner 不是 OS 沙箱；可信调用方若提供过弱验证命令，系统不能证明代码真正满足自然语言目标。

### 9.2 Experiment

原生 Experiment Agent 实现 `experiment_run`：在统一工作区上由 RepoMaterializer 确认 source+commit，EnvironmentManager 用 `run_id + workspace_id` 绑定基础环境（`inspect`/`prepare`/`audit`，env 目录来自 `ResourceLayout.env_root`），依赖安装由共享 `run_setup` 完成，HardwareAudit 提供硬件上下文；Run 数据集由组合根从 `DatasetCatalog` 自动注册，任务级 `DatasetRef(dataset_id, relative_path)` 再解析到 `ResourceLayout.dataset_root` 下的具体只读目录，并经通用环境变量 `RESAGENT2_DATASET_ROOT`/`RESAGENT2_DATASETS_JSON` 交给脚本；实验命令需先通过绑定当前环境的 audit。finalizer 要求至少一次实验命令成功，且证据文件必须相对本 Attempt 的 `WorkspaceSnapshot` 基线新增或改变。实验在授权工作区产出证据，由 ArtifactRegistry 冻结；命令日志使用 Attempt 输出目录，不把这些 Run 数据混入共享缓存。

ProcessRunner 同样不是 OS 沙箱；environment audit 是流程正确性检查而非安全隔离；setup/experiment 分类也不是安全分类。详细约束见 ADR-0004、ADR-0005 和 contracts。

## 10. 模块通信规则

本节是通信总览；完整的输入、返回分支、状态归属、失败与重复调用说明见 [六张接口卡](INTERFACES.md)。模块侧阅读入口在 §8，不需要从字段全集反推架构。

专业 Agent 不能直接互调。当前边界只有：

- Orchestrator → Scientific：`ScientificTurnRequest`；
- Scientific → Orchestrator：`ScientificTurnResult`；
- Orchestrator 内部：`WorkRequest` → WorkflowCompiler → `WorkflowProposal | WorkflowPatch`；
- Scheduler ↔ Coding/Experiment：`ModuleTaskRequest` → `ModuleResult`；
- Orchestrator → Scientific：`WorkOutcome` + 已授权 `ArtifactRef`；
- Agent → Orchestrator：`QuestionDraft`；Orchestrator ↔ User：`PendingQuestion` / `UserAnswer`。

命名约定：`ResAgent2` 是整个项目；`Orchestrator` 是研究编排模块，代码包名为 `resagent2_orchestrator`；`ResearchController` 是其中驱动研究闭环的组件。不再用“ResAgent”作为当前模块的第二个名字。

`WorkflowProposal` / `WorkflowPatch` 仍是 typed boundary，但不再跨 Scientific Agent 边界；它们由 Orchestrator 内部的 WorkflowCompiler 产生。

返回时，Scientific 的 `context.build_context` 调用内部纯函数 `interpreter.render_work_brief`，把 WorkOutcome 整理成模型需要的工作简报；interpreter 不是独立模块、Agent 或新调度层。

- `purpose`：上一轮请求的业务目的；`outcomes`：完成情况、解释性 narrative 与需读取的证据指针。
- `blocking_items`：保留失败工作的业务 objective、错误信息及有界 stderr 摘录；隐藏内部 TaskId 不等于删除工作目标。
- narrative 不自证结果；warnings 只投影 code/message；诊断摘录只用于执行诊断，命令、日志路径及完整 details 留在审计记录中。

原始 WorkOutcome 仍保留。最终 Validator 从 Run 对账失败任务，Scientific 用 limitations 说明影响，报告确定性展示执行问题。

## 11. 状态与生命周期

### 11.1 TaskStatus

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> running: scheduler creates Attempt
    running --> completed: completed result
    running --> failed: terminal failed result
    running --> blocked: blocked result
    running --> needs_user_input: question result
    failed --> pending: explicit/automatic retry
    blocked --> pending: explicit recovery + retry
    needs_user_input --> pending: matching answer persisted
```

### 11.2 ResearchRun

`planning`、`analyzing`、`replanning` 都是活动，不是 RunStatus：

```mermaid
stateDiagram-v2
    [*] --> running: create ResearchRun
    running --> paused: PendingQuestion exists
    paused --> running: matching answer persisted
    running --> completed: ScientificOpinion passes final gate
    running --> failed: unrecoverable system/contract failure or exhausted budget without valid opinion
```

WorkRequest 执行期间 Run 仍为 running；无需增加 planning 或 waiting_for_work 状态。单个 Task 失败先进入 WorkOutcome，而不是直接把 Run 置为 failed。

### 11.3 当前实现

production composition root 走 `ResearchController`：`ScientificAgent` 提出 `WorkRequestDraft`，`WorkflowCompiler` 生成 WorkflowProposal/Patch，`WorkflowScheduler` 执行 Coding/Experiment 图，`WorkOutcome` 回传 Scientific Session 再形成最终 `ScientificOpinion`，经 `ScientificCompletionValidator` 后写 completed。`WorkflowScheduler` 只执行任务图、不决定 ResearchRun 完成；`ResearchController` 是唯一的 Run 创建/回答/完成入口；任务级 ask/resume 在同一 Attempt 上继续。

产品入口位于 `apps/cli`：`composition.build_application()` 负责 production 装配，一次性 CLI 命令与交互 shell 都只调用现有 `ResearchController`。交互 shell 仅轮询原子持久化的 Run snapshot 与可选 LLM trace 来渲染进度，不引入第二套调度或状态机；`e2e/real_e2e.py` 仍是独立的真实验收组合根。

### 11.4 中断恢复边界

`RUNNING` 是已经持久化的执行意图，不是进程存活证明。当前系统是单进程、同步调用模型：当新的 `ResearchController.run_until_stable()` 读取到遗留的 running Task/Attempt 时，它将该 Attempt **保留为历史**，结算为 `failed + ErrorCode.interrupted + retryable=True`；只有剩余 Attempt 预算允许时，Task 才回到 `pending` 等待新的 Attempt。系统不得静默删除或覆盖中断 Attempt。

恢复只有 `ResearchController.run_until_stable()` 入口：它先整理遗留 Attempt，再重新计算 ready Task。`WorkflowScheduler` 不自行猜测旧进程是否仍在执行，也不把 Run 判为 completed。

ScientificSession 的引用在首次 Scientific turn **之前**由 Controller 绑定到 ResearchRun；runtime 仍独占 Session 内容。首次 turn 中断后，Scientific Agent 以确定性 session id 重新打开已保存的 `active` checkpoint；正常的 ask-user/request-work 继续只允许从 `paused` Session 恢复。Session 不存在时可创建首次 checkpoint，已完成或失败的 Session 不可作为恢复目标。

最终完成 gate 必须拒绝任何 `pending`、`running` 或 `needs_user_input` Task；只有所有 Task 都处于终态，ScientificOpinion 才能被验证为 Run completed。详细的取舍见 ADR-0012。

## 12. Artifact 与安全边界

Artifact 保持两道检查：

1. capabilities/Tool 执行前按 WorkspaceGrant 做 resolve、symlink 和读写授权检查；
2. Orchestrator 登记时重新检查文件存在、相对路径、containment、symlink escape，计算 hash，绑定 run/task/attempt（或 session/orchestrator）并冻结复制。

只有 completed/completed-with-warnings Attempt 的 Artifact 自动作为成功证据传播；失败/blocked Attempt 的诊断 Artifact 可以登记并进入 WorkOutcome，但必须保留失败语义。

Scientific Agent 只能通过本 Run 的 ArtifactRef 授权集合读取已有证据。静态与动态 resolver 返回的引用均在读取前检查 Run 和 artifact_id，再校验文件 hash。它输出的 evidence_artifact_ids 还必须确实通过 `read_artifact` 或 `literature_search` Tool 观察过；最终 observed 校验不能替代读取前授权，见 [I6](INTERFACES.md#i6-工件登记与读取)。

ArtifactRef 的 provenance 是互斥三态（见 `CONTRACTS.md` §13）：执行 Artifact（coding/experiment + task/attempt）、Scientific Tool Artifact（scientific + session）、Orchestrator Artifact（orchestrator + source_type=import/final_report）。`literature_search` 成功后先规范化结果，通过 composition root 注入的 Artifact registration port 交给同一个 Orchestrator Artifact Registry，以当前 run/session 冻结登记，再把 ArtifactRef 返回 Agent。Scientific Tool 不能自行分配 ArtifactId、hash 或伪造 provenance。

Registry 自产的 Scientific JSON 使用多行序列化（`indent=2`），使长文献列表的后部可通过现有行范围读取；SHA256 按最终冻结字节计算。只影响新工件，不重写旧工件或在读取时重新格式化，避免破坏完整性校验。

observation history 的所有者是 runtime SessionStore；Orchestrator 不读取原始 prompt、reasoning 或任意 Session event。跨边界只传 ScientificPort finalizer 从 trusted Tool result 派生的 `observed_artifact_ids`，ResearchRun 持久化其已复核并集用于最终审计。

## 13. 完成判定

Coding、Experiment、Scientific 各自的确定性 finalizer 是领域完成证据的唯一判断者；Orchestrator 不从 summary 或任意 payload 猜测完成状态。

完成检查分两处：领域 finalizer 判断实际操作证据；接收方核验公开契约。Scheduler 按 capability 验证成功 payload，Controller/gate 验证 Session、控制状态及已登记、已观察的证据；`required_evidence_kinds` 按工件种类检查，已读且授权的导入证据也有效。两处都不把“schema 通过”当作科学正确的证明。

ResearchRun 只有同时满足以下条件才能 completed：

1. Scientific Agent 已通过 `finish` 返回合法 ScientificOpinion；
2. 没有 active WorkRequest、running Task 或 PendingQuestion；
3. opinion 引用的 ArtifactId 都属于本 Run、已登记，且包含在 ScientificTurnResult 的 code-derived observed_artifact_ids 和 ResearchRun 已复核 trace 中；
4. opinion 明确写出观点、证据、局限和未解决问题；
5. final report 只展示 ResearchRequest、Run state、registered Artifact 和 ScientificOpinion 中可追踪事实；
6. 每个执行 Task 的领域完成证据已经由所属 capability finalizer 验证，不能由 summary 冒充；
7. 所有仍 failed/blocked 的 Task 都由 Validator 直接从 Run 对账并写入执行问题；Scientific 的 limitations 必须非空，不能静默丢失其对结论的影响。

`inconclusive` 是合法科学观点，不等于运行失败。若系统忠实完成了可执行工作、证据可追踪且 opinion 说明为何不能下结论，Run 可以 completed。运行失败表示系统没有形成可靠闭环，例如预算耗尽且无合法 opinion、状态损坏或不可恢复契约错误。

这里的「验证」是闭环一致性验证，不是科学真理验证。Scientific Agent 的 deterministic completion check 使用 Session 工具记录、传入的未解决工作和证据要求检查候选；Orchestrator 的 ScientificCompletionValidator 再基于完整 Run 的经校验深拷贝复核。二者输入和职责不同，不能称为对同一 snapshot 重复校验。最终 gate 拒绝时，不能把无效候选写成 completed。观点是否在语义上正确属于模型质量与评测，不属于确定性状态机能够证明的事项。

## 14. 不可破坏的架构约束

1. Scientific Agent 不输出执行图字段；WorkflowCompiler 不形成科学结论、不决定代码文件/验证命令/物理目录；
2. Orchestrator 可以使用 LLM 编译图，但状态转换只由代码执行；
3. 专业 Agent 不直接互调；
4. WorkflowTask、Attempt、Session、AgentAction 不得混为同一层；
5. 跨模块持久事实必须成为 contract 字段或 Artifact，不能只藏在 prompt/summary；
6. runtime 不包含领域 Tool，capabilities 不包含 Agent 策略，Agent 不包含顶层调度器；
7. 失败、警告和不确定性必须保留，不能通过自然语言包装成成功；
8. 每次扩展先证明简单方案不足，禁止为未来假设过度设计。
