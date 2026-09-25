# 设计原则与架构约束

本文说明两件事：**系统围绕什么目标设计，以及修改代码时必须保持哪些规则。** 人和 AI 参与开发时，都应先对照这些规则。

本文中的“必须”是开发约束；“当前实现”说明代码已经做了什么；“待讨论”表示尚未决定或实现，不能当成已有能力。原则用于检查和指导改动，不表示可以顺便重构代码。

模块和流程看 [ARCHITECTURE](ARCHITECTURE.md)，接口和字段看 [CONTRACTS](CONTRACTS.md)，模型实际收到的信息看 [CONTEXT](CONTEXT.md)。本文不重复维护这些细节。

## 1. 三个总体设计目标

<a id="llm-first"></a>

### 1.1 围绕 LLM 的能力构建系统

LLM 是系统理解、推理和决策的核心。开发应帮助它获得准确的信息、使用合适的工具，并完成复杂任务。代码负责提供这些条件，以及可靠地执行和记录操作。

判断一项工作交给谁，可以看它需要什么能力：

| 问题 | 主要负责人 |
| --- | --- |
| 研究要解决什么问题，还需要哪些工作和证据 | Scientific 的 LLM |
| 怎样把已提出的工作需求转成任务及依赖 | Compiler 的 LLM |
| 怎样修改代码、分析实验结果、解释错误原因 | 对应专业 Agent 的 LLM |
| 证据意味着什么，是否支持假设，下一步研究什么 | Scientific 的 LLM |
| 请求是否超预算，路径是否获授权，答案是否对应当前问题 | 代码 |
| 命令退出码、文件 hash、实际状态和调用记录是什么 | 代码读取事实并保存 |

涉及含义、意图、因果或专业判断时，优先由职责对应的 LLM 处理。不要不断增加关键词匹配和特殊分支来代替这些判断。能直接从结构化事实得到的结果，例如退出码、字段匹配或预算余额，由代码处理。

优先让已有 Agent、Compiler 或 Interpreter 完成自己职责内的语义工作。只有已有职责确实无法承接时，才讨论新的 LLM 环节；不能每遇到一个问题就增加模型调用或新 Agent。

代码校验负责确认执行和记录是否满足约定，不能据此宣称科学观点正确。LLM 的判断也不能改写实际执行记录，或绕过预算、授权和证据检查。

例如，模型批准后反复列目录，应先检查它是否知道操作尚未执行、答案是否到达、任务指令是否冲突。应修正信息和反馈链，不添加“列目录三次后自动删除”这样的特例。

<a id="layered-autonomy"></a>

### 1.2 分工清楚，每层保留适当的自主性

可以用人体作比喻，但实际职责以下表为准：

| 比喻 | 项目模块 | 职责 |
| --- | --- | --- |
| 大脑 | Scientific Agent | 理解研究目标，提出假设和证据需求，阅读结果，形成科学判断 |
| 翻译与协调系统，类似小脑和神经系统 | Compiler、Interpreter、Scheduler、Controller | Compiler 把需求转成任务；Interpreter 把执行材料整理成科研反馈；Scheduler 按依赖和约束执行任务；Controller 管理整个 Run 的往返过程 |
| 专业器官或躯干 | Coding、Experiment Agent | 在代码和实验领域完成具体工作，并返回解释、证据和局限 |

Coding 和 Experiment 本身也使用 LLM。上层交付目标、材料和约束，专业 Agent 在授权与预算内选择具体工具步骤。上层不应为所有任务预先写死操作顺序。

Scientific 负责科学意义，不负责具体 Agent 调用、任务状态或环境绑定。Compiler 负责需求到任务的转换，不形成最终科学结论。Scheduler 根据已经接受的任务图和状态执行，不用固定规则代替科研决策。

### 1.3 正向传递意图，反向返回可理解的结果

架构的对称性首先是**两个方向都有明确的信息交接职责**：

```text
正向：科学目标和证据需求 → 翻译为任务 → 专业 Agent 执行
反向：执行事实、解释和证据 → 对应原需求整理反馈 → Scientific 更新判断
```

两个方向都必须满足以下要求：

- 正向保留目标、约束和证据需求。编译不能把“一次实际操作”误改成“只能调用一次工具”。
- 反向说明原需求完成了哪些、哪些未完成，并保留失败、警告、局限和证据入口。
- 每层通过公共输入输出合作。Scientific 无需依赖下游私有状态来理解结果，专业 Agent 无需代替 Scientific 决定研究结论。
- 反馈整理不能悄悄改变事实。简报中的解释应能追溯到原报告或证据，不能把解释当成新的测量。

对称性不要求两边目录相同、类的数量相同，或各调用一次 LLM。是否需要模型，取决于该步骤是否要做语义判断。证据编号用于追溯，应保留；任务编号和调度字段是否需要进入模型输入，应按用途决定。

<a id="interpreter-current"></a>

**当前实现**

| 方向或职责 | 当前行为 |
| --- | --- |
| 正向 | Scientific 提出工作需求；Compiler 的 LLM 生成任务草图；代码分配身份、校验结构，Scheduler 执行 |
| 反向 | Controller 收集已登记材料及历次尝试事实；Interpreter 的代码生成科研目录，LLM 生成带引用简报；Controller 保存后交给 Scientific |
| 产物权威 | Run.artifacts 是唯一登记表，负责原产物身份与来源；科研目录只是可重新生成的导航，不复制路径、hash 或权限 |
| 状态归属 | Controller 保存当前目录和每轮反馈引用；Interpreter 不修改 Run、不管理 Session、不调度任务 |
| 默认呈现 | 每轮工作交接提供最新完整科研目录和本轮带引用简报；底层登记引用供工具读取和校验，不再额外展开整份登记表或执行记录 |
| 阅读闭环 | 目录、简报引用和原件读取使用同一登记来源；完整问答也可追溯。阅读答案不等于消费批准或恢复任务 |

Compiler 与 Interpreter 都在 Orchestrator 包内，通过小接口注入具体实现。Interpreter 的解释不替代 Scientific 的最终判断，引用有效也不证明语义正确。目录和简报不授予权限；读目录不等于读到原证据。完成检查沿用原 AgentLoop 反馈与接收端登记边界；候选文件事实规则已共享。Run 级明确产物要求和新的运行前检查仍按后续阶段推进。

## 2. 修改时保持的十二条约束

下面的规则用于落实三个总体目标。工件是供交接和追溯的文件或记录；其登记、冻结和读取规则由公共契约定义。

| 约束 | 必须保持的行为 |
| --- | --- |
| 1. 职责分离 | Scientific 判断研究问题，Compiler 翻译工作需求，Interpreter 整理反向反馈，Scheduler 执行任务，Controller 管 Run，CLI 提供用户入口并装配模块。不能在修复时悄悄转移职责。 |
| 2. 公共接口和依赖方向 | Agent 不直接互调；上游只通过公共请求、结果、SessionRef 和工件交接，不读下游私有 Session/memory。具体实现由外层入口选择和注入。 |
| 3. 每个 Agent 一种调用和业务模式 | 统一使用 invoke(AgentRequest) → AgentResult。任务由 instruction/input_artifacts 表达，结果用 report/artifacts 交付。分析、修改、执行和问答恢复不另设业务模式；可写不等于必须修改。 |
| 4. 共享运行机制，保留专业判断 | 三个 Agent 共用 AgentLoop，装配各自工具、上下文、权限策略和完成检查。不复制循环，也不往共享循环中加入某个 Agent 的专用业务分支。 |
| 5. LLM 处理语义，代码检查执行约束 | 需要理解意图、专业推理、诊断原因或解释证据的工作，优先由相应 LLM 完成；结构化事实能直接确定的问题由代码判断和反馈。身份、状态、图结构、预算、权限和记录一致性由代码检查。不能用报告文字推断机器状态，也不能用机器校验代替科学判断。 |
| 6. 每项状态和事实有明确来源 | Run/WorkRequest、Task/Attempt、Session 按各自职责管理；授权、资源登记、实际可用性和环境绑定各有可信来源。摘要、提示和缓存不能再维护另一份独立的权威状态。 |
| 7. 三层循环分工 | Controller 管研究往返，Scheduler 管任务执行，AgentLoop 管工具步骤。Compiler 和 Interpreter 没有 Session，只在有限调用内翻译当前需求或结果。不因纠错新增另一套控制流程。 |
| 8. 回答继续原工作，重试另开尝试 | 任务 Agent 的回答继续同一 Task/Attempt/Session；Scientific 的回答继续同一 Run/Session。任务失败后的重试才创建新 Attempt。必须匹配原题和作用域，不能重复消费旧答案，或在恢复时重置预算、基线和授权。 |
| 9. 内部预算和权限不超过 Run | 子调用只能继承或收紧 Run 的调用余额、期限和授权。Compiler、Interpreter、模型重试、纠错和压缩共用总账；人工等待按已有规则计算。资源不足时不能自行扩大授权或擅自替换数据集。 |
| 10. 区分申请、批准和实际执行 | 批准绑定准确操作、参数、上下文和身份，执行前持久消费。执行仍需检查权限和目标；不能复用批准执行其他操作。结果未知时不自动重放可能已发生的副作用。 |
| 11. 证据和完成按层检查 | 登记层校验来源并冻结工件，跨任务用显式 output_name 绑定；Agent 检查领域执行事实，接收端和 Run 最终检查各自规则。可读不等于已读，运行成功不等于假设成立。 |
| 12. 保留失败、消耗和历史 | 保留失败、警告、用量和中断记录；任务图按规则追加，执行历史不改写。接口清理同步生产者和消费者；旧 schema 不兼容恢复，不留下无人需要的兼容路径。 |

这些约束允许精简内部实现，例如删除无消费者字段、合并重复函数或调整文件位置。改动必须保持相应行为；确实需要改变设计时，应说明理由和取代关系，同步契约、实现与测试。不能只改文档来掩盖意外的行为变化。

## 3. 模块独立、依赖倒置和开闭原则

### 3.1 模块独立：通过约定合作

模块有自己的职责和状态，可以单独测试、替换实现。共享 contracts/runtime/components 是有意设计；独立不要求零依赖、每类一个包或每个模块单独部署。

同一行为保留一条生产主线，优先使用已有组件。出现语义一致的重复需要时，再考虑提取共享机制；只用一次的小函数通常留在所属模块。已经明确属于公共运行机制的能力，仍按既有边界放置。不要为了可能的未来需求先造基类、管理器或插件框架。

### 3.2 依赖倒置：调用接口，由入口选择实现

Scheduler 要调用 Agent，但它在源码中只认识 ModulePort 这个公开调用约定。CLI/E2E 入口负责创建具体 Agent，并把它传给 Scheduler。这就是本项目依赖倒置的主要做法。

| 接口或注入位置 | 具体实现由谁提供 |
| --- | --- |
| [ModulePort](../../packages/orchestrator/src/resagent2_orchestrator/ports.py) | CLI/E2E 提供 Scientific 和任务 Agent |
| [WorkflowCompiler](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[WorkInterpreter](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py) | CLI/E2E 提供正向编译与反向解释实现 |
| [AgentDefinition / CompletionCheck / PermissionPolicy](../../packages/runtime/src/resagent2_runtime/loop.py)、[Tool](../../packages/runtime/src/resagent2_runtime/tools.py) | 各 Agent 装配工具和专业策略，通用循环调用这些约定 |
| [LLMClient](../../packages/runtime/src/resagent2_runtime/llm.py)、[SessionStore](../../packages/runtime/src/resagent2_runtime/store.py)、[RunStore](../../packages/orchestrator/src/resagent2_orchestrator/store.py) | 入口或调用方提供模型和存储实现 |
| [LiteratureSearchBackend](../../packages/components/src/resagent2_components/literature/backends.py) | 入口装配文献来源，Scientific 不处理供应商响应格式 |
| [ArtifactRegistrationPort](../../packages/components/src/resagent2_components/artifacts.py) | 入口接入 Orchestrator 的登记实现，文献 Tool 不反向导入 Orchestrator |

Python 的 Protocol 描述接口形状，不要求具体实现继承同一个基类。替换实现还必须遵守身份、暂停、预算、失败和工件等行为约定，仅有同名方法不够。

完整依赖范围看[模块边界](ARCHITECTURE.md#modules)。当前 Orchestrator 除 contracts 外，还使用 runtime.budget 的共享执行预算，以及 components.workspace/artifacts/materials 的工作区边界和已登记工件读取。Controller 与 Scheduler 同包内共用 store/registry 和必要辅助函数，也不等于跨 Agent 读取私有状态。稳定的普通组件不必再套一层接口。

### 3.3 开闭原则：沿已有接口扩展，控制修改范围

替换实现或增加能力时，优先使用已有接口，避免修改所有调用方或核心循环。新增公共概念或安全规则，仍可能需要明确修改代码。

| 变化 | 正常修改范围 |
| --- | --- |
| 替换某个 Agent 实现 | 实现同一 ModulePort，在入口接线，通过相同的行为测试；总控不增加识别具体实现类的分支 |
| 增加普通 Tool | Tool/输入 schema、所属 Agent 的显式工具列表和动作 schema；按需要复用 Components，不改通用循环的流程 |
| 增加有副作用的操作 | 同时检查授权、目标快照、执行、失败和恢复；必要时修改共享权限策略，不能认为工具登记后就自动安全 |
| 替换模型、存储或文献源 | 修改接口实现和入口装配；预算、协议身份、错误和持久化约定仍需保持 |
| 新增顶层 Agent 类型或公共字段 | 明确职责与契约变化，同步枚举、生产者、消费者、装配、版本和测试；不保留两套业务协议 |

Capabilities 是模型工具入口，Components 是普通操作与共享呈现。两者不要求一一对应或强制逐层调用。Coding 的 run_verification 与 Experiment 的 run_command 各自保留专业规则，共用 ProcessRunner。

最小模型客户端与原生工具客户端、Compiler/Interpreter JSON 与 Agent 工具调用，是底层调用协议的区别，不是 Agent 的业务模式。Session 固定自己的协议身份，恢复时不能自动降级或静默换协议。

## 4. 每次修改怎样检查

评审应回答以下问题，并给出受影响代码和验证依据：

1. **问题性质**：这是语义理解问题，还是事实、权限、状态等执行问题？是否交给了正确的 LLM 或代码环节？有没有用特殊规则掩盖信息缺失？
2. **职责与依赖**：模块是否仍各管自己的事？是否出现 Agent 互调、反向导入、读取别人私有状态或重复保存权威事实？
3. **双向交接**：目标和约束是否准确向下传递？结果、证据、失败和局限是否对应原需求返回？不能只看某个转换函数，要检查完整模型输入及实际消费。
4. **扩展方式**：已有接口、Tool、完成检查或普通函数能否完成修改？有没有新业务模式、兼容分支、重复循环或无实际需求的框架？
5. **完整流程**：追踪生产、校验、保存、消费、失败、问答和重启。身份、基线、批准消费、预算和授权是否延续正确？
6. **验证与文档**：运行受影响的边界和行为测试；改动模型输入或执行链时再安排真实验收。同步当前文档；重要取舍追加 ADR，阶段结果放 history/reviews。不能放宽断言来消除失败。

已有包边界测试检查 import，契约测试检查接口，行为测试检查预算、权限、恢复、证据和完成；公开入口整链检查模块组合后的流程。命令见[开发与验证](../guides/DEVELOPMENT.md#local-checks)和[测试目录](../../tests/README.md)。

静态依赖检查不证明所有行为正确，固定模型响应的测试不证明真实模型总会正确选择，一次真实 Run 也不证明通用成功率。当前仍以单 Run 单写入者为前提；权限检查不是操作系统沙箱，持久化也不保证所有外部副作用恰好发生一次。

## 5. 设计来源与维护

- [ADR-0001](../history/decisions/0001-monorepo-and-module-boundaries.md)：模块边界与独立测试；旧专用输入输出已由统一协议取代。
- [ADR-0002](../history/decisions/0002-shared-agentic-loop.md)：共享循环，通过注入表达差异。
- [ADR-0007](../history/decisions/0007-scientific-control-and-workflow-compilation.md)：科学判断、需求翻译、确定性调度和最终验收分离。
- [ADR-0011](../history/decisions/0011-stabilization-schema-3.md) / [0012](../history/decisions/0012-state-recovery-boundaries.md)：单一控制面、状态来源和恢复边界；具体旧字段与计量方法以后续契约为准。
- [ADR-0014](../history/decisions/0014-semantic-handoffs.md)：完整解释与原题配对，区分报告解释和原始证据。
- [ADR-0015](../history/decisions/0015-tool-components-boundary.md)：模型工具与普通组件分开，不增加成对的类层级。
- [ADR-0016](../history/decisions/0016-unified-agent-io-and-run-controls.md)：统一 Agent IO、Run 总预算与授权、准确的单次批准。
- [ADR-0017](../history/decisions/0017-research-index-and-work-interpreter.md)：派生科研目录与带引用简报；取代 ADR-0014 中 Interpreter 位于 Scientific 内、仅作固定投影的实现选择。
- [ADR-0018](../history/decisions/0018-complete-index-and-paired-answers.md)：每轮交接展示完整目录，打通成对问答的索引、引用和阅读；取代 ADR-0017 的增量展示选择。

本文明确总体设计目标，并汇总仍有效的规则。历史 ADR 中已被取代的字段、接口和实现不会因此重新生效。发现目标与实现有差距时，应记录差距，再决定具体改动；不能把设计目标写成已经实现或验证的能力。
