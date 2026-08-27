# ResAgent2 开发计划

**文档角色**：开发顺序、阶段范围、完成状态和验收证据的唯一事实来源

**语义上级**：`ARCHITECTURE.md`

**接口上级**：涉及 wire 字段时引用 `CONTRACTS.md`，不在本文件重复定义模型

## 1. 计划纪律

1. 一次只允许一个阶段为 `in_progress`；
2. 先更新架构/契约/计划，再写改变语义的代码；
3. 目标能力与当前实现必须分栏，不用未来目标证明当前阶段完成；
4. 阶段只有在代码、测试、文档和验收证据都完成时才能标记 completed；
5. 发现已完成阶段的核心声明不真实时，必须重开该阶段；
6. 不因为“以后可能需要”提前增加抽象。

状态定义：

| 状态 | 含义 |
|---|---|
| not_started | 尚未开始 |
| in_progress | 当前正在开发或重新对齐 |
| blocked | 有明确外部阻塞，当前无法推进 |
| completed | 范围、代码、测试、文档和证据全部满足 |

## 2. 当前路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 0 | 仓库、顶层架构和目录基线 | completed |
| Phase 1 | contracts 1.0 基础实现 | completed |
| Phase 2 | 最小 shared runtime / Agentic Loop | completed |
| Phase 3 | ResAgent Workflow Core 与语义对齐 | completed |
| Phase 4 | Planning Port、Legacy Adapters 与最小黄金闭环 | completed |
| Phase 5 | Coding Agent vNext | completed |
| Phase 6 | Experiment Agent vNext | not_started |
| Phase 7 | Scientific Agent vNext 与科学闭环 gate | not_started |
| Phase 8 | 稳定化与按需高级能力 | not_started |

Phase 3、Phase 4 与 Phase 5 已完成。原生 Coding Agent 已替换 legacy Coding adapter，并在服务器真实闭环中登记 patch、代码、实验和科学结论四类证据。Phase 6 尚未开始。

## 3. Phase 0：仓库与架构基线

### 目标

建立 monorepo、项目专属环境、模块边界、两种循环、顶层 WorkflowTask 和文档先行纪律。

### 已完成交付

- README 和三份核心文档；
- `docs/decisions/` 初始 ADR；
- packages/tests 目录骨架；
- Conda 环境 `ResAgent2` 和 environment.yml；
- 新仓库与首次推送。

### 历史验收

- [x] 四个模块职责被区分；
- [x] WorkflowTask 与 AgentAction 被分层；
- [x] LLM 计划与确定性调度被分工；
- [x] 动态 WorkflowPatch 有修改边界；
- [x] 用户确认初始方向。

后续发现初始文档混合目标和实现。该问题不重开仓库基线，而在 Phase 3 以三文档统一和 traceability 修复。

## 4. Phase 1：contracts 1.0

### 目标

实现无 runtime/orchestrator/agent 依赖的最小公共数据包。

### 已实现范围

- ID namespace 和 schema_version；
- ResearchRequest、Workflow、WorkflowTask、WorkflowPatch；
- ModuleTaskRequest、ModuleResult；
- Attempt、ArtifactCandidate/ArtifactRef；
- Question/Answer；
- Capability registry、状态和错误枚举；
- strict Pydantic validation。

### 历史验收

- [x] 包名 `resagent2-contracts 0.1.0`；
- [x] wire schema `1.0`；
- [x] contracts 不依赖其他项目包；
- [x] ID、图、状态组合和 provenance 有测试；
- [x] 当时 22 个 contract tests 通过。

### 已知后续工作

contracts 包“代码已实现”不等于所有字段“运行语义已接通”。Phase 3 已强制控制面 capability 边界并拒绝带未解决 questions 的 Proposal；success_criteria 求值、生产 payload 模型/消费方、完整 pre-run 问答和 schema 演进仍按后续阶段推进。

## 5. Phase 2：最小 shared runtime

### 目标

用一个可注入 Profile 的 Agentic Loop 支撑三类模块，而不提前实现真实 filesystem/process/Git。

### 已实现范围

- AgentDefinition、AgentLoop；
- typed AgentAction；
- Tool protocol/registry/dispatcher；
- ScriptedLLMClient；
- Context sections/budget；
- PermissionPolicy；
- 内存 SessionStore 和 event snapshots；
- completion check、needs_user_input、timeout 和结构化错误；
- read_value、write_value、finish、ask_user 四个测试 Tool。

### 历史验收

- [x] Loop 不 import 具体 Agent；
- [x] Tool 输入经过 schema 校验；
- [x] 权限检查发生在 Tool 执行前；
- [x] action/observation/error 增量进入 Session；
- [x] finalizer 不信任 LLM proposed status；
- [x] 三个测试 Profile 复用同一 Loop；
- [x] 当时 14 个 runtime tests 通过。

### 明确未实现

- 真实 LLM provider；
- 磁盘 SessionStore；
- 根据 parent_session_id 恢复 Session；
- filesystem/process/Git/Artifact IO。

## 6. Phase 3：Workflow Core 与语义对齐（已完成）

### 目标

让 ResAgent 的确定性核心与最高级架构语义完全一致，并且只对实际实现的行为作完成声明。

### 6.1 v0.1.0 已实现能力

- ResearchRun 与 RunStore；
- Workflow DAG 校验和稳定 ready 顺序；
- Task/Attempt 状态映射；
- capability → ModulePort binding；
- retry、blocked、question pause/answer；
- ArtifactCandidate 的登记、冻结、hash 和 provenance；
- WorkflowPatch 与 workflow_history；
- JsonRunStore 原子替换；
- fake ModulePort 的线性、并行、repair、ask-user 和非法图测试。

当前 finish gate 只包含：无 PendingQuestion、无 ready/running Task、所有 required 非-superseded Task completed。

### 6.2 为什么 Phase 3 被重开

审查确认原文档把最终目标写进“Phase 3 已实现”，并存在控制面/任务面、状态名、字段示例和责任边界冲突。因此历史 v0.1.0 代码保留，但 Phase 3 状态改回 in_progress。

### 6.3 收尾对齐清单

文档基线：

- [x] `ARCHITECTURE.md` 成为概念和控制流的最高级事实来源；
- [x] `CONTRACTS.md` 只负责 wire 字段和语义；
- [x] 本文件只负责阶段、状态和验收；
- [x] README 作为派生摘要同步 Phase 状态和文档权威关系；
- [x] planning/replanning 不再冒充 RunStatus，skipped 统一为 superseded；
- [x] Artifact runtime 检查与登记复核被定义为两道边界；
- [x] 当前 finish gate 与最终目标 gate 分开；
- [x] 示例使用真实 capability、ID namespace、必填字段和字段类型。

代码/测试：

- [x] validator 禁止 scientific_plan 和 ask_user 进入 WorkflowTask；
- [x] create_run 拒绝 questions 非空的 WorkflowProposal；
- [x] 为上述两条增加负向测试；
- [x] 裁定 success_criteria 方向：保留可执行语义，不降级为纯说明字段。AUTOMATIC 表示任务完成后由机器基于已登记 Artifact 自动判定，MANUAL 表示需人工确认；evidence_key 的解析目标与求值器留待 evidence 求值阶段（Phase 7）定义，本阶段只锁定方向；
- [x] 记录 Phase 3 当时的 payload 策略；Phase 4 随后增加 `Attempt.payload` 持久化，但 Scheduler 仍不解释 payload，跨任务数据仍必须成为 Artifact；每个原生 capability 的强类型 payload model 和领域消费方在对应 Agent 阶段定义；
- [x] 将 `.resagent2/` 加入 `.gitignore`；
- [x] 清除 src/build 误读风险：正式验证只针对 `packages/*/src`，构建产物不得作为源码；
- [x] 复核“失败 Attempt Artifact 不向下游传播”的代码与测试；
- [x] 运行全仓测试、文档交叉检查和 `git diff --check`。

### 6.4 Phase 3 完成标准

- [x] 三份核心文档按权威关系无互相矛盾；
- [x] Workflow 中只允许任务面 capability；
- [x] Phase 3 明确且测试了 Proposal.questions 边界：非空 Proposal 不得创建 Run；回答后重新规划属于 Phase 4 Planning Port；
- [x] 当前 gate 的可观察条件有直接测试，文档不再声称未实现 gate；
- [x] Task/Run/Attempt 状态图只使用 contracts 枚举；
- [x] Artifact 自动传播只来自成功或 completed-with-warnings Attempt；
- [x] state/artifact 默认目录不污染 Git；
- [x] 全仓测试、Conda 环境 package check 和 `git diff --check` 通过；
- [x] 用户要求完成 Phase 3 收尾。

### 6.5 本阶段不做

- 不接真实旧 Agent；
- 不接真实 LLM；
- 不实现 filesystem/process/Git Tools；
- 不实现最终 ScientificConclusion gate；
- 不为了未来并行执行改写同步 Scheduler。

## 7. Phase 4：Planning Port、Adapters 与最小黄金闭环

### 目标

在不重写三个旧模块的前提下，用新 ResAgent 跑通一个边界清楚、可恢复的最小科研闭环。

### 顺序

1. 定义并实现 Scientific Planning Port adapter；
2. Legacy Coding Adapter；
3. Legacy Experiment Adapter；
4. Legacy Scientific Analyze Adapter；
5. deterministic mock E2E；
6. 本地真实小任务；
7. 服务器固定 commit 的短实验。

Planning Port 在创建 Workflow 之前运行，不能作为黄金 Workflow 中的 plan Task。

### Adapter 规则

- 只转换公开请求和结果；
- 不修改旧模块内部 state；
- 不复制旧模块业务 validator；
- 原生失败状态必须保留；
- 所有兼容逻辑集中在 adapter 并写删除条件；
- 不把 summary 文本解析成状态。

### 最小黄金闭环

```text
ResearchRequest
  → Planning Port 产生 WorkflowProposal
  → Validator 接受 code → experiment → scientific_analyze Workflow
  → Coding Adapter 产生小 patch Artifact（legacy retry 例外见下文）
  → Experiment Adapter 运行短命令并产生 metrics Artifact
  → Scientific Adapter 形成 ScientificConclusion
  → 当前工程 gate 完成，并单独报告尚未实现的科学闭环限制
```

### 完成标准

- [x] 一个命令运行完整 mock E2E；
- [x] mock E2E 每一步都有 Task/Attempt/Artifact；真实 legacy E2E 的实验结果和科学结论必须登记 Artifact，代码证据只允许下述有界例外；
- [x] Planning 不出现在 WorkflowTask 列表；
- [x] ask-user 能穿过 orchestrator 和 runtime 完成真实 resume；
- [x] 可从磁盘恢复到最后一次稳定保存的 Run 状态，即 Task 边界；中断一个正在运行的模块调用并原样续跑不在 Phase 4 范围；
- [x] ModuleResult payload 持久化到 Attempt，不被静默丢弃；跨 Task 消费仍只使用 ArtifactRef；
- [x] 本地 mock/全仓测试通过后，在服务器固定 commit 上完成真实短实验。

### 已接受的 legacy 限制

旧 CodingAgent 可能在失败 Attempt 中已经修改目标文件，retry 成功时却返回空 `changed_files`。这会导致 code Task 最终 completed，但没有可登记的 `code_change` Artifact。Phase 4 不修补即将在 Phase 5 删除的旧模块，也不把失败诊断伪装为成功交付物。

真实 E2E 仅在以下条件同时成立时接受这个例外：三个预期 Task 都最终 completed；code Task 有 Attempt 且 `util.py` 相对测试仓库 Git 基线确实改变；`experiment_result` 与 `scientific_decision` 分别由对应 Task 登记为 ArtifactRef。缺少实验或科学证据仍必须返回非零退出码。原生 Coding Agent 在 Phase 5 仍必须交付 code Artifact，不能继承这个例外。

## 8. Phase 5：Coding Agent vNext

### 目标

用 shared AgentLoop 实现第一个真实专业 Agent，并通过 Coding 与 Experiment 的共同需求提取通用执行能力。

### Runtime 新增能力

- filesystem boundary；
- safe process runner；
- Git workspace/read/diff；
- readonly input；
- command logs；
- ArtifactCandidate 输出辅助。

本阶段只抽取已能被 Coding 使用、并且语义上可被 Experiment 复用的机制。runtime 不包含 Coding prompt、编辑完成条件或代码 Artifact 选择策略。

### Coding 专有能力

- Coding context/prompt；
- code tools 和 edit policy；
- code result payload；
- changed/untracked file finalizer；
- verification policy。

### 已裁定的执行语义

- `code_understand` 是物理只读 profile：不提供写 Tool 或进程 Tool，完成时再次检查 Git 未变化；
- `code_modify` 要求 WorkspaceGrant 为 read_write，且 workspace 是已有 Git 仓库（不要求干净；预存在改动会一并计入 patch）；
- 文件读取受 WorkspaceGrant 的 allowed/denied paths 限制，写入还要同时满足 `CodeModifyInput.allowed_paths`；
- `.git` 与 `.resagent2` 是 runtime 保留目录，LLM 文件 Tool 不可访问；
- verification command 由调用方在 `CodeModifyInput.verification_commands` 中声明，LLM 只能请求运行整组命令；
- command 使用 `shlex` 解析为 argv 并以 `shell=False` 执行；管道、重定向、命令替换和复合命令直接拒绝；
- 每次文件变化递增 edit revision；verification 前后 Git diff 必须一致，且只有绑定最新 diff hash、全部通过的结果才能支持 completed；
- changed/new/deleted files、patch 和验证结果由 deterministic finalizer 从 Git 与进程结果生成，不信任 finish 文本；
- 输入 Artifact 只能通过 artifact id 读取，读取前校验 file URI、文件存在性和 sha256；
- 原生 Coding payload 使用 `CodeUnderstandResult` / `CodeModifyResult`，跨 Task 仍只传播 ArtifactRef；
- 脏工作区、非 Git workspace、任意 shell、包管理和仓库 materialization 不在 Phase 5 范围。

### 完成标准

- [x] read-only 任务不写文件；
- [x] 路径、symlink 和复合命令不能绕过权限；
- [x] 新文件进入 code Artifact；
- [x] verification 失败影响 ModuleStatus，且验证期间改变 Git diff 也不得完成；
- [x] 通过旧 CodingAgent 的 docstring + verification 黄金用例；
- [x] orchestrator 可登记原生 Coding Agent 的 `code_patch` / `code_change` Artifact；
- [x] 服务器真实 `code → experiment → analyze` 闭环通过，三个 Task completed、四个 Artifact 冻结、退出码为 0；
- [x] Legacy Coding Adapter 已删除。

Phase 4 的 code Artifact retry 例外只保留为历史记录；Phase 5 真实 E2E 已恢复严格 code Artifact 要求。

## 9. Phase 6：Experiment Agent vNext

### 目标

复用 Phase 5 已验证的 filesystem/process/Git 机制，实现实验员，不复制一套相似 runtime。

### Experiment 专有能力

- repo materialization policy；
- environment binding/creation；
- GPU/hardware audit；
- dataset cache；
- experiment confirmation；
- metrics/evidence finalizer；
- structured ExperimentResult payload。

### 完成标准

- [ ] repo identity 不依赖 basename；
- [ ] timeout 清理完整进程树；
- [ ] command policy 不依赖 LLM 自报 stage；
- [ ] completed 只绑定当前 Attempt 的合格 evidence；
- [ ] 通过旧 reproagent 黄金用例；
- [ ] Legacy Experiment Adapter 可删除。

## 10. Phase 7：Scientific Agent vNext 与科学闭环

### 目标

在稳定 Planning/Analyze Ports 后重写科学顾问，并把“工程执行完成”提升为“可解释的科研闭环完成”。

### 范围

- Scientific context 和只读 Artifact allowlist；
- literature tools；
- WorkflowProposal/WorkflowPatch；
- ScientificConclusion；
- evidence/risk/confidence 语义；
- required scientific analysis closure；
- final summary 只引用已登记事实。

### 进入代码前必须先裁定

- success_criteria/evidence_key 是否由模块 finalizer、独立 validator 或 Scheduler 求值；
- 哪些 Artifact kind 强制需要 scientific_analyze；
- final report 的最小契约；
- “inconclusive” 与运行失败如何分别呈现。

### 完成标准

- [ ] Scientific Agent 不输出 executor/path/env/status；
- [ ] Proposal/Patch 通过统一 validator；
- [ ] 执行成功与 ScientificVerdict 分离；
- [ ] 只读取授权 ArtifactRef；
- [ ] 最终 gate 的每个科学条件有模型和测试；
- [ ] Legacy Scientific Adapter 可删除。

## 11. Phase 8：稳定化与按需高级能力

只在有已复现需求时评估：长期 Conversation、Session 索引、并行 worker、Skill 原语、plugin 自动发现、云端 durable execution、多模型独立验证、内容寻址环境与镜像加速（国内网络下复用 conda 环境、减少重复下载）。

每项必须先有：

- 已复现需求；
- 简单方案不足的证据；
- ADR；
- 测试；
- 删除或回退方案。

## 12. 跨文档—代码追踪表

| 架构概念/约束 | contract/接口 | 实现阶段 | 当前状态 |
|---|---|---|---|
| planning 在控制面 | ScientificPlanInput、WorkflowProposal | Phase 3/4 | 已实现：validator 拒绝控制面 task |
| 顶层唯一 WorkflowTask | TaskProposal、WorkflowTask | Phase 1/3 | 已实现 |
| 确定性调度 | ModuleTaskRequest/ModuleResult | Phase 3 | 核心已实现 |
| ask-user 是控制信号 | QuestionDraft/PendingQuestion/UserAnswer | Phase 3/4 | orchestrator + runtime resume 已接通 |
| retry/resume/repair 分离 | Attempt、SessionRef、WorkflowPatch | Phase 3/4 | 已实现：retry 新 Attempt、ask-user resume 复用并校验 Session、repair 使用 WorkflowPatch |
| Artifact 两道边界 | WorkspaceGrant、Candidate、Ref | Phase 3/5 | 登记复核已有，runtime 访问层待实现 |
| 只传播成功 Attempt Artifact | Attempt.artifact_ids | Phase 3 | 已实现并测试 |
| success criterion 求值 | SuccessCriterion | Phase 3/7 | 仅存储，方向已裁定为可执行语义，求值器待 Phase 7 |
| ModuleResult payload | ModuleResult[PayloadT]、Attempt.payload | Phase 4/5-7 | Core 原样持久化但不解释；原生强类型模型与领域消费方在对应 Agent 阶段定义 |
| 科学分析闭环 | ScientificConclusion | Phase 7 | 未实现 |
| final summary 事实约束 | 最终报告契约待定 | Phase 7 | 未实现 |

## 13. 文档同步检查

每次变更检查：

- [ ] 是否改变概念、职责、控制流或状态语义？先更新 ARCHITECTURE。
- [ ] 是否改变跨模块字段、类型或组合约束？更新 CONTRACTS 和 contract tests。
- [ ] 是否开始/完成阶段或暴露缺口？更新 DEVELOPMENT_PLAN。
- [ ] README 中的当前阶段、已实现边界和文档入口是否需要同步？
- [ ] 是否作出难以逆转的决定？新增/更新 ADR。
- [ ] 是否清楚区分“目标”“当前实现”“已知缺口”？
- [ ] 是否在多个文档重复定义了同一事实？删掉副本并改为引用。
- [ ] 对应代码与测试是否在同一变更中？

## 14. 实施记录

| 日期 | 阶段 | commit/状态 | 证据 | 备注 |
|---|---|---|---|---|
| 2026-08-26 | Phase 0 | `a7cb70a`, `5552c42` | 文档与仓库基线 | 已推送 |
| 2026-08-26 | Phase 1 | `bd80aca` | 22 contract tests | contracts 0.1.0 |
| 2026-08-26 | Phase 2 | `deaa126` | 14 runtime tests | runtime 0.1.0 |
| 2026-08-26 | Phase 3 v0.1.0 | `d4e23b0` | 当时全仓 50 tests | 后因文档/语义冲突重开 |
| 2026-08-26 | Phase 3 对齐与收尾 | completed | 全仓测试、Conda package check、文档交叉检查 | 控制面边界、questions、Artifact 传播、payload 策略和 finish gate 已对齐 |
| 2026-08-27 | Phase 4 hardening 与收尾 | `phase4/planning-adapters-mock-e2e` | 全仓测试、mock E2E、服务器真实短闭环 | Planning/adapters/resume/payload/Artifact 映射完成；记录 legacy code retry 例外 |
| 2026-08-27 | Phase 5 Coding Agent vNext | completed（未提交工作树） | 本地/服务器 92 tests；服务器真实闭环 4 Artifacts | 原生 Coding、shared workspace/process/Git/Artifact、legacy Coding adapter 删除 |
