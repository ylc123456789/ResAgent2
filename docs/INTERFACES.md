# 接口说明卡

这份文档按六个实际调用边界解释接口；[ARCHITECTURE §8](ARCHITECTURE.md#8-模块职责) 按模块解释职责，两者互相对应。字段全集、类型和 wire 版本仍以 [CONTRACTS](CONTRACTS.md) 为准，不在这里再维护一份 schema。

**阅读方式**：先看谁调用谁，再看输入、返回分支、状态归属和失败处理。这里的“接口”是当前进程内 Python 调用，不是新增 RPC、MCP 或 A2A 服务；Protocol 也不意味着实现自动获得隔离、幂等或完整结果验证。

**审查基线**：`54dfa99`，2026-09-05。修复进度与验收见 [CONTRACT_FIXES](reviews/CONTRACT_FIXES.md)。这里解释当前行为，不另外维护字段全集；F/D 编号仅用于追溯审查。

## 导航

| 接口卡 | 调用方向 | 对应架构模块 |
|---|---|---|
| [I1 科学决策](#i1-科学决策) | Controller → ScientificPort | Orchestrator、Scientific Agent |
| [I2 工作编译](#i2-工作编译) | Controller → WorkflowCompiler | Orchestrator 内部 Compiler 与图校验 |
| [I3 执行任务](#i3-执行任务) | Scheduler → ModulePort | Orchestrator、Coding、Experiment |
| [I4 单步工具](#i4-单步工具) | AgentLoop → ToolRegistry → Tool | runtime、capabilities、领域 Agent |
| [I5 完成检查](#i5-完成检查) | AgentLoop → CompletionCheck | runtime 与各 Agent 的确定性 finalizer |
| [I6 工件登记与读取](#i6-工件登记与读取) | 生产方 → Registry；Scientific → reader | Orchestrator、capabilities、组合根 |

## I1 科学决策

**目的**：把“当前证据意味着什么、下一步需要什么”交给 Scientific，而不是把任务调度交给它。

```python
ScientificPort.run(request: ScientificTurnRequest) -> ScientificTurnResult
```

- **调用方 / 实现方**：`ResearchController` / `ScientificAgent`（或实现同一 Port 的替代实现）。Port 声明在 orchestrator 的 `controller.py`，不是 Scientific 内部的一套调度 API。
- **输入**：研究目标与约束、授权 ArtifactRefs、上一轮 `WorkOutcome + WorkRequestDraft`、全图未解决任务、属于 Scientific 的用户回答、剩余预算与父 Session ID。结果和答案按当前交付路径送入，不是每次重放全部 Run 历史。
- **输入权威**：Run 与授权工件由 Controller 提供；Scientific 不自行修改它们。Session 正文归 Agent/runtime，Controller 只保存 `SessionRef` 和经复核的 observed IDs。

| 返回 status | 必要业务内容 | Controller 下一步 |
|---|---|---|
| `request_work` | assessment + WorkRequestDraft + paused Session | 保存观点；为工作请求分配身份、编译和执行；之后恢复 Scientific |
| `needs_user_input` | assessment + QuestionDraft + paused Session | 保存 PendingQuestion，Run paused，等待用户 |
| `completed` | opinion + completed Session | 交给最终 `ScientificCompletionValidator`；通过并登记报告后才完成 Run |
| `failed` | ModuleError；Session 可为空，否则为 failed Session | Run 置 failed，具体原因保存在 terminal_error，CLI 可查询 |

每个分支还带 code-derived `observed_artifact_ids` 和本次新增 `llm_calls`。`completed` 只是 Scientific 模块完成，不等于 Run 已完成。

- **给模型看什么**：`interpreter.render_work_brief` 将执行结果投影成目的、完成情况、解释性 narrative、警告、失败诊断和证据指针。不把原始执行对象或内部 Task ID 原样 dump 给模型。summary 可帮助模型理解，但不是机器状态或数字证据的权威。
- **副作用**：Scientific 可调用只读工件工具、检索文献；检索产物通过注入的 registration port 登记。它不改 Workflow/Task，不直接调用 Coding 或 Experiment。
- **自有能力与服务故障**：Prompt 要求检索、证据阅读和科学判断自行使用对应 Tool；timeout/429 经工具重试仍失败不是派代码/实验任务的理由。需要材料或恢复决定时使用已有 ask_user。这是行为指引，不是新的确定性路由保证。
- **暂停与恢复**：一个 Run 使用同一个 Scientific Session，跨越多个 WorkRequest。原生实现按 `work_request_id` / `question_id` 缓存已交付结果，重复交付不重新追加观测或调用 LLM；这不是所有替换 Port 自动具备的能力。
- **完成边界**：最终 gate 从 Run 对账执行问题、证据归属与观察记录；失败任务的 ID 由代码写入最终报告，不要求模型传回。用户答案的路由以已持久化问题为准，不广播到其他 Agent。
- **接收验收**：四种响应都先校验 schema、所属 Scientific Session/状态、观察与引用的 Run 归属，再确认交付。非法回复不能消费 stable WorkOutcome、标记答案已交付或替换 Session；已发生的调用仍计账。最终 gate 是可独立调用的入口，会再验完整 completed 响应。Scientific 自己在转译失败时结算其所属 Session，不让 failed 与 completed 状态矛盾。
- **证据要求**：`required_evidence_kinds` 表示观点必须引用已登记且已观察的指定种类 Artifact；Native 和最终 gate 共用纯判据，授权导入的文献也可以满足，不偷偷等价成“必须调用某工具”。失败简报保留 objective、具体错误及有界 stderr 摘录，但不暴露内部 Task ID。

**源码**：[Controller / ScientificPort](../packages/orchestrator/src/resagent2_orchestrator/controller.py)、[ScientificAgent](../packages/agents/scientific/src/resagent2_scientific/agent.py)、[interpreter](../packages/agents/scientific/src/resagent2_scientific/interpreter.py)、[最终 gate 与报告](../packages/orchestrator/src/resagent2_orchestrator/completion.py)。

**契约 / 测试**：[CONTRACTS §16](CONTRACTS.md#16-科学控制契约)；[Controller 测试](../tests/orchestrator/test_controller.py)、[四种返回边界](../tests/orchestrator/test_scientific_turn_boundary.py)、[最终完成测试](../tests/orchestrator/test_scientific_completion.py)、[简报测试](../tests/scientific/test_interpreter.py)。

## I2 工作编译

**目的**：将一轮语义化工作请求翻译成当前可以执行的任务图，不执行任务，也不形成科学观点。

```python
WorkflowCompiler.compile(
    request: WorkRequest, *,
    current: Workflow | None,
    registry: CapabilityRegistry,
    budget: RunBudget,
    workspaces: list[WorkspaceDescriptor] | None = None,
    remaining_calls: int | None = None,
) -> CompilationResult
# CompilationResult.output: WorkflowProposal | WorkflowPatch
# CompilationResult.llm_calls: 本次编译实际使用的 LLM 调用数
```

- **调用方 / 实现方**：Controller / `LLMWorkflowCompiler`，测试可注入确定性 Compiler。此接口在 orchestrator 内部，不是 Scientific 的输出协议。
- **输入权威**：持久化 WorkRequest 指定本轮目的与约束；registry 给出可用 capability；current 给出已有图；workspace descriptor 是逻辑工作区摘要，不是任意文件访问授权。
- **调用前置**：Controller 调用新编译前检查剩余任务名额；名额耗尽时，当前 WorkRequest/Run 以 `budget_exhausted` 失败，不调用 Compiler、不追加任务、不增加编译消费。已接受图（含接收后尚未迁移至 executing 的恢复窗口）优先恢复，不因任务名额已占满而中止。
- **审查输入与职责**：draft/review 共用能力说明；review 同时读目标、依赖、约束和与物化器一致的输入投影。要求写在 inputs/constraints 中即有效，不必在标题重复。代码验证不等于正式实验交付，不能为适应任务名额让一种能力承担另一种能力的职责。语义审查可能漏判，不能冒充结构硬校验。
- **交付名称边界**：LLMCompiler 不读代码，也没有 typed 精确输出键/路径来源；实验语义及条件写 Task.instructions。物化时清空其猜测的 expected_metrics/expected_artifacts，错放文本降为本 Task 的语义说明。公开精确字段留给可信直接/确定性调用方，不用模糊匹配把描述当文件名。
- **模型与代码的分工**：LLM 只生成局部 `CompilationDraft`，代码分配 Run 内 Task ID、绑定 WorkRequest、解析依赖和 workspace，随后可做一次短语义 review。这里从局部 key 到正式 ID 的“全局物化”不表示 Task ID 在不同 Run 之间唯一。不给 Compiler 代码扫描、环境安装或执行工具。
- **输出**：无 current 时返回 Proposal；已有图时返回只追加 Patch。返回候选不等于接受候选；Controller 仍交由 Scheduler 接受并保存。
- **接收判据**：`workflow_validation.validate_workflow_candidate` 是非空及本轮依赖的共享纯函数。Compiler 在 review 前使用它生成纠错反馈，Scheduler 在持久化前再次使用，替代 Compiler 不能绕过。Pydantic 保证图结构，物化器检查声明，Scheduler 另核实际 binding、workspace、预算与 revision；不是把不同边界的检查全部合并。
- **LLM 返回边界**：CompilerLLM 可返回 dict 或 BaseModel。原生 Compiler 将模型实例投影为值后，按 CompilationDraft/CompilationReview 重新校验，不因已是模型实例就相信其字段有效；候选类型支持不等于跳过校验。
- **状态与副作用**：不保存 Session、不修改 Run/Workflow、不执行任务；真实实现会调用外部 LLM 并产生 trace，实例维护本次调用计数。因此“无长期会话”不等于纯函数，也不保证同实例可并发调用。
- **纠错与失败**：结构拒绝和语义拒绝**共享一次纠错重编**，总计最多两版 draft，每版最多一次 review；仍失败则抛 `CompilationError`。调用预算限制真实尝试数，Controller 负责失败状态及记账。成功从 `CompilationResult.llm_calls` 取数；失败必须由 `CompilationError.llm_calls` 报告本次消费，保留 cause，不读取实现实例的隐藏属性。替代实现抛其它异常时受控结束 Run，标记 `compiler_usage_known=False`，账目只是已知消费下界，不把未知消费当成确定零次。
- **重启与重复**：COMPILING 已接受图则继续执行，未接受图则可重编。不能保证再次调用产生同一图；确定性保证落在身份物化、结构校验及接受阶段，不在 LLM 的任务选择上。
- **依赖语义**：`depends_on` 表示上游成功后才可运行；不是“上游失败则执行”。条件修复须等失败成为 WorkOutcome，再由 Scientific 发下一轮 WorkRequest，不能提前塞进成功依赖图。
- **边界提醒**：“Workflow Validator”是模型校验、Compiler 检查、Scheduler 接受检查的合称，不存在额外独立服务；各处检查范围并不完全相同。语义 review 也不是自然语言目标必然完整的证明。

**源码**：[Compiler](../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[Scheduler 接受图](../packages/orchestrator/src/resagent2_orchestrator/scheduler.py)、[图模型](../packages/contracts/src/resagent2_contracts/models.py)。

**契约 / 已有测试**：[CONTRACTS §6](CONTRACTS.md#6-workflow-执行图契约)、[§16.5](CONTRACTS.md#165-workflowcompiler-边界)；[Compiler 测试](../tests/orchestrator/test_compiler.py)、[repair 图测试](../tests/orchestrator/test_repair_flow.py)。

## I3 执行任务

**目的**：让 Scheduler 调用一个领域执行能力，而不依赖其内部 Agent 实现。

```python
ModulePort.invoke(request: ModuleTaskRequest) -> ModuleResult
# ModuleBinding(owner, port) 将 capability 路由到实现。
```

- **调用方 / 实现方**：WorkflowScheduler / NativeCodingAgent、NativeExperimentAgent 或替换 Port。
- **输入**：run/task/attempt 身份、capability + 专属 inputs、任务目标和约束、授权输入工件及数据集、任务预算、WorkspaceGrant、workspace 来源、Attempt 输出目录、父 Session 和该任务的答案。
- **输入权威**：Scheduler 从 Run 与已接受 Task 组装请求。任务 constraints 是下游约束来源，不再广播原始研究控制约束；数据集来自 ResearchRequest，Python 硬约束来自 WorkspaceSpec，物理访问范围以 WorkspaceGrant 为准。
- **路由**：`code_understand → CodeUnderstandResult`；`code_modify → CodeModifyResult`；`experiment_run → ExperimentResult`。成功时应携对应 payload；payload 的数字/验证事实不能用 summary 代替。

| ModuleResult.status | Scheduler 行为 | Attempt / Session 含义 |
|---|---|---|
| `completed` | Task completed；登记成功证据 | 本 Attempt 终结 |
| `completed_with_warnings` | Task completed，同时保存 warnings | 不是无警告成功；本 Attempt 终结 |
| `failed` | 保存失败；retryable 且预算允许时重试 | 新 retry 是新 Attempt，默认新 Session |
| `blocked` | 保留阻塞，不自动当成功重试 | 当前 Attempt 终结，需显式恢复决策 |
| `needs_user_input` | 保存问题并暂停 Run | **当前 Attempt 未结束**；回答后同编号、Session、输出目录与基线继续 |
| `request_work` | 子模块返回此分支会被拒绝 | 只有 Scientific 可以向研究控制层请求工作 |

- **状态与副作用**：Scheduler 拥有 Task/Attempt 历史并在调用前保存 running 意图；Agent/runtime 拥有 Session 正文。Agent 可操作授权工作区与环境，返回 ArtifactCandidate；Registry 才负责冻结为 ArtifactRef。
- **调用粒度**：一次 `invoke` 是一个执行区间，不保证整个 Attempt 结束。`llm_calls` 是该次返回对应的新增消费；问答续跑要增量累计，不能重复报 Session 全历史调用数。
- **恢复与重放**：用户问答续跑使用 `parent_session_id`。中断后恢复会保留失败的旧 Attempt，再按预算开始新 Attempt；没有通用“重复 invoke 自动无副作用”的承诺。失败时写过的代码也不会自动回滚，原生 Coding 会尽量产出诊断 patch。
- **身份规则**：共享 Session ID 函数包含 Run/task/attempt，并处理长 ID；Scientific 与 Controller 也共用有界 Run Session ID。每个新用户问题生成独立 ID 并持久化，同 Attempt 多次提问不会复用身份；Runtime 恢复时核对 Attempt 编号（F01/F05/D1 已修）。
- **返回验收**：Scheduler 重验外壳，并按 capability 用已有结果模型检查成功 payload；空或错类型 payload 变为不可自动重试的 contract_error。这里检查结构，原生 finalizer 仍负责真实测试/实验事实。登记失败保留 Session、已发生调用、原始诊断、warnings 及已登记的前序工件，错误另记，不伪装成成功。

**源码**：[ModulePort](../packages/orchestrator/src/resagent2_orchestrator/ports.py)、[Scheduler](../packages/orchestrator/src/resagent2_orchestrator/scheduler.py)、[Coding](../packages/agents/coding/src/resagent2_coding/agent.py)、[Experiment](../packages/agents/experiment/src/resagent2_experiment/agent.py)。

**契约 / 已有测试**：[CONTRACTS §8–§12](CONTRACTS.md#8-moduletaskrequest)；[Workflow 测试](../tests/orchestrator/test_workflows.py)、[payload 持久化](../tests/orchestrator/test_payload.py)、[Controller 问答恢复](../tests/orchestrator/test_controller.py)。

## I4 单步工具

**目的**：让所有领域 Agent 共用同一动作执行机制，具体能力可以独立复用。

```python
ToolRegistry.dispatch(name, arguments: dict, state: AgentState) -> ToolObservation
Tool.execute(state: AgentState, arguments: BaseModel) -> ToolObservation
# 每个 Tool 必须声明 name 和 input_model。
```

- **调用方 / 实现方**：AgentLoop 经 ToolRegistry / capabilities 中的共享工具或 Agent 自己的领域工具。Tool 是单步能力，ModulePort 是多步任务；两者不是同一粒度。
- **输入检查**：Loop 校验 action 外壳和权限；Registry 用工具的 `input_model` 校验 arguments 后才调用 execute。`tool_contracts` 只向模型展示顶层必填名和可选 guidance，**不是完整嵌套 schema，也不替代执行前验证**。
- **普通返回**：`ok` 是执行成败信号，`summary` 是解释，`value` 是 JSON 兼容观测，`memory_updates` 是交给 Runtime 应用的记忆更新。`value` 没有一套通用领域输出模型，由该工具与其消费者约定。
- **控制返回**：`finish_candidate` 提议完成；`question` 提议暂停问用户；`request_work` 只用于 Scientific 的工作请求。工具不自己完成 Session、创建 PendingQuestion 或安排下一项 Task。
- **状态与副作用**：工具按约定不直接修改 AgentState，返回 memory_updates；Runtime 保存观测并应用更新。工具仍可操作授权文件、进程或共享 EnvironmentBinding，“不改 AgentState”不等于纯函数。
- **错误处理**：参数校验错误、工具返回 `ok=False` 或执行时抛出的 PermissionError 等可转成反馈后继续，受预算和连续失败上限限制；PermissionPolicy 返回不允许时则立即以 permission_denied 失败，不进入重试。不可恢复异常/耗尽限制也返回模块失败。未知工具按既有拒绝策略处理，不放宽 schema 来吞错。
- **重复与恢复**：读取通常可重复；修改文件、安装依赖和启动实验不承诺幂等。Session checkpoint 保存观测，不是外部副作用的 exactly-once 事务。
- **共享位置**：通用片段/目录预算机制属于 runtime；capabilities.workspace_context 应用于三个 Agent。Coding/Experiment 使用文件+工件工作集，并投影同一 EnvironmentBinding 的实时状态；Scientific 只使用工件工作集，不传绑定、不因此获得执行能力。安全文件访问、process、Git、environment、dataset 属于 capabilities；代码验证要求和科学判断属于具体 Agent。不要为每个 Agent 再复制一套工具协议。
- **读取出口**：read_file/read_artifact 共用有界行切片，工件先核验整个文件 hash 再切片；search_text 可搜单文件或目录，是大小写不敏感的字面子串搜索，不支持正则。文件/工件正文各 6000 字符，在 workspace_reads 中分组；优先选择近期片段，按事件顺序展示。observed_at 标读取事件，modified_after_read_at 标后续成功文件修改；旧正文可保留作历史，不冒充当前版本。context_truncated 区分工作集追加截断与原始读取，短 history preview 不等于完整工具结果。复用读取逻辑不等于共享同一份局部额度，两类均计入 Agent 总输入预算。工作集不是永久记忆，省略内容可按来源/范围取回；环境已审计状态不依赖最近六条观测是否还在。
- **截止与控制信号**：LLM/权限检查之后、真正派发工具之前再检查 wall-clock 截止时间；过期动作不执行，已发生调用仍记账。这不是执行中工具的抢占取消。ToolObservation 模型强制三种控制信号至多一个，不依赖分支顺序解释冲突。
- **客户端与 trace**：客户端只需实现 next_action，可选预算/trace hooks 按能力检测调用；无 attempt 计量时按每请求一次记账。action_valid 记录响应解析/Action 调试状态，不证明工具 arguments 合法或工具执行成功；验收还需关联参数错误、observation.ok 和最终状态。

**源码**：[Tool 协议和 Registry](../packages/runtime/src/resagent2_runtime/tools.py)、[Loop](../packages/runtime/src/resagent2_runtime/loop.py)、[ToolObservation](../packages/runtime/src/resagent2_runtime/models.py)、[共享能力](../packages/capabilities/src/resagent2_capabilities/)。

**契约 / 已有测试**：[CONTRACTS §19](CONTRACTS.md#19-运行时反馈与连续失败保护)；[Runtime guards](../tests/runtime/test_guards.py)、[tool contracts](../tests/runtime/test_tool_contracts.py)、[恢复](../tests/runtime/test_resume.py)。

## I5 完成检查

**目的**：模型只能提出“我做完了”；代码依据实际记录判定可以成功、应该失败，还是需要继续。

```python
CompletionCheck.evaluate(
    state: AgentState, candidate: FinishCandidate | None,
) -> CompletionDecision
```

- **调用方 / 实现方**：AgentLoop / Coding、Experiment、Scientific 各自的 completion check，通过 AgentDefinition 注入。它不是另一个 Agent，不追加 LLM 推理。
- **输入权威**：candidate 是模型提议，不是事实；check 还持有请求与能力依赖，用 Session 里的真实工具记录、工作区基线、证据文件或当前 Run snapshot 校验。

| Decision | Runtime 行为 |
|---|---|
| `complete=True` | 校验已配置的 result_type，产出 payload/候选工件；有 warnings 则 completed_with_warnings |
| `failure` 非空 | 立即返回带 ModuleError 的 failed，不继续等模型再猜 |
| 两者都没有 | 继续循环；decision.summary 非空时注入反馈，完成候选被拒才计 completion 连败；仍受预算限制 |

`complete=True` 与 `failure` 互斥。`evaluate` 返回并不自行改变 Run/Task；Session 的结束由 Runtime 执行。

- **Coding 判据**：understand 要有确实观察的文件且不改代码；modify 要有相对 Attempt baseline 的真实改动、实际验证记录及对应代码版本。检查与产出 patch 可能访问文件/Git，不应把所有 finalizer 都称为无 IO 纯函数。
- **Experiment 判据**：成功命令、有效 baseline、本 Attempt 新增/改变的真实证据、完整 JSON 证据集派生的 metrics；即使 expected_* 留空，完全没有本次证据也不放行。已知精确要求部分缺失产生 warnings；自然语言目标由 Scientific 读证据后判断。真实非零退出/超时命令及日志可支持确定性失败出口，LLM 不能直接自证 metrics。
- **Scientific 判据**：原生 check 根据 Session 工具记录、传入的未解决任务与 required evidence 检查观点和引用，不持有完整 ResearchRun；Controller 正式完成时再独立执行基于完整 Run 的最终 gate。
- **不是同一接口**：Orchestrator 的 `ScientificCompletionValidator.validate(run, result) -> CompletionValidation` 检查整个 Run；`FinalReportRenderer.render(data)` 只确定性渲染 typed report。它们不代替子 Agent 对测试/实验事实的领域验收。
- **验证有效性**：Coding 提示与 gate 共用规则：验证非空、全部通过，覆盖当前 edit revision 和已认证环境 generation。prepare/setup 开始执行以及进程重新绑定环境都会使旧验证过期；重新 audit 不能替代重跑测试。Experiment 指标规范化后精确匹配，同名不同值的证据可恢复拒绝，不静默覆盖。Scientific 最终要求的替换边界见 I1。

**源码**：[CompletionCheck / Loop](../packages/runtime/src/resagent2_runtime/loop.py)、[CompletionDecision](../packages/runtime/src/resagent2_runtime/models.py)、[Coding check](../packages/agents/coding/src/resagent2_coding/completion.py)、[Experiment check](../packages/agents/experiment/src/resagent2_experiment/completion.py)、[Scientific check](../packages/agents/scientific/src/resagent2_scientific/completion.py)。

**契约 / 已有测试**：[CONTRACTS §10](CONTRACTS.md#10-领域-payload)、[§16.6](CONTRACTS.md#166-scientificcompletionvalidator-与-final-report)；[Coding control state](../tests/coding/test_control_state.py)、[Experiment completion](../tests/experiment/test_completion.py)、[Scientific 最终验收](../tests/orchestrator/test_scientific_completion.py)。

## I6 工件登记与读取

**目的**：将可变文件变成有来源、可核验的证据；传递指针而不是把所有内容塞进消息。

```text
执行 Agent: ArtifactCandidate → Scheduler → ArtifactRegistry.register → ArtifactRef
Scientific Tool: Candidate → 注入的 ArtifactRegistrationPort → 同一个 Registry
用户输入 / 最终报告: Controller → Registry 的专用登记入口
读取: ReadArtifactTool → RegisteredArtifactReader.read_text(artifact_id) → 内容观测
```

- **输入权威**：Candidate 只声明文件和用途，不能自行声称 hash 或生产者身份；Registry 从调用上下文绑定 run/task/attempt 或 session/orchestrator provenance。读取使用已授权 ArtifactRef，不接受模型任意指定文件路径。
- **输出**：Registry 返回冻结内容的 ArtifactRef；reader 返回内容与引用信息，工具再包装为 ToolObservation。只有登记引用不等于模型已观察内容，Scientific 的 observed IDs 从成功工具记录派生。
- **文件与状态所有权**：Registry 校验文件与来源，计算 hash；任务工件 register 使用完整单工件 staging 目录 rename 提交，import/scientific/final_report 专用入口当前采用先建目录、临时文件替换的提交方式，不能一概称为整目录事务。Controller/Scheduler 将执行工件 Ref 写入 Run；Scientific 的共享 `ScientificArtifactRegistration` 冻结后更新 Run 索引及本进程动态查找。Registry 不改变 TaskStatus 或科学观点。
- **读取规则**：按授权引用解析并核验内容完整性；动态 resolver 支持同一 turn 刚由 literature_search 登记的工件。必须在读取字节前满足当前 Run 的授权，而非等最终引用校验才拦截。
- **自产 JSON 的可读性**：register_scientific 在冻结前按 indent=2 生成多行 JSON，再按实际字节计算 hash；让长文献列表可按行取回后部。旧工件不改写，读取器不重新格式化，空范围读取不代表已经取回缺失内容。
- **成功与诊断**：成功依赖工件可传给下游；failed/blocked 的 Artifact 可以保存为诊断，但不能被包装成成功实验。summary、stderr 摘录和 typed metrics 各有用途，原始冻结工件保留证据根源。
- **失败与原子性**：非法路径、丢失文件、hash 不符应拒绝。单工件 staging 不意味着一次批量登记或 Run + Session + Artifact 是跨资源事务；可能已有前面的工件登记成功，后面的登记失败。
- **重复调用**：各入口有各自重复登记检查；最终报告已有幂等恢复路径，不能推广为所有外部副作用 exactly-once。读取可重复，授予更多工件必须经过登记和授权链。
- **模型可见性**：Scientific 看到授权目录、简报和主动读取内容，不应看到任意候选路径或另一 Run 的文件。工件正文经共享 workspace_context 从成功读取事件投影，按来源/行范围保留，共计 6000 字符、required；不再使用 read_artifact_summaries 前缀缓存。已观察 ID 不保证正文仍可见，省略的细节可按范围重读。full trace 是独立调试记录，不自动成为 Artifact 或科学证据。
- **读取隔离**：reader 显式绑定 Run，读取字节前核对 Ref.id/run_id；动态 resolve 显式接收 run_id，共享登记适配器用 (run_id, artifact_id) 索引，同内容的跨 Run 工件不会相互覆盖。CLI/E2E 各自注入 Registry/RunStore，不复制登记机制；重启后已持久化的 Ref 由下一轮授权集合提供，动态表不是第二个持久化仓库。hash 校验仍只负责内容完整性。
- **登记失败**：不清零已发生的调用、不丢 Session/原诊断；已有原错误时追加 artifact_registration_error，禁止因登记失败自动重试。此前成功登记的工件保留为可追踪的部分结果，不承诺批量原子性。

**源码**：[ArtifactRegistry / ScientificArtifactRegistration](../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)、[RegisteredArtifactReader](../packages/capabilities/src/resagent2_capabilities/artifacts.py)、[Scientific tools](../packages/agents/scientific/src/resagent2_scientific/tools.py)。

**契约 / 已有测试**：[CONTRACTS §13](CONTRACTS.md#13-artifact-契约)；[持久化与工件](../tests/orchestrator/test_persistence_and_artifacts.py)、[最终报告登记](../tests/orchestrator/test_scientific_completion.py)。

## 使用这些卡片开发

1. 修改模块内部算法：先查所属卡的输入、输出和副作用，保持调用方不需要了解内部步骤。
2. 增加共享能力：先找已有 capabilities 与 Tool；不要为复用一个文件/环境操作而新增 Agent 或新的调度接口。
3. 修改字段或状态语义：先同步 ARCHITECTURE 的模块边界，再改 CONTRACTS 与对应卡片，补接收方拒绝非法结果的测试。
4. 替换 Port：不仅测正常返回，还测错误身份、暂停、失败、重复交付、消费保留；“满足 Python 方法签名”不等于满足行为契约。
