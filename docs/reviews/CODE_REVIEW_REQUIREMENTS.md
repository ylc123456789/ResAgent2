# ResAgent2 独立代码复核任务书

## 1. 任务目标

请对 ResAgent2 当前 `main` 做一次独立、只读、证据驱动的代码复核。目标不是提出更多功能，而是验证当前实现是否存在：

1. 跨模块逻辑断链；
2. 同一功能两套不一致逻辑；
3. production 不可达的死代码/死契约；
4. 明显冗余；
5. 与当前规模不相称的过度设计；
6. “通用能力提取与复用”是否真的降低了复杂度和不一致性。

另有初审报告 `docs/reviews/CODE_REVIEW_2026-08-31.md`。请先独立阅读代码并形成判断，再与初审报告交叉核对；不要把初审结论当作必须同意的答案。

## 2. 严格范围

### 2.1 必审代码

- `packages/contracts/src/resagent2_contracts/`
- `packages/runtime/src/resagent2_runtime/`
- `packages/capabilities/src/resagent2_capabilities/`
- `packages/orchestrator/src/resagent2_orchestrator/`
- `packages/agents/scientific/src/resagent2_scientific/`
- `packages/agents/coding/src/resagent2_coding/`
- `packages/agents/experiment/src/resagent2_experiment/`
- `e2e/mock_e2e.py`
- `e2e/real_e2e.py`
- 所有 `tests/`

### 2.2 必审权威文档

按以下顺序处理冲突：

1. 当前 contracts Python 模型与验证器；
2. `docs/ARCHITECTURE.md` 的当前架构裁定；
3. `docs/CONTRACTS.md` 的当前 schema 2.0 语义；
4. ADR-0007、0008、0009、0010；
5. `docs/DEVELOPMENT_PLAN.md`；
6. README 与历史说明。

若文档本身互相矛盾，请单列“文档漂移”，不能任选一段然后把代码判错。

### 2.3 明确不在范围

除非现有代码已经声明并半实现，否则不要建议：

- 分布式 Scheduler、消息队列、数据库、Web/API；
- 多租户、RBAC、Kubernetes、自动扩缩容；
- 新 Agent、新人格、多 Agent 辩论；
- 全文 PDF/RAG/向量数据库；
- 通用工作流 DSL；
- 任意指标表达式语言；
- 论文平台替换、更多 literature backend；
- 单纯为了“未来可能”增加 interface/factory/manager/repository。

## 3. 审查原则

### 3.1 每条问题必须有完整证据链

一条有效 finding 必须包含：

1. 触发入口；
2. 数据或状态经过的路径；
3. 断点的准确文件与行号；
4. 实际后果；
5. 为什么现有测试没有覆盖或为什么测试仍能全绿；
6. 最小根治方案；
7. 应补的测试。

只写“可能有问题”“建议重构”“这里复杂”不算 finding。

### 3.2 区分四种结论

- Confirmed bug：合法路径可触发错误、错误成功、错误失败或无法恢复；
- Design debt：当前有意限制或两套语义，主链仍能运行；
- Simplification：不可达/无消费者/冗余，但不影响行为；
- Not an issue：初审误报、已有 validator/gate/test 覆盖，或只是个人风格偏好。

### 3.3 修复建议必须简洁通用

优先顺序：

1. 删除无调用者表面；
2. 统一唯一状态所有者/唯一权威来源；
3. 提取小型纯函数或私有 helper；
4. 补 deterministic validator；
5. 最后才考虑新增公共抽象。

不得用“再加一层 adapter/manager/service”作为默认答案。

## 4. 必须逐条追踪的跨模块链路

### 4.1 自然语言研究主链

`ResearchRequest → ResearchController → ScientificTurnRequest → ScientificAgent → WorkRequestDraft → WorkRequest → WorkflowCompiler → WorkflowProposal/Patch → WorkflowScheduler → ModuleTaskRequest → Agent → ModuleResult/ArtifactCandidate → WorkOutcome → ScientificAgent → ScientificOpinion → completion gate → final report`

对每个字段检查：谁生产、谁校验、谁持久化、谁消费、失败时谁恢复。

### 4.2 ask-user 两条路径

分别追踪：

1. Scientific ask-user → Controller pause → answer → Scientific resume；
2. Coding/Experiment ask-user → Scheduler pause → Controller answer → Task/Session resume → WorkOutcome → Scientific。

必须测试 `max_attempts_per_task=1`、跨进程恢复、提问前已修改 workspace 三种边界。

### 4.3 Attempt 与 workspace provenance

追踪：

- 前序 Task 留下未提交改动；
- 当前 Attempt baseline；
- ask/resume；
- retry；
- GitDiffTool 展示；
- completion changed_files；
- failed diagnostic patch；
- Artifact 注册。

检查“Agent 看到的变更”和“finalizer 归属的变更”是否是同一语义。

### 4.4 环境链路

`EnvironmentSpec/项目文件 → prepare_environment → EnvironmentBinding → run_setup → audit_env → verification/run_command → resume/restart → cleanup`

检查：Python 版本如何进入任务、审计失效规则、不同 run/workspace 隔离、环境重用、失败重建、生命周期回收。

### 4.5 Artifact/evidence 链路

分别追踪：

- 用户初始 input artifact；
- Coding patch/code_change；
- Experiment result；
- literature search 同 turn 动态注册；
- Scientific observed/cited；
- final report。

检查 URI、hash、producer、run/session/task/attempt provenance，及失败窗口的幂等性。

### 4.6 预算链路

逐项列出：

- Scientific LLM call；
- Compiler draft/review/retry call；
- Coding/Experiment LLM call；
- action schema invalid call；
- Task max_steps/max_llm_calls；
- Run max_tasks/max_attempts/max_llm_calls/timeout。

不要只看字段是否存在，要验证是否有唯一总账与真实扣减。

### 4.7 约束与资源链路

检查：

- ResearchRequest.constraints 是否只经 Scientific/Compiler 分配给 Task；
- 已满足的控制约束是否会旁路到子 Agent；
- dataset refs 的 Run/Task 两个入口；
- workspace_id/workspace_spec/grant；
- environment_spec；
- input_artifacts。

## 5. 必须验证的十个对抗场景

不要求都跑真实 LLM；优先使用 ScriptedLLM/ScriptedModulePort 做确定性测试。

1. 任务级 ask-user 经 ResearchController 完整恢复；
2. Coding 编辑后 ask-user，再恢复并正确生成当前 Attempt patch；
3. `max_attempts_per_task=1` 的 ask/resume 不越界；
4. 两条 verification 命令只执行第一条时不得通过；
5. verification 非零退出必须产生 `ToolObservation.ok=False`；
6. ResearchRequest.input_artifacts 能被 Scientific 读取并引用；
7. 上游 Python version 能到达 Coding/Experiment EnvironmentBinding；
8. Artifact 注册在 grant/path 校验失败后，同 ID 重试不被残留目录污染；
9. Experiment typed metric 与 JSON evidence 值不一致时不得冒充已验证；
10. 一个 Run 同时出现两个 active WorkRequest 时必须被拒绝，而不是静默选择第一个。

## 6. 死代码与重复逻辑专项检查

必须给每个候选符号列出 production 调用者和 test-only 调用者：

- `WorkflowScheduler.create_run` 与无 WorkRequest finish fallback；
- `PendingTaskUpdate`、`supersede_task_ids`、`pending_task_updates`；
- `TaskProposal.required` / `WorkflowTask.required`；
- per-task `rationale`；
- GitWorkspace HEAD-relative legacy methods；
- `snapshot_workspace` 与 GitBaseline；
- AgentLoop unknown-tool 两个分支；
- JsonRunStore/JsonSessionStore 原子写入；
- Agent context 的 `last_observation`/全量 memory 与 Runtime recent history；
- Scientific evidence validation 的多个层次。

注意：被 `.gitignore` 忽略且未跟踪的 build/egg-info/__pycache__ 不是仓库死代码，不得误报为需要修改的源码。

## 7. 通用能力落实评分表

请对每项给出 Green/Yellow/Red，并附代码证据：

| 项目 | 要回答的问题 |
|---|---|
| contracts 独立性 | 是否只描述跨模块语义，是否夹入实现细节/死字段 |
| runtime 通用性 | 三个 Agent 是否真复用同一 loop；是否含领域特例 |
| capabilities 粒度 | 是否是可复用物理能力；是否出现 Agent/调度语义 |
| orchestrator 解耦 | 是否依赖具体 Agent/runtime；状态所有者是否唯一 |
| Agent 独立性 | 是否互调或复制基础设施；差异是否只在领域工具/prompt/finalizer |
| workspace/env/data | 是否有唯一布局、明确所有权、可恢复与可清理 |
| evidence | typed payload 与冻结证据是否一致 |
| 简洁性 | 公共 API 是否都有 production 调用者；是否存在假扩展点 |

## 8. 严重度标准

- P0：可能破坏用户数据、越权执行，或主路径普遍不可用；
- P1：合法现有路径能稳定触发错误/错误成功/不可恢复；
- P2：主路径可用，但存在两套真相、生命周期缺口或高维护风险；
- P3：局部冗余、诊断差、文档漂移；
- 建议：没有当前失败证据的未来增强，不得标 P1/P2。

## 9. 输出格式

请输出一个 Markdown 报告，顺序固定：

1. 一页结论；
2. 审查基线（branch/commit/tests）；
3. findings，按 P0→P3 排列；
4. 每条 finding 的证据链、影响、最小根治、测试；
5. 与 `CODE_REVIEW_2026-08-31.md` 的交叉核对表：确认/降级/否定/新发现；
6. 通用能力落实评分；
7. 建议修复批次；
8. 明确“不建议做什么”。

不要直接改代码，不要提交，不要 push。若为了证明问题写临时测试，必须放在临时目录或在结束前删除，并在报告中给出复现代码。

## 10. 验收标准

复核完成必须满足：

- 所有 P1 都有可复现路径，不靠猜测；
- 所有“死代码”都证明 production 无调用者；
- 所有“重复逻辑”都说明两套语义哪里不同；
- 所有修复建议都没有扩大产品范围；
- 至少明确指出三项当前设计做得好的地方；
- 不能用“347 tests passed”证明没有断链，也不能用一次 LLM E2E 波动证明架构错误；
- 报告能直接转成后续开发任务，但本轮不实现。

## 11. 可直接复制给审查 AI 的提示词

```text
请对 /home/cyl/ResAgent2 当前 main 做一次只读、证据驱动的全面代码复核。完整遵守 docs/reviews/CODE_REVIEW_REQUIREMENTS.md。不要改代码、不要提交、不要 push；不要扩展新功能范围。重点验证逻辑断链、两套语义、production 不可达的死契约、冗余与过度设计，以及通用能力抽取是否真正落实。先独立审查，再与 docs/reviews/CODE_REVIEW_2026-08-31.md 逐条交叉核对。每条问题必须给出入口→状态/数据路径→断点行号→实际后果→最小根治→应补测试；没有完整证据链的内容只能列为建议，不能标 P1/P2。
```
