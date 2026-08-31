# ResAgent2 稳定化改造方案（Stabilization 3.0）

## 1. 这轮改造要达到什么状态

这不是一次新功能开发，也不是重新设计一个更大的框架。目标是把 Phase 7 已跑通的主链收紧成一套稳定、清晰、可以由人直接理解和维护的系统。

完成后，项目必须满足六句话：

1. `ResearchController` 是研究 Run 唯一的外部入口和状态负责人；
2. `WorkflowScheduler` 只执行任务图，不决定研究是否完成；
3. pause/resume 是同一次 Attempt 的继续，只有失败重试才创建新 Attempt；
4. Runtime 对成功、失败、反馈、预算只有一套机器语义；
5. dataset、environment、workspace、input artifact 各有唯一权威来源；
6. 每个公共字段都有生产者、校验者、持久化位置和消费者，否则删除。

这轮完成后再冻结架构。后续新增科研能力时，只允许在稳定边界上增加 capability、Tool 或 Agent 领域逻辑，不再修改总控基本语义。

## 2. 为什么叫 Stabilization 3.0

当前 schema 2.0 已完成 Phase 7，但本轮需要删除 `required`、per-task `rationale`、Patch mutation 等公共字段，并调整 Attempt、environment、dataset 和 input artifact 的语义。这些是有意的不兼容清理，不能伪装成小修补。

因此建议：

- 开一个 `stabilization/schema-3` 分支；
- 用一个 ADR 明确最终边界；
- 一次性把 wire schema 升为 3.0；
- 不保留 deprecated alias、双路由或兼容 adapter；
- 完成全部验收后一次合并 main。

项目目前没有需要兼容的外部稳定用户，趁现在做一次干净的不兼容收敛，比长期维护“新旧都能走”更简单。

## 3. 最终架构：每层只负责一件事

```text
用户 / API
   │  自然语言目标、约束、workspace、dataset、初始 Artifact
   ▼
ResearchController
   │  唯一拥有 ResearchRun、PendingQuestion、WorkRequest、RunBudget、最终完成
   ├──────────────► ScientificAgent
   │                 只给科学判断：finish / request_work / ask_user
   │
   ├──────────────► WorkflowCompiler
   │                 只把语义 WorkRequest 翻译成 append-only Task 图
   │
   └──────────────► WorkflowScheduler
                     只执行 Task / Attempt，并返回 WorkOutcome
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
             CodingAgent             ExperimentAgent
                 └────────────┬────────────┘
                              ▼
                          AgentLoop
                 会话、Tool 调用、反馈、预算、持久化
                              │
                              ▼
                         capabilities
              workspace / git / process / env / dataset /
                    artifact / repo / literature
```

### 3.1 contracts：系统词典

只描述跨模块传递的数据及其准确含义。不负责状态迁移，不包含 test-only 扩展点，也不保留“以后也许用”的字段。

### 3.2 ResearchController：研究总控

负责：

- 创建、恢复、完成 ResearchRun；
- 调用 ScientificAgent；
- 创建和迁移 WorkRequest；
- 接收唯一的用户 answer 入口；
- 维护 Run 总预算；
- 调用最终科学 gate 和 final report。

不负责：执行命令、修改代码、解释实验结果文件。

### 3.3 WorkflowScheduler：纯任务图执行器

负责：

- 接受 Controller 已批准的 append-only graph；
- 计算 ready Task；
- 创建/暂停/恢复/重试 Attempt；
- 调用 ModulePort；
- 登记任务 Artifact；
- 在图稳定后产生 WorkOutcome。

不负责：创建 ResearchRun、决定最终 Run completed、直接面向用户。

### 3.4 AgentLoop：共享执行内核

负责：

- LLM action → schema validation → permission → Tool → observation；
- feedback、recent history、failure streak；
- Session 持久化与恢复；
- 精确的 LLM-call/step 计数；
- 通用完成/失败出口。

不负责：Git baseline、实验指标解释、科学证据真假。

### 3.5 capabilities：可复用物理能力

负责真实世界的可审计动作：文件、Git、进程、环境、数据集路径、仓库物化、Artifact 读取、文献检索。

它不决定“下一步科学上该做什么”，也不决定 Run/Task 状态。

### 3.6 Agents：领域策略

Agent 只由以下五项组成：prompt、可用 Tools、领域 context、permission、deterministic finalizer。Agent 之间不互相调用，基础设施不复制。

## 4. 五个根因与统一解法

### 根因 A：Controller 和 Scheduler 都在管理同一状态

涵盖问题：任务级 ask-user 断链、两套 answer_question、Scheduler 直驱 Run、required 旧完成语义、WorkRequest 时间戳和 active 请求不变量。

统一解法：

1. `ResearchController.create_run()`、`answer_question()`、`resume_run()` 成为唯一外部入口；
2. 删除 public `WorkflowScheduler.create_run()`；测试通过 Controller 或一个明确的内部 fixture 构造已有 Run；
3. Scheduler 不再把无 WorkRequest 的图直接判为 ResearchRun completed；
4. Controller 根据 `PendingQuestion.task_id` 分流：
   - `None`：Scientific 问题，answer 只回 Scientific；
   - 非空：调用 Scheduler 恢复该 Task；
5. 提取一个私有 `validate_answer(question, answer)`，两层不再各写一套；
6. 提取 `_transition_work_request()`，唯一负责状态合法性与 `updated_at`；
7. 保存 ResearchRun 前验证 active WorkRequest 最多一个。

不引入通用状态机框架。状态仍使用现有 enum + 少量 transition helper。

### 根因 B：pause/resume 与 Attempt 没有统一定义

涵盖问题：ask/resume 消耗 Attempt、越过 max_attempts、Coding baseline 丢失、output_dir 分叉、同一 Session 却创建新 Attempt。

最终裁定：

- Attempt 表示一次“从开始到成功/失败/blocked”的任务尝试；
- `NEEDS_USER_INPUT` 是 Attempt 的暂停状态，不是终态；
- answer 后继续同一个 Attempt number、Session、output_dir、workspace baseline；
- 只有 `FAILED/BLOCKED → retry` 才创建下一 Attempt；
- `max_attempts_per_task` 只限制失败/blocked 后的新尝试，不限制同一 Attempt 内的用户往返。

实现方式：

1. Scheduler 将 `_start_attempt()` 与 `_resume_attempt()` 分开；
2. needs-user-input 时保留未完成 Attempt，不填写最终 finished_at；
3. resume 时把同一个 Attempt 置回 RUNNING；
4. Coding 首次进入 Attempt 时把 `GitBaseline` 的 tree/untracked hash 序列化到 AgentState.memory；
5. resume 从 Session 恢复 baseline，禁止重新 snapshot；
6. Experiment 已把 workspace baseline 放入 memory，按同一规则保留；
7. retry 前统一检查 attempt budget。

### 根因 C：Runtime 的机器语义没有被所有 Tool 遵守

涵盖问题：verification 失败仍 `ok=True`、环境未审计仍 `ok=True`、部分命令漏跑却 passed、Scientific 调用数从 step 猜测、Agent context 重复。

统一解法：

1. 写入一条全局不变量：
   - action/Tool 被接受并产生有效结果：`ok=True`；
   - 命令失败、参数拒绝、前置条件未满足、可恢复错误：`ok=False`；
2. 为所有 ToolObservation 做参数化契约测试；
3. `run_verification` 必须执行全部命令，或者为未执行命令生成失败记录；
4. verification passed 必须同时满足：
   - 至少一条命令；
   - result 数量等于 command 数量；
   - 全部 exit 0 且未 timeout；
   - workspace digest 未变化；
5. AgentState 增加持久化 `llm_calls_used`，每次 provider call 发出后立即递增，schema 错误也计数；
6. ModuleResult 返回本 Attempt 的真实 `llm_calls_used`，Scientific 不再用 step 差；
7. Runtime 唯一注入 runtime_feedback 和 recent_observations；Agent context 删除 required last_observation 和整份 memory dump；
8. Agent 只注入经过白名单和限长的领域 memory。

### 根因 D：资源和约束没有唯一权威来源

涵盖问题：ResearchRequest.input_artifacts 丢失、EnvironmentSpec 不可达、DatasetRef 两个来源、Git diff 两种含义、Experiment/Coding 两套 snapshot。

统一解法：

#### Workspace 与 Environment

环境实际按 `(run_id, workspace_id)` 共享，因此 `EnvironmentSpec` 应属于 `WorkspaceSpec`，不属于 LLM 生成的 Task：

```text
WorkspaceSpec
  workspace_id
  source_kind
  location
  environment: EnvironmentSpec | None
```

- 上游明确指定 Python 版本时，它是 workspace 的硬约束；
- 没指定时，Coding/Experiment 从项目文件推断；
- Scheduler 从 WorkspaceRecord 确定性写入 ModuleTaskRequest；
- Compiler 不生成 Python 版本，也不接触物理路径。

#### Dataset

- `ResearchRequest.dataset_refs` 是唯一数据集注册表；
- 删除 `ExperimentRunInput.dataset_refs`；
- Scheduler 把 Run 已授权的 dataset refs 传给 Experiment；
- 若以后真的需要子集，再增加 `dataset_ids`，现在不提前设计。

#### 初始 Artifact

当前 `ResearchRequest.input_artifacts: list[ArtifactRef]` 会让调用方伪装成已注册 Artifact，应改成最小输入类型 `ArtifactImport`：

```text
ArtifactImport
  uri
  kind
  media_type
  summary
  expected_sha256: optional
```

Controller 创建 Run 时：验证本地 URI → 冻结复制 → 校验 hash → 生成本 Run 的 orchestrator/import ArtifactRef → 写入 run.artifacts。

初始 imported artifacts 对该 Run 的 Scientific 与任务都授权；任务还会继承依赖 Task 的 Artifact。先采用这个简单规则，不设计复杂 ACL。

#### Workspace baseline

- capabilities 提供一个内部 `WorkspaceSnapshot` 表达 Attempt 起点；
- Git workspace 使用 GitBaseline；
- 只有非 Git workspace 才使用有界 file-hash fallback；
- GitDiffTool、Coding finalizer、failed patch、Experiment evidence ownership 都消费同一个 snapshot；
- 删除 HEAD-relative legacy diff API，或改成明确的 repository diagnostic，不能与 Attempt diff 混用。

### 根因 E：Phase 7 原子切换后仍保留假扩展点

涵盖问题：WorkflowPatch mutation、required、per-task rationale、unknown-tool 双分支、重复 JSON/Artifact helpers、历史文档混入当前事实。

统一解法：

1. WorkflowPatch 只保留 `add_tasks`；删除 `PendingTaskUpdate/supersede_task_ids/pending_task_updates`；
2. 删除 TaskProposal/WorkflowTask 的 `required`；
3. 删除 per-task rationale，只保留 proposal/patch 级 compilation rationale；
4. 删除 Scheduler 的无 WorkRequest completion fallback；
5. AgentLoop unknown-tool 只保留一种可恢复策略；
6. Git HEAD-relative legacy 方法在调用者迁移后删除；
7. store 与 Artifact 内部重复只提取私有 helper，不创建新 package；
8. ARCHITECTURE/CONTRACTS 只写当前 3.0，Phase 1—7 历史留在 DEVELOPMENT_PLAN/ADR/history。

## 5. 证据可信度的最小根治

### 5.1 Artifact 注册必须事务化

顺序固定：

1. 校验 candidate/grant/source/path；
2. 计算目标 ID；
3. 写同目录临时文件；
4. 校验 hash；
5. 原子 rename；
6. 生成 ArtifactRef。

任何失败都只能留下可识别的临时文件，不能留下正式 artifact directory。scientific/final/import/task registration 复用两个私有原子 helper。

### 5.2 Experiment metrics 不再由 LLM 自证

不建立通用指标语言，采用最小规则：

- Experiment Agent 只报告 summary、evidence_files、residual_risks；
- finalizer 对声明的 JSON evidence 读取顶层数值字段；
- 根据 expected_metrics 的规范化 key 生成 `ExperimentResult.metrics`；
- LLM 不能直接提供最终 typed metric value；
- 非 JSON 证据可以被冻结和引用，但没有确定性解析器时，typed metrics 为空并给明确 warning；
- Scientific 必须 read Artifact 后才能引用内容。

`parameters` 若没有 production 消费者一并删除；不要保留未经校验却看起来可信的 typed 字段。

### 5.3 Scientific evidence 校验保留三层

1. Tool：未读引用立即给可恢复反馈；
2. Scientific finalizer：Session 不能带未读证据完成；
3. Orchestrator gate：最终检查 Artifact 属于本 Run、已 observed、失败任务已确认。

三层共用一个纯函数模块，不再各自维护五份类似集合运算。

## 6. 预算与超时：实现一个真正的 Run 总账

为保证系统可控，本轮建议把 `RunBudget` 做实，而不是改名回避。

### 6.1 唯一账本

ResearchRun 持久化：

- `llm_calls_used`：Scientific + Compiler + Coding + Experiment 的总调用数；
- `started_at/created_at`：计算 Run 剩余 wall-clock；
- Task/Attempt 各自保留局部 step/call 统计用于诊断。

### 6.2 调用计数

- AgentLoop 的 ModuleResult 带真实 calls；
- ScientificTurnResult 直接转发 AgentLoop calls；
- Compiler 返回内部 `CompilationResult(output, llm_calls)`；
- Scheduler 把 ModuleResult calls 累计到 Run；
- malformed JSON/schema action 也计数；
- provider transport retry 每次实际 HTTP 调用都计数。

只新增一个简单的 usage integer，不建设价格/Token 计费系统。

### 6.3 超时

- Controller 每轮先计算 `deadline - now`；
- 给 Compiler/Task/Tool 的 timeout 不得超过 remaining；
- remaining <= 0 时确定性失败；
- conda create、clone、process 等 blocking subprocess 都必须有 timeout；
- 不实现抢占式分布式取消。

## 7. 环境和仓库的生命周期

### 7.1 环境清理

现有 marker 扩展为：

```json
{
  "env_id": "...",
  "run_id": "...",
  "workspace_id": "...",
  "python_version": "...",
  "created_at": "...",
  "last_used_at": "..."
}
```

提供三个简单操作：

- `list_managed_environments()`；
- `plan_environment_cleanup(completed_run_ids, older_than)`，只返回计划；
- `apply_environment_cleanup(plan)`，再次验证 prefix 在 env_root 且 marker 匹配后删除。

默认不自动删除；服务器验收脚本结束后可以显式调用 dry-run + apply。不要做后台 GC 服务。

### 7.2 仓库物化

GIT/COPY/GENERATED 统一使用同级 staging directory：成功验证后 rename 到正式 workspace；失败只清理 staging。LOCAL 永远原地绑定，不复制、不删除。

## 8. 实施阶段

### S0：先写裁定，不动行为

产物：ADR-0011、3.0 CONTRACTS delta、ARCHITECTURE 最终图、字段生产/消费矩阵。

退出条件：每个即将新增/删除/移动的字段都有明确语义；没有“开发时再决定”。

### S1：统一控制面和 Attempt

范围：根因 A + B。

包括：唯一 answer 入口、同 Attempt resume、持久 baseline、WorkRequest transition、删除 Scheduler 直驱完成语义。

退出条件：task/scientific ask-resume、跨进程恢复、max_attempts=1、编辑后提问全部通过。

### S2：Runtime 成功/失败/预算语义

范围：根因 C + Run usage ledger。

包括：ToolObservation invariants、verification completeness、llm call 精确统计、Run remaining timeout、context 去重。

退出条件：任何未执行/失败命令不能 completed；invalid action 计入预算；三 Agent 使用同一反馈/history 规则。

### S3：资源契约单一化

范围：根因 D。

包括：WorkspaceSpec.environment、ResearchRequest dataset 唯一来源、ArtifactImport、统一 WorkspaceSnapshot/GitDiff。

退出条件：每类资源只有一个上游权威来源；无默认覆盖或丢字段。

### S4：证据与失败原子性

范围：Artifact transaction、JSON metrics、Scientific 三层校验、managed repo staging。

退出条件：不存在错误成功；失败重试不被残留目录污染；typed metrics 来自证据。

### S5：删除旧表面

范围：Patch mutation、required、per-task rationale、legacy Git API、不可达 unknown-tool、重复小 helper、过期 README。

退出条件：所有 public contract 字段都有 production producer+consumer；全文搜索无旧符号。

### S6：生命周期与最终验收

范围：环境 manifest/cleanup、完整文档、代码阅读指南、服务器 clean-workdir 验收。

退出条件：见第 10 节。

## 9. 每个阶段的开发纪律

1. 先更新 ADR/CONTRACTS 的目标 delta，再写代码；
2. 代码、contract tests、跨层负例、文档必须同一提交；
3. 一个阶段只解决该阶段问题，不顺手加新 capability；
4. 不保留新旧 production 双路径；
5. test fixture 不得迫使 production 保留死字段；
6. 新公共类型必须能指出至少一个 production producer 和 consumer；
7. 每个状态字段必须回答：谁写、谁读、何时更新、崩溃后如何恢复；
8. 所有 LLM 输出都视为不可信建议，状态/identity/evidence/success 由代码裁定；
9. 不因为真实 E2E 偶发失败就先改 prompt，先检查输入、状态、反馈、证据；
10. 阶段结束必须 `git diff --check`、全量测试、文档事实核对。

## 10. 最终验收标准

### 10.1 架构验收

- ResearchController 是唯一 ResearchRun 创建/回答/完成入口；
- Scheduler 没有独立 ResearchRun completion 语义；
- pause/resume 不增加 Attempt；
- WorkflowPatch 只有 append-only add_tasks；
- contracts/runtime/capabilities/orchestrator/agents 边界测试全过；
- public API 表中不存在 test-only 符号。

### 10.2 正确性验收

至少覆盖：

1. Scientific ask → 跨进程 resume；
2. Experiment ask → Controller answer → 同 Attempt resume；
3. Coding 编辑后 ask → resume → 正确 patch；
4. max_attempts=1 pause/resume；
5. 多条 verification 部分未执行必须失败；
6. 失败 Tool 全部 `ok=False`；
7. malformed LLM action 消耗预算；
8. Run 总 LLM 预算跨 Scientific/Compiler/Coding/Experiment 生效；
9. Run wall-clock remaining 生效；
10. input artifact 导入、冻结、Scientific/Task 可读；
11. workspace Python hard constraint 到达两个执行 Agent；
12. 多 dataset 无静默覆盖；
13. Artifact 注册失败后同 ID 可安全重试；
14. JSON metric 与 typed metric 一致；
15. 两个 active WorkRequest 被模型校验拒绝；
16. repo clone/copy 中断后可安全重试；
17. env cleanup dry-run 不会选择无 ResAgent2 marker 或 env_root 外目录。

### 10.3 真实服务器验收

在最终 HEAD、全新 workdir 上依次跑五场景：

- direct；
- code-experiment；
- repair；
- ask-start → 退出进程 → ask-resume；
- literature。

状态敏感的 repair 与 ask-resume 各额外重复一次。保留最终成功 Run snapshot、Session、Artifact、LLM full trace（权限 700/600）和环境 manifest；删除失败中间产物与无价值旧环境。

### 10.4 文档验收

- ARCHITECTURE 只讲当前 3.0；
- CONTRACTS 每个字段与 models.py 一致；
- DEVELOPMENT_PLAN 记录历史与迁移，不充当当前接口手册；
- README 状态一致；
- 新增 `docs/CODE_READING_GUIDE.md`；
- 不出现“current/target/legacy”互相冲突的表格。

## 11. 改造完成后的代码阅读顺序

为了让项目真正可由你参与开发，最终补一份阅读指南，并推荐按以下顺序读：

1. `docs/ARCHITECTURE.md`：只理解五层职责和一条主链；
2. contracts 中的 ResearchRequest、WorkRequest、WorkflowTask、ModuleTaskRequest、ModuleResult、ArtifactRef；
3. `controller.py`：看研究闭环；
4. `compiler.py`：看语义请求如何变成图；
5. `scheduler.py`：看 Task/Attempt 如何执行；
6. `runtime/loop.py`：看所有 Agent 共享的循环；
7. Coding Agent：看一个 Agent 如何由 prompt/tools/context/finalizer 组成；
8. Experiment/Scientific Agent：只看领域差异；
9. capabilities：按需要读 workspace/git/process/environment；
10. `e2e/real_e2e.py`：看完整组合根。

阅读时不需要先理解所有 Pydantic 模型；先沿一条数据流读，再回头看 validator。

## 12. 明确不做

- 不新建 Agent；
- 不新建公共 package；
- 不拆回多个 Git；
- 不引入状态机框架、工作流 DSL、事件总线、数据库；
- 不建设通用 metrics 表达式或自动 schema 推断；
- 不建设后台环境 GC 服务；
- 不同时支持 2.0/3.0 两套 production；
- 不为测试保留 production 死分支；
- 不做与稳定化无关的 prompt 调优或论文源扩展。

## 13. 最终判断

这套方案不是把现有问题逐个贴胶布，而是把它们压缩成五个明确的架构裁定。实施量不算小，但边界有限：主要是重新统一已经存在的对象，不是引入新系统。

完成后，ResAgent2 的核心理解模型应当非常简单：Scientific 负责判断，Controller 负责研究控制，Compiler 负责翻译任务，Scheduler 负责执行图，AgentLoop 负责 Agent 会话，capabilities 负责真实动作。任何代码如果同时跨越其中两种职责，就应当被质疑。
