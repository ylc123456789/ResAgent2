# Compiler 额度修复与 L3 验收收尾

日期：2026-09-16。产品提交：`e6688f387030489e65f8cb1650fdb22913f27b2c`，基于 `bf38e42`，schema **8.0** 不变。本次收尾只同步文档与已知限制，不改产品代码、模型、prompt、预算、研究工件或服务器安装指针，不重新运行付费测试。

结论：**Compiler 定向探针通过；新 L3 自主完成真实研究闭环，认可验收通过。** 这不是“全程零错误”、通用成功率或所有科学结论已被确定性验证的声明。服务器原 MANIFEST、旧 Run、失败现场与环境原样保留；本文作为独立复核与勘误记录，与原报告一起阅读。

## 1. 本轮只修了什么

- Compiler 的默认输入额度从 4096 调整为共享 `DEFAULT_AGENT_CONTEXT_TOKENS=128000`；CLI 与 real E2E 不再分别写不同默认值。
- 仍可通过 `RESAGENT2_COMPILER_CONTEXT_TOKENS` 单独覆盖。128000 是输入上限；CLI Profile 的 256000 是单次输出预留/上限（包含模型思考），不是另一个 Agent 输入额度，也不是固定消耗。
- Compiler 仍按正文 JSON 生成草案与审查，不使用原生工具、Session 历史或压缩；没有重构 Controller/Scheduler 或业务契约。

当前规则见 [CONTEXT 预算](../../current/CONTEXT.md#budgets)、[Runtime 接口](../../current/CONTRACTS.md#runtime-context)、[CLI 配置](../../../apps/cli/README.md)。源码入口：[CLI composition](../../../apps/cli/src/resagent2_cli/composition.py)、[real E2E](../../../e2e/real_e2e.py)。

产品提交的本地确定性基线为 **1015 passed, 1 skipped**；2026-09-16 文档收尾时在隔离 cwd 复跑，仍为 **1015 passed, 1 skipped**（20.99s），mock E2E completed、`git diff --check` 干净。当前文档、教程、导航与本记录共 12 份文档的 290 处本地链接/锚点检查通过。不冒称重新执行服务器 GPU 矩阵。

## 2. 编译探针与完整 L3 分开判定

| 验证 | 原始证据根 | 实际结果 |
|---|---|---|
| 旧失败 work_2 的编译探针 | `/root/autodl-tmp/e2e-ccd-e6688f3-K2pQ9x/` | 草案 2471、审查 4326 估算 tokens，2 次调用，合法任务图；不执行生成任务 |
| 新 L3 | `/root/autodl-tmp/e2e-l3-e6688f3-N7tQ3w/` | COMPLETED，3 个任务各一次 Attempt，50/200 调用，约 3h37m32s；无 ask_user，无外部研究协助 |

旧 `bf38e42` Run 在 work_2 的审查输入预算边界失败。新探针的 **4326 > 4096** 证明原额度会卡住这一形态；4326 是新生成草案的审查值，不是对旧失败输入精确长度的回算。完整 L3 的草案/审查则为 **2123 / 4069**，本身没有超过旧 4096；它证明新版本整体闭环，不单独证明加额度是这一次成功的唯一原因。新 L3 不是恢复旧 Run，也未注入探针的候选方案。

## 3. 完整 L3 的可复核结果

冻结预算：12 tasks / 每 Task 2 attempts / 200 LLM calls / 14400 秒 Run 执行时间；Flash 单配置。CLI 使用 1M 窗口、256K 输出额度、四个模块各 128K 输入上限。中性实验仓库从 `7974b1f` 新建副本，保留预检与人工准备记录，不把基础设施准备称为 Agent 自主完成。

- Run：`run_l3_schedule_e6688f3`；时间 `2026-09-15T21:56:38.947979Z` 至 `2026-09-16T01:34:10.505361Z`，user_wait=0。
- 两份真实文献检索工件均被 Scientific 读取；文献为检索摘要，不是论文全文。当前成功来源不代表 arXiv 限流已恢复。
- Compiler 一份任务图：代码核查 → 实现候选 → 正式对照；后两者按已接受依赖执行。
- 候选是 MultiStepLR，200 epochs 时在 100/150 衰减、gamma=0.1；保留原 cosine 分支，未改网络、数据划分、优化器或训练循环。README 与调度单测也有对应变更，不能将“只改变研究变量”误写成“只改一个文件”。
- 两个 schedule 各做冒烟及两个 seed 的完整训练，四份原始 `metrics.jsonl` 均为 epoch 0–199、200 条记录，学习率轨迹符合各自调度。
- 四次训练结束后，使用同一评估代码在官方 test 集评估各自的最佳验证集 checkpoint。没有看到依据 test 结果改候选、调参或选 checkpoint；这是补充 test 评估，不是 final-epoch test 或逐 epoch test。

| 配对 seed | cosine test accuracy | multistep test accuracy | cosine − multistep |
|---|---|---|---|
| 0 | 95.08% | 94.67% | +0.41 个百分点 |
| 1 | 95.02% | 94.81% | +0.21 个百分点 |
| 均值 | 95.05% | 94.74% | +0.31 个百分点 |

原始评估命令 stdout、整理后的文件、冻结工件与最终意见相符；28 个登记工件的 sha256 均核对通过。Scientific 实际读取比较结果、代码、模块报告及局限，给出不采用此候选的 `refutes` 意见，并限制在本次设置。

测试方评分 **10/10**；主开发认可硬门槛满足与闭环通过，保留以下质量边界，不把满分当无缺口证明：候选实际并不明显比 cosine 更简单；只有两个 seed，不能凭此断言“差异已证明只是噪声”或普遍优劣。更稳妥的结论是“本次没有观察到采用收益，重复数不足以作稳健推广”。原意见不改写，后续评价应保留此区别。

计量：50 条主 trace、50 个唯一 call_id、50 次 HTTP 尝试，与 Run.llm_calls_used 一致；分布为 Scientific 7、Compiler 2、Coding understand 5、Coding modify 14、Experiment 22。全部主调用 model 为 `deepseek-v4-flash`。trace 权限 0700/0600 已复核；测试方报告密钥定值扫描 0 命中，主开发不重新输出或收集密钥。

## 4. 原报告勘误与恢复记录

| 原报告说法 | 复核后的口径 |
|---|---|
| 4 份文献检索工件 | 实际登记 2 份：`artifact_sci_2412d63b831b7c87`、`artifact_sci_da36fdc2821c8c71`；另有两次读取动作，不能把读取再计为新工件 |
| 无错误 | 主 trace 没有 JSON 解析失败，但 Session 有命令、参数与完成校验失败；最终均自主恢复，无终止性失败 |
| 候选不同于之前探针，因此证明独立设计 | 探针同样包含 100/150、gamma=0.1 的 MultiStepLR；命名不同不证明方法不同。新工作区、短目标与无探针方案注入才是独立运行的依据 |
| 安装约 1.5 小时，迫使 3 seed 降为 2 seed | run_setup 实测 4419.55 秒，约 73 分 40 秒。安装占用预算属实，但本轮原始计划只要求预算允许时多于一对，未找到明确的 3→2 降级决策 |
| 唯一问题是安装慢 | 还暴露了跨实验指标身份限制，见下节 O1 |

关键恢复（事件编号属于各自 Session）：

- Coding modify：observation 34 的单测发现 tests 目录不可导入；创建 `tests/__init__.py` 后重新验证，16 个测试通过。
- Experiment：observation 42/44 的结果整理命令 exit 1，后来修正；四次训练与四次 test 评估本身 exit 0。
- Experiment：observation 61 拒绝未正确交付 evidence 的 finish；63 拒绝 evidence_files/residual_risks 放错参数层级；66 拒绝跨文件同名指标不同值；最终 observation 82 的 finish 后完成检查通过。
- 最后一次纠正使用组别/seed 限定指标键；原始逐 epoch JSONL 与配置仍留在工作区，整理后的证据与原始评估 stdout 数值一致。不能把为了满足提取器而重排 JSON 当成新增独立测量。

`action_valid=true` 与工具入参、命令执行及领域完成检查不是同一层。此次 50 条主 trace 全 true 不能推出 Session 全程无拒绝；测试规程已同步要求分别核对。

证据定位（均相对于新 L3 根）：

- `MANIFEST.md`、`protocol/PRE-CHECK.md`：原测试报告与准备记录，原文不覆盖。
- `traces/llm_traces.jsonl`：完整模型请求/返回，含上述动作及纠正过程。
- `data/state/run_l3_schedule_e6688f3.json`：最终 Run、任务、工件身份与调用总账。
- `data/sessions/{coding,experiment,scientific}/`：动作、观测、完成检查与工具回执。
- `workspace/pytorch-cifar/runs/`：四组训练、test 评估、配置与比较文件；`data/artifacts/run_l3_schedule_e6688f3/` 为冻结副本。

<a id="follow-ups"></a>

## 5. 后续优化项（仅记录，本轮不实现）

### O1：多组实验的指标身份

**事实与边界：** [Experiment finalizer](../../../packages/agents/experiment/src/resagent2_experiment/completion.py) 把各 JSON evidence 的顶层数值按规范化名称合并，路径/组别/seed 不参与身份，数值配置 `seed` 也会被读取。不同 seed 的同名指标因此被判冲突；嵌套表与 JSONL 不自动展开。本次模型给键加限定前缀并重整证据后恢复，不代表这一限制已经消失。当前调用约定见 [结果契约](../../current/CONTRACTS.md#payloads)。

**后续最小方向：** 先补“同名指标来自不同组/seed”“同一测量真的冲突”“数值配置不是测量”三类边界用例，再选一个明确、可追溯的指标身份约定，复用现有证据派生路径。不得简单取消冲突检查、最后文件覆盖、靠模型口述数字，或扫描整个工作区猜身份；不为这一个案例新增统计平台。是否需要改字段/schema 在该专项审查后决定，本记录不是已批准实现方案。

**验收：** 标准库多组夹具即可；不同测量可区分、真实冲突仍拒绝、每个导出值能定位源文件/字段；原始配置/日志可以保留且不必为交付重写数值。无需先重跑 GPU L3。

### O2：新环境的依赖安装成本

**事实与边界：** `python -m pip install torch torchvision` 的 run_setup duration=4419.55s，约占本 Run 三分之一。新 Run/Workspace 绑定与 pip/conda 包缓存是两回事；耗时本身不能证明缓存损坏或未配置，也未核实每个包的缓存命中。现有审计和跨 Run 隔离不因想加速而跳过，见 [资源契约](../../current/CONTRACTS.md#resources)。

**后续最小方向：** 先只读核对实际下载 URL、缓存目录/命中、包版本和 CPU/CUDA 发行选择，将下载、解包安装与训练时间分别记录；优先在既有部署/包管理器配置内解决可重复下载。不把依赖缓存放回 ResearchRequest，不复用旧 Run ID 冒充新试验，不新增可变共享环境池或新资源管理层。

**验收：** 在授权的独立环境比较安装日志、实际耗时、环境绑定与 audit 结果；不把缓存命中当认证，也不通过删除旧环境制造对照。任何新安装/下载/费用需另行确认。

## 6. 收口边界

本轮收尾内容为产品默认值修复、对应测试、文档同步和上述待办；不混入指标或环境改造。旧 L3 不恢复、不迁移，服务器证据与 editable 指针不自动改动。后续新付费运行仍按 [L3 规程](../../guides/L3_RESEARCH_TEST.md) 重新确认成本；一次成功不授权无限复测。
