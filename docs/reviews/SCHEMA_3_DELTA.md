# Schema 3.0 目标 delta 与字段所有权矩阵（Stabilization 3.0）

**角色**：S0 裁定产物，S1—S6 实现时逐字段对照的唯一目标。不是当前 `CONTRACTS.md` 的替代，完成后随 S6 折叠进 `CONTRACTS.md`（只写当前 3.0）。

**上级**：ADR-0011。

本文只回答两个问题：3.0 相对 2.0 **改了什么字段**；每个公共字段**谁写、谁校验、谁持久化、谁消费**。凡在矩阵中无法指出至少一个 production producer 和 consumer 的字段，一律删除。

---

## 1. 字段变更清单（2.0 → 3.0）

### 1.1 删除（不兼容）

| 位置 | 字段/类型 | 理由 |
|---|---|---|
| `TaskProposal` | `rationale: NonEmptyStr` | 生成后立即丢失，无 consumer（根因 E-3） |
| `TaskProposal` | `required: bool = True` | 只服务 Scheduler 直驱旧语义（根因 E-2） |
| `WorkflowTask` | `required: bool = True` | 同上 |
| `WorkflowPatch` | `supersede_task_ids: list[TaskId]` | 无 production 调用方（根因 E-1） |
| `WorkflowPatch` | `pending_task_updates: list[PendingTaskUpdate]` | 同上 |
| `PendingTaskUpdate` | 整个类型 | 同上 |
| `ExperimentRunInput` | `dataset_refs: list[DatasetRef]` | 两个权威来源，Scheduler 静默覆盖（根因 D-Dataset） |
| `CompilationTaskDraft` | `rationale`（compiler 内部） | 与 TaskProposal.rationale 同步删除 |

### 1.2 新增

| 位置 | 字段/类型 | 语义 |
|---|---|---|
| `WorkspaceSpec` | `environment: EnvironmentSpec \| None = None` | Python 版本硬约束归 workspace，不归 LLM 生成的 Task（根因 D-Env） |
| `ArtifactImport` | 新类型：`uri`、`kind`、`media_type`、`summary`、`expected_sha256: str\|None` | 最小输入类型；调用方不得伪装成已注册 ArtifactRef（根因 D-Artifact） |
| `ModuleResult` | `llm_calls: int = 0` | 本 Attempt 真实 LLM 调用数（根因 C-计数 / 预算总账） |
| `AgentState`（runtime 内部） | `llm_calls_used: int = 0` | 会话级持久调用计数，含 schema 错误（根因 C） |

### 1.3 语义变更（字段不改名，改含义）

| 位置 | 字段 | 2.0 语义 | 3.0 语义 |
|---|---|---|---|
| `Attempt.status` | `NEEDS_USER_INPUT` | 终态（填 `finished_at`） | 暂停态：`finished_at=None`、`error=None`、`session` 指向 paused Session；resume 置回 `RUNNING`（根因 B） |
| `Attempt` validator | 终态集合 | `{非 RUNNING}` | 终态 = `{completed, completed_with_warnings, failed, blocked}`；`running` 与 `needs_user_input` 非终态 |
| `ResearchRequest.input_artifacts` | `list[ArtifactRef]` | 由 `list[ArtifactImport]` 取代（Controller import 后生成 ArtifactRef） |
| `ResearchRun.llm_calls_used` | 仅累计 Scientific | 累计 Scientific + Compiler + Coding + Experiment（Run 总账） |
| `SessionRef.state_uri` | `memory://sessions/<id>` | `session://<id>`（不承诺存储介质） |
| `ModuleResult.payload` | 任意 JsonValue | 不变，但 Experiment metrics 由证据派生（见 §3.2） |

---

## 2. 字段所有权矩阵

约定：P=生产者（唯一），V=校验者，S=持久化位置，C=消费者。一个字段若同时有多个 P 或 C，说明需要收敛。

### 2.1 控制面状态（根因 A）

| 字段 | P | V | S | C |
|---|---|---|---|---|
| `ResearchRun.status` | ResearchController（唯一） | ResearchRun 模型 | RunStore | Scheduler / gate / CLI |
| `ResearchRun.pending_question` | ResearchController | 模型 | RunStore | answer_question 分流 |
| `ResearchRun.answers` | ResearchController | 模型 | RunStore | Scheduler 按 `answer_task_ids` 投递 |
| `ResearchRun.answer_task_ids` | ResearchController | 模型 | RunStore | Scheduler 构造 ModuleTaskRequest.answers |
| `ResearchRun.work_requests` | ResearchController（创建） + `_transition_work_request`（迁移） | WorkRequest 模型 + active≤1 校验 | RunStore | Compiler / Scheduler / gate |
| `WorkRequest.status` | `_transition_work_request()`（唯一迁移） | WorkRequest 模型 | RunStore（随 Run） | Controller / Scheduler / gate |
| `WorkRequest.updated_at` | `_transition_work_request()` | 模型 | RunStore | 诊断 |
| `PendingQuestion.task_id` | Scheduler（任务问题）/ Controller（科学问题） | 模型 | RunStore | Controller.answer_question 分流 |

### 2.2 Attempt 与执行（根因 B）

| 字段 | P | V | S | C |
|---|---|---|---|---|
| `Attempt.number` | Scheduler `_start_attempt` | 模型（连续从 1） | RunStore | 诊断 / Artifact provenance |
| `Attempt.status` | Scheduler | Attempt 模型 | RunStore | gate / report |
| `Attempt.finished_at` | Scheduler（仅终态填） | 模型 | RunStore | gate |
| `Attempt.session` | ModulePort 返回 → Scheduler | ModuleResult 模型 | RunStore | resume（同 Attempt 继续） |
| `Attempt.baseline`（coding，经 memory） | Coding 首入 Attempt | — | AgentState.memory | resume 恢复，禁止重 snapshot |
| `WorkspaceRecord.root` | Scheduler `_resolve_workspaces` | 模型 | RunStore | 派生 WorkspaceGrant |
| `ModuleTaskRequest.parent_session_id` | Scheduler（resume 同 Attempt 时） | 模型 | wire | AgentLoop 恢复同一 Session |

### 2.3 Runtime 机器语义（根因 C）

| 字段 | P | V | S | C |
|---|---|---|---|---|
| `ToolObservation.ok` | 各 Tool | 参数化契约测试 | AgentEvent | AgentLoop 连续失败保护 |
| `AgentState.runtime_feedback` | AgentLoop（唯一） | — | SessionStore | context 注入 |
| `AgentState.recent_observations` | AgentLoop（唯一） | — | 派生自 events | context 注入 |
| `AgentState.llm_calls_used` | AgentLoop（每次 provider call 后 +1） | — | SessionStore | ModuleResult.llm_calls |
| `ModuleResult.llm_calls` | AgentLoop | ModuleResult 模型 | Attempt | Scheduler 累计到 Run 总账 |
| Agent context `last_observation`/`audit_memory` dump | （删除） | — | — | 由 runtime 的 feedback/history 取代 |

### 2.4 资源权威（根因 D）

| 字段 | P | V | S | C |
|---|---|---|---|---|
| `WorkspaceSpec.environment` | composition root（唯一） | 模型 | ResearchRun.workspaces | Scheduler → ModuleTaskRequest.environment_spec |
| `ResearchRequest.dataset_refs` | 调用方（唯一注册表） | 模型 | RunStore | Scheduler 注入 Experiment |
| `ExperimentRunInput.dataset_refs` | （删除） | — | — | — |
| `ArtifactImport` | 调用方 | 模型 + Controller import（hash 校验） | 冻结后成 ArtifactRef | Scientific / Task |
| `ArtifactRef`（import） | ArtifactRegistry `register_import` | ArtifactRef 模型 + hash | run.artifacts | Scientific authorized / Task input |

### 2.5 契约收敛（根因 E）

| 字段 | 结论 |
|---|---|
| `WorkflowPatch.add_tasks` | 唯一 Patch 能力；`reason` 是 patch 级 rationale |
| `WorkflowProposal.compilation_rationale` | 保留，proposal 级 rationale |
| `TaskProposal.rationale` / `required` | 删除 |
| HEAD-relative `GitWorkspace.diff/changed_paths/...` | 删除或改名 repository diagnostic；Attempt diff 只用 `*_since(baseline)` |

---

## 3. 关键语义精化

### 3.1 Attempt 暂停与恢复

```
NEEDS_USER_INPUT（暂停，finished_at=None）
      │  answer（同一 Attempt）
      ▼
RUNNING（继续，复用 Session / output_dir / baseline）
      │
      ├── completed / failed / blocked（终态，填 finished_at）
```

`max_attempts_per_task` 只在 `FAILED/BLOCKED → retry` 时检查；同一 Attempt 内的 ask/resume 往返不消耗 attempt 预算。

### 3.2 Experiment metrics 证据绑定

- Agent 只报告 `summary` / `evidence_files` / `residual_risks`；
- finalizer 对声明的 JSON evidence 读取顶层数值字段，按 `expected_metrics` 规范化 key 生成 `ExperimentResult.metrics`；
- LLM 不能直接提供最终 typed metric value；
- 非 JSON 证据可冻结引用，但无确定性解析器时 typed metrics 为空并给明确 warning；
- `parameters` 若无 production consumer 一并删除。

### 3.3 Run 总账

- `ResearchRun.llm_calls_used` = Scientific + Compiler + Coding + Experiment；
- `started_at/created_at` 算 wall-clock remaining；
- 每轮先算 `deadline - now`，下游 timeout 不超过 remaining；`remaining <= 0` 确定性失败。

---

## 4. 实施顺序（对照 STABILIZATION_PLAN §8）

- **S1**（根因 A+B）：§2.1、§2.2、§3.1；
- **S2**（根因 C + 总账）：§2.3、§3.3；
- **S3**（根因 D）：§2.4、§1.2 的 `WorkspaceSpec.environment` / `ArtifactImport`；
- **S4**（原子性 + 证据）：Artifact 事务化、§3.2；
- **S5**（删旧表面）：§2.5、§1.1 全部；
- **S6**（生命周期 + 验收）：env manifest/cleanup、`CODE_READING_GUIDE.md`、折叠本文进 `CONTRACTS.md`。
