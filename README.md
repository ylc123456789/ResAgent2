# ResAgent2

ResAgent2 是一个面向科研任务的、可审计的 Agent 工作流项目。它把开放的 LLM 推理与确定性的执行、记录和验收组合起来，也为后续研究复用已有组件提供基础。

## 先理解一条主链

```text
用户通过 CLI 提交研究目标
  → Scientific 决定还需要什么工作和证据
  → Orchestrator 编译任务、调度、记录状态
  → Coding 修改并验证代码 / Experiment 运行实验并交付证据
  → Scientific 阅读证据、更新判断
  → Orchestrator 验收完成条件、生成报告
```

ResAgent2 是整个项目的名字；Orchestrator 是其中的研究编排模块。先记住这四个职责，不必先记所有类名：

| 角色 | 负责 | 不负责 |
|---|---|---|
| Scientific Agent | 科学判断、证据需求、最终观点与局限 | 生成执行图、直接调用其他 Agent |
| Orchestrator | 工作请求转任务图、调度、状态、预算、暂停恢复和最终验收 | 自己改代码、跑实验或形成科学观点 |
| Coding Agent | 阅读、修改、准备环境并验证代码 | 形成最终科学结论 |
| Experiment Agent | 分析已有结果、准备环境、运行实验并提交证据 | 修改产品代码或形成最终科学结论 |

Orchestrator 内部的 `ResearchController` 是唯一 Run 入口；Compiler 翻译当前工作请求，Scheduler 执行任务。Scientific 的 `interpreter.py` 只是整理返回结果的内部纯函数，不是新 Agent 或调度层。

## 从 CLI 使用

`resagent2`（`apps/cli`）是外层薄入口：

- `run / show / answer / resume`：一次性命令；
- 无参数或 `resagent2 shell`：交互监控壳；Ctrl-C 只停监看，不取消 Run；
- 共享数据集由部署者在 `RESAGENT2_DATASET_ROOT/catalog.json` 注册；用户不必每次指定物理路径。缺必需数据集时询问用户，不自行下载或静默替换。

安装、模型配置、数据根目录与完整示例见 [CLI README](apps/cli/README.md)。

## 当前实现与验证边界

当前只实现 contracts schema `13.0`（`SCHEMA_VERSION="13.0"`）；旧 schema 的 Run 不支持恢复，既有 state/session/trace 原样保留，不迁移、不重写、不自动清理。Session 的解析边界见 [CONTRACTS](docs/current/CONTRACTS.md#schema)。三个原生 Agent 共用 runtime、components 和 capabilities。

Scientific、Coding、Experiment 都只有一个调用入口和一种业务模式：`invoke(AgentRequest) -> AgentResult`。业务输入是 `instruction + input_artifacts`，业务输出是 `report + artifacts`；身份、权限、预算、工作区、恢复和控制信号保持结构化。Coding 可以理解或修改代码，Experiment 可以分析已有结果或执行新实验，无需切换模式。精确验收要求、数据集目录、问答和工作反馈都通过冻结工件传递。

调用方不预先填写数据集或依赖缓存：系统提供部署资源目录，Agent 在运行中发现需求。数据集缺失时通过已有问答请求人工补充，回答后重新检查；依赖沿用安装/审计能力。显式问答等待不消耗 Run 超时预算，其他耗时仍计入。用法见 [CLI 资源库](apps/cli/README.md#4-数据集资源库)。

Run 是预算与授权的上限：RunBudget 只含模型请求次数和时间，任务数/尝试数单列为 ExecutionLimits；三个 Agent 与 Compiler 共用持久请求用量及截止时间，每次模型 HTTP 尝试发送前登记。工作区统一使用 read_paths/write_paths/denied_paths，操作权限显式继承。共享规则将操作判为允许、询问或拒绝；批准仅供本次动作使用，普通文件删除可直接进行，非空目录清理需确认目标快照。参数见 [CLI 运行控制](apps/cli/README.md#run-controls)。

路径检查和命令规则不是 OS 沙箱。没有隔离后端时，受限工作区不能执行任意脚本；完整可读写、无用户排除路径的工作区才开放可信代码执行。数据集、环境和依赖缓存继续按原有职责管理，不新增通用资源配额层。

Workflow 只按 `coding / experiment` 路由，任务同样用一条 `instruction` 表达意图。跨任务产物通过逻辑 `output_name` 与显式绑定交接；Compiler 生成一个任务草图，结构不合法时最多纠正一次。设计背景见 [统一 Agent IO V2 方案](docs/history/reviews/UNIFIED_AGENT_IO_V2_PLAN_2026-09-20.md)，各提交的真实验证范围与记录限制见 [统一 IO 验收](docs/history/reviews/UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md) 和 [Run 控制验收](docs/history/reviews/RUN_CONTROL_SERVER_REVIEW_2026-09-22.md#fixed-round)。

`report` 解释发现、结果与局限，较长说明可作为 `module_report` 工件交接；测量以原始证据为准。Controller 将原题与用户回答配对成 `answer` 工件，再恢复对应 Session。命令、验证与观察记录由原生 Agent 的确定性完成检查生成，上游只接收公共结果和工件，不读取下游私有 Session。

确定性检查证明的是身份、状态、执行记录和证据引用符合规则，**不是 LLM 的科学观点一定正确**。同样，trace 的 `action_valid` 不能代替工具成功或最终完成验收。

已完成一个真实仓库 L3 案例：自主查资料、实现一个学习率调度候选、完成两组配对训练并交付负结果；这是单案例闭环证据，不是通用成功率或统计显著性的保证。产品提交、原始证据复核、报告勘误及后续优化项见 [Compiler 额度与 L3 收尾记录](docs/history/reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md)。

## 通用部分如何复用

Scientific、Coding、Experiment 使用同一 `AgentLoop`，只装配不同的 prompt、Tool、上下文、权限和完成检查。`capabilities` 放模型可调用的 Tool；`components` 放文件授权、Git、进程、环境、工件读取、文献后端等普通 Python 实现。Tool、Agent 和组合根按需直接调用组件，不要求一一对应，也不强制经过中间层。Runtime 仍只管运行机制。入口见 [工具目录](packages/capabilities/README.md) 与 [组件目录](packages/components/README.md)。

LLM 客户端的必需方法是 `next_action`；最小客户端由共享入口在调用前计一次，自带 HTTP 重试的客户端须逐次接入同一用量接口。trace hooks 可选。Compiler 使用同一 LLM/上下文与执行预算基础，但不必运行 Agentic Loop。

```text
apps/cli/                     人类入口与生产装配
packages/
  orchestrator/               Controller、Compiler、Scheduler、Run 状态
  agents/scientific/          科学判断
  agents/coding/              代码理解、修改与验证
  agents/experiment/          实验执行与证据交付
  runtime/                    共享 Agent 循环、上下文、Tool 协议、LLM、Session
  capabilities/               按用途分组的模型 Tool 与输入 schema
  components/                 普通调用可复用的操作、资源和内容呈现
  contracts/                  跨模块对象、字段与组合约束
tests/                        本地契约与行为测试
e2e/                          独立的端到端装配和验收入口
docs/                         current / history / guides 三类文档
```

## 本地开发

项目使用名为 `ResAgent2` 的 Conda 环境：

```bash
conda env create -f environment.yml
conda activate ResAgent2
python -m pytest tests apps/cli/tests
```

已有环境可用 `conda env update -n ResAgent2 -f environment.yml --prune` 同步依赖。

## 文档入口

统一从 [文档导航](docs/README.md) 进入：

- [当前实现与规范](docs/current/ARCHITECTURE.md)：架构、[模块接口与契约](docs/current/CONTRACTS.md)，以及 [模型实际看到的上下文](docs/current/CONTEXT.md)。
- [决策与历史](docs/history/README.md)：为什么这样设计、各轮计划和验收，不代替现行规则。
- [入门与实践](docs/guides/README.md)：从一次任务理解系统，再学习使用和开发。

命令和部署参数仍集中在 [CLI README](apps/cli/README.md)。教程解释规则，历史保留事实，不再分别维护 contracts 和 interfaces 两套参考。

## 开发约束

- 同一行为保留一条生产主线；不添加仅为历史兼容存在的分支。
- Agent 之间不直接互调，上游不读下游私有 Session；跨模块需求经 Orchestrator 处理。
- 至少有两个语义一致的使用者，再抽取新的共享机制；已有组件优先复用。
- 不把类型合法当成业务正确；接收端检查公共契约，领域完成检查验证真实执行依据。
- 不在仓库保存 SSH 私钥、API key 或服务器凭据。

旧项目保留作需求、实现经验和回归样本来源，不整仓复制，也不是当前生产执行的依赖。
