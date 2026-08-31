# ResAgent2 代码阅读指南

**目标**：让一个不了解历史、只想读懂当前系统的开发者，用最短路径建立准确的心智模型。本文只描述当前 3.0 架构（ADR-0011），不重复 Phase 1—7 的演进历史（历史在 `DEVELOPMENT_PLAN.md`、`docs/decisions/`）。

一句话：**Scientific 负责判断，Controller 负责研究控制，Compiler 负责翻译任务，Scheduler 负责执行图，AgentLoop 负责 Agent 会话，capabilities 负责真实动作。**

## 1. 先看架构

读 `docs/ARCHITECTURE.md`。只看五层职责和一条主链，不要纠结 Pydantic 字段。

```text
用户 / API
  → ResearchController（唯一 Run 入口与状态负责人）
      ├→ ScientificAgent（finish / request_work / ask_user）
      ├→ WorkflowCompiler（语义 WorkRequest → append-only Task 图）
      └→ WorkflowScheduler（执行 Task / Attempt → WorkOutcome）
            → CodingAgent / ExperimentAgent（共享 AgentLoop）
                  → capabilities（workspace / git / process / env / dataset / artifact / repo / literature）
```

## 2. 再看契约

读 `packages/contracts/src/resagent2_contracts/models.py`，按一条数据流读，先不看 validator：

1. `ResearchRequest`（用户目标、约束、dataset、`input_artifacts`）；
2. `WorkRequest` / `WorkRequestDraft`（Scientific 的语义工作请求）；
3. `WorkflowProposal` / `WorkflowPatch` / `WorkflowTask` / `Attempt`（图与尝试）；
4. `ModuleTaskRequest` / `ModuleResult`（Scheduler 与执行 Agent 的边界）；
5. `ArtifactRef` / `ArtifactCandidate`（证据 provenance）；
6. `ScientificTurnRequest` / `ScientificTurnResult`（Scientific 边界）。

对照 `docs/CONTRACTS.md` 的字段表理解语义。每个公共字段都有 producer/validator/persistence/consumer。

## 3. 看研究闭环

读 `packages/orchestrator/src/resagent2_orchestrator/controller.py` 的 `ResearchController`：

- `create_run` / `answer_question` / `run_until_stable` 是唯一外部入口；
- `_scientific_turn` → `_apply_turn`：把 Scientific 的 `request_work`/`ask_user`/`finish` 变成 WorkRequest/PendingQuestion/最终意见；
- `_execute_work_request`：驱动 Compiler 与 Scheduler；
- `answer_question` 按 `PendingQuestion.task_id` 分流（Scientific 问题 vs 任务问题）。

## 4. 看任务翻译

读 `compiler.py` 的 `LLMWorkflowCompiler`：LLM 只输出 `CompilationDraft`（局部 key、capability、goal、depends_on、inputs），`_materialize_draft` 用确定性代码分配 TaskId、绑定 work_request_id、解析 workspace、转换依赖，再做一次有界语义审查。

## 5. 看任务执行

读 `scheduler.py` 的 `WorkflowScheduler`：只执行任务图，不决定 Run 完成。`execute_task` 分发到 `_start_task`（新 Attempt）或 `_resume_task`（同一 Attempt 恢复）；`_invoke` 调用 ModulePort、登记 Artifact、结算 Attempt。`_evaluate_run` 在图稳定时冻结 WorkOutcome 并置 WorkRequest 为 stable。

## 6. 看共享循环

读 `packages/runtime/src/resagent2_runtime/loop.py` 的 `AgentLoop`：LLM action → schema 校验 → 权限 → Tool → observation；`ToolObservation.ok` 是唯一机器成功标志；`runtime_feedback` 与 `recent_observations` 由 Runtime 唯一注入；连续失败保护与精确 `llm_calls` 计数都在这里。

## 7. 看一个 Agent 怎么组装

读 `packages/agents/coding/src/resagent2_coding/agent.py`：一个 Agent = prompt + tools + context builder + permission policy + deterministic finalizer，全部通过 `AgentDefinition` 注入共享 `AgentLoop`。`CodeModifyCompletionCheck` 用 `GitBaseline` 隔离 Attempt 增量，验证失败/漏跑命令不能 completed。

## 8. 看领域差异

- `experiment/agent.py` + `completion.py`：实验证据归属、typed metrics 从 JSON evidence 派生；
- `scientific/agent.py` + `completion.py`：证据读取控制、未读引用拒绝、三层校验。

## 9. 按需读 capabilities

`workspace.py`（边界）、`git.py`（GitBaseline）、`process.py`（shell-free 执行）、`environment.py`（env 生命周期与 cleanup）、`dataset.py`、`artifacts.py`、`repo.py`（staging 物化）、`literature.py`。

## 10. 看组合根

读 `e2e/real_e2e.py`：`_build_controller` 把 registry、scheduler、controller、三个 Agent、LLM client、session store 装配成唯一 production 路径。`ModuleBinding.owner` 从 `CapabilityRegistry` 派生（单一来源）。

## 阅读时的经验法则

1. **沿一条数据流读**，不要先背全部 Pydantic 模型；遇到 validator 再回头查；
2. **LLM 输出都是不可信建议**：身份、状态、证据、成功都由代码裁定；
3. **一个字段若有多个 producer/consumer，说明需要收敛**；
4. 任何代码同时跨越两层职责（控制/调度/执行/能力），就应当被质疑。
