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
| Experiment Agent | 准备环境、运行实验、采集指标和冻结证据 | 修改产品代码或形成最终科学结论 |

Orchestrator 内部的 `ResearchController` 是唯一 Run 入口；Compiler 翻译当前工作请求，Scheduler 执行任务。Scientific 的 `interpreter.py` 只是整理返回结果的内部纯函数，不是新 Agent 或调度层。

## 从 CLI 使用

`resagent2`（`apps/cli`）是外层薄入口：

- `run / show / answer / resume`：一次性命令；
- 无参数或 `resagent2 shell`：交互监控壳；Ctrl-C 只停监看，不取消 Run；
- 共享数据集由部署者在 `RESAGENT2_DATASET_ROOT/catalog.json` 注册；用户不必每次指定物理路径。缺必需数据集时询问用户，不自行下载或静默替换。

安装、模型配置、数据根目录与完整示例见 [CLI README](apps/cli/README.md)。

## 当前实现与验证边界

当前只实现 contracts schema `5.0`（`SCHEMA_VERSION="5.0"`），不保留旧 schema 的第二条运行路径；旧 4.0 及更早的 Run 不支持恢复，既有 state/session/trace 原样保留，不迁移、不重写、不自动清理。Session 的解析边界见 [CONTRACTS](docs/current/CONTRACTS.md#schema)。三个原生 Agent 共用 runtime 和 capabilities，真实执行不依赖旧项目的 Agent。

接口契约优化已完成并合入 main。分阶段提交、真实服务器验收及已知边界见 [决策与历史](docs/history/README.md)；当前文档不再维护一份重复的轮次清单。

确定性检查证明的是身份、状态、执行记录和证据引用符合规则，**不是 LLM 的科学观点一定正确**。同样，trace 的 `action_valid` 不能代替工具成功或最终完成验收。

## 通用部分如何复用

Scientific、Coding、Experiment 使用同一 `AgentLoop`，只装配不同的 prompt、Tool、上下文、权限和完成检查。文件、Git、进程、环境、Artifact 读取等能力放在 `capabilities`，供需要它们的 Agent 复用；不要为名字相似但语义不同的职责强造统一接口。

LLM 客户端的必需方法是 `next_action`；预算和 trace hooks 可选。Compiler 使用同一 LLM/上下文基础，但不必运行 Agentic Loop。

```text
apps/cli/                     人类入口与生产装配
packages/
  orchestrator/               Controller、Compiler、Scheduler、Run 状态
  agents/scientific/          科学判断
  agents/coding/              代码理解、修改与验证
  agents/experiment/          实验执行与证据交付
  runtime/                    共享 Agent 循环、上下文、Tool 协议、LLM、Session
  capabilities/               可复用的文件、Git、进程、环境、证据等组件
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

- [当前实现与规范](docs/current/ARCHITECTURE.md)：架构，以及已合并的 [模块接口与契约](docs/current/CONTRACTS.md)。
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
