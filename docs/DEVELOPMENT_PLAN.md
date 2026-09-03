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
| Phase 6 | Experiment Agent vNext | completed |
| Phase 7 | Scientific Agent vNext、科学控制循环与闭环 gate | completed（历史 schema 2.0 主链） |
| Stabilization 3.0 | 按 ADR-0011 收敛控制面、契约、资源与生命周期 | completed（`d3c5560` 代码树；本地与服务器验收通过） |
| Stabilization 3.1 | 状态恢复边界：中断 Attempt、Scientific 首次 Session 与终态 gate 收敛（ADR-0012） | completed（`a9b1a5e`；本地 422 passed、服务器 clean-workdir 五场景验收通过） |
| Phase 8 | 按需高级能力 | not_started |

Phase 1—7 是历史实施记录。当前 production 只保留 schema 4.0 和唯一 Scientific 路径（`ResearchController` + 原生 `ScientificAgent` + `LLMWorkflowCompiler`）；schema 4.0 是 clean break，旧 3.0 state 不恢复。Stabilization 3.0 已完成本地全量测试、mock E2E 和服务器 clean-workdir 五场景验收。repair 连续通过 3 次；ask/resume 完成两次真实跨进程恢复；五场景均保留权限为目录 `0700`、文件 `0600` 的 full LLM trace。Stabilization 3.1 随后完成中断恢复收敛，并以 `2c2b56f` 合入输入边界、结果语义与 CLI 组合根的一致性修复。

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

contracts 包“代码已实现”不等于所有字段“运行语义已接通”。Phase 3 已强制控制面 capability 边界并拒绝带未解决 questions 的 Proposal。历史上曾把 success_criteria 求值留给后续阶段；ADR-0007/Phase 7 已改为在 schema 2.0 删除这套未使用的通用字段，改由 capability finalizer 验证领域完成证据。

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
- [x] Phase 3 当时裁定 success_criteria 保留可执行语义；该历史决定在 Phase 7 设计中被 ADR-0007 后续裁定取代：schema 2.0 删除未运行的通用 SuccessCriterion/evidence_key，完成证据归各 capability finalizer；
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

本节是已完成 Phase 4 的历史实现记录，其中 Planning Port、scientific_analyze 和 ScientificConclusion 不代表 Phase 7 目标接口；目标架构与删除时序只以 §10、ARCHITECTURE 和 ADR-0007 为准。

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
- `code_modify` 要求 WorkspaceGrant 为 read_write；仓库经 `RepoMaterializer` 准备/复用，改动相对 Attempt 基线（`GitBaseline`）计算，不再要求全局 clean；
- 文件读取受 WorkspaceGrant 的 allowed/denied paths 限制，写入受 WorkspaceGrant 限制（`CodeModifyInput.allowed_paths` 已于 7.7 删除）；
- `.git` 与 `.resagent2` 是 runtime 保留目录，LLM 文件 Tool 不可访问；
- verification command 由 Coding Agent 根据项目实际自主选择，经 `VerificationCommandPolicy` 约束（7.7 起，取代调用方预声明 `CodeModifyInput.verification_commands`）；
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

### 已知后续工作（Phase 5 遗留，非阻断）

- 边界违反（保留目录/越界路径）被映射为不可重试失败，而 FileNotFoundError 可重试，健壮性不对称；
- `read_paths` 记录原始参数、`search_text` 记录规范化路径，`code_understand` 的证据匹配对等价拼写敏感；
- `resolve_write_file` / `resolve_system_write` 存在 check-then-use 的 TOCTOU 窗口；
- 进程树终止不完整：daemonize 子进程可逃过 killpg、Windows 只杀直接子进程、killpg 有 ProcessLookupError 竞态；
- `VerificationResult` 不记录「验证期间工作区被改动」维度，审计痕迹有损；
- real_e2e 的 code 验证命令对基线恒真，未真正确认 docstring 目标达成；
- real_e2e 判据未强制检查 `code_patch` Artifact（只查 code_change/experiment_result/scientific_decision）；
- wire schema 版本判定已在 Phase 6 落地为 1.1（见 §9 与 ADR-0005）；
- `CodeModifyResult.changed_files` 与 code_change ArtifactCandidate 双写同一事实；
- contracts 非法组合分支（changed/deleted 重叠、空 verification 等）测试覆盖不足。

## 9. Phase 6：Experiment Agent vNext

### 目标

复用 Phase 5 的 filesystem/process/Git，实现原生 Experiment Agent，替换 LegacyExperimentAdapter；同时把克隆、环境、数据集缓存抽成 runtime 可复用组件，供 Coding 与 Experiment 共享。

### 设计决策（已确认）

- 实验 Agent 自己克隆仓库、自己创建虚拟环境；
- 环境按内容寻址复用：`env_id = resenv_<project_slug>_<sha256(repo identity + env spec)[:12]>`，内容不变则复用同一环境；暂不做 drift 检测（resolved fingerprint），留待后续；
- dataset cache 本阶段就做，与共享目录一起做成可配置 + 默认值；
- experiment confirmation 复用现有 ask_user 机制，开关放 `ExperimentRunInput.confirm_before_experiment`。

### 顺序

1. runtime 抽三个可复用组件：`RepoMaterializer` / `EnvironmentManager` / `HardwareAudit`；
2. contracts：扩展 `ExperimentRunInput`，新增 `ExperimentResult`；
3. 原生 Experiment Agent（`packages/agents/experiment/`）；
4. delivery validation 黄金用例 + 删除 LegacyExperimentAdapter。

### Runtime 新增组件（可复用）

- `RepoMaterializer`：按 `WorkspaceSpec`（GIT 克隆 / COPY 复制 / LOCAL 就地绑定 / GENERATED 空受管）产出 workspace；repo identity 用 (source + commit hash)，不依赖 basename；
- `EnvironmentManager`：读 env spec → 创建/绑定 conda env，按 `env_id` 内容寻址复用；
- 数据集引用：`DatasetRef`/`resolve_dataset_refs` 解析到 `dataset_root` 下，经通用 `RESAGENT2_DATASET_ROOT`/`RESAGENT2_DATASETS_JSON` 交给脚本（不混入模型/Hub 缓存）；
- `HardwareAudit`：`nvidia-smi` 等 GPU/硬件信息。

共享目录默认 `.resagent2/data/resources/`（下分 `envs/` 与 `datasets/`），可用 `RESAGENT2_RESOURCE_ROOT` 覆盖；`data_root` 用 `RESAGENT2_DATA_ROOT` 覆盖。

### Experiment 专有能力

- experiment confirmation：`confirm_before_experiment` 为真时先 `ask_user` 再跑实验；
- certification gate：`audit_env` 通过后才允许实验命令，由代码状态判定，不依赖 LLM 自报 stage；
- metrics/evidence finalizer：delivery check 判定 `expected_metrics`/`expected_artifacts` 是否全满足，缺则降级 `completed_with_warnings`，WarningRecord(code=`delivery_not_met`) 记录 `[NOT MET] Missing required X`；
- structured `ExperimentResult` payload。

### 完成标准

- [x] repo identity 不依赖 basename；
- [x] timeout 清理完整进程树（顺带修 Phase 5 遗留的 ProcessRunner 进程树 kill）；
- [x] command policy 不依赖 LLM 自报 stage；
- [x] completed 只绑定当前 Attempt 的合格 evidence；
- [x] 通过旧 reproagent 的 delivery validation 黄金用例；
- [x] Legacy Experiment Adapter 可删除。

### 收尾补强（2026-08-28）

- [x] Attempt 证据归属：finalizer 要求至少一次 experiment command 成功，evidence 文件相对本 Attempt 基线（workspace 文件 hash 快照）新建或内容变化，预存未变文件不登记为本 Attempt Artifact；
- [x] RepoMaterializer source identity：clone/copy 写运行时 metadata 文件（source type + 规范化 source），复用前校验一致，source 不匹配返回 `RepoMaterializerError` 而非静默复用；
- [x] 已知限制已记录：certification/confirmation 不是安全沙箱（`audit_env` 是正确性检查、`confirm_before_experiment` 只问正式命令、setup 命令可能有副作用、setup/experiment 是工作流分类），见 ARCHITECTURE §8.7 与 ADR-0005。

### 运行时整理：runtime / capabilities 拆分（2026-08-28）

Phase 6 把 Git/环境/数据集等能力抽进 runtime 后，runtime 内部三层（Agentic Loop / 物理执行边界 / provisioning）平铺、抽象层级不一致。按「runtime = Agent 怎么运行，capabilities = Agent 能做什么」重新划分：

- `runtime` 只保留运行引擎：Agentic Loop、Agent 状态、上下文、LLM、Session、Tool 接口与 finish/ask_user；
- 新建 `capabilities` 包承接具体能力：workspace / process / artifacts / git / repo / environment / dataset / hardware / workspace_tools（Phase 7 再加 literature）。

代码依赖分成两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`；composition root（CLI/E2E）负责把具体 Agent 注入 orchestrator。runtime 不依赖 capabilities，capabilities 不依赖任何具体 Agent；各 Agent 只通过 Tool Profile 装配自己需要的能力，依赖 capabilities 不等于自动获得全部能力。

## 10. Phase 7：Scientific Agent vNext、科学控制循环与闭环 gate

### 10.1 本阶段解决什么

Phase 7 不只是“把 `LegacyScientificAnalyzeAdapter` 换成原生 Agent”。它要把顶层控制流从“一张任务图执行完就结束”改成真正的研究闭环：

```text
自然语言目标
  → Scientific Agent 形成当前科学判断
  → 证据不足时提出 WorkRequest
  → ResAgent 编译、校验和执行 Workflow
  → WorkOutcome/Artifact 返回同一 Scientific Session
  → Scientific Agent 更新判断并继续
  → ScientificOpinion 通过 gate 后结束
```

ADR-0007 已裁定：Scientific Agent 只负责科学判断；WorkflowProposal/Patch 由 ResAgent 内部 WorkflowCompiler 生成。总控模块允许修改，不把 Phase 6 的 `create_run(request, proposal)` 和 `_evaluate_run` 当作冻结接口。

### 10.2 已裁定的设计，不再留给实现临时决定

1. Scientific Agent 只有一套可恢复 Agentic Loop，不提供 plan/analyze 等公开模式；
2. 对外输入始终是目标 + 当前状态 + 授权证据 + 新 WorkOutcome/回答；
3. Scientific Agent 对外控制结果只有 request_work、needs_user_input、completed、failed；
4. request_work 必须包含当前 ScientificAssessment 和语义化 WorkRequestDraft；
5. WorkRequest 不含 capability、依赖、路径、环境、状态或重试字段；
6. WorkflowCompiler 属于 ResAgent，可使用一次结构化 LLM 调用，但无 Session、无 Tool、无科学结论；
7. Workflow 仍由 deterministic validator/scheduler 执行；
8. literature_search 是 Scientific Tool，不是 WorkflowTask；Phase 7 首个 backend 使用 arXiv 元数据/摘要检索，保留一个小型 backend Protocol，但不同时建设多源聚合、自动 fallback 或全文 PDF 管线；
9. schema 1.1 的通用 success_criteria/evidence_key 在 schema 2.0 删除，不再开发万能求值器；Coding/Experiment/Scientific 各自由强类型 finalizer 验证领域完成证据；
10. `inconclusive` 是合法 ScientificVerdict，可对应 completed Run；运行 failed 表示没有形成可靠闭环；
11. 一个 Run 同时只允许一个 active WorkRequest，先做串行闭环；
12. 新旧 Scientific 生产路径在最终 composition root 中不能共存；
13. 顶层模块叫 Research Orchestrator/ResAgent，内部控制组件叫 ResearchController，Scientific Agent 边界只叫 ScientificPort；
14. ScientificCompletionValidator 只验证结构、provenance 和闭环状态，不宣称验证科学观点真假；
15. Phase 7 是未发布的原子迁移单元，每个阶段退出点必须可导入、全仓测试可运行。

### 10.3 明确不在 Phase 7 范围

- 多科学 Agent 辩论、角色人格、投票或树搜索；
- 并行 WorkRequest、多 Workflow 并发或分布式 scheduler；
- 长期 Conversation 产品、通用聊天 UI；
- 多论文平台聚合、推荐排序研究、全文下载/解析、引文图；
- 自动选择任意第三方插件；
- OS 沙箱、云端 durable execution；
- 为 schema 1.1 RunStore 快照提供通用原地迁移框架；
- 同时重构已经稳定的 Coding/Experiment 内部 Agentic Loop。

如果上述能力出现真实需求，进入 Phase 8 单独立项，不能在 Phase 7 审查中顺手扩 scope。

### 10.4 实施顺序

#### 7.0 文档与 ADR（当前工作）

- [x] ADR-0007 裁定科学判断与任务图编译的所有权；
- [x] ARCHITECTURE 写明目标闭环、失败语义、状态和迁移边界；
- [x] CONTRACTS 同时保留当前 schema 1.1 事实和 schema 2.0 目标，不能混写；
- [x] 本计划给出阶段顺序、测试和删除项；
- [x] README 与包级 README 同步派生摘要。
- [x] success_criteria 删除、completion validator、WorkRequest 状态和 schema 2.0 原子切换已获得 ADR/Architecture/Contract 锚点。

验收：全文搜索不存在“Scientific Agent 在 Phase 7 直接输出 Proposal/Patch”“literature_search 是目标 WorkflowTask”“success_criteria 求值器仍待决定”等并存说法。

#### 7.1 schema 2.0 基础与 success_criteria 原子清理

本小节结束时必须保持全仓可导入、测试可运行；禁止先删 contract symbol，再留给 7.2/7.7 修消费者。

在同一个原子变更中：

1. 将 `SCHEMA_VERSION` 切到 `"2.0"`，新增 WorkRequestId、WorkRequestStatus、ScientificAssessment、WorkRequestDraft/WorkRequest、WorkTaskOutcome/WorkOutcome、ScientificOpinion、ScientificTurnRequest/ScientificTurnResult，以及 ArtifactRef 的 Task/Attempt、Scientific Session、Orchestrator 三种 provenance 和 work_request_id traceability；
2. 删除 `SuccessCriterion`、`VerificationMode`、TaskProposal/WorkflowTask.success_criteria 和 WorkflowProposal.questions；同时把 `scientific_rationale` 改为 `compilation_rationale`、`Workflow.created_from` 改为 WorkRequestId，并同步修改 planning.py、scheduler.py、create_run 的旧 questions 分支和全部 fixture/tests；
3. `ResearchRun` 的 Phase 7 字段（§20.10.1）、`ScientificCompletionValidator`/`FinalReportData`（§20.10.2）和 `ScientificPort` 协议（§20.7）都是 orchestrator 内部模型，随 7.5/7.6 落地，7.1 只做 contracts 公共数据契约；
4. 暂时保留并标为 deprecated：ScientificPlanInput、ScientificAnalyzeInput、LiteratureSearchInput、ExperimentPrepareInput、AskUserInput、ScientificConclusion，以及旧 scientific/ask_user/experiment_prepare capability。它们只服务当前唯一旧 production 路径，7.7 原子切换时删除。

旧 PlanningPort 在该未发布迁移期为 Proposal/Task 填入 Run 内固定的 `work_legacy_initial`，repair fixture 使用显式 `work_legacy_repair_<n>`；Scheduler 不再从 summary/reason 生成 Workflow.created_from。legacy ID 只保证旧测试路径可运行，不创建虚假的 WorkRequest，也不得进入新 ResearchController。

2.0 在 7.7 完成前是未发布开发版本，不对外承诺临时 deprecated symbol。

测试：

- 每个新模型 round-trip；
- discriminated union 每种合法结果和互斥字段负例；
- WorkRequest 禁止执行字段，并验证合法状态转换、字段组合、active 唯一性和 stable→consumed 幂等；
- supports/refutes 无 evidence 非法，inconclusive/not_applicable 空 evidence 合法；
- WorkOutcome 重复/未知 Task、未知 Artifact、status/error 非法组合被拒绝；
- ArtifactRef 的 task/attempt/session/orchestrator provenance 非法组合被拒绝；
- Proposal/Patch/Task 缺失或错绑 work_request_id 被拒绝；
- WorkflowProposal 要求 compilation_rationale，拒绝 scientific_rationale/questions，Workflow.created_from 只接受 WorkRequestId；
- schema 1.1 wire/JsonRunStore 被 2.0 loader 明确拒绝；
- planning.py、scheduler.py、全仓 fixtures 不再引用 SuccessCriterion/VerificationMode/success_criteria，不再把 summary/reason 写入 Workflow.created_from；
- contracts 包依然不依赖 runtime/orchestrator/agents。

退出条件：contracts 与全仓测试全绿，所有 production import 可解析；CONTRACTS §20 与 models.py/exports 对已实现部分一致；旧 production Scientific 路径仍是唯一启用路径。

**状态：已完成（2026-08-28）。** `SCHEMA_VERSION="2.0"`；新 2.0 数据契约（WorkRequestId/WorkRequestStatus/ScientificAssessment/WorkRequestDraft/WorkRequest/WorkTaskOutcome/WorkOutcome/ScientificOpinion/ScientificTurnRequest/ScientificTurnResult）已落地 `models.py` 并导出；`SuccessCriterion`/`VerificationMode`/`success_criteria`/`WorkflowProposal.questions` 已删除，`scientific_rationale` 改名 `compilation_rationale`；`ArtifactRef` 改为 task/attempt、session、orchestrator 三态 provenance；Proposal/Patch/Task/Workflow 加 `work_request_id`。旧 scientific/planning 类型标 deprecated 保留；`DeterministicPlanningPort` 填 `work_legacy_initial`。本地 165 tests 通过，`git diff --check` 干净。

#### 7.2 WorkflowCompiler

新增最小 Port：输入 WorkRequest、CapabilityRegistry、Run 约束和当前 Workflow 摘要；输出 WorkflowProposal 或 WorkflowPatch。

实现两种注入对象：

- `LLMWorkflowCompiler`：production 用的一次结构化调用；
- `DeterministicWorkflowCompiler`：测试 fixture，不承担 production 规划语义。

Compiler 只翻译，不持久化、不执行 Tool、不调用 Agent、不修改状态。Validator 负责 DAG、capability、预算、revision、pending-only patch 和 work_request_id。

测试至少覆盖：初始 proposal、已有图 patch、未知 capability、循环依赖、超预算、错误 revision、修改 running/completed Task、编译器返回非法 JSON。测试必须证明同一 WorkRequest 可追溯到 Workflow.created_from。

退出条件：新 Compiler Port 的测试路径完整；PlanningPort/DeterministicPlanningPort 继续仅服务旧 production 路径，到 7.7 一次性删除，不能同时接入 production composition root。

**状态：已完成（2026-08-28）。** `WorkflowCompiler` Protocol + `DeterministicWorkflowCompiler`（测试 fixture）+ `LLMWorkflowCompiler`（注入本地 `CompilerLLM`，一次结构化调用，`model_validate` 后强制把 work_request_id 绑定到 `request.id`，保证 traceability 不依赖 LLM 返回值）落地 `orchestrator/compiler.py`。orchestrator 不 import runtime，`CompilerLLM` 由 composition root 适配。测试覆盖 initial proposal、已有图 patch、缺 patch、非法 JSON、循环依赖、超预算，以及 WorkRequest→Workflow.created_from 追溯。未接 production composition root。本地 173 tests 通过。

**后续收敛（2026-08-29，ADR-0010）。** 场景 3 repair 暴露：让 LLM 直接输出完整 Proposal/Patch 会诱导空图和跨 WorkRequest 依赖。据此把 production Compiler 改为“语义草图 + 确定性物化 + 一次纠错重编译”：LLM 只输出内部 `CompilationDraft`（局部 `key`/`depends_on`/`capability`/`inputs`，不进 contracts）；确定性 `_materialize_draft` 分配全局 TaskId、绑定 `work_request_id`、解析 workspace、转换局部依赖并产出只追加 Patch；validator 拒绝时携带精确原因最多重编译一次。改动范围仅 `orchestrator/compiler.py` + 两处测试文件（test_compiler.py、test_repair_flow.py）+ 四份文档（ARCHITECTURE/CONTRACTS/DEVELOPMENT_PLAN/decisions README）+ 新增 ADR-0010。本地 319 tests 通过。

#### 7.3 literature capability

在 `packages/capabilities` 增加小型 `LiteratureSearchBackend` Protocol 和 arXiv 实现，只提供 Scientific Agent 真正需要的字段：paper id、title、authors、published_at、abstract、source_url。

边界：

- query、max_results、可选时间范围有 schema 和上限；
- 请求 timeout、有限重试、指数退避和清晰错误；
- 不把 API key/网络异常写成空结果；
- 原始响应不直接进入 prompt，先规范化、截断和去重；
- 每次成功检索先规范化结果，经注入的 registration port 交给 ResAgent Artifact Registry 冻结，再把 ArtifactRef 返回 Scientific Agent；
- backend 由 composition root 注入，Scientific Agent 不 import arXiv SDK 细节。

测试使用 fake HTTP/backend；只做一个受环境变量控制的非默认网络 smoke test，避免 CI 依赖外部服务。

退出条件：Scientific Tool 可在无网络 fake backend 下确定性测试；真实 smoke test 能处理限流/超时而不伪造成功；检索结果带 session provenance 且 Tool 自己不能分配 ArtifactId/hash。

**状态：已完成（2026-08-28）。** `capabilities/literature.py` 落地 `LiteratureSearchBackend` Protocol + `ArxivLiteratureBackend`（stdlib urllib + defusedxml，https，timeout/有限重试/指数退避，规范化/去重/截断，网络异常抛 `LiteratureSearchError` 不伪装成空结果）+ `LiteraturePaper` + `ArtifactRegistrationPort` Protocol + `LiteratureSearchTool`（从 `AgentState` 取 run_id/session_id 做 provenance，Tool 不分配 ArtifactId/hash）。backend 由 composition root 注入。新增依赖 `defusedxml`（安全解析不可信 XML）。本地 181 tests + 1 opt-in smoke skip 通过。

#### 7.4 Scientific Agent vNext

复用现有 AgentLoop，新增一个 Scientific definition/profile，不复制 loop。最小 Tool 集：

- `read_artifact`：只读 allowlist；
- `literature_search`：调用注入的 backend；
- `request_work`：验证 ScientificAssessment + WorkRequestDraft 并暂停 Session；
- `ask_user`：复用现有问题机制；
- `finish`：验证 ScientificOpinion 候选。

Context 固定分区：ResearchRequest、当前 Run 摘要、authorized Artifact 清单、最新 WorkOutcome/回答、已通过 Tool 观察的证据摘要、预算。Prompt 只描述科学职责和证据纪律，不塞 capability 名和任务图格式。

Scientific finalizer 必须：

- 交叉检查 evidence_artifact_ids 与 Tool observation history；
- 由代码派生并返回整个 Session 累计的 observed_artifact_ids，LLM action 不能填写该字段；
- 根据 unresolved_task_outcomes 要求 opinion 提供 limitations；精确 Task 对账留在 Validator；
- 保留 limitations/unresolved_questions；
- request_work 时确保 assessment 和 expected_evidence 非空；
- finish 时执行 verdict/evidence 组合约束；
- 预算耗尽、工具错误和契约错误返回结构化 failed，不用 summary 掩盖。

测试覆盖：已有证据直接 finish、先检索再 finish、request_work pause、WorkOutcome resume、ask-user resume、越权 Artifact、伪造 evidence id、step/LLM budget exhaustion。

退出条件：Scientific Agent 可在不认识 WorkflowProposal/TaskProposal 的情况下通过唯一 ScientificPort 完成 scripted loop；新 Port 只在 tests/composition fixture 装配，production 仍未双路由。

**状态：已完成（2026-08-28）。** 新建 `packages/agents/scientific`，`ScientificAgent` 实现 ScientificPort（`run(ScientificTurnRequest) -> ScientificTurnResult` 四态）。复用 AgentLoop：放宽 `AgentState.task_id/attempt_number` 为可选、`AgentLoop.run` 的 request 参数放宽为 `LoopRequest` Protocol（ContextBuilder/PermissionPolicy 的 request 参数放宽为 `Any`）、`ToolObservation`/`ModuleResult`/`ModuleStatus` 加 `request_work` 暂停通道。Tool 集：read_artifact（allowlist）+ literature_search（注入 backend/registration port）+ request_work + ask_user（带 assessment）+ finish（ScientificFinish）。finalizer 交叉检查 evidence、派生 observed_artifact_ids；failed/blocked Task 的精确对账由 Validator 直接从 Run 完成，Scientific 通过 limitations 表达其影响。修一个 7.1 落地 bug：`ScientificTurnRequest` 首次调用不应禁止 `unresolved_task_outcomes`（契约 §20.6 只禁 work_outcome/answers）。本地 190 tests 通过。未接 production composition root。

#### 7.5 Research Controller 与 Run 生命周期

在 orchestrator 增加轻量 `ResearchController`，只编排已有组件，不把 Scheduler 重写成巨型类：

1. `create_run(request)` 创建 running Run 和 Scientific Session；
2. 处理 ScientificTurnResult；
3. request_work 时持久化唯一 active WorkRequest；
4. 调用 Compiler + Validator，交给现有 Scheduler 执行；
5. 图稳定后生成 WorkOutcome 并恢复同一 Scientific Session；
6. needs_user_input 时复用 PendingQuestion/UserAnswer；
7. completed 时调用 scientific gate；
8. fatal error/预算耗尽时才进入 Run failed。

ScientificTurnResult 到 RunStatus 的唯一映射采用 CONTRACTS §20.11；不得在 Controller 中新增另一套隐式状态规则。

ResearchRun 内部字段以 CONTRACTS §20.10.1 为准。work_requests 列表是事实源，active WorkRequest 由 status 派生；ResearchController 负责 requested→compiling→executing→stable→consumed，使用 work_request_id 幂等恢复 Scientific Session。runtime SessionStore 拥有原始 observation history，ResearchRun 只保存 Registry 复核后的 scientific_observed_artifact_ids 并集，不复制 Session 私有内容。

调整 `_evaluate_run`：它只判断一次执行图是否稳定并生成 WorkOutcome，不再因为一个 required Task 终止就直接结束整个 ResearchRun。Scheduler 的 Task/Attempt 映射保持不变。

测试覆盖：零任务直接结论、一个 work cycle、多个串行 work cycle、Task failure 后请求替代工作、paused/restart/resume、Compiler/contract fatal error、总预算耗尽。

退出条件：JsonRunStore 重启后可从 ResearchRun 边界恢复；不要求从一个正在执行的子进程中间恢复。

**状态：已完成（2026-08-28）。** 新增 `orchestrator/controller.py`：`ScientificPort` Protocol + `ResearchController`（编排 ScientificPort + WorkflowCompiler + WorkflowScheduler）。`create_run(request)` 自然语言入口；ScientificTurnResult 四态按 §20.11 映射；WorkRequest 状态机 requested→compiling→executing→stable→consumed；compiler→accept_proposal/apply_patch→scheduler 执行→`_evaluate_run` 图稳定生成 WorkOutcome→resume 同一 Scientific Session（work_request_id 幂等）；ask_user 复用 PendingQuestion/UserAnswer；completed 调用 `ScientificGate`（7.5 用 `_MinimalGate` 占位，7.6 替换为 ScientificCompletionValidator）。`ResearchRun` 加 §20.10.1 六字段，`workflow` 改 Optional。`_evaluate_run` 兼容旧路径：有 active WorkRequest 时图稳定生成 WorkOutcome 保持 running，无则旧完成语义。本地 207 tests 通过。文献真实冻结 adapter（register_scientific）留待 7.7 接 composition root。

#### 7.6 scientific finish gate 与 final report

实现 `ScientificCompletionValidator`，输入同一个不可变 ResearchRun snapshot 和 ScientificCompletedResult；`run.artifacts` 作为 Artifact Registry 已持久化的只读索引，构造时注入 CapabilityRegistry 复核 owner。它逐条实现 ARCHITECTURE §13 的七项 gate：

1. completed result/session/run 绑定合法；
2. 无 active WorkRequest、running Task、PendingQuestion；
3. opinion evidence 属于本 Run、Registry 可查，并同时存在于 result 和 Run 的 observed trace；
4. opinion 的 statement、verdict、evidence、limitations、unresolved_questions 字段组合合法；
5. final report 只从 typed Run state、ArtifactRef 和 ScientificOpinion 渲染；
6. completed Task 的最后 Attempt 状态/error/Artifact producer 与 Scheduler 的合法 ModuleResult 映射一致；gate 不重跑领域 finalizer，也不从 summary 推断；
7. 所有 failed/blocked Task 都由 Validator 从 Run 对账并进入 final report，且 limitations 非空。

Validator 不判断科学观点真假或证据语义是否充分。ScientificPort completion check 应先验证同一 snapshot；ResAgent 复核失败属于 contract_error，不得把 invalid candidate 写成 completed。

通过时 Validator 构造 CONTRACTS §20.10.2 的 FinalReportData。final report 使用只接受该模型的确定性 renderer，不再调用 LLM 二次“润色”。Validator 通过后 renderer 才运行；报告以 kind=final_report、media_type=text/markdown、orchestrator/final_report provenance 登记为 Artifact，final_opinion、final_report_artifact_id 全部持久化成功后才能写 Run completed。

测试覆盖 supports、refutes、inconclusive/not_applicable；缺 evidence、跨 Run Artifact、未观察 Artifact、伪造 observed trace、active work、未确认 failed/blocked Task、completed Task 无合法 finalizer result 均拒绝完成。

**状态：已完成（2026-08-28，未提交）。** 新增 `orchestrator/completion.py`：ScientificCompletionValidator 按 CONTRACTS §20.10.2 固定顺序验证 Session/control state/opinion/evidence/failed Task/completed Attempt/ID，输出结构化 violations 或 FinalReportData；失败 violations 持久化在 ResearchRun，FinalReportRenderer 只消费 FinalReportData，确定性生成 Markdown ArtifactCandidate。ArtifactRegistry 新增同内容幂等、原子落盘的 orchestrator final report 登记；ResearchController 默认替换 `_MinimalGate`，只有报告 Artifact、final_opinion 和 final_report_artifact_id 全部形成后才写 completed。同时补齐 7.5 的“workflow 已接受、WorkRequest 仍停在 compiling”恢复窗口和 compiled workflow 拒绝处理。全仓 230 passed、1 skipped；尚未接 production，7.7 真实 E2E 未执行。

#### 7.7 切换、删除和真实 E2E

7.7 是 schema 2.0 的原子发布切换：同一个变更先切 production composition root，再删除旧入口和所有 deprecated symbol，更新调用者/tests/E2E；变更结束前不得发布或合并部分状态。

**状态：切换与删除已完成（2026-08-28）。** 已删除 PlanningPort/`DeterministicPlanningPort`、`LegacyScientificAnalyzeAdapter` 及 `ScientificPlanInput`/`ScientificAnalyzeInput`/`LiteratureSearchInput`/`ExperimentPrepareInput`/`AskUserInput`/`ScientificConclusion` 与旧 `scientific_plan`/`scientific_analyze`/`literature_search`/`experiment_prepare`/`ask_user` capability enum 值；`CapabilityInput` 收缩为 `CodeUnderstandInput`/`CodeModifyInput`/`ExperimentRunInput`。`mock_e2e.py` 与 `real_e2e.py` 重写为唯一 Scientific 路径（`ResearchController` + 原生 `ScientificAgent` + `WorkflowCompiler`），`real_e2e.py` 新增 `_CompilerClient` 把 runtime client 适配到 orchestrator `CompilerLLM`，并注入 `ArxivLiteratureBackend` + `_ScientificArtifactRegistration`（把 Scientific Tool 登记的 artifact 写回 run 索引）+ `JsonSessionStore`（跨进程恢复 Scientific Session），`ArtifactRegistry` 新增 `register_scientific`。受影响的 scheduler 测试把 `scientific_analyze` 下游节点改写为 `code_understand`。全仓本地测试通过，mock E2E 一条命令跑通；服务器真实 E2E 未执行。

切换时删除：

- PlanningPort / DeterministicPlanningPort；
- `LegacyScientificAnalyzeAdapter` 及其旧 ExpAgent 路径；
- 旧 scientific capability binding/payload translation；
- 7.1 暂留的 ScientificPlanInput、ScientificAnalyzeInput、LiteratureSearchInput、ExperimentPrepareInput、AskUserInput、ScientificConclusion 和旧 capability enum value；
- 只服务旧路径的测试和文档。

`experiment_prepare` 的删除依据 ADR-0007 §8：原生 Experiment Agent 已把准备/审计纳入 experiment_run，且旧 capability 没有 production binding，不保留第二入口。

保留的核心：WorkflowProposal/Patch、Validator、Scheduler、ModulePort、ArtifactRegistry、Coding/Experiment vNext。

真实 E2E 至少包含：

1. goal → direct inconclusive/answer（无需执行图）；
2. goal → code_modify → experiment_run → evidence → final opinion；
3. 实验失败 → WorkOutcome → Scientific 请求修复/替代实验 → 最终 opinion；
4. Scientific ask_user → 进程退出 → answer/resume；
5. literature search → literature Artifact → opinion 引用。

服务器验收必须保存 Run JSON、Scientific Session、每个 Workflow revision、WorkRequest/WorkOutcome、冻结 Artifact 和最终报告；命令退出码由直接 shell/script 捕获，不使用会吞 `$?` 的嵌套引号验证。

### 10.5 Phase 7 总完成标准

- [x] schema 2.0 代码/导出/文档/测试一致；
- [x] Scientific Agent 只有一套 Agentic Loop 和一个 Port；
- [x] Scientific Agent 不输出 capability/task/path/env/status 等执行字段；
- [x] WorkflowCompiler 不形成科学结论，所有输出经过统一 validator；
- [x] 自然语言 ResearchRequest 可在无预建 Workflow 时启动 Run；
- [x] WorkRequest → Workflow → WorkOutcome → 同一 Scientific Session 的闭环可恢复；
- [x] Task 失败不会被掩盖，也不会在仍可科学恢复时过早结束 Run；
- [x] literature 是可注入 capability，限流/超时不会伪装为空结果；
- [x] ScientificOpinion 只引用已授权、已通过 trusted Tool observation、已登记 Artifact；
- [x] inconclusive 与 failed 的语义和测试分离；
- [x] final report 是确定性渲染且只引用 typed facts；
- [x] PlanningPort、`LegacyScientificAnalyzeAdapter` 和旧 scientific task capability 删除；
- [x] production composition root 只有一条 Scientific 路径；
- [x] ModuleBinding.owner 与 CapabilityRegistry.definitions[capability].owner 同源（否则 completed Task 被 ScientificCompletionValidator 误判 inconsistent_task_result，见 CONTRACTS §20.10.2 owner 单一来源约束）；
- [x] 全仓测试、mock E2E、服务器真实 E2E、`git diff --check` 通过；（五场景 E2E 全部通过：1 direct / 2 code-experiment / 3 repair / 4 ask-resume / 5 literature。本地 347 tests。含：Compiler 语义审查 + Coding/Experiment 控制状态；Scientific 证据闭环 + Runtime 可恢复 schema 校验；`WorkflowTask` 自身约束（Scheduler 不再广播 ResearchRequest 级约束）；最小 LLM JSONL trace——off/metadata/full 三档、目录 0700/文件 0600、metadata 只记 hash/tool/valid 不记内容、保留坏 JSON 原始响应、call_id/created_at 关联）
- [x] ARCHITECTURE、CONTRACTS、DEVELOPMENT_PLAN、README 和包级 README 同步。

### 10.6 Phase 7.7 Hardening：工作区、CodingAgent 与路径管理

解决真实 E2E 暴露的根因：Compiler 不应决定代码文件/验证命令，CodingAgent 应自主处理代码细节；一个 Run 可有多个工作区，Coding 与 Experiment 复用同一 `workspace_id`；Run 产物与共享缓存分离；不在源码仓库写内部运行文件。详见 ADR-0008 与 CONTRACTS §21。

实施顺序（每步测试通过）：

1. 新增 ADR，明确 Compiler/CodingAgent/工作区/缓存职责；
2. 同步 ARCHITECTURE、CONTRACTS、DEVELOPMENT_PLAN；
3. 改 workspace contracts（WorkspaceSourceKind/WorkspaceSpec/WorkspaceRecord，Task 加 workspace_id）；
4. 增 RunLayout/ResourceLayout；
5. 改 RepoMaterializer 元数据位置（写入 run 目录，不污染仓库）；
6. ResearchRun 加工作区注册表；
7. Task 加 workspace_id；
8. 改 Scheduler 工作区解析（workspace_id→WorkspaceRecord→WorkspaceGrant）；
9. 简化 CodeModifyInput（删 allowed_paths/verification_commands）；
10. CodingAgent 直接复用 RepoMaterializer；
11. CodingAgent 动态验证 + 命令权限检查；
12. ExperimentAgent 用统一工作区（删 repo source 字段）；
13. 缩减 Compiler 输入与职责（workspace_id 校验，不输出文件/命令）；
14. 迁移 mock/real E2E；
15. 本地测试通过后再到服务器跑真实场景。

必测项：Git clone 后可改可验证；外部本地仓库不复制；copy 不污染源目录；同源 materialize 复用；来源不一致拒绝复用；一个 Run 两个 workspace_id 不串目录；Coding 改后 Experiment 同工作区可见；Attempt 产物隔离；脏工作区不自动重试；Compiler 编造 workspace_id 被拒；Compiler 输出不含文件/命令；Coding 自选验证命令；验证失败可继续修复；completion 拒绝不无限 finish；目标仓库不生成 `.resagent2/runs`；dataset_root 独立时不进 data_root。

明确不做：GitHub push/PR、自动 commit、每 Attempt 一个 Git worktree、自动回滚、跨 Run 仓库缓存、分布式工作区、通用 DAG 调度器、复杂 WorkspaceManager 基类体系。

## 11. Phase 8：稳定化与按需高级能力

只在有已复现需求时评估：长期 Conversation、Session 索引、并行 worker、Skill 原语、plugin 自动发现、云端 durable execution、多模型独立验证、镜像加速（国内网络下复用 pip/conda 下载缓存、减少重复下载）。

每项必须先有：

- 已复现需求；
- 简单方案不足的证据；
- ADR；
- 测试；
- 删除或回退方案。

## 12. 跨文档—代码追踪表

| 架构概念/约束 | contract/接口 | 实现阶段 | 当前状态 |
|---|---|---|---|
| 科学判断与任务图编译分离 | ScientificTurnResult、WorkRequest、WorkflowProposal/Patch | Phase 7 | 契约已落地（7.1）；WorkflowCompiler 已实现（7.2）；7.7 已接 production |
| 顶层唯一 WorkflowTask | TaskProposal、WorkflowTask | Phase 1/3 | 已实现 |
| 确定性调度 | ModuleTaskRequest/ModuleResult | Phase 3 | 核心已实现 |
| ask-user 是控制信号 | QuestionDraft/PendingQuestion/UserAnswer | Phase 3/4 | orchestrator + runtime resume 已接通 |
| retry/resume/repair 分离 | Attempt、SessionRef、WorkflowPatch | Phase 3/4 | 已实现：retry 新 Attempt、ask-user resume 复用并校验 Session、repair 使用 WorkflowPatch |
| Artifact 两道边界 | WorkspaceGrant、Candidate、Ref | Phase 3/5/6.5 | capabilities 访问检查与 orchestrator 登记复核均已实现 |
| 只传播成功 Attempt Artifact | Attempt.artifact_ids | Phase 3 | 已实现并测试 |
| 领域完成证据 | capability input/payload/finalizer | Phase 5-7 | Coding/Experiment/Scientific 已实现；schema 2.0 删除未使用的通用 success_criteria |
| ModuleResult payload | ModuleResult[PayloadT]、Attempt.payload | Phase 4/5-7 | Core 原样持久化但不解释；原生强类型模型与领域消费方在对应 Agent 阶段定义 |
| 科学控制闭环 | ScientificAssessment、WorkRequest、WorkOutcome、ScientificOpinion | Phase 7 | 契约已落地（7.1）；Scientific Agent 四态已实现（7.4）；7.7 已接 production |
| WorkRequest 生命周期 | WorkRequestStatus、work_request_id 幂等 | Phase 7 | 状态机已落地（7.1）；ResearchController 驱动已实现（7.5）；7.7 已接 production |
| Scientific evidence trace | ScientificTurnResult.observed_artifact_ids | Phase 7 | Session 派生 + RunStore 复核并集已实现（7.4/7.5） |
| final report 事实约束 | FinalReportData + ArtifactRef 的确定性 renderer | Phase 7 | 7.6 已实现并测试；7.7 已接 production |
| 统一工作区与 Coding 自主 | WorkspaceSourceKind、WorkspaceSpec、WorkspaceRecord、WorkspaceId、workspace_id、GitBaseline、VerificationCommandPolicy | Phase 7.7 | 已实现（`9543ab7`, `5d1746d` 起） |
| 恢复闭环（信息不丢） | previous_work_request、真实 summary/stderr_tail、Experiment prompt 规则 | Phase 7.7 hardening | 已实现：Scientific 重发 self-contained WorkRequest、Experiment 先读接口不臆造 CLI、按错误改命令 |
| 运行时反馈与连续失败保护 | ToolObservation.ok、runtime_feedback、recent_observations、连续失败上限 | Phase 7.7 hardening | 已实现：拒绝落 ok=False 持久反馈、有界最近历史 head+tail 截断、finish 由 completion check 判定、连续 5 次 TOOL_FAILED |
| 数据集两层资源模型 | DatasetRef、dataset_refs、RESAGENT2_DATASETS_JSON/RESAGENT2_DATASET_ROOT | Phase 7.7 hardening | 已实现：dataset_root 是公共根、DatasetRef 指向具体只读目录、通用 id→路径映射、重复 id 拒绝 |
| 环境中断恢复 | EnvironmentManager `.resagent2_base_ready` marker、删除重建 | Phase 7.7 hardening | 已实现：半成品基础环境确认在 env_root 内后删除重建，不静默复用 |
| 共享环境能力与 Agent 自主选型 | EnvironmentSpec、environment_spec、prepare_environment/run_setup/audit_env、EnvironmentBinding | Phase 7.7 hardening（ADR-0009） | 已实现：环境归 run_id+workspace_id、Agent 选 Python/依赖、系统 inspect/prepare/audit、Coding/Experiment 共用、Manager 不自动装依赖 |

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
| 2026-08-27 | Phase 6 Experiment Agent vNext | completed | 本地 146 tests；delivery 黄金用例 + 服务器真实闭环 | 原生 Experiment、provisioning 组件、内容寻址环境、schema 1.1、legacy Experiment adapter 删除、Attempt 证据归属、source identity |
| 2026-08-28 | Phase 7 架构/契约（7.0） | completed | ADR-0007、三份核心文档交叉检查 | Scientific 只作科学判断；WorkflowCompiler 归 ResAgent；schema 2.0 草案 |
| 2026-08-28 | Phase 7.1 schema 2.0 | `3653895` | 本地 165 tests | 新 2.0 类型、删 success_criteria、ArtifactRef 三态 provenance、work_request_id 追溯、SCHEMA_VERSION=2.0 |
| 2026-08-28 | Phase 7.2 WorkflowCompiler | `44f7cd8` | 本地 173 tests | WorkflowCompiler Protocol + Deterministic/LLM 实现，未接 production |
| 2026-08-28 | Phase 7.3 literature capability | `59265c6` | 本地 181 tests | ArxivLiteratureBackend + LiteratureSearchTool + ArtifactRegistrationPort |
| 2026-08-28 | Phase 7.4 Scientific Agent | `445bb6d` | 本地 190 tests | 原生 Scientific Agent 四态，复用 AgentLoop，未接 production |
| 2026-08-28 | Phase 7.1–7.4 hardening 收尾 | `5cbfaec` | 本地 200 tests | 修 7 条契约硬违约（assessment 证据、acknowledged 双向、幂等、patch 隔离、跨 run 拒绝、orchestrator provenance、控制信号互斥）+ 证据摘要 + prompt；负例测试 |
| 2026-08-28 | Phase 7.5 ResearchController | `a1562fe` | 本地 207 tests | 自然语言入口、WorkRequest 状态机、compiler→scheduler→WorkOutcome→resume、ScientificGate 占位、ResearchRun §20.10.1 字段 |
| 2026-08-28 | Phase 7.5 hardening 收尾 | `36c4d8b` | 本地 213 tests | 修 resume 幂等（work_outcome/answers 键）、consumed 时机、answers 只传新增、WorkOutcome 按 work_request_id 隔离、unresolved 从整个 workflow 派生、observed 复核、Run 总预算；补 6 类负例测试 |
| 2026-08-28 | Phase 7.5 hardening 收尾（二） | `f870bc3` | 本地 215 tests | JsonSessionStore 持久化 + 真实重启恢复、run_until_stable 按 WorkRequest 状态分派、预算 remaining + 事后复核、observed 复核失败即 failed；补真实重启/预算超限负例 |
| 2026-08-28 | Phase 7.6 finish gate + final report | `c9fc5f3` | 本地 230 passed、1 skipped | 结构化完成复核与 violation 持久化、typed deterministic report、orchestrator Artifact 登记、7.5 acceptance crash-window 恢复；未接 production |
| 2026-08-28 | Phase 7.7 原子切换 | 未提交 | 本地 227 passed、1 skipped | 删除 PlanningPort/DeterministicPlanningPort/LegacyScientificAnalyzeAdapter 及全部 deprecated 类型与 enum 值；production 切到唯一 Scientific 路径；e2e 重写；scheduler 测试下游节点改写 code_understand；服务器真实 E2E 未跑 |
| 2026-08-29 | Phase 7.7 Hardening（工作区/Coding 自主） | `9543ab7`, `5d1746d` | 本地 253 passed、1 skipped；mock E2E completed | 统一工作区（WorkspaceSourceKind/WorkspaceSpec/WorkspaceRecord + workspace_id）、Coding 自主验证命令、RepoMaterializer 元数据出仓、RunLayout/ResourceLayout 分离 Run 数据与共享缓存；服务器真实 E2E 未跑 |
| 2026-08-29 | Phase 7.7 recovery-loop hardening 收尾 | `0a57b6c`, `812c0aa` + 本地未 push | 本地 281 passed、1 skipped；mock E2E completed；服务器真实 E2E 场景 2 completed（baseline 0.4367 → candidate 0.5079，verdict=supports） | 修 3 条 P1（数据集通用绑定、运行时 ok=False/completion rejection 计数、环境中断恢复）+ 恢复闭环（previous_work_request + 最近历史/连续失败保护 + prompt 规则）；全新 workdir 场景 2 跑通 |
| 2026-08-29 | 共享环境能力改造（ADR-0009） | 本地未 push | 本地 287 passed、1 skipped；mock E2E completed；服务器真实 E2E 场景 2 completed（verdict=supports） | 环境改 run_id+workspace_id 绑定；EnvironmentSpec/environment_spec 取代 ExperimentRunInput.python_version；EnvironmentManager inspect/prepare/audit + PreparedEnvironment + EnvironmentBinding；三个共享工具 prepare_environment/run_setup/audit_env；Coding 接环境化 verification、Experiment 删私有环境逻辑；Manager 不自动装依赖；半成品删除重建 + `.resagent2_base_ready` |
| 2026-08-29 | Phase 7 五场景真实 E2E | `750b4d8` 起 + 本地未 push | 场景 1/2/4/5 通过；场景 3 repair 契约断点已修、真实 LLM 待收敛 | 1 direct inconclusive ✅；2 code-experiment ✅（work7，0.4237→0.5359）；4 ask-start/resume ✅（跨进程恢复）；5 literature ✅（修 budget_exhausted + 同 turn 动态 resolve 两个硬错误）；3 repair 修 Compiler 跨 WorkRequest supersede/update 契约断点 + 确定性测试覆盖（failed→新 WorkRequest→新增任务→旧失败保留→completed），真实 LLM 图生成仍抖动 |
| 2026-09-01 | Stabilization 3.0 完成 | `d3c5560`（被验收代码树） | 本地 378 passed、1 skipped；mock E2E completed；服务器 clean-workdir 五场景全部通过 | Compiler 一 WorkRequest 一当前可执行轮；LLM transport retry 纳入剩余 Run 预算；Coding/Experiment 共享有界 read-file context；repair 3 连过、ask/resume 两次跨进程通过；五场景 full trace 权限为目录 0700/文件 0600。其后仅同步验收文档。 |
| 2026-09-03 | schema 4.0 输入与证据闭环 | `a41b92a`（已合并 main） | 本地 456 passed、1 skipped | SCHEMA_VERSION=4.0（clean break，旧 3.0 state 不恢复）；`requested_fields`/`UserAnswer.values` 必填；`required_evidence_kinds` 仅 `literature_search`；Coding/Experiment 共享 `recent_tool_snippets`（path+行范围片段）、Experiment 额外保留有界目录清单；删除 `recent_tool_text_values`；补 LLM 坏 JSON 跨层恢复链测试。 |
