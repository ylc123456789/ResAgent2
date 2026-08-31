# ADR-0011：Stabilization 3.0 —— 控制面、Attempt、机器语义、资源权威与契约收敛的最终边界

**状态**：accepted

**日期**：2026-08-31

**取代**：部分取代 ADR-0005 / ADR-0007 / ADR-0008 / ADR-0009 中关于 attempt/resume、`required`、per-task `rationale`、dataset/environment 上游来源的实现细节；wire schema 由 `2.0` 不兼容升为 `3.0`。

## 背景

Phase 7 主链已跑通，五个服务器真实 E2E 场景有验收记录。但全面代码审查（`docs/reviews/CODE_REVIEW_2026-08-31.md`）发现 9 项 P1 契约/数据闭环断链与 15 项 P2 重复语义、死契约和生命周期缺口。这些问题不是 LLM 质量或风格争议，而是同一状态/数据在多层各写一套、或契约字段没有完整传递链。审查把它们收敛为五个根因，`STABILIZATION_PLAN.md` 据此给出统一解法。

本 ADR 记录最终架构边界，作为后续冻结架构、不再修改总控基本语义的依据。它不是一次功能开发，而是把已存在但分散的语义重新统一。

完成后系统必须满足六句话：

1. `ResearchController` 是研究 Run 唯一的外部入口和状态负责人；
2. `WorkflowScheduler` 只执行任务图，不决定研究是否完成；
3. pause/resume 是同一次 Attempt 的继续，只有失败重试才创建新 Attempt；
4. Runtime 对成功、失败、反馈、预算只有一套机器语义；
5. dataset、environment、workspace、input artifact 各有唯一权威来源；
6. 每个公共字段都有生产者、校验者、持久化位置和消费者，否则删除。

## 决策

### 1. 单一控制面（根因 A：Controller 与 Scheduler 都在管理同一状态）

1. `ResearchController.create_run()` / `answer_question()` / `resume_run()` 是唯一外部入口；
2. 删除 public `WorkflowScheduler.create_run()`；测试通过 Controller 或一个明确的内部 fixture 构造已有 Run；
3. `WorkflowScheduler` 不再把无 WorkRequest 的图直接判为 ResearchRun completed（删除 required-task 完成回退）；
4. `ResearchController.answer_question` 按 `PendingQuestion.task_id` 分流：`None` 为 Scientific 问题，answer 只回 Scientific；非空则调用 Scheduler 恢复该 Task；
5. 提取一个私有 `validate_answer(question, answer)`，两层不再各写一套；
6. 提取 `_transition_work_request()`，唯一负责 WorkRequest 状态迁移合法性与 `updated_at` 更新；
7. 保存 ResearchRun 前，模型校验 active WorkRequest 最多一个。

不引入通用状态机框架。状态仍使用现有 enum + 少量 transition helper。

### 2. Attempt 统一定义（根因 B：pause/resume 与 Attempt 没有统一定义）

最终裁定：

- **Attempt 表示一次「从开始到成功/失败/blocked」的任务尝试**；
- `NEEDS_USER_INPUT` 是 Attempt 的暂停状态，不是终态；
- answer 后继续**同一个 Attempt number、Session、output_dir、workspace baseline**；
- 只有 `FAILED/BLOCKED → retry` 才创建下一 Attempt；
- `max_attempts_per_task` 只限制失败/blocked 后的新尝试，不限制同一 Attempt 内的用户往返。

实现方式：

1. Scheduler 将 `_start_attempt()` 与 `_resume_attempt()` 分开；
2. needs-user-input 时保留未完成 Attempt，不填写最终 `finished_at`；
3. resume 时把同一个 Attempt 置回 `RUNNING`；
4. Coding 首次进入 Attempt 时把 `GitBaseline` 的 tree hash 序列化到 `AgentState.memory`；
5. resume 从 Session 恢复 baseline，禁止重新 snapshot；
6. Experiment 已把 workspace baseline 放入 memory，按同一规则保留；
7. retry 前统一检查 attempt budget。

### 3. Runtime 唯一机器语义（根因 C：机器语义没有被所有 Tool 遵守）

全局不变量：

- action/Tool 被接受并产生有效结果：`ok=True`；
- 命令失败、参数拒绝、前置条件未满足、可恢复错误：`ok=False`。

实现方式：

1. 为所有 ToolObservation 做参数化契约测试（可恢复拒绝必须 `ok=False`）；
2. `run_verification` 必须执行全部命令，或者为未执行命令生成失败记录；
3. verification passed 必须同时满足：至少一条命令、result 数量等于 command 数量、全部 exit 0 且未 timeout、workspace digest 未变化；
4. `AgentState` 增加持久化 `llm_calls_used`，每次 provider call 发出后立即递增，schema 错误也计数；shared client 的下一次 transport retry 次数受调用方剩余预算约束，不能用一次逻辑调用越过 Run 硬上限；
5. `ModuleResult` 返回本 Attempt 的真实 `llm_calls_used`，Scientific 不再用 step 差推导；
6. Runtime 唯一注入 `runtime_feedback` 和 `recent_observations`；Agent context 删除 required `last_observation` 和整份 memory dump；
7. Agent 只注入经过白名单和限长的领域 memory；文件正文等大观察通过 runtime 共享纯函数按一个 section 总预算保留，不能在多个 Agent 复制提取逻辑或按每文件上限无限放大。

### 4. 资源单一权威（根因 D：资源和约束没有唯一权威来源）

#### Workspace 与 Environment

环境实际按 `(run_id, workspace_id)` 共享，因此 `EnvironmentSpec` 属于 `WorkspaceSpec`，不属于 LLM 生成的 Task：

```text
WorkspaceSpec
  workspace_id
  source_kind
  location
  environment: EnvironmentSpec | None
```

- 上游明确指定 Python 版本时，它是 workspace 的硬约束；
- 没指定时，Coding/Experiment 从项目文件推断；
- Scheduler 从 `WorkspaceRecord` 确定性写入 `ModuleTaskRequest`；
- Compiler 不生成 Python 版本，也不接触物理路径。

#### Dataset

- `ResearchRequest.dataset_refs` 是唯一数据集注册表；
- 删除 `ExperimentRunInput.dataset_refs`；
- Scheduler 把 Run 已授权的 dataset refs 传给 Experiment；
- 若以后真的需要子集，再增加 `dataset_ids`，现在不提前设计。

#### 初始 Artifact

`ResearchRequest.input_artifacts` 由 `list[ArtifactRef]` 改为最小输入类型 `list[ArtifactImport]`：

```text
ArtifactImport
  uri
  kind
  media_type
  summary
  expected_sha256: optional
```

Controller 创建 Run 时：验证本地 URI → 冻结复制 → 校验 hash → 生成本 Run 的 `orchestrator/import` ArtifactRef → 写入 `run.artifacts`。初始 imported artifacts 对该 Run 的 Scientific 与任务都授权；任务还会继承依赖 Task 的 Artifact。不设计复杂 ACL。

#### Workspace baseline

- capabilities 提供一个内部 `WorkspaceSnapshot` 表达 Attempt 起点；
- Git workspace 使用 `GitBaseline`；
- 只有非 Git workspace 才使用有界 file-hash fallback；
- `GitDiffTool`、Coding finalizer、failed patch、Experiment evidence ownership 都消费同一个 snapshot；
- 删除 HEAD-relative legacy diff API，或改成明确的 repository diagnostic，不能与 Attempt diff 混用。

### 5. 契约收敛（根因 E：Phase 7 原子切换后仍保留假扩展点）

1. `WorkflowPatch` 只保留 `add_tasks`；删除 `PendingTaskUpdate` / `supersede_task_ids` / `pending_task_updates`；
2. 删除 `TaskProposal` / `WorkflowTask` 的 `required`；
3. 删除 per-task `rationale`，只保留 proposal/patch 级 compilation rationale；
4. 删除 Scheduler 的无 WorkRequest completion fallback；
5. `AgentLoop` unknown-tool 只保留一种可恢复策略；
6. Git HEAD-relative legacy 方法在调用者迁移后删除；
7. store 与 Artifact 内部重复只提取私有 helper，不创建新 package；
8. `ARCHITECTURE` / `CONTRACTS` 只写当前 3.0，Phase 1—7 历史留在 `DEVELOPMENT_PLAN` / ADR / history。

### 6. 证据可信度的最小根治

1. Artifact 注册事务化：校验 candidate/grant/source/path → 计算目标 ID → 写同目录临时文件 → 校验 hash → 原子 rename → 生成 ArtifactRef；任何失败只留下可识别临时文件，不留正式 artifact directory；
2. Experiment metrics 不再由 LLM 自证：finalizer 从声明的 JSON evidence 读取顶层数值字段，按 `expected_metrics` 的规范化 key 生成 `ExperimentResult.metrics`；非 JSON 证据可冻结引用，但 typed metrics 为空并给明确 warning；`parameters` 无 production 消费者则删除；
3. Scientific evidence 校验保留三层（Tool 可恢复反馈 / Session finalizer / Orchestrator gate），三层共用一个纯函数模块，不再各自维护五份近似集合运算。

### 7. 预算与超时：Run 总账

1. `ResearchRun` 持久化 `llm_calls_used`（Scientific + Compiler + Coding + Experiment 总调用数）与 `started_at/created_at`（算 wall-clock remaining）；Task/Attempt 保留局部统计；
2. 调用计数：AgentLoop 的 `ModuleResult` 带真实 calls；`ScientificTurnResult` 直接转发；Compiler 返回内部 `CompilationResult(output, llm_calls)`；Scheduler 累计到 Run；malformed JSON/schema action 也计数；provider transport retry 每次实际 HTTP 调用都计数；
3. 超时：Controller 每轮先算 `deadline - now`，给 Compiler/Task/Tool 的 timeout 不超过 remaining；`remaining <= 0` 确定性失败；blocking subprocess 都必须有 timeout；不实现抢占式分布式取消。

### 8. wire schema 升为 3.0

本次删除 `required`、per-task `rationale`、Patch mutation 等公共字段，并调整 Attempt、environment、dataset、input artifact 的语义，属于有意的不兼容清理。因此：

- 开 `stabilization/schema-3` 分支；
- `SCHEMA_VERSION` 一次性升为 `"3.0"`；
- 不保留 deprecated alias、双路由或兼容 adapter；
- 完成全部验收后一次合并 main。

项目目前没有需要兼容的外部稳定用户，趁现在做一次干净的不兼容收敛。

## 最终架构：每层只负责一件事

```text
用户 / API
   │  自然语言目标、约束、workspace、dataset、初始 Artifact
   ▼
ResearchController         唯一拥有 ResearchRun、PendingQuestion、WorkRequest、RunBudget、最终完成
   ├──────────────► ScientificAgent        只给科学判断：finish / request_work / ask_user
   ├──────────────► WorkflowCompiler       只把语义 WorkRequest 翻译成 append-only Task 图
   └──────────────► WorkflowScheduler      只执行 Task / Attempt，并返回 WorkOutcome
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
             CodingAgent             ExperimentAgent
                 └────────────┬────────────┘
                              ▼
                          AgentLoop         会话、Tool 调用、反馈、预算、持久化
                              │
                              ▼
                         capabilities        workspace / git / process / env / dataset /
                                             artifact / repo / literature
```

任何代码如果同时跨越其中两种职责，就应当被质疑。

## 为什么不用其它方案

- **不引入通用状态机框架 / 工作流 DSL / 事件总线 / 数据库**：状态仍用现有 enum + 少量 transition helper；问题不是缺少状态表达，而是同一状态被两层各写一份；
- **不建设通用 metrics 表达式语言**：最小 JSON evidence 解析即可让 typed metric 来自证据，通用表达式求值器是过度设计；
- **不做后台环境 GC 服务**：只提供显式 `list / plan / apply` 清理操作；
- **不为测试保留 production 死分支**：`required`、per-task `rationale`、Patch mutation 的消费方只有测试 fixture，应删除而不是保留；
- **不同时支持 2.0/3.0 两套 production**：没有外部稳定用户，做一次性不兼容收敛更简单。

## 不变的原则

- LLM 输出一律视为不可信建议，状态 / identity / evidence / success 由代码裁定；
- 专业 Agent 不直接互调；基础设施不复制；
- 跨模块持久事实必须成为 contract 字段或 Artifact，不能只藏在 prompt/summary；
- 失败、警告和不确定性必须保留，不能通过自然语言包装成成功；
- 不因为真实 E2E 偶发失败就先改 prompt，先检查输入、状态、反馈、证据。

## 后果

正面：

- 研究 Run 只有一个状态负责人，问答恢复不再分叉；
- pause/resume 不再错误地消耗 Attempt 预算或丢失 Coding baseline；
- ToolObservation.ok 成为全局一致的机器语义，失败不再被静默包装成成功；
- dataset / environment / input artifact / workspace 各有唯一权威来源，无静默覆盖或丢失；
- 公共契约只剩有 production producer+consumer 的字段，更易理解与维护；
- Run 总账与 remaining timeout 让长期研究任务真正可控。

代价：

- schema 从 2.0 不兼容升到 3.0，需要一次原子切换与全仓测试；
- Scheduler 失去直接驱动 Run 的能力，测试 fixture 需要调整；
- 删除 `required`/`rationale`/Patch mutation 会改动 compiler、scheduler 与多份测试；
- 需要新增 field matrix、CODE_READING_GUIDE 等文档以维持可读性。

## 明确不做

- 不新建 Agent、不新建公共 package、不拆回多个 Git；
- 不引入状态机框架、工作流 DSL、事件总线、数据库；
- 不建设通用 metrics 表达式或自动 schema 推断；
- 不建设后台环境 GC 服务；
- 不同时支持 2.0/3.0 两套 production；
- 不做与稳定化无关的 prompt 调优或论文源扩展。

## 参考

- `docs/reviews/CODE_REVIEW_2026-08-31.md`：根因清单与证据；
- `docs/reviews/STABILIZATION_PLAN.md`：分阶段实施与验收；
- ADR-0007：WorkflowCompiler 归 ResAgent 内部；
- ADR-0008：统一工作区与 Coding 自主性；
- ADR-0009：共享环境能力（`env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`）；
- ADR-0010：语义草图 + 确定性物化 + 一次纠错重编译。
