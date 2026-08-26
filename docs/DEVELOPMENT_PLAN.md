# ResAgent2 开发计划

**状态**：当前唯一实施计划
**原则**：文档、代码、测试同步；一次只推进一个阶段。

## 1. 状态标记

| 状态 | 含义 |
|---|---|
| not_started | 尚未开始 |
| in_progress | 当前唯一开发阶段 |
| blocked | 有明确外部阻塞 |
| completed | 实现、文档和验收全部完成 |

同一时间只能有一个 `in_progress` 阶段。

## 2. 总体阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 文档和目录基线 | completed |
| Phase 1 | contracts 包 | completed |
| Phase 2 | 最小共享 runtime 和 Agentic Loop | not_started |
| Phase 3 | ResAgent Workflow Core | not_started |
| Phase 4 | Legacy Adapters 与黄金闭环 | not_started |
| Phase 5 | Coding Agent vNext | not_started |
| Phase 6 | Experiment Agent vNext | not_started |
| Phase 7 | Scientific Agent vNext | not_started |
| Phase 8 | 稳定化与按需迁移高级能力 | not_started |

## 3. Phase 0：文档和目录基线

### 目标

在写功能代码前，确定：

- 系统职责和模块边界；
- 共享 Agentic Loop；
- LLM 计划与确定性调度的分工；
- 顶层 WorkflowTask；
- 模块请求/结果/Artifact/Question 契约；
- 分阶段开发顺序。

### 交付

- `README.md`；
- `docs/ARCHITECTURE.md`；
- `docs/CONTRACTS.md`；
- 本文件；
- `docs/decisions/` 中的初始 ADR；
- `packages/` 和 `tests/` 文档骨架。

### 验收

- [x] 新仓库建立；
- [x] 目录结构明确；
- [x] 四个模块职责明确；
- [x] 任务图与 Agent 内部动作明确分离；
- [x] 动态计划修订有边界；
- [x] 代码尚未开始；
- [x] 用户确认文档基线；
- [x] 文档 commit 已推送。

用户确认并推送后，Phase 0 标记 completed，Phase 1 才能开始。

## 4. Phase 1：contracts 包

### 目标

将 `CONTRACTS.md` 中最小公共类型实现为无运行时依赖的 Python 包。

### 范围

- ID/value objects；
- Workflow/WorkflowTask/WorkflowPatch；
- ModuleTaskRequest/ModuleResult；
- Attempt；
- ArtifactRef/ArtifactCandidate；
- Question/Answer；
- Capability registry 类型；
- status enums；
- schema version。

### 不做

- LLM；
- Tool；
- Workflow 执行；
- 文件/进程/Git；
- legacy 兼容。

### 先写测试

- schema round-trip；
- ID namespace；
- DAG 引用基本校验；
- status 与 question/error 条件；
- Artifact provenance 必填；
- 禁止非法字段组合。

### 完成标准

- [x] contracts 无对 runtime/orchestrator/agents 的依赖；
- [x] 所有公共模型有 docstring 和 CONTRACTS 对应项；
- [x] 22 个 contract tests 全部通过；
- [x] 文档实现状态更新；
- [x] `ResAgent2` 专属 Conda 环境和 `environment.yml` 已建立。

### 实施记录（2026-08-26）

- 发布包：`resagent2-contracts 0.1.0`；
- wire schema：`1.0`；
- 唯一第三方运行依赖：Pydantic 2；
- 验证命令：`python -m pytest tests/contracts`；
- Phase 2 未自动开始，避免在确认 Phase 1 前提前扩张抽象。

## 5. Phase 2：最小共享 Runtime

### 目标

实现一个可被三个 Agent 复用的最小 Agentic Loop。

### 范围

- AgentDefinition；
- AgentLoop；
- typed AgentAction；
- Tool protocol/registry/dispatcher；
- mock LLM client；
- Context sections 和总 token budget；
- PermissionPolicy protocol；
- event/state persistence；
- completion check；
- needs_user_input signal；
- timeout 和结构化错误。

### 初始 Tools

只实现测试用内存工具：

- `read_value`；
- `write_value`；
- `finish`；
- `ask_user`。

真实 filesystem/process/git 留到 Coding Agent 阶段，避免提前抽象。

### 黄金测试

使用同一 AgentLoop 注入三个最小 Profile：

1. 只读分析 Profile；
2. 可写执行 Profile；
3. 需要用户输入 Profile。

验证 Loop 不修改即可产生不同 Agent 行为。

### 完成标准

- [ ] AgentLoop 不 import 任何具体 Agent；
- [ ] Tool 输入全部 schema 校验；
- [ ] 权限在执行前检查；
- [ ] 每步增量持久化；
- [ ] finalizer 不信任 LLM proposed status；
- [ ] runtime tests 和文档通过。

## 6. Phase 3：ResAgent Workflow Core

### 目标

实现不依赖真实子 Agent 的确定性 Scheduler。

### 范围

- ResearchRun；
- Workflow validator；
- ready Task 计算；
- Task/Attempt 状态机；
- capability → ModulePort；
- ModuleResult 状态映射；
- retry policy；
- PendingQuestion 暂停/回答；
- ArtifactCandidate 校验和登记；
- WorkflowPatch 校验；
- finish gate；
- state 原子持久化和恢复。

### 必测 Workflow

1. 线性：plan → code → experiment → analyze；
2. 并行：plan → baseline + treatment → analyze；
3. repair：experiment blocked → code repair → experiment retry；
4. ask user：task → paused → answer → resume；
5. invalid graph：环、未知依赖、重复 ID、未知 capability。

这些测试使用 fake ModulePort，不调用 LLM。

### 完成标准

- [ ] 同一输入 state 得到同一 ready Task 集合；
- [ ] Scheduler 不调用 LLM 选择普通状态转换；
- [ ] failed payload 不可能变 completed；
- [ ] Attempt/Artifact 历史不可覆盖；
- [ ] restart 后可从 state 恢复；
- [ ] ARCHITECTURE 与实际一致。

## 7. Phase 4：Legacy Adapters 与黄金闭环

### 目标

在不重写三个旧模块的情况下，让新 ResAgent 跑通一个最小科研闭环。

### 顺序

1. Legacy Scientific Adapter；
2. Legacy Coding Adapter；
3. Legacy Experiment Adapter；
4. deterministic mock E2E；
5. 本地真实小任务；
6. 服务器短实验。

### Adapter 规则

- 只转换公开请求和结果；
- 不修改旧模块内部 state；
- 不复制旧模块业务 validator；
- 原生失败状态必须保留；
- 所有兼容逻辑集中在 adapter；
- 标记删除条件。

### 黄金闭环

```text
固定 ResearchRequest
  → Scientific Adapter 生成固定 Workflow
  → Coding Adapter 生成小 patch
  → Experiment Adapter 运行短命令并产出 metrics
  → Scientific Adapter 分析
  → finish gate 通过
```

### 完成标准

- [ ] 一个命令运行完整 mock E2E；
- [ ] 每一步有 Task/Attempt/Artifact；
- [ ] 中途 kill 后可恢复；
- [ ] 用户能从 state 和 summary 理解过程；
- [ ] 服务器只使用固定 commit 和短任务。

## 8. Phase 5：Coding Agent vNext

### 目标

用共享 AgentLoop 实现第一个真实专业 Agent，并首次提取通用执行能力。

### 新增 Runtime 能力

- filesystem boundary；
- safe process runner；
- Git workspace/read/diff；
- readonly input；
- command logs；
- patch Artifact finalizer。

### Coding 专有能力

- Coding context；
- code tools；
- edit policy；
- code result schema；
- changed/untracked file finalizer；
- verification policy。

### 完成标准

- [ ] read-only 问答不写文件；
- [ ] 路径和复合命令不能绕过权限；
- [ ] 新文件进入 patch Artifact；
- [ ] verification 失败影响状态；
- [ ] 通过旧 CodingAgent 黄金用例；
- [ ] Legacy Coding Adapter 可删除。

## 9. Phase 6：Experiment Agent vNext

### 目标

复用 Coding 阶段验证过的 filesystem/process/Git，实现实验员。

### 新增通用能力的条件

只有 Coding 与 Experiment 语义相同时，才把能力移入 runtime。模块专有策略留在 Experiment 包。

### Experiment 专有能力

- repo materialization policy；
- environment binding/creation；
- GPU/hardware audit；
- dataset cache；
- experiment confirmation；
- metrics/evidence finalizer；
- structured ExperimentResult。

### 完成标准

- [ ] repo identity 不因 basename 冲突；
- [ ] timeout 清理完整进程树；
- [ ] command policy 不依赖 LLM stage_hint；
- [ ] completed 绑定当前 Attempt evidence；
- [ ] 通过旧 reproagent 黄金用例；
- [ ] Legacy Experiment Adapter 可删除。

## 10. Phase 7：Scientific Agent vNext

### 目标

在 ScientificAdvisorPort 后重写科学顾问，不影响 Scheduler。

### 范围

- Scientific context；
- Artifact allowlist reader；
- literature tools；
- WorkflowProposal/WorkflowPatch 输出；
- ScientificConclusion；
- deterministic schema validator；
- evidence/risk/confidence 语义。

### 完成标准

- [ ] 不输出 executor/path/env/status；
- [ ] Proposal 图通过统一 validator；
- [ ] 运行失败与 ScientificDecision 分离；
- [ ] 只读授权 Artifact；
- [ ] 纯解释、规划、结果分析语义清楚；
- [ ] Legacy Scientific Adapter 可删除。

## 11. Phase 8：稳定化与高级能力

只按真实需求逐项评估：

- Conversation 长期记忆；
- Session 索引；
- 内容寻址环境；
- 并行 worker；
- Skill 原语；
- plugin/adapters 自动发现；
- 云端 durable execution；
- 多模型独立验证。

每项必须先有：

- 已复现需求；
- 简单方案不足的证据；
- 设计决策；
- 测试和删除/回退方案。

## 12. 文档同步规则

每个 PR/commit 检查：

- [ ] 是否改变公开接口？更新 CONTRACTS。
- [ ] 是否改变组件、状态或流程？更新 ARCHITECTURE。
- [ ] 是否完成/开始计划项？更新本文件状态和证据。
- [ ] 是否作出难以逆转的决策？新增或更新 ADR。
- [ ] 是否新增概念？README 能否仍用简单语言解释？
- [ ] 文档描述的是已实现还是目标？标记是否准确？
- [ ] 对应测试是否在同一变更中？

## 13. 实施记录

| 日期 | 阶段 | commit | 测试/证据 | 备注 |
|---|---|---|---|---|
| 2026-08-26 | Phase 0 | `a7cb70a` | `git diff --cached --check` | 初始文档基线已推送，等待用户确认 |
