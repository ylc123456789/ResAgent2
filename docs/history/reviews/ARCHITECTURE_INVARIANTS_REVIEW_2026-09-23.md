# 代码健康分支：架构与流程不变量复核

日期：2026-09-23。审查基线：`fix/code-health@7f8173e2b5a340c3b257775a3a2c355f5142e880`，对照 `main@5fe2c7f1ad5731bca9b8449fec7dccf650ada2a6`，schema 14.0。用户要求保持分支不合并，审查不限于单一 Agent 调用模式，还须核对模块独立、开闭原则、依赖倒置和既有机制/流程。

结论：**在本轮代码、依赖图和确定性验证覆盖范围内，未发现 code-health 分支破坏既有模块职责、依赖方向、状态归属或核心流程。** 找到一项既存架构测试缺口和一处旧类说明漂移，均以最小改动补齐。设计原则原先分散在 README、ARCHITECTURE 和 ADR，本轮将仍有效的约束收敛为 [DESIGN_PRINCIPLES](../../current/DESIGN_PRINCIPLES.md)，没有重新设计架构。

## 1. 方法和范围

- 对照 ADR-0001/0002/0007/0011/0012/0015/0016、现行 ARCHITECTURE/CONTRACTS/CONTEXT；识别旧版专用接口、资源字段、计量与包位置的取代关系，不把历史全部要求误当现行规范。
- 检查全部生产 Python 包的静态项目导入，比较 main 与审查基线依赖图；检查 Port、组合根、AgentDefinition、工具、完成检查和登记接口的实际接线。
- 逐项审查本分支编排/CLI 变更和共享 Runtime、Scientific 完成校验、能力说明收敛等改动；沿主流程核对正常、失败、问答、恢复、用量和工件交付。
- 执行现有模块边界与装配/返回契约测试，并运行全量确定性回归与 mock 闭环。前轮真实服务器验收作为已核实的历史证据，不重新调用模型或 GPU。

这不是“仓库每行代码绝无问题”的保证；静态导入不覆盖任意动态行为，结构校验不能证明 Compiler 永不误解自然语言，单次真实运行也不证明通用成功率。

## 2. 原则对照结果

| 约束 | 本轮核对的实现与证据 | 判断 |
| --- | --- | --- |
| 模块职责和公共边界 | Controller 管 Run，Compiler 只翻译工作请求，Scheduler 执行任务；CLI 调用 Controller。Agent 之间无直接 import，上游无读取 Agent 私有 Session/memory 的调度旁路 | 保持 |
| 依赖倒置 | Orchestrator 定义 ModulePort；Scheduler/Controller 注入调用；CLI/E2E 选择具体实现。Scientific 文献登记使用 ArtifactRegistrationPort，不反向 import Registry 实现 | 保持 |
| 依赖图 | contracts 不依赖项目其他包；runtime 只依赖 contracts；components 不依赖 Tool/Agent/Orchestrator；capabilities 不依赖 Agent/Orchestrator。Orchestrator 额外依赖严格限于 runtime.budget 与 components.workspace | 无环，与 main 相同 |
| 单一业务模式 | 三个 Agent 共用 invoke(AgentRequest) → AgentResult；新增批准状态投影不是新入口或 mode；分析与执行仍由 instruction、工件、预算和授权决定 | 保持 |
| 共享 Loop 与开闭原则 | AgentDefinition 注入工具、LLM、context_builder、permission_policy、completion_check；新增 pending_operation 使用共享状态，Loop 中未出现按具体 Agent 名称区分的逻辑 | 保持 |
| 状态权威及控制循环 | Run/WorkRequest、Workflow/Task/Attempt、Session 分层持有；Compiler 无长期 Session；任务失败反馈 Scientific，Scheduler 不自行宣布研究完成 | 保持 |
| 追加工作和依赖 | patch 只追加任务；图约束由公共候选模型和执行接收校验承担。拓扑失败传播不再受列表顺序影响，WorkOutcome 在本轮任务全部终态后形成 | 保持且修复一致性 |
| 问答和恢复 | Controller 配对原题/作用域，Scheduler 复用暂停 Attempt；只有失败重试开新 Attempt。删除人工 retry_task 去掉无消费者入口，没有去掉既有失败重试 | 保持 |
| 预算及权限 | 同一 Run 账本先占用后请求，Compiler/Agent/重试/纠错共用余额；恢复使用已保存工作区授权。共享策略和文件/进程边界仍独立检查，批准不能扩大范围 | 保持 |
| 副作用与未知结果 | pending_operation 只投影未消费的 pending_action；真正 dispatch 前持久消费批准，未知结果不自动重放。确认不是执行，暂停恢复不是新副作用 | 保持 |
| 工件与最终完成 | 输入、answer、work_feedback、验收要求沿冻结工件交接；逻辑名绑定保持唯一。Scientific 的新完成预检仍在原 Loop，Registry 和 Run gate 继续复验各自边界 | 保持 |
| 失败历史与兼容边界 | 诊断失败保留原错误/Session/工件；图与 Attempt 历史不清空。schema 13→14 是已记录的字段清理，不保留旧版兼容运行层 | 保持 |

### 2.1 开闭原则的真实边界

本项目支持通过已有 Port 替换 Agent、模型、Store 和文献来源；这不意味着任意新 Agent 类型或副作用 Tool 可以只注册而不审查契约、安全与状态。

新增 Tool 仍需同步所属 Agent 的工具列表/动作 schema；新增外部操作可能要修改共享权限策略。当前有限的 coding/experiment 路由、Compiler 对 Experiment 的领域提示、已知操作判定，都是显式策略，不应为追求零修改扩展而引入万能插件架构。

Controller 与 Scheduler 在同一个 Orchestrator 包内共享 store/registry 和 helper 属于包内协作；依赖倒置重点是跨边界调用依赖约定，而不是把每个类独立服务化。

### 2.2 本分支的精简不等于改变架构

清理无消费者字段、通用 snapshot 包装和测试专用 Tool，不应恢复成旧兼容层。Coding 仍保留实际需要的 GitBaseline，问答延续基线；Experiment 不再采集无消费者快照；删除重复图校验后，相关规则仍在公共候选校验中执行。这些是内部实现收敛，不是第二条执行主线。

## 3. 发现与本轮最小修正

### A. Scientific 缺少包依赖防退化检查

Coding、Experiment 及其他共享包已有 AST import 边界测试，Scientific 当时没有。已有 test_scientific_turn_boundary 验证返回契约，不能代替生产代码依赖检查。当前源码本身合规；缺口在未来错误依赖未被自动阻止。

新增 [test_scientific_boundary.py](../../../tests/scientific/test_scientific_boundary.py)，按既有测试方式扫描生产包，禁止依赖 Orchestrator、其他 Agent 或 CLI。不新增测试框架或生产抽象。

### B. TaskProposal 的旧类说明错误

[models.py](../../../packages/contracts/src/resagent2_contracts/models.py) 的 TaskProposal 仍写“Scientific suggestion”。实际任务提案由 Compiler 产生、Scheduler 接受，main 中也已有该误述。改为“Compiler-produced proposal”，未改字段、校验或执行逻辑。该类说明不属于 Compiler 当前发给模型的 CompilationDraft schema。

### C. 原则分散，评审容易只检查某一项

新增 current/DESIGN_PRINCIPLES.md，统一十二条现有约束及可操作的评审方式；ARCHITECTURE 原有原则段和根 README 改为链接统一入口，保留原 anchor。同步 docs 导航、开发指南与 tests README，避免再维护几份不完整原则列表。未新增 ADR，因为本轮没有改变既有设计取舍。

## 4. 验证与结论边界

修正后在 WSL ResAgent2 环境、PYTHONNOUSERSITE=1 下执行：

```bash
python -m pytest tests apps/cli/tests -q
# 1238 passed, 1 skipped in 31.28s
python -m e2e.mock_e2e
# run_golden completed, artifacts=9
# Coding/Experiment 各一个 completed Attempt
git diff --check
# clean
```

相比服务器实测 `2bad2d9a` 的 1237 项，只新增 1 项 Scientific 依赖测试。生产执行逻辑未修改，唯一源码改动是 TaskProposal 类说明；无需再做付费模型/GPU 验收。前轮真实整链、跨 OS 进程问答、20 次调用对账及其限制仍见[服务器收尾](CODE_HEALTH_SERVER_REVIEW_2026-09-23.md#verified-closeout)，不把它写成本次新提交重新跑过服务器。

包边界扫描只是第一道检查；Protocol 形状不保证替代实现自动守约，仍需接收边界及行为测试。权限仍不是 OS 沙箱；未知外部副作用、单写入者与非全局事务限制没有改变。后续修改按新原则入口检查全部受影响项，而非只核对 invoke 的数量。

本轮仅收敛原则文档、补一道依赖测试并纠正类说明；不合并 `fix/code-health`，不修改 main，不做相邻架构重构。
