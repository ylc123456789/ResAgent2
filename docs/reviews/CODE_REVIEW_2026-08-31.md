# ResAgent2 全面代码审查报告（2026-08-31）

## 1. 结论先行

ResAgent2 已经“跑通”，但这里的准确含义是：当前唯一 production 主链已经形成，五个服务器真实 E2E 场景有验收记录，当前 `main` 的本地全量测试也通过。它不再是一个需要推倒重写的项目。

不过，“主链跑通”不等于所有契约分支都闭环。本次审查仍发现若干真实断链，其中最重要的是：任务级 ask-user 恢复走错入口、Coding 暂停恢复时 Attempt 基线丢失、验证命令可能没有全部执行却被判成功、用户输入 Artifact 没进入 Run、EnvironmentSpec 没有上游传递路径。这些不是 LLM 质量问题，也不是代码风格争议，而是代码层面的状态或数据传递缺口。

总体判断：

- 架构方向正确，继续开发有价值，不建议重开项目。
- `contracts → runtime → capabilities → agents / orchestrator` 的依赖边界落实较好。
- 通用 AgentLoop、环境、Git、workspace、process、Artifact、dataset、literature 已经真正复用，不只是换了目录。
- 当前主要问题已从“架构混乱”收敛为一组可以分批解决的跨层语义问题。
- 修复应优先合并语义、删除不可达分支，不应再增加新的抽象层或 Agent 类型。

## 2. 审查边界与基线

### 2.1 基线

- 仓库：`/home/cyl/ResAgent2`
- 分支：`main`
- Commit：`f27f3c9`
- 与 `origin/main`：同步
- 未跟踪内容：仅 `.vscode/`
- 本次本地验证：`347 passed, 1 skipped in 4.97s`
- 服务器五场景：本次未重新消耗服务器资源运行；以仓库中已记录的 direct、code-experiment、repair、ask-resume、literature 验收结果为基线。

### 2.2 只检查以下问题

1. 代码逻辑是否断链；
2. 同一功能是否存在两套含义不同的实现；
3. 是否存在 production 不可达的死契约或死代码；
4. 是否有明显冗余；
5. 是否存在与当前需求不相称的过度设计；
6. “通用能力抽取、模块独立、代码简洁”是否真正落实。

本报告不把以下内容当作缺陷：未来可能需要的新功能、分布式调度、多租户、Web/API、完整论文平台、任意工作流 DSL、自动扩缩容、性能优化猜测。

### 2.3 严重度

- P1：已有合法输入或已有契约路径能触发错误、错误成功或无法恢复；应在继续扩功能前修。
- P2：主链可运行，但存在两套语义、长期维护风险或明显的生命周期缺口；应分批收敛。
- P3：冗余、文档漂移、诊断质量或局部简化项；不阻塞运行。

本次未发现需要停止使用仓库的 P0 问题。

## 3. P1：确定的逻辑断链

### P1-1 任务级 ask-user 无法通过 ResearchController 正确恢复

类型：逻辑断链 + 同一功能两套逻辑。

证据：

- `scheduler.py:382-396` 在子任务返回 `needs_user_input` 时，把 Task 置为 `NEEDS_USER_INPUT`，并创建带 `task_id` 的 `PendingQuestion`。
- `scheduler.py:416-436` 的 `WorkflowScheduler.answer_question()` 会记录 `answer_task_ids`，并把对应 Task 重新置为 `PENDING`。
- production 入口 `controller.py:100-112` 又实现了一套 `answer_question()`，但只清除问题并把 Run 置为 `RUNNING`，既不写 `answer_task_ids`，也不恢复 Task。
- 现有 Controller 测试覆盖的是 Scientific 自己提出的问题；任务级问题只在 Scheduler 直驱测试中覆盖。

后果：Controller 恢复后，WorkRequest 仍是 executing，但任务还停在 `NEEDS_USER_INPUT`。Scheduler 无 ready task 后会试图建立 WorkOutcome；`_build_work_outcome()` 不认识该状态，可能形成空 tasks 并触发契约校验异常。

最小根治：只保留一个答案入口。Controller 根据 `pending_question.task_id` 分流：Scientific 问题由 Controller 消费；任务问题委托 Scheduler 恢复，再由 Controller 继续控制循环。公共的 question/answer 校验提取为一个小函数，不再复制。

必须补的测试：通过 `ResearchController` 触发“Experiment/Coding ask_user → pause → answer → 同一任务恢复 → Scientific 收到 WorkOutcome”。

### P1-2 ask-user 恢复与 Attempt 预算/基线语义互相矛盾

类型：状态语义断链。

证据：

- `scheduler.py:225` 每次执行 Task 都用 `len(attempts)+1` 创建新 Attempt。
- `scheduler.py:249-255` 把上一次 paused Session 作为 `parent_session_id` 恢复，因此 Runtime 认为它是同一 Session 的继续。
- `execute_task()` 没有在开始前检查 `attempt_number <= max_attempts_per_task`；只有失败自动重试与显式 retry 的部分路径检查预算。
- Coding 在每次 `invoke()` 的 `agent.py:126-128` 都重新抓取 Git baseline。

后果：同一 Session 的 ask/resume 被 Scheduler 计为新 Attempt；当 `max_attempts_per_task=1` 时仍可能产生第 2 次调用。若 Coding 在提问前已经编辑文件，恢复时的新 baseline 会把这些编辑当成“原有状态”，最终无法归属 patch 或通过 completion。

最小根治：先在契约中做一个明确裁定。推荐把 ask/resume 定义为“同一 Attempt 的暂停与继续”，不增加 attempt number，复用原 output_dir、Session 和 baseline；失败重试才创建新 Attempt。如果暂时保留“恢复即新 Attempt”，至少必须持久化原 baseline、强制检查 attempt budget，并说明跨 Attempt 变更归属规则。

### P1-3 Coding verification 可能漏跑命令却判为成功

类型：错误成功。

证据：

- `workspace_tools.py:353-374` 用一个总 deadline 逐条执行验证命令；剩余时间小于等于零时直接 `break`。
- `passed` 只检查“已执行结果全部成功”，没有检查 `len(results) == len(commands)`。
- `CodeModifyCompletionCheck` 也没有要求 verification results 非空或数量完整。
- `CodeModifyResult` 的 `verification_results` 默认空列表，`all([])` 为 True，因此 `verification_passed=True` 可以与空结果一致。

后果：例如两条验证命令中第一条消耗了全部总时限且成功，第二条完全没执行，仍可能标记 passed。

最小根治：一次 verification 必须为每条声明命令产生结果；未执行命令要产生明确的 timed_out/skipped failure，或整次直接失败。Completion 与 contract 同时要求结果非空且数量与本次命令数一致。

### P1-4 RunVerificationTool 的机器成功标志与结果矛盾

类型：跨层语义断链。

证据：`workspace_tools.py:387-403` 返回 `ToolObservation` 时没有设置 `ok=passed`，因此验证命令失败时 `ok` 仍使用默认 True。

后果：Runtime 的连续失败保护、recent observations 和 LLM 控制反馈把失败验证视为成功 Tool；这与 `ToolObservation.ok` 的契约说明直接冲突。

最小根治：设置 `ok=passed`，并保留结构化 results。补一个非零退出与 timeout 的 Runtime 级测试，不能只断言 payload 中的 `passed`。

### P1-5 Experiment 的“环境未审计”分支也被标为成功

类型：跨层语义断链。

证据：`experiment/tools.py:120-124` 返回“run audit_env first”时没有 `ok=False`；同文件“无环境”和“非实验命令”分支已经正确设置了 `ok=False`。

后果：同一类可恢复控制错误，在 Runtime 中有两种不同计数行为。

最小根治：该分支补 `ok=False`，为所有“blocked/rejected” ToolObservation 增加参数化契约测试：只要是可恢复拒绝就必须 `ok=False`。

### P1-6 ResearchRequest.input_artifacts 在 production 链路中被丢弃

类型：数据传递断链。

证据：

- `ResearchRequest` 定义了 `input_artifacts`。
- `ResearchController.create_run()` 初始化的 `run.artifacts` 为空，没有导入这些 Artifact。
- Scientific context 的 research 区只包含 goal/hypothesis/context/constraints，不包含 request input artifacts。
- Scientific 的 authorized artifacts 只来自 `run.artifacts`。
- ArtifactRef 已允许 orchestrator `source_type=import`，但 ArtifactRegistry 没有 `register_import()` 或等价入口。

后果：调用方合法传入的初始论文、报告或证据 Artifact，不会被 Scientific 观察，也不会被后续任务继承。

最小根治：由 Controller 在创建 Run 时完成一次确定性 import：验证来源、冻结到该 Run 的 ArtifactRegistry、生成 `producer=orchestrator/source_type=import` 的新 ArtifactRef，然后写入 `run.artifacts`。不要让 Agent 直接信任调用方提供的 URI/hash。

### P1-7 EnvironmentSpec 有下游消费方，但没有上游生产路径

类型：契约断链。

证据：

- `ModuleTaskRequest.environment_spec` 已定义，Coding/Experiment 都读取 `python_version`。
- `CompilationTaskDraft`、`TaskProposal`、`WorkflowTask` 没有 `environment_spec`。
- Scheduler 构造 ModuleTaskRequest 时没有设置该字段，因此总是使用默认空 EnvironmentSpec。

后果：系统看似支持上游指定 Python 版本，实际经 ResearchController/Compiler/Scheduler 的 production 路径无法传到 Agent。

最小根治：不要再增加第二种环境对象。把 `EnvironmentSpec` 加到唯一合适的任务语义链上，并确定性透传：Compilation draft（或 WorkRequest 的可信约束）→ TaskProposal → WorkflowTask → ModuleTaskRequest。若裁定 Python 版本只能由 Agent 推断，则删除这个当前不可达的上游字段，避免假能力。

### P1-8 Artifact 注册失败可能留下目录并破坏同次重试

类型：失败恢复断链。

证据：`artifacts.py:59-60` 在验证 workspace grant、source path、allowed/denied paths 之前创建目标目录；这些校验失败发生在清理 try/except 之外。再次注册相同 task/attempt/index 时 `mkdir(exist_ok=False)` 会因为残留目录失败。

后果：第一次是正常的契约错误，第二次变成“目录已存在”，真实原因被掩盖且无法幂等恢复。

最小根治：所有来源与 grant 校验先完成，再创建目标目录；或者把“建目录到返回 ArtifactRef”的完整过程包在一个统一事务清理块中。优先前者，更简单。

### P1-9 Experiment 的 typed metrics 没有与 evidence 内容绑定

类型：证据语义断链。

证据：`ExperimentCompletionCheck` 验证成功命令、证据文件存在/变化以及 metric key 是否存在，但 `finish.metrics` 的值由 LLM 提供，代码没有从 JSON/CSV 等证据中解析或核对值。

后果：Artifact 文件中的实际 accuracy 可以是 0.5，而 typed `ExperimentResult.metrics` 可以是 0.9；两个对象都能通过 finalizer。Scientific 若只读 Artifact，结论仍可能正确，但任何消费 typed payload 的代码会得到未经验证的数字。

最小根治：不要建设通用指标 DSL。先支持最小、明确的结构化证据：当 expected artifact 是 JSON 时，由确定性代码读取一层数值字段并生成/核对 metrics；其他格式把指标标记为“model-extracted claim”，同时记录 source artifact/path，不能冒充已验证数值。

## 4. P2：重复语义、死契约与长期维护风险

### P2-1 RunBudget 不是整个 Run 的真实总预算

`RunBudget` 文档称为 Run 的 hard limits，但当前 `llm_calls_used` 只累计 Scientific turn；Compiler、Coding、Experiment 的 LLM 调用不进入这个账本。Scheduler 还会给每个 Task 一份最多 50 calls 的预算。`timeout_seconds` 也被分别作为 Scientific turn、Task 和 Tool 时限使用，没有从 Run created_at 扣减总时长。

CONTRACTS §20 已明确“暂不统计 Coding/Experiment”，所以这不是隐藏实现错误，而是字段命名与通用预算直觉不一致的已实现限制。

最小收敛有两个选项，必须二选一：

1. 真正实现 RunBudget ledger，所有 LLM seam 报告 usage，Controller 分配 remaining；或
2. 把字段改名为 `max_scientific_llm_calls`、`turn_timeout_seconds`，明确它不是全局成本保护。

若项目目标是可控科研 Agent，长期更推荐选 1，但不需要引入复杂计费框架。

### P2-2 Scientific LLM 调用数用 step 差值推导，会漏掉 malformed action

AgentLoop 在 action schema 校验前已消耗一次 LLM call，但 `state.step` 只有校验成功后才递增。ScientificAgent 用 session step 的前后差计算 llm_calls，因此 malformed action 不计入 Controller 预算。

最小根治：AgentLoop/ModuleResult 返回真实 `llm_calls_used`，或把它作为 AgentState 的持久字段；不要再从 step 推导。step 表示有效动作序号，不应同时承担计费语义。

### P2-3 Scheduler 仍保留第二套顶层完成语义

production 文档声明唯一入口是 ResearchController，但 `WorkflowScheduler.create_run()` 和 `_evaluate_run()` 仍支持“没有 WorkRequest 时，required task 全完成就直接完成 Run”的直驱模式。调用者主要是单元测试、两个 native E2E 和 README 示例。

这不是当前 production 双路由，但它让 Scheduler 同时承担“研究 Run 总控”和“纯任务图执行器”两种完成定义，也保住了下面的 `required` 死语义。

最小根治：把直驱创建移到测试 fixture/示例 helper，Scheduler production API 只接受已经存在的 ResearchRun/WorkRequest。若确实要保留通用图执行器，应把它命名为独立的低层 API，并明确它产生的不是完整 Research Run。

### P2-4 WorkflowPatch 暴露了 production 永远禁止的修改能力

`WorkflowPatch` 支持 `supersede_task_ids` 与 `pending_task_updates`，Scheduler 也实现了它们；但 LLMWorkflowCompiler 明确只生成 append-only patch，并用 `_reject_cross_request_mutations()` 禁止这两种字段。当前 production 的修复模型是“新 WorkRequest 增加新 Task、保留旧历史”。

最小根治：schema 2.x 删除 `PendingTaskUpdate`、`supersede_task_ids`、`pending_task_updates` 和 Scheduler 对应分支，只保留 add_tasks。除非能给出一个真实 production 调用者，否则不要为假设需求保留公共契约。

### P2-5 TaskProposal.rationale 生成后立即丢失

Compiler 要求 LLM 为每个 task 输出 rationale，物化进 `TaskProposal`；Scheduler 转成 `WorkflowTask` 时没有 rationale 字段，也没有任何 production 消费者。

最小根治：直接删除 per-task rationale，保留 proposal/patch 级 compilation rationale 即可。若它确有审计价值，则持久化到 WorkflowTask 并在报告/trace 中使用；不能继续“生成但丢弃”。基于简洁原则，推荐删除。

### P2-6 required 只服务 Scheduler 直驱旧语义

Compiler draft 没有 required，所有 production 编译任务默认 True；只有直接 Scheduler 测试会构造 `required=False`。ResearchController 的 Scientific 闭环不靠它完成。

最小根治：随 P2-3 删除 public `required`；若纯图执行测试需要 optional task，在测试 fixture 内表达，不进入跨模块契约。

### P2-7 DatasetRef 有两个权威来源，Scheduler 静默覆盖

`ResearchRequest.dataset_refs` 与 `ExperimentRunInput.dataset_refs` 都能声明数据集；Scheduler 发现 Run 级 refs 非空时直接覆盖 Task inputs 中的 refs。相同字段有两套来源且没有冲突规则。

最小根治：把 Run 级 dataset refs 定义为唯一资源注册表，Task 只引用 dataset_id 子集；短期至少执行显式 merge/dedup/conflict validation，禁止静默覆盖。

### P2-8 Git 变更有 HEAD 相对与 Attempt 相对两套语义

Coding completion 已正确使用 `GitBaseline` 的 Attempt 相对 diff；但 `GitDiffTool` 仍展示 HEAD 相对 diff，GitWorkspace 还保留 `require_clean/changed_paths/deleted_paths/diff/write_patch` 一组 legacy HEAD API。这样 Agent 看到的 diff 可能包含前序任务改动，而 finalizer 只归属当前 Attempt。

最小根治：让 GitDiffTool 接收同一个 baseline 并展示 `diff_since(baseline)`；随后删除只剩测试使用的 legacy HEAD API，或重命名为明确的 repository-wide diagnostics，避免混用。

### P2-9 Coding 与 Experiment 各自实现工作区基线

Coding 用 GitBaseline；Experiment 用 `snapshot_workspace()` 遍历并 hash 每个可读文件。两者都在解决 Attempt 证据归属，但算法与成本不同。Experiment 的全仓 hash 对大仓库、数据文件或大量缓存不友好。

最小根治：提取一个很小的 `WorkspaceSnapshot` 接口，Git workspace 默认复用 GitBaseline；只有非 Git workspace 才用有界文件 hash fallback。不要建设复杂 provenance 框架。

### P2-10 Runtime 与三个 Agent 重复注入相同上下文

Runtime 已自动加入 bounded `recent_observations` 和持久 `runtime_feedback`；Coding/Experiment/Scientific 又各自把 `last_observation` 设为 required，并把整个 state.memory 作为 audit_memory/observed_evidence 注入。Experiment 的 environment/repo/datasets 还会在整份 memory 中重复一次。

后果是 token 浪费、同一事实多个版本、required section 过多时触发 ContextBudgetExceeded。

最小根治：Runtime 唯一负责 feedback 与 observation history；Agent context builder 只负责领域事实。memory 按白名单选择字段并分别限长，不再 dump 全量 state.memory。

### P2-11 Scientific 证据校验重复，但层级职责没有集中说明

未读证据检查同时存在于 Scientific tools、Scientific completion、turn result adapter、ResearchController observed review 与最终 ScientificCompletionValidator。多层防御本身合理，但谓词与失败语义分散。

最小根治：保留三层而非五套独立实现：

1. Tool 层给 LLM 可恢复反馈；
2. Session finalizer 防止 Agent 错误完成；
3. Orchestrator gate 做跨 Run 的最终信任校验。

共享“authorized/observed/cited”的纯函数，避免各层自行解释。

### P2-12 Environment 有创建/复用/重建，但没有回收生命周期

EnvironmentManager 按 `(run_id, workspace_id)` 创建重量级环境，却没有 list/ownership manifest/last_used/retention/cleanup API。服务器旧测试环境曾占用约 19GB，这已经是被真实运行证明的生命周期缺口，不是假想需求。

最小根治：不做复杂缓存管理器。给每个 env marker 增加 run_id、workspace_id、created_at、last_used，提供一个显式 CLI/函数按已完成 Run 或保留天数列出并删除“确认归 ResAgent2 管理”的环境。默认不自动删成功 Run 环境，删除必须显式指定策略。

### P2-13 RepoMaterializer 不是事务式物化

git clone 或 copy 失败可能留下非空 workspace；下次重试看到非空目录就拒绝，无法自行恢复。

最小根治：managed source 一律物化到 workspace 同级临时目录，成功验证 repo 和 metadata 后原子 rename；失败只清理自己创建的临时目录。LOCAL 不参与该逻辑。

### P2-14 WorkRequest 时间戳和活跃请求不变量没有落实

WorkRequest 有 `updated_at`，但状态从 requested → compiling → executing → stable → consumed/failed 时从未更新；ResearchRun 也没有“最多一个 active WorkRequest”的模型校验，Controller 只返回列表中的第一个。

最小根治：封装一个 `_transition_work_request()` 小函数，统一校验合法迁移并更新 updated_at；ResearchRun 保存前检查 active 数量不超过 1。无需引入状态机框架。

### P2-15 SessionRef.state_uri 固定写成 memory://

即使实际使用 JsonSessionStore，AgentLoop 仍返回 `memory://sessions/...`。这会给恢复、诊断和外部消费者一个错误定位信息。

最小根治：`SessionStore` 暴露一个可选的 `uri(session_id)`，或统一使用不承诺存储介质的 `session://<id>`。推荐后者，最简单。

## 5. P3：局部冗余与文档漂移

### P3-1 JsonRunStore 与 JsonSessionStore 的原子 JSON 写入几乎重复

可以提取一个包内私有 `atomic_write_json(path, text)`；不要为此创建新的公共 storage package。由于 runtime 与 orchestrator 依赖边界不同，也可以保留少量重复，但应统一权限、fsync 和异常策略。

### P3-2 ArtifactRegistry 内有多段近似的临时写入逻辑

task content、workspace copy、scientific artifact、final report 都各自实现 tempfile + replace。提取两个私有 helper（write bytes / copy file）足够，不需要 Repository 模式。

### P3-3 AgentLoop 的 unknown-tool 分支存在不可达处理

先通过 `registry.contains()` 直接返回失败，后面的 `except ToolNotFoundError` 对同一路径基本不可达；而 typed action 通常又已经用 Literal 限制工具名。应选择一个策略并删除另一个分支。

### P3-4 final report 注册异常丢失诊断

Controller 对 final report render/register 使用 `except Exception`，只把 Run 置 failed，不保存 ModuleError 或 completion violation。失败后无法从 Run snapshot 看出原因。

最小修复：记录一个明确的 orchestrator error/violation，保留异常类型和有限消息。

### P3-5 文档仍混合历史和现状，存在明确过期描述

至少包括：

- `packages/README.md` 仍说 Scientific 与 Experiment 处于 legacy adapter 过渡期；
- `ARCHITECTURE.md` 的现状/目标表仍写 Scientific 是 legacy planning/analyze adapter；
- `ARCHITECTURE.md` 顶部仍说服务器 E2E/schema freeze 尚未完成，而 README 和 DEVELOPMENT_PLAN 已宣告五场景完成；
- `DEVELOPMENT_PLAN.md` 顶部部分状态仍只写“场景 2 completed”；
- `ResourceLayout` docstring 声称 model caches，但类中没有 model_root。

最小根治：保留 ADR 与 development plan 中的历史记录；核心 ARCHITECTURE/CONTRACTS 只描述当前事实，历史 schema 移到 `docs/history/` 或明确折叠附录。不要在一份“当前架构”表中同时写 Phase 6 旧事实与 Phase 7 目标。

### P3-6 本地 build/egg-info/__pycache__ 是忽略产物，不是仓库死代码

当前存在 3 个 build 目录、7 个 egg-info 目录和若干 pycache，但 `git ls-files` 显示它们均未跟踪，且 `.gitignore` 已覆盖。它们会干扰人工阅读，但不应作为代码缺陷误报。可用开发清理命令删除；不要去修改产品实现。

## 6. 设计理念落实情况

### 6.1 已经落实得比较好的部分

| 设计目标 | 结论 | 证据 |
|---|---|---|
| 模块依赖清晰 | 好 | contracts 无实现依赖；runtime 只依赖 contracts；capabilities 依赖 runtime/contracts；orchestrator 不 import runtime 或具体 Agent，并有 AST 边界测试 |
| Agentic loop 复用 | 好 | Scientific、Coding、Experiment 都通过同一个 AgentLoop，差异由 prompt、tools、context、completion 注入 |
| 通用能力组件化 | 好 | workspace、process、Git、repo materialization、environment、dataset、Artifact reader、file tools、literature backend 均在 capabilities |
| LLM 与确定性控制分离 | 好 | Scientific 只提出语义 WorkRequest；Compiler 用 semantic draft；代码分配 task/work_request identity；Scheduler 决定状态迁移 |
| 专业 Agent 独立 | 好 | Agent 通过 contracts 接收请求，不互相 import/调用；orchestrator 通过 ModulePort 注入 |
| Artifact provenance | 较好 | task/session/orchestrator 三态 provenance、冻结复制、hash、最终报告 gate 已落地 |
| 工作区隔离 | 较好 | RunLayout、ResourceLayout、WorkspaceBoundary、逻辑 workspace_id、Attempt GitBaseline 已形成 |

这些设计说明“通用组件”不是伪抽象。Coding 与 Experiment 已经真正复用了环境工具、仓库物化、WorkspaceBoundary、ProcessRunner、Artifact reader；以后增加新的执行型 Agent，可以直接复用这些能力，而不是复制粘贴整套实现。

### 6.2 只实现了一半的部分

| 设计目标 | 当前缺口 |
|---|---|
| 单一状态所有者 | Controller/Scheduler 各有 answer_question 与完成语义，问答恢复已出现分叉 |
| 单一 Attempt provenance | Coding 用 GitBaseline，Experiment 用全文件 hash；Coding resume 又会重新抓 baseline |
| 单一资源语义 | dataset refs、environment spec、input artifacts 的上游/下游权威来源仍不统一 |
| 预算可控 | max_tasks/attempts 较明确；LLM/timeout 仍是局部预算而非真正 Run 总账 |
| 失败可恢复 | AgentLoop 层已明显加强；Artifact 注册、Repo materialization、task ask-resume 仍有残留窗口 |
| 生命周期管理 | Run/Session 有持久化，重量级 env 缺少显式回收 |
| 简洁契约 | append-only 是 production 真相，但 Patch 仍保留 supersede/update；rationale/required 等旧表面仍在 |

### 6.3 是否过度设计

当前文件结构本身不过度。`runtime` 6 个文件、`capabilities` 一层十余个按物理能力划分的文件、每个 Agent 约 4—5 个文件，这个粒度合理；不建议再把 capabilities 拆成大量子目录、为每个 Tool 建包、或新增“manager/factory/service/repository”层。

真正的过度设计主要存在于契约表面，而不是目录：

- WorkflowPatch 的 supersede/update 能力没有 production 调用方；
- required、per-task rationale 被旧路径或中间层保留；
- 历史 schema 长期混在当前核心文档中；
- 相同安全谓词在太多层各写一遍。

因此下一步应是“删和合并”，不是继续分层。

## 7. 推荐整改顺序

### 第一批：先修会产生错误行为的闭环

1. P1-1/P1-2：统一 task/scientific ask-user 恢复与 Attempt 语义；
2. P1-3/P1-4/P1-5：统一 ToolObservation.ok 和 verification 完整性；
3. P1-6：接通 ResearchRequest.input_artifacts；
4. P1-7：裁定并接通/删除 EnvironmentSpec；
5. P1-8：Artifact 注册前置校验与失败清理；
6. P1-9：最小 JSON metrics 证据绑定。

要求：每项一个跨层负例测试，不能只补单类单元测试。

### 第二批：收敛两套逻辑和死契约

1. Scheduler 直驱路径与 ResearchController 的边界；
2. 删除 Patch supersede/update、required、无消费的 per-task rationale；
3. DatasetRef 单一权威来源；
4. GitDiffTool 与 Experiment snapshot 统一到 Attempt snapshot；
5. Runtime 统一 observation/feedback context。

### 第三批：生命周期与可诊断性

1. 环境 ownership manifest + 显式 cleanup；
2. managed repo 临时目录物化；
3. WorkRequest transition helper 与时间戳；
4. Session URI、final report error、store/artifact 私有 helper；
5. 核心文档清理。

## 8. 不建议做的事情

- 不要因为这些问题重开仓库；
- 不要重新拆成四个 Git；
- 不要为修 answer_question 引入通用工作流引擎或状态机框架；
- 不要为 metrics 建通用表达式语言；
- 不要为 store 重复建立数据库抽象层；
- 不要为了“更像多 Agent”增加 Agent；
- 不要把真实 E2E 偶发性全部归因于 prompt；先检查状态、输入、反馈和证据是否完整。

## 9. 收尾判断

ResAgent2 当前可以定义为：Phase 7 主链已跑通、架构可继续演进，但仍有 9 项 P1 级契约分支/数据闭环需要修正，尤其是任务级暂停恢复和验证语义。完成第一批后，才适合称为“核心控制面收尾”；完成第二批后，项目会明显更简单、更容易理解。

这批问题数量看起来不少，但本质集中在五个根上：

1. Controller 与 Scheduler 状态职责没有完全合并；
2. Attempt 的定义没有贯穿暂停恢复；
3. Runtime 的机器反馈语义仍有少数 Tool 未遵守；
4. 资源/约束存在声明但缺少完整传递链；
5. Phase 7 切换后旧契约表面没有彻底删除。

它们是有限、可收敛的问题，不是“永远改不完”的新一轮混乱。
