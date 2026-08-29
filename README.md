# ResAgent2

ResAgent2 是一个面向科研任务的、可审计的 Agent 工作流系统。

它不追求成为通用 AGI 框架。目标科研闭环是把科研过程组织成一条清晰、可暂停、可验证、可追踪的执行链：

```text
研究目标
  → 科学顾问形成当前科学判断
  → 证据不足时提出“还需要什么工作/证据”
  → ResAgent 把工作请求编译成任务图并调度
  → 程序员修改和验证代码
  → 实验员运行实验并冻结证据
  → 证据返回同一个科学会话，更新判断
  → 最终科学意见通过闭环 gate 后结束 Run
```

## 当前状态

**阶段：Phase 6 已完成；Phase 7 完成（7.1—7.7 代码路径已全部落地并通过本地测试；服务器真实 E2E 已跑通场景 2——CIFAR-10 baseline vs SE）。**

当前已实现 `resagent2-contracts` schema 2.0（`SCHEMA_VERSION="2.0"`）、共享 runtime/capabilities、确定性的 Workflow Core，以及原生 Coding/Experiment/Scientific Agent。Phase 5/6 已接通 workspace、无 shell process、Git、只读 Artifact、真实 LLM client、finalizer、provisioning 和内容寻址环境；真实执行不再依赖旧 CodingAgent 或旧 reproagent。

Phase 7 已落地：7.1 契约切 2.0（新增 ScientificAssessment/WorkRequest/WorkOutcome/ScientificOpinion/ScientificTurnResult 等科学控制类型，删除 SuccessCriterion/evidence_key，ArtifactRef 三态 provenance，work_request_id 追溯）；7.2 WorkflowCompiler；7.3 literature capability；7.4 原生 Scientific Agent（实现 ScientificPort 四态，复用 AgentLoop）；7.5 ResearchController（自然语言入口，编排 ScientificPort + WorkflowCompiler + WorkflowScheduler，WorkRequest 状态机）；7.6 ScientificCompletionValidator + 确定性 final report；7.7 原子切换——删除 PlanningPort/`DeterministicPlanningPort`/`LegacyScientificAnalyzeAdapter` 和全部 deprecated scientific/planning/ask_user/experiment_prepare 类型与 enum 值，production composition root 切到唯一 Scientific 路径（`ResearchController` + 原生 `ScientificAgent` + `LLMWorkflowCompiler`）。schema 2.0 现由唯一新路径装配；服务器真实 E2E 已跑通（场景 2）。

## 四个角色

| 角色 | 简单理解 | 负责 | 不负责 |
|---|---|---|---|
| ResAgent / Research Orchestrator | 神经系统/总管 | 自然语言入口、工作请求转任务图、状态、Attempt、Artifact、暂停恢复、完成判定 | 自己改代码、跑实验、形成科学观点 |
| Scientific Agent | 科学大脑 | 根据目标和证据形成当前判断，说明还缺什么，给出最终科学意见 | 输出 capability/依赖/路径/环境，直接调其他 Agent |
| Coding Agent | 程序员 | 阅读、修改、验证代码 | 判断实验是否支持假设 |
| Experiment Agent | 实验员/操作员 | 准备环境、运行实验、采集指标和证据 | 形成最终科学结论 |

## 一个共享 Agentic Loop

三个子 Agent 使用同一套循环：

```text
读取状态
  → 构建上下文
  → LLM 选择一个类型化动作
  → 校验动作与权限
  → 执行 Tool
  → 记录 Observation
  → 保存状态
  → 确定性检查是否完成
  → 下一轮
```

不同 Agent 只注入不同的：

- system prompt；
- tools；
- context sections；
- permission policy；
- action schema；
- result schema；
- completion check。

## 科学推理与确定性执行

Phase 7 目标中有两种不同的 LLM 工作，但状态仍由代码控制：

```text
Scientific Agent：开放地判断证据意味着什么，以及还需要什么证据。
WorkflowCompiler：有界地把 WorkRequest 翻译成 Proposal/Patch。
确定性代码：校验图、计算 ready Task、转换状态、登记 Artifact、执行 gate。
```

Scientific Agent 不需要 plan/analyze 等公开模式。它只有一套可恢复 Agentic Loop，对外通过 request_work、ask_user 或 finish 表达下一步；WorkflowCompiler 属于 ResAgent，不是第二个科学 Agent。

LLM 不能直接：

- 把 Task 标为 completed；
- 绕过 depends_on；
- 修改 Attempt 历史；
- 把 workspace 文件自动当作 Artifact；
- 在存在 PendingQuestion 时绕过暂停；
- 绕过 Artifact 登记阶段已实现的 workspace 边界、hash 和 provenance 检查；
- 绕过已经实现的 ScientificCompletionValidator 科学闭环复核。

ScientificOpinion/final-report 完成门已在 Phase 7.6 实现并由 ResearchController 新路径调用：它只验证状态、ID、Artifact provenance、observation trace 和失败任务确认，不判断科学观点真假；final report 只从通过验证的类型化事实确定性渲染。Phase 7.7 已删除旧 PlanningPort 路径，production composition root 只保留这一条 Scientific 路径；环境能力已改为 Coding/Experiment 共享的通用能力（ADR-0009：`prepare_environment`/`run_setup`/`audit_env`，环境归 `run_id + workspace_id`）；服务器真实 E2E 场景 2 已在全新 workdir 跑通（verdict=supports，SE 块提升测试精度）。

详细说明见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 仓库结构

```text
packages/
  contracts/                  跨包类型和接口语义
  runtime/                    共享 Agentic Loop（运行引擎）
  capabilities/               可装配的具体能力（Git/文件/进程/环境/仓库/数据集/硬件）
  orchestrator/               ResAgent 工作流与状态机
  agents/
    scientific/               科学顾问
    coding/                   程序员
    experiment/               实验员

docs/
  ARCHITECTURE.md              当前架构和目标组件
  CONTRACTS.md                模块接口与字段语义
  DEVELOPMENT_PLAN.md         当前唯一开发计划
  decisions/                  少量重要架构决策

tests/
  契约、运行底座、工作流和端到端测试
```

## 本地开发环境

项目使用名为 `ResAgent2` 的 Conda 环境：

```bash
conda env create -f environment.yml
conda activate ResAgent2
python -m pytest tests/contracts
```

环境已存在时使用 `conda env update -n ResAgent2 -f environment.yml --prune` 同步依赖。

逻辑模块保持独立，但暂时放在同一个 Git 仓库中：

- 每个模块有独立输入输出；
- 不读取其他模块内部 state；
- 不直接调用其他子 Agent；
- 只依赖 `contracts`、`runtime` 和需要的 `capabilities`；
- 可以独立测试；
- 以后确有独立用户和发布需求时再拆包或拆仓。

## 文档入口与权威关系

README 只是第一次进入项目的摘要，不是独立事实来源。发生冲突时按以下职责裁定：

1. [ARCHITECTURE.md](docs/ARCHITECTURE.md)：系统概念、职责、控制流和状态语义的最高级事实来源；
2. [CONTRACTS.md](docs/CONTRACTS.md)：跨模块字段、类型、组合约束和 wire 版本的唯一事实来源；
3. [DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)：阶段、状态和验收证据的唯一事实来源；
4. [decisions/](docs/decisions/)：难以逆转的架构决定及其理由；
5. 代码和测试：当前实现行为的最终证据。

README 必须从上述来源同步摘要，不得覆盖或重新定义它们。

代码与文档必须同步：改变公开行为的 commit 必须同时更新对应文档和测试。

## 开发纪律

- 没有进入 `DEVELOPMENT_PLAN.md` 当前阶段的功能不开发。
- 每个阶段先写接口、状态和验收测试，再写实现。
- 架构文档明确区分“目标”和“已实现”。
- 同一行为只保留一条生产主线。
- 新增共享抽象前，必须存在至少两个语义一致的真实使用者。
- 子 Agent 遇到跨模块需求时返回结构化结果，由 ResAgent 调度，禁止直接互调。
- 不在仓库保存 SSH 私钥、API key 或服务器凭据。

## 旧项目关系

ResAgent、ExpAgent、CodingAgent、reproagent 的旧仓库继续保留，作为：

- 已验证需求来源；
- 旧行为和真实实验样本；
- 可迁移实现来源；
- 回归测试来源；
- vNext 早期的 legacy adapter 调用对象。

旧代码不会整仓复制进来。每段迁移代码都必须重新确认职责、依赖、测试和文档归属。
