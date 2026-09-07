# 接口契约优化计划

基线：`main@2fbf00c`。工作分支：`fix/interface-contracts`。

目标：正常科研功能不变，使替代模块仅凭公开契约即可正确接入；减少重复机制、无效声明和隐含约定。非法输入从错误放行变为拒绝、E2E 补入预算限制及公开字段删除，不冒充完全无可观察变化。

## 设计依据与范围

- [Parnas 1972：模块信息隐藏](https://www.cs.lafayette.edu/~gexia/cs301/resources/parnas.html)：按需要隐藏的设计决策分工，不是越多包越模块化。
- [Meyer 1992：Design by Contract](https://se.inf.ethz.ch/~meyer/publications/computer/contract.pdf)：前置条件、结果保证与不变量。错误/生命周期/预算的具体安排是本项目应用，不是照搬语言机制。
- [MCP 工具规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)：结构与说明一致，描述提示不能冒充权限执行。
- [A2A 1.0 规范](https://a2a-protocol.org/v1.0.0/specification/)：隐藏实现，显式生命周期、消息/成果和重复调用语义；不把本地 Task/Attempt 改成 A2A Task。
- [Pydantic AI 工具定义](https://pydantic.dev/docs/ai/tools-toolsets/tools/) 与 [SWE-agent 论文](https://arxiv.org/html/2405.15793v3)：真实类型、工具说明和模型反馈相互一致；不迁移框架、不新增 LLM。
- [Composition Root](https://blog.ploeh.dk/2011/07/28/CompositionRoot/)：独立入口可保留少量装配重复。只共用机制，不合并 CLI/E2E。

类型字段以实际模型及 CONTRACTS 为准；ARCHITECTURE 记录职责与规范，INTERFACES 六张卡记录输入、返回分支、状态、副作用、失败、重入及替代实现保证，不再复制字段全集。

## 分阶段状态

| 阶段 | 范围 | 状态 |
|---|---|---|
| P0 | 规范、基线、公开变化裁定 | 完成；schema 5.0 裁定已落地 |
| P1 | 共享图候选判据；Compiler 纠错 + Scheduler 接收 | 本地完成 |
| P2 | 编译失败携带本次消费；不读实现属性 | 本地完成 |
| P3 | 删除死缓存、无效兜底；schema 5.0 注册表只留真实声明 | 本地完成 |
| P4a | Composer 对最终标题/分隔符计量 | 本地完成；runtime/工作集/E2E 确定性测试 146 passed |
| P4b | 共用 Prompt 适配及 Scientific 工件登记，保留两个组合根 | 本地完成；定向测试 32 passed |
| P5 | 本地集成、同步文档、交接真实服务器验收 | 本地完成；a2c6afa 首轮服务器核心通过、E2E 8/9，追加修复待重新验收 |

注册表 `request_model/result_model/side_effects/permission_policy/completion_evidence` 没有运行消费者，用户已明确批准删除并按现有版本规则升至 5.0。旧 4.0 Run 不续跑，既有记录原样保留，不引入迁移或兼容分支。

基线验证：隔离 cwd、`PYTHONPATH=/home/cyl/ResAgent2`，本地全量 `703 passed, 1 skipped`；不使用服务器历史测试代替本轮验证。

阶段验证：P1 orchestrator `184 passed`；P2 Compiler/Controller `70 passed`；P3 集成全量 `724 passed, 1 skipped`。P3 删除的文献 summaries 只是无人读取的累计副本，完整工件、短预览、观察 ID 和历史不动。缺失暂停问题走既有失败出口，不再生成虚构问题。公开删字段见 CONTRACTS 的 CapabilityRegistry 说明及 §18。

最终本地集成：隔离 cwd 全量 **740 passed, 1 skipped**，mock E2E completed，`git diff --check` 干净。额外锁定：旧 4.0 Run 读取拒绝后文件字节不变；共享图候选拒绝进入一次有界纠错；typed LLM 模型实例经值投影重验，不能利用 model_copy 绕过 schema。

本轮未增加 MCP/A2A、插件或统一 bootstrap；正常任务目标和执行策略不改。可观察变化明确包括 schema 5.0、不再接受空图/跨轮依赖、标题分隔符计入预算、E2E Compiler 开始执行 4096 模块上限。E2E 保留其原有无 ModelProfile 的测试配置；CLI 保留配置驱动的 ModelProfile，适配器在底层提供模型容量 hook 时取更小值，不强行统一入口配置。

## 最小验收

1. 图候选：空图/空追加、跨 WorkRequest 依赖在接收前拒绝；合法本轮依赖通过；拒绝不修改既有图。原生与替代 Compiler 使用同一接收规则。
2. 计量：成功/失败、零次/调用后失败、连续调用均为本次消费；保留 cause。非法替代实现异常受控失败，不将未知消费描述为已知零次。
3. 精简：保留 literature 工件、短预览、观察 IDs 和事件；不再累计无人消费的 summaries。暂停必须有真实问题，不能虚构业务默认值。
4. 预算：估算最终渲染（含标题/分隔符），边界内必需段可容纳，超限在 LLM 调用前拒绝；仍是字符估算，不宣称 tokenizer 精确值。
5. 组合根：CLI/E2E 各自选择依赖和配置；共享机制独立测试。真实 E2E 不绕过 Composer，变化的 Prompt 包装与拒绝行为明确记录；登记必须同轮可读、按 Run 隔离。
6. 每批针对性测试 + `git diff --check`；本地全量在隔离 cwd 运行，再跑 mock E2E。最终针对确切 SHA 交接真实模型验收，不使用历史成功覆盖未测代码。

## 交付纪律

每阶段独立提交；本轮不自行合并、push 或上服务器。服务器只同步/测试/分析，由用户指定的另一 AI 执行；保留失败现场、full trace 与安装指针，不清理环境/缓存/数据集。验收要求在本地完成后另附，未验收项明确保持未完成。

代码验收基线：`a2c6afa2da07b33f864be74c9d6b067403b3ca8f`。真实模型与 CLI 检查按 [服务器验收单](INTERFACE_OPTIMIZATION_ACCEPTANCE.md) 执行；该文档提交不改变待测产品代码。

## 首轮验收反馈与追加收口（2026-09-07）

原 P0–P5 的代码及本地集成均已完成，不是还剩未实施的大阶段；但最终服务器验收与合并没有完成。`a2c6afa` 服务器 740 passed, 1 skipped、E2E 8/9、CLI 2/2；编译上下文、真实调用计量与权限核验通过。失败现场保留在 `/root/autodl-tmp/e2e-interface-a2c6afa-D5CYL6/`。

原始 trace/Session 修正了测试报告的根因描述：先跑未实现代码导致实验失败，剩一名额时把修改和正式实验压入 Coding；Coding 已成功编辑，但正式训练命令被验证工具拒绝，随后继续重读并耗尽预算。最后零名额仍调用 Compiler，先返回两任务被拒，再返回空图。不是“Coding 失败后才实验失败”，也不是“两次均无故空图”。完整代码片段曾多次可见，不能只归因于截断或模型抖动。

追加改动仍保持小范围，不新建状态机/Agent/公开字段，不增预算、不放开命令策略、不改 CLI/E2E 装配或场景：

| 收口批次 | 内容 | 提交与状态 |
|---|---|---|
| R1 | Scientific 区分已知前置修改和假设未来失败；编译/审查共用能力语义；审查含实际输入投影、约束和依赖 | `a458405`，定向 55 passed |
| R2 | Controller 零任务名额预检：不调用任何 Compiler；当前 WorkRequest/Run 都保留预算失败；已接受工作仍可恢复 | `6d81045`，集成 747 passed, 1 skipped |
| R3 | 生产组合根适配器验证完整审查仍计入 4096，超限前拒绝且消费不多算；同步验收单 | `0d611a7`，全量 749 passed, 1 skipped；mock completed；服务器待验收 |

本次 schema 仍为 5.0，旧记录原样保留。预算预检是确定性保证；规划/职责规则和语义 review 仍依赖模型判断，单测只证明输入、反馈和拒绝链正确，不能宣称永久消除重读循环。

新代码验收基线为 `0d611a7aaaf069e00c42baf534057f5decde126f`，按 [任务职责收口验收单](INTERFACE_SCOPE_ACCEPTANCE.md) 执行。新一轮 full trace 验收通过后，再由用户决定是否合并 main；目前未合并、未 push。
