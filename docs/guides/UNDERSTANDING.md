# 理解一次研究任务

ResAgent2 可以先用一句话理解：**让模型决定科研上需要什么，再让代码把工作安排、执行记录和证据检查管起来。**

你不需要先读完 models.py。先认识四个角色，再沿着例子看请求和结果如何往返。本文解释当前代码，不是新规范；字段和规则查 [接口与契约](../current/CONTRACTS.md)。

## 1. 先认识四个角色

| 角色 | 可以把它理解成 | 它交付什么 |
|---|---|---|
| Scientific Agent | 科学判断者 | 当前观点、工作需求、用户问题或最终意见 |
| Orchestrator | 研究过程的控制程序 | 已接受任务图、执行状态、结果汇总与正式报告 |
| Coding Agent | 程序员 | 代码理解，或真实修改与验证结果 |
| Experiment Agent | 实验执行者 | 命令记录、证据工件、文件中提取的指标 |

ResAgent2 是项目名，不是额外的第五个 Agent。Orchestrator 内有三个分工：Controller 管整个过程，Compiler 把当前需求翻成任务图，Scheduler 执行图中的任务。

<a id="walkthrough"></a>

## 2. 沿着一个例子走一遍

假设你提出：“实现一个模型改动，并和原模型比较效果。”以下是**说明性路径**，不是保证模型每次用相同动作或数值。

### 第一步：入口把目标交给 Controller

CLI 接收目标、工作区和配置，组成 ResearchRequest，调用 ResearchController.create_run。这里同时开始执行，通常一直到完成、失败或需要你回答才返回。

CLI 只是入口，不再实现一套调度。它和 E2E 脚本都负责装配依赖，但两者是独立入口。

### 第二步：Scientific 说“我需要这些工作”

Scientific 关心：“为了回答这个研究问题，还缺什么证据？”它可以先检索文献、读工件；需要修改和比较时，提出 WorkRequestDraft。

它说清目标、期望证据、约束，但不决定 task_id、执行器、依赖图和物理路径。自然语言在这里表达科学意图，不是要求下游猜所有控制信息。

### 第三步：Compiler 翻译，Scheduler 接受

Compiler 可能生成候选：

```text
实现改动并验证 [code_modify]
              ↓ 依赖上游成功
运行正式比较 [experiment_run]
```

LLM 负责语义草图，代码分配正式身份、绑定工作区并校验。Scheduler 再检查路由、授权、预算和图版本，接受后才执行。

所以 Scientific 不需要知道每个任务怎么调度：翻译和接收由 Orchestrator 承担。

### 第四步：Coding 改代码，但不能只说“改好了”

Scheduler 通过 ModulePort.invoke 交给 Coding 一个 ModuleTaskRequest，包含任务目标、专属输入、工作区授权、预算和证据引用等。

Coding 内部运行共享 AgentLoop：读取 → 修改 → 调工具 → 看真实结果 → 验证。完成检查核对相对本 Attempt 基线的变化，以及当前代码/环境是否真正验证通过。

ModuleResult 同时有机器状态、typed payload、说明和候选工件。summary 可以解释过程，但不能推翻真实验证结果或替代它。

### 第五步：Experiment 真跑，代码提取指标

上游成功后，Experiment 收到自己的任务，准备或复用环境、审计、执行正式命令、收集本次新建或变化的证据。

实验脚本写出的 JSON 指标文件是证据来源。完成检查从完整证据集中提取数值，不让模型在 finish 随意报一个数字作为事实。

原始工件是根源，metrics 是便于机器消费的投影，summary 是解释。看到矛盾应回查证据，而不是因为某字段听起来可信就忽略原文件。

### 第六步：Scientific 收到可理解的工作简报

Scheduler 登记工件、保存结果，再形成 WorkOutcome。Scientific 内的 interpreter.render_work_brief 整理成：做了什么，有什么结果/警告，为什么失败，有哪些证据可以读。

interpreter 是无状态纯函数，不再调用一次 LLM。它属于 Scientific，因为它决定“给 Scientific 看什么”，不拥有执行状态。

Scientific 主动读取授权工件后判断结果是否支持假设，或还需下一轮工作。它不必替 Scheduler 抄回失败任务内部编号；报告里的执行问题由代码从 Run 核对。

### 第七步：意见完成，不代表整个 Run 自动通过

Scientific 提出最终 opinion，Orchestrator 再检查执行状态、证据归属、已读引用和必要局限，通过后登记报告并标记 completed。

“实验成功执行”和“假设被支持”不是同一回事。证据不足时给 inconclusive，也可能是诚实完成的研究过程。

## 3. 失败或问你问题，会发生什么

**实验失败**：保存原始错误和诊断，返回 Scientific。需要修复时再提出新工作请求，Compiler 编译“修复 → 重跑”；依赖成功的箭头不能解释成“失败后执行”。

**需要用户选择**：ask_user 提出问题并声明非空字段。Controller 保存问题、Run paused；回答以实际 pending question 字段为准，不猜字段名。任务级问答恢复同一 Attempt 和 Session，不算一次 retry。

例如你回答“用 mul 模式”，系统不只是把这句话记进 Run：它还会把本 Task 的实际回答放进 Agent 后续每一步的 `answers` 上下文段，供模型据此选择命令。这个段和文件片段共用原来的上下文预算，没有另造记忆系统。回答“数据准备好了”则仍要与重新检查的目录事实对照；目录仍缺，应继续询问。

**进程中断**：快照帮助恢复身份和状态，但不是外部操作的事务回滚。保留 Session 不代表命令一定只执行过一次。

## 4. 五个名字，分别指什么

### 插曲：为什么启动时不用填写资源？

开始时不一定知道后面会用什么。ResearchRequest 只写目标和约束；部署者维护共享数据集目录，系统把当前已知引用交给 Agent，Agent 在运行中判断需要什么。

例如先读代码，才发现需要数据集 demo：已有就用；缺少就 ask_user，用户放好并在 catalog 登记，回答后系统重新检查，再继续原 Session/Attempt。仅仅回答“好了”不等于目录已经存在。等待用户不会吃掉运行超时，但安装、推理、执行仍会计时。

依赖则沿用已有安装与审计，下载缓存归 pip/conda。数据集登记表、运行环境、包下载缓存是三件事，不需要为了“都是资源”塞进同一个类。具体操作见 [CLI 资源库](../../apps/cli/README.md#4-数据集资源库)。

### 五个运行身份

| 名字 | 例子中的含义 |
|---|---|
| Run | 整件“实现并比较效果”的研究请求 |
| WorkRequest | Scientific 某一轮需求；修复通常是下一轮 |
| Task | 图中一个节点，例如 code_modify |
| Attempt | 某任务的一次尝试；问答续跑不变，retry 才新增 |
| Session | Agent 的动作、观测和内部记忆；Scientific 跨回合，执行 Agent 的属于 Attempt |

Artifact 也容易混淆：Candidate 是“请登记这个文件”，Ref 是登记后有身份、来源与 hash 的引用。拿到 Ref 只是拿到证据指针，不表示模型已读内容。

## 5. 为什么三个 Agent 不各写一套循环

它们都需要请求模型、调工具、记录观测、处理失败，所以复用 runtime.AgentLoop，只装配不同 prompt、工具、权限、上下文和完成检查。

文件、Git、进程、环境和工件校验放在 capabilities。文件与工件复用按行读取，三个 Agent 复用片段机制，但 Scientific 不因此获得写文件或运行命令的权力。

Compiler 需要上下文和 LLM，不需要整个工具循环，因此只复用 PromptLLMClient。**复用能力不等于采用同一个业务流程。**

## 6. 上下文不是一直塞入所有历史

完整观测在 Session，冻结证据在 Artifact 存储，full trace 是独立调试记录。每次调用只把所需部分装入模型上下文。

任务、控制状态、工具契约和反馈占一部分；读过的片段占另一部分。片段带来源、行范围、观察顺序；旧文件读取可以带后续修改标记。

需要被省略的细节时可按范围再读。“曾经读过”不代表当前 prompt 有全文。模型总窗口变大也不会自动扩大模块输入额度，配置见 [CLI README](../../apps/cli/README.md#6-模型与上下文预算)。

## 7. 想看代码时，只找对应入口

| 想回答的问题 | 从这里看 |
|---|---|
| Run 怎么开始、恢复？ | [controller.py](../../packages/orchestrator/src/resagent2_orchestrator/controller.py) |
| 需求怎么变成任务？ | [compiler.py](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py) |
| 谁选择 ready Task、记录 Attempt？ | [scheduler.py](../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py) |
| Scientific 怎样看执行结果？ | [interpreter.py](../../packages/agents/scientific/src/resagent2_scientific/interpreter.py) |
| Agent 怎样共享循环？ | [loop.py](../../packages/runtime/src/resagent2_runtime/loop.py) |
| 文件、工件如何进入上下文？ | [workspace_context.py](../../packages/capabilities/src/resagent2_capabilities/workspace_context.py) |
| 某字段是什么意思？ | [接口与契约](../current/CONTRACTS.md)，再查 [models.py](../../packages/contracts/src/resagent2_contracts/models.py) |

不必依次精读这些文件。先跑 [本地 mock](DEVELOPMENT.md#local-checks)，再挑一条想改的行为，更容易把代码和流程对应起来。
