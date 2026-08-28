# ADR-0007：科学判断与任务图编译分离

**状态**：accepted

**日期**：2026-08-28

**取代**：ADR-0003 中“Scientific Agent 直接输出 WorkflowProposal / WorkflowPatch”的部分

## 背景

早期架构把 Scientific Agent 同时当作科学顾问和任务图生成器：它既要判断现有证据意味着什么，又要输出包含 capability、依赖关系和执行输入的 `WorkflowProposal` / `WorkflowPatch`。这会把两个变化速度和责任边界不同的问题耦合起来：

- 科学判断应以自然语言目标、证据和不确定性为中心；
- 任务图必须符合当前 capability、契约、预算和调度器限制；
- Coding / Experiment 能力发生变化时，不应迫使 Scientific Agent 学习新的执行字段；
- Scientific Agent 若直接拥有图，就容易把“建议做什么”与“系统具体怎样执行”混为一体。

用户希望系统以 LLM 的科学推理为核心，但不让固定工作流限制推理；同时保留任务图，供 ResAgent 做可恢复、可审计、确定性的执行。

## 决策

### 1. Scientific Agent 是科学大脑

Scientific Agent 只有一个长期职责：根据研究目标、当前状态和已登记证据形成科学判断。

它使用一套可恢复的 Agentic Loop，不暴露 `plan`、`analyze` 等互斥工作模式。输入始终是自然语言目标加结构化状态/证据；每次对外决策必须包含当前科学观点、证据引用、局限和未解决问题。

当证据已经足够时，它通过 `finish` 返回最终 `ScientificOpinion`。当证据不足时，它通过 `request_work` 返回当前科学判断和一个语义化 `WorkRequestDraft`。`request_work` 对 Scientific Agent 来说是一种可暂停、可恢复的异步工具调用。

### 2. ResAgent 是控制与执行神经系统

ResAgent 接受用户自然语言目标，持久化 ResearchRun，管理 Scientific Session、用户问题、工作请求、Workflow、Task/Attempt、Artifact 和最终完成判定。

ResAgent 内部的 `WorkflowCompiler` 把 `WorkRequest` 翻译成：

- 当前还没有可执行图时的 `WorkflowProposal`；或
- 已有执行历史时的 `WorkflowPatch`。

WorkflowCompiler 可以使用一次有界、结构化的 LLM 调用理解语义，但它是无状态编译器，不是第二个科学 Agent，也不形成科学结论。确定性 validator 仍负责 schema、DAG、capability、预算和修改边界。

### 3. 任务图是 ResAgent 的内部执行表示

Workflow 不再是 Scientific Agent 的公开产物。Scientific Agent 不输出：

- capability 名；
- task id、depends_on；
- executor / owner；
- workspace、path、env；
- retry、status 或 Attempt 字段。

Scientific Agent 只说明“为了推进目标，还需要得到什么证据、满足什么约束”。ResAgent 决定怎样把该请求落实为 Coding / Experiment 任务。

### 4. 一个研究 Run 是科学闭环，不是一张图的生命周期

目标控制流为：

```text
用户自然语言目标
  → Scientific Agent 形成当前科学判断
  → 证据不足：request_work
  → ResAgent 编译、校验并执行 Workflow
  → 冻结 Artifact，并把 WorkOutcome 返回同一 Scientific Session
  → Scientific Agent 更新判断
  → 重复，直到 finish 或需要用户输入
```

一次 Coding / Experiment Task 失败不自动等于整个 ResearchRun 失败。ResAgent 先把成功、失败、警告和证据组织成 `WorkOutcome` 返回 Scientific Agent；Scientific Agent 可以修改假设、请求替代工作或给出带局限的结论。只有预算耗尽、契约/系统不可恢复错误，或最终 gate 无法满足时，Run 才失败。

### 5. 文献检索是 Scientific Agent 的可装配能力

Phase 7 的文献检索是 Scientific Agent 直接使用的只读 Tool，放在 `capabilities`，不作为顶层 WorkflowTask。规范化检索结果经注入的 registration port 由 ResAgent Artifact Registry 以当前 Scientific Session provenance 冻结，Tool 只接收登记后的 ArtifactRef。只有将来出现确实需要长时间、独立预算和单独恢复的文献任务时，才考虑把它升级为 WorkRequest 所触发的执行任务。

### 6. 领域完成证据归 capability finalizer

schema 1.1 的 `SuccessCriterion` / `evidence_key` 只被保存，从未参与运行判断；它们与 Coding、Experiment 已经实现的强类型输入、payload 和 finalizer 重复。schema 2.0 删除 `SuccessCriterion`、`VerificationMode` 以及 TaskProposal/WorkflowTask 的 `success_criteria`，不再建设通用证据路径语言或中心求值器。

完成证据由产生领域结果的确定性 finalizer 验证：Coding 检查 Git diff 和 verification result；Experiment 检查实验命令、metrics 和 expected artifacts；Scientific completion check 检查 opinion、Artifact provenance 和执行问题确认。需要人工确认时使用 Question/Answer 控制信号。ResAgent 只消费通过验证的外层状态、Artifact 和显式 completion trace，不解释任意 payload。

### 7. “可验证科学意见”的边界

“可验证”只表示系统可以用代码验证闭环一致性，不表示代码可以证明科学观点真实或证据在语义上必然支持 verdict。

Scientific Agent 内部的 deterministic completion check 先根据 trusted Tool observations 验证 evidence 引用和未解决执行问题；只有通过后才产生 completed ScientificTurnResult。ResAgent 的 `ScientificCompletionValidator` 再独立检查 Run state、Artifact Registry、observed artifact trace、失败/blocked Task 确认和字段组合，全部通过才把 Run 置为 completed。科学正确性由 Scientific Agent 负责，后续通过评测而非伪装成确定性 gate 解决。

### 8. schema 2.0 采用原子切换

Phase 7 是一个未发布的迁移单元。7.1 可以新增 2.0 类型并同步移除 success_criteria 的所有代码引用，但旧 Scientific/Planning 类型、capability 和 adapter 必须保留到新控制路径可运行。最终切换时，在同一原子变更中切 composition root、删除旧类型和旧路径、更新全仓测试；任何阶段退出点都必须可导入且全仓测试可运行，不允许先删 contracts 再等待后续阶段修消费者。

`experiment_prepare` 也在最终切换时删除：Phase 6 的原生 Experiment Agent 已把准备、审计和执行统一在 `experiment_run` 内，且该 capability 没有 production binding。该删除是清理未实现的重复入口，不改变 Experiment Agent 职责。

## 为什么不是显式 plan/analyze 模式

Scientific Agent 不需要由调用方选择工作模式。自然语言目标、当前证据和 Tool 结果已经说明本轮需要做什么；强制模式会增加接口数量，并把科学推理切成不自然的阶段。

系统仍保留少量结构化边界，因为下游需要可靠地判断：是向用户提问、请求执行工作，还是形成最终结论。结构化外壳服务于控制和 provenance，不限制观点正文的自然语言表达。

## 不变的原则

- Workflow 的接受、状态转换和 Artifact 登记仍由确定性代码决定；
- 专业 Agent 不直接互调；
- Coding / Experiment 仍通过 ModulePort 执行 WorkflowTask；
- runtime 仍只有一套共享 Agentic Loop；
- capabilities 只提供可装配能力，不承担领域决策；
- LLM 不直接修改 RunStatus、TaskStatus 或历史 Attempt。

## 迁移影响

- ADR-0003 保留其“LLM 可参与语义规划、代码确定性调度”的原则，但其 Proposal/Patch 所有者被本 ADR 取代；
- `PlanningPort` / `DeterministicPlanningPort` 在 Phase 7 被 `ScientificPort`、`ResearchController` 与 `WorkflowCompiler` 替换；
- `scientific_plan`、`scientific_analyze`、`literature_search` 不再作为生产 WorkflowTask capability；
- `WorkflowProposal` / `WorkflowPatch` 继续存在，但成为 ResAgent 内部编译和调度边界；
- `LegacyScientificAnalyzeAdapter` 删除前，新旧实现只可在未发布开发分支中短暂共存，production composition root 始终只启用一条；
- wire schema 需要按契约版本规则演进，当前代码在 Phase 7 原子切换前仍保持 schema 1.1 的 production 行为；中间开发状态不得发布。

## 后果

正面：

- Scientific Agent 的接口更接近“自然语言科学顾问”，不被执行字段绑死；
- ResAgent 可以独立演进 capability、调度和恢复策略；
- 图仍然可校验、可追踪、可重放；
- 失败实验可以作为科学信息返回，而不是立即终止整个研究；
- 只有一套 Scientific Agentic Loop，减少模式和端口数量。

代价：

- ResAgent 需要新增 WorkRequest 生命周期和 WorkflowCompiler；
- Run 完成逻辑要从“任务图清空即结束”升级为“Scientific Agent 给出可验证最终意见”；
- 必须测试语义请求到执行图的 traceability，避免编译器改变请求含义；
- Scientific Session 可能跨越多个执行周期，需要可靠持久化与恢复。

## 明确不做

- 不引入多科学人格辩论、树搜索或 supervisor swarm；
- 不为 Scientific Agent 设计多个公开模式或多套 loop；
- 不让 WorkflowCompiler 持有长期会话或自行调用专业 Agent；
- 不把全部自然语言内容强行枚举化；
- 不在 Phase 7 同时建设通用聊天产品、分布式调度或插件市场。

## 参考的设计原则

- [Anthropic《Building effective agents》](https://www.anthropic.com/engineering/building-effective-agents)：优先采用简单、可组合的 agent/workflow 模式，并区分预定义工作流与模型自主使用工具；
- [OpenAI Agents SDK orchestration](https://openai.github.io/openai-agents-python/multi_agent/)：manager 可以把专业能力作为工具使用，同时由代码承担确定性编排；
- [Google AI co-scientist](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/)：以自然语言研究目标驱动科学推理，由独立控制层管理执行资源和工作队列；
- [Asta scientific skills](https://github.com/allenai/asta-plugins)：把论文检索等能力作为可装配 Tool，而不是把每个能力都变成独立 Agent。
