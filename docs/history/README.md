# 决策与历史

这里回答“为什么改、当时做了什么、怎样验收”。**不是当前规范，也不是入门必读列表。** 当前行为查 [架构](../current/ARCHITECTURE.md) / [接口与契约](../current/CONTRACTS.md) / [模型上下文](../current/CONTEXT.md)。

## 2026-10-06：通用网页工具

`feat/web-search-hardening` 从 `main@0e5bb96` 增加通用 `web_search` 与独立 `web_fetch`：默认复用DeepSeek key使用托管搜索，也可显式选择Tavily。沿现有Components/Capabilities/Scientific分层、Registry和research index交接；额外搜索模型请求共用Runtime预算/截止时间/trace，Scientific仍按用户目标选择材料，没有固定“网页→论文”顺序；schema保持23.0。本地及服务器最终回归均1926 passed / 1 skipped、mock13工件；服务器pip check clean，本地pdfminer-six缺cryptography的环境差异如实保留。`f69df60` 已通过大陆服务器探针与两个真实任务验收，并独立核对32/32冻结hash、60 public + 5 private清单及共享用量。实测搜索snippet全空、结果含噪声，记录为质量限制，不将任务完成等同检索精度优秀。本轮已收尾，保留独立分支、未合并main。完整结果及后续部署检查见[实施及交接](reviews/WEB_TOOLS_2026-10-06.md)，取舍见[ADR-0025](decisions/0025-general-web-capability.md)。

## 2026-10-05：文献搜索合并收尾

用户确认采用后，fix/literature-search@1735d40以合并提交ac240e3进入main；保留开发分支、原阶段记录和服务器失败现场，schema仍为23.0。生产代码与已验收5ff44f1一致；Ubuntu-D九包源码指针、合并代码一致性、mock completed/13工件及文档检查通过。current的接口/上下文/架构与代码一致，当前联网范围为arXiv/OpenAlex检索及PDF获取，未实现通用网页搜索。相对main的收益、混合排序和模型表述边界见[对照与收尾](reviews/LITERATURE_MAIN_COMPARISON_2026-10-05.md)。

## 2026-10-05：文献搜索相对main的完整Agent对照

main64de57f与candidate72447ee（代码5ff44f1）以相同模型/预算完成三目标各一轮、共六Run；main交付1/3、候选3/3，274工件与45请求已对账。已知论文打平，候选在局限任务取得新增已读依据；漂移选文各有优势，候选更早阅读并完成。固定查询排序混合，完整摘要保存避免实际反例丢失；科学条件表述仍有过强及错标，不能当作科研全正确。该评估支持采用，后续已按用户授权合并；协议、取舍和证据见[相对main的交付对照](reviews/LITERATURE_MAIN_COMPARISON_2026-10-05.md)。

## 2026-10-05：文献搜索独立分支与标题修复

本阶段基础已以64de57f合入并推送main，搜索在fix/literature-search独立验收；后续合并状态见本页收尾。title完整短语修复的五标题对照为arXiv hit@5 0/5→5/5、OpenAlex保持5/5；5ff44f1服务器1765 passed / 1 skipped、mock13、真实Scientific两目标及64public/3private清单、42件工件已独立复核。记录修正OpenAlex未印证、9份摘要、正文仅前235行及两次完成纠错；功能验收通过；后续相对main的对照已完成，见上一节。本阶段标题验收见[修复与验收](reviews/LITERATURE_TITLE_SEARCH_2026-10-05.md)。

## 2026-10-05：目标驱动的文献检索

在同一分支完善 literature_search 的小批结果、范围、来源、分页和错误反馈，保留完整来源摘要；Scientific 按用户目标与材料缺口选择行动，不规定检索阶段或论文配额。schema仍为23.0；本地1751 passed / 1 skipped、mock completed / 13工件。本地验证与服务器测试步骤见[实施及交接](reviews/AGENTIC_LITERATURE_SEARCH_2026-10-05.md)，服务器1faab0f的真实小测与分页探针已完成，工件/调用对账通过；独立复核发现title逐词AND对完整标题的召回问题及服务器报告遗漏的恢复过程，见同一记录的验收章节。不把功能通过等同商业搜索质量。

## 2026-10-05：文献基础与通用工具合并收尾

已验收基础以e03a151为代码切点合入main，涵盖单篇文献、外部导入、证据/验证边界、统一工件ID、PDF300秒配置及文本窗口；搜索增量从1faab0f独立继续验收。服务器e03全量1727/1skip、mock13、PDF/文本原件与清单已只读复核。各阶段L3、资料覆盖和139.6秒计时证据边界见[基础收尾](reviews/LITERATURE_BASELINE_ACCEPTANCE_2026-10-05.md)。

## 2026-10-05：PDF 超时与工作区文本工具

PDF 默认解析上限调为300秒并支持CLI配置；工作区读、搜、创建和替换共享10 MiB处理规则，读取支持字符窗口、保留原换行，搜索报告不完整覆盖。公共schema仍为23.0；本地1727 passed / 1 skipped、mock completed / 13工件，真实模型补测尚未执行，本地pip check仍有已知缺依赖项。后续测试方报告服务器回归和PDF/文本两场景通过，原阶段状态与补测事实分开保留。实施、取舍与服务器补测见[本轮交接](reviews/PDF_TEXT_TOOLS_2026-10-05.md)；旧120秒超时和L3记录保持原事实。

## 2026-10-04：统一工件 ID（schema 23）

所有登记入口统一 artifact_ 加完整 64 位 SHA256；保留 Task 槽位、导入复用、来源快照和最终报告原语义，移除超长命名特例。旧 schema22 Run 拒绝恢复，材料原样保留。
本地全量 1686 passed / 1 skipped、mock completed / 13 工件；真实模型/L3尚未执行，分支未合并。
详见 [ADR-0024](decisions/0024-unified-artifact-identifiers.md)和[当前 L3 交接](reviews/L3_SCHEMA23_HANDOFF_2026-10-04.md)。

## 2026-10-04：完整代码审查与开发基线

对 refactor/literature-foundation@361e9fc / schema 22 完成分层代码审查、全量本地回归和临时目录边界复现。
整体架构可继续复用；确认的七组冻结证据、验证记录、工作输入和 Git/身份边界问题已局部修复，并补行为回归；schema 仍为 22.0。
详见[完整审查](reviews/CODE_REVIEW_BASELINE_2026-10-04.md)及[服务器 L3 交接](reviews/L3_SCHEMA22_HANDOFF_2026-10-04.md)。
修复集成本地回归 1675 passed / 1 skipped、mock completed / 13 工件；当前文档已同步。本次没有执行真实模型或服务器 L3，不把旧验收结论推广到当前版本。

<a id="literature-foundation-closeout"></a>

## 2026-10-03：文献基础与外部导入（schema 22 验收完成）

refactor/literature-foundation 的产品 ef5f836 + 61f4592720ca26985f0e598038611eb963806926
已完成服务器回归和真实模型补测：1641 passed / 1 skipped、pip check 干净、mock 13 工件。
首轮 A/B 是合成 PDF 链路验收；后续 MobileNets 真实论文成功与真实模型解析失败补齐缺口。
Guo 论文两次 120 秒解析超时的 paused 现场保留。详见
[验收收尾与性能边界](reviews/LITERATURE_FOUNDATION_ACCEPTANCE_2026-10-03.md)。
该分支已推送至 361e9fc，尚未合并；后续超时配置计划尚未实现。

## 2026-10-03：文献基础改造（原本地阶段记录）

`refactor/literature-foundation` 将搜索回执、单篇论文、原始 PDF 和解析文本分开登记，
并把访问记录移出运行门禁。schema 升为 21.0，旧 Run 不迁移；外部论文导入留待后续。
本地全量 **1612 passed / 1 skipped**、mock completed（13 工件），隔离项目环境依赖检查通过。
本轮尚未进行真实服务器文献/LLM 验收，用户级依赖差异、测试边界及短 Run 步骤见
[实施记录与测试交接](reviews/LITERATURE_FOUNDATION_2026-10-03.md)，设计见
[ADR-0022](decisions/0022-paper-materials-and-access-records.md)。

<a id="prompt-shell-closeout"></a>

## 2026-10-01：提示词一致性、共享 Shell 与委托约束收尾

`refactor/prompt-consistency` 的产品验收基线为 **`e902698d13e5f1391e9a6b315f5b89d03a620950`（schema 20.0）**。本轮文档收尾不改变产品，也不表示已合入 main。服务器 pip check 干净、全量 **1563 passed / 1 skipped**、mock completed（13 工件）；A/B/C 定向补测均通过，并已只读核对原始记录。A/B 补齐旧验证失效与独立失败回执覆盖；C 在相同原始输入下验证指定方式完整传递、创建被拒后不执行且如实收尾。C 两次只读批准只产生一次实际执行，不把批准次数写成执行次数。

此前 `c13f672` 的五个行为场景和校准 L3 通过由测试方报告，其中依赖成功修复路径补齐了 09-29 的未覆盖项；后续 schema 20 未重跑完整 L3。各提交的验证范围、首轮失败/覆盖缺口、报告勘误、证据路径和剩余边界集中在 [本轮验收收尾](reviews/PROMPT_SHELL_ACCEPTANCE_2026-10-01.md)。原报告与现场保留，不把单次模型成功说成普遍保证；没有本轮必须追加的产品修复或测试。

<a id="code-health-closeout"></a>

## 2026-09-29：代码健康、固定 Interpreter 与 GPU 环境验收收尾

`fix/code-health` 本轮实现随本次文档收尾快进合入 `main`，保留分段提交与开发分支。产品验收基线为 **`f8439c23d19ff287bfd1e996ea8a329eae2b8cbd`（schema 19.0）**；其后的收尾只修改文档，不把文档提交说成重新运行过模型或 GPU 测试。此前代码健康修复、完整科研目录、Validation 阶段 1/2、任务完成状态、文献与 trace 修复、固定 Interpreter，以及环境事实和执行 Agent 提示一并进入主线。旧阶段记录中的“未合并”“待验收”仅描述当时状态。

测试方在服务器 26089 报告该 SHA 的 `pip check`、9 包源码导入核对通过，全量 **1510 passed / 1 skipped**，mock completed（13 工件）。正常 GPU 小测有真实 CUDA 前向和矩阵运算；完整 L3 `run_l3_calibration_v1_20260929_r4` 使用 RTX 4090 D、driver 580.142，完成 200 epoch GPU 训练和温度缩放，测试方评分 10/10。测试方从逐样本文件独立复算 accuracy/NLL/ECE/MCE，与交付一致，并检查 checkpoint hash、切分和 test 使用边界。test accuracy 0.9503 不变，NLL 0.193978→0.170550、ECE-15 0.028542→0.010553；MCE 0.706732→0.748345 的上升与单样本分箱限制如实保留，不宣称所有指标都改善。

本次合并准备已只读核对服务器 MANIFEST、产品 HEAD 和原始 Run JSON：MANIFEST 记录上述验收基线，产品 HEAD 与之相同；原始 Run 为 completed，三个任务均 completed，`completion_violations=[]`、`terminal_error=null`，最终 verdict=supports，账本记录 116 次请求，comparison_results 已登记。本次没有重新执行逐样本复算或完整 trace 审计；科研评分及复算结果以上述测试方验收为依据。原始证据保留在服务器 `/root/autodl-tmp/resagent2/runs/l3-calibration-v1-20260929-r4/`（MANIFEST.md、logs、traces、data、protocol、workspace），不修改现场。

剩余范围明确保留：

- 继承 CPU-only 依赖的小测验证了诊断、ask_user、批准、修复尝试和失败复验；网络阻断后 Run 耗尽 50 次调用预算而 failed。它不是“依赖已修复成功”，也不证明网络失败处理效率；成功修复后继续任务的路径尚未补验，本次不再补测。
- 论文服务和依赖下载仍受外部可用性影响；第三论文源尚未实现。L3 中 pip 缓存占满根分区，由测试方将缓存移到数据盘并恢复后继续，属于部署维护，不是产品已实现自动磁盘管理。
- Validation 阶段三按[原决定](reviews/VALIDATION_DESIGN.md)暂缓，不作为合并前提。科研方案、证据含义与任务语义仍由对应 Agent 判断。
- 主线 schema 从 13 升到 19；schema 18 及更早 Run 不支持在新版恢复。旧 Run、Session、trace 和工件保留，需要继续研究时新建 Run，不自动迁移旧状态。

## Interpreter 固定代码化（已完成本轮验收）

2026-09-28 按[修改方案](reviews/DETERMINISTIC_INTERPRETER_PLAN_2026-09-27.md)在开发分支实现固定 Interpreter，schema 19.0：保留科研索引和反向交接，组织子 Agent 已记录报告，移除每轮 LLM 二次简报；上下文共用原预算机制，补齐长 JSON 正文的字符窗口读取。原观察与 validation 边界保持不变。首次服务器测试方报告 1480 passed / 3 failed / 1 skipped、mock completed；三项错误测试随后修正，后续全量回归与真实 L3 已完成，最新结果及覆盖限制见本页收尾记录。原因与边界见方案末尾的实施记录及 [ADR-0020](decisions/0020-deterministic-work-interpreter.md)。

## L3 规程换题（后续已执行）

2026-09-26 更新[现行 L3 指南](../guides/L3_RESEARCH_TEST.md)：保留真实 CLI、短目标、有限问答和独立验收，研究题目改为 CIFAR-10 / ResNet18 的置信度校准；覆盖当前统一 IO、Interpreter、完整科研目录和明确产物交付。官方基准依据与取舍列在指南中；2026-09-26 当时只完成规程设计，后续 R4 验收见本页收尾记录。旧学习率调度[规程](reviews/L3_RESEARCH_TEST_2026-09-16.md)原文归档，原验收不重新评分。

## Validation 分阶段修改（阶段记录）

2026-09-25 在 `fix/code-health` 完成阶段 0 盘点和阶段 1 完成候选检查，产品提交 `8fc0e79`、schema 16.0。本地全量 **1291 passed / 1 skipped**、mock completed（13 工件）；测试方报告服务器相同回归通过，Coding/Experiment 真实定向反馈探针 **40/40 PASS**。原始 Session、trace、脚本、账本和冻结工件已独立复核，支持通过；验证器弱断言已离线补核，无新增必补测试。结果与固定任务覆盖边界见 [独立复核](reviews/VALIDATION_PHASE1_TEST_2026-09-25.md#independent-review)。随后阶段 2 的 Run 级明确产物要求已完成实现与服务器验收（schema 17.0），阶段 3 新增运行前 Validation 于 2026-09-26 决定暂缓；证据与收尾见[分阶段方案](reviews/VALIDATION_DESIGN.md)。记录当时分支未合并；本次主线收尾见上文。

## 完整科研目录与问答阅读（阶段记录）

2026-09-24 在 `fix/code-health` 继续收敛 WorkRequest 交接：本轮带引用简报保持原职责，Scientific 收到更新后的完整科研目录；子任务成对问答按原来源入目录，并打通原件读取。schema **16.0** 删除 index_changes，不保留旧反馈兼容；登记表仍为唯一来源，批准与恢复作用域不变。该记录当时处于实现与验证阶段，服务器尚未验收；见 [ADR-0018](decisions/0018-complete-index-and-paired-answers.md) 与[服务器验收计划](reviews/COMPLETE_INDEX_QA_TEST_2026-09-24.md)。此前 schema 15 的结果保留在下一节，不能代替本轮测试。

## 科研目录与 Interpreter（阶段记录）

2026-09-23 在同一 `fix/code-health` 分支实现科研目录与带引用的反向简报，schema **15.0**。产品 `b31648d`、专项回归 `42efaa1`，本地 **1269 passed、1 skipped**，mock completed。原产物登记表保持唯一权威，Interpreter 与 Compiler 并列，权限、预算和 Agent 单入口规则保留。服务器 `36360f85` 已完成两条公开整链（27/132 次请求）和 CUDA 完整训练，核心目录/简报/引用通过独立复核；后续 CLI 定向补测已验证 Experiment 任务问答和完整 Controller 续跑（14 次调用、completed），GPU 采样封存、脚本留存和核验整理完成，三处验收缺口关闭。候选文件名错误的失败处理、任务答案的跨层可发现性作为后续设计事项保留。见[服务器复核与最终收尾](reviews/RESEARCH_HANDOFF_SERVER_REVIEW_2026-09-24.md#verified-closeout)，不把测试方通过计数等同于计划全部覆盖。设计见 [ADR-0017](decisions/0017-research-index-and-work-interpreter.md)，测试步骤与证据要求见[本轮交接](reviews/RESEARCH_HANDOFF_TEST_2026-09-23.md)。此前 schema 14 的通过结论只适用于原测试提交。

## 主线健康审查

2026-09-22 对合并后的 `main@5fe2c7f` 做代码健康审查，在 `fix/code-health` 分阶段修复并清理遗留代码，schema 14.0。原产品基线 `51c3238` / 服务器实测 `b6258c7` 的回归 **1224 passed、1 skipped** 与 mock 通过，两条真实模型整链分别预算耗尽和最终工件契约错误。复核发现批准恢复语义缺口、Compiler 能力说明陈旧及 Scientific 完成检查不完整；2026-09-23 已按原机制修复，产品与测试基线 `9b425f2`，本地与服务器 **1237 passed、1 skipped**、mock 通过；服务器 `2bad2d9a` 的真实公开入口整链也通过，跨进程批准、Experiment 任务内问答和最终工件完成，20 次调用与账本一致。原始证据已复核，验收范围内具备合并条件；记录当时分支尚未合并。Scientific verdict 曾反馈纠正、非法 data 纠正由确定性覆盖及中间算式文字瑕疵均保留于[收尾与边界](reviews/CODE_HEALTH_SERVER_REVIEW_2026-09-23.md#verified-closeout)。原始发现见[主线审查](reviews/MAIN_CODE_HEALTH_REVIEW_2026-09-22.md)，前轮实施见[原交接](reviews/CODE_HEALTH_TEST_HANDOFF_2026-09-22.md)，本轮证据勘误、实现及测试步骤见[服务器复核与根因修复复测](reviews/CODE_HEALTH_SERVER_REVIEW_2026-09-23.md)。

2026-09-23 又对当前分支做[架构与流程不变量复核](reviews/ARCHITECTURE_INVARIANTS_REVIEW_2026-09-23.md)：检查模块独立、依赖倒置、开闭原则的适用边界及控制/恢复/预算/权限/证据流程，未发现本分支破坏核心设计；补齐 Scientific 包边界测试和 TaskProposal 旧说明，汇总[设计原则](../current/DESIGN_PRINCIPLES.md)。本地 1238 passed / 1 skipped，mock 通过；执行逻辑未变，服务器实测仍为上段提交，当时分支继续不合并。

## 此前已完成的主线

Run 控制简化已完成修复轮定向功能复核，并将 `refactor/run-control@84063e4` 快进合入 **main**（schema **13.0**，产品 `602ffee`，实测 `8b071e6a`）：Coding 删除动作、批准恢复时环境核验两处缺陷关闭，本地/服务器 **1153 passed、1 skipped**。4 个真实模型场景覆盖 5 项功能；固定 Task 执行不等于 Scientific 完整 Run E2E。原始 Session 拒绝快照未保存、只读探针重跑覆盖旧状态等限制保留在[修复轮复核](reviews/RUN_CONTROL_SERVER_REVIEW_2026-09-22.md#fixed-round)。合并保留分段提交与开发分支，无需为记录勘误再跑模型/GPU。服务器仍使用固定 `projects/ResAgent2`。设计见[方案](reviews/RUN_CONTROL_SIMPLIFICATION_PLAN_2026-09-22.md)，复现步骤见[测试交接](reviews/RUN_CONTROL_TEST_HANDOFF_2026-09-22.md)。

统一 Agent IO V2 已完成分阶段服务器验收（schema **12.0**，产品实测 `22347c5`，开发分支 `refactor/unified-agent-entry`）：三个 Agent 共用单一 invoke 和报告/工件协议；复测 **1071 passed、1 skipped**，完整 CUDA 场景与分析探针通过。repair 保留“自动谓词 FAIL、包装命令人工复核通过”，命令确认采用既有真实流程与新增确定性拒绝测试的组合证据；详见 [最终复核](reviews/UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md)。该实现已随 `refactor/run-control` 一并合入 main。最初本地结果另保留于[本地验收记录](reviews/UNIFIED_AGENT_IO_V2_ACCEPTANCE_2026-09-20.md)。

Tool / Components 职责整理已完成服务器四个小探针及原始证据复核（实测 `6672376`，产品 `4cdb725` / `54fab5c`），schema 10.0 不变。新增 Components，Capabilities 聚焦模型 Tool，Runtime 引擎不改；当前每个模型可调用 Tool 都有独立实现文件，`__init__.py` 只负责公开导出。`refactor/tool-components` 的实现已包含于 main 的 `f771a70e` 基线。设计见 [ADR-0015](decisions/0015-tool-components-boundary.md)，本地验证、29 次模型调用、文献阅读范围和报告勘误见 [最终复核与收尾](reviews/TOOL_COMPONENTS_ACCEPTANCE.md#verified-closeout)。四探针不代表重跑科研 L3。

自然语言字段精简与答案键规范已完成阶段验收（产品 `c03115b`、`3476222`，最终实测 `ee7d821`，schema **10.0**）：Scientific 完成摘要从 opinion.statement 派生，提问背景进 text，Compiler 不再重复填写图级说明；三个 Agent 与公共问答模型共用 AnswerFieldName。旧版 Compiler 新旧六探针通过，新版三问答补齐 Coding 自问及 Experiment 经 CLI 回答，结果 222/6/F1；本地/服务器 1052 passed、1 skipped。总计 21 次调用，单探针均未超过 20。Coding 测试驱动恢复时重给完整额度的偏差和后续复用要求见 [最终复核与收尾](reviews/SEMANTIC_FIELD_SLIMMING.md#verified-closeout)，不能写成预算扣减已验证；本轮无须额外付费重跑，不改写旧报告/失败现场。

Compiler 默认额度与 L3 已完成收尾（产品 `e6688f3`，schema **8.0**）：Compiler 与三个 Agent 共用 128000 输入默认值，独立覆盖入口保留，CLI/E2E 同源；不改变 JSON-only 编译/审查或增加 Compiler 压缩。旧失败工作请求的编译探针通过，新 L3 自主完成四次 200-epoch 训练并交付负结果，50 次调用与账本一致。通过不等于全程零错误；原始 trace 中的恢复、指标身份限制、安装成本和报告勘误见 [验收收尾与待办](reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md)。

前一阶段原生工具调用、串行批次、统一调用预算和上下文分配已完成阶段验收（产品 `f98b6fd`，schema **8.0**）：三个Agent共用Tool、Loop、Session与Composer，Compiler仍用正文JSON；移除TaskBudget.max_steps及隐藏50步上限，材料按权重起步、空余按优先级借用，历史压缩保存检查点，整包真正不足仍报错。本地/服务器全量1014 passed、1 skipped；六个真实小探针完成。主开发复核并纠正了报告的累计计量和暂停状态，详见[最终结果、勘误与边界](reviews/CONTEXT_ALLOCATION_REVIEW.md#verified-closeout)、[分阶段实施](reviews/RUNTIME_CONTINUATION_PLAN.md)。旧L3保持暂停、不迁移；该轮小探针不是科研级长任务通过声明。

上下文语义、128K 预算与文献呈现已完成阶段验收（产品 `3efce21`，服务器实测 `ba84547`，schema 7.0 不变）。状态/历史语义、共享失败诊断、相关风险提示和预算均在原始请求中核对；本地及服务器全量 859 passed、1 skipped。实时文献两次因 arXiv timeout/429 暂停，另用历史真实检索记录加真实 Flash 模型补齐文献消费链验证，不能写成实时检索已恢复。见 [最终结果、勘误与边界](reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout)、[原始审查与演变](reviews/CONTEXT_REVIEW_2026-09-13.md)；当前规则见 [CONTEXT](../current/CONTEXT.md)。未新增记忆系统、动态预算分配器或 JSON 专项修复。

前一阶段语义交接（产品 `704dbd9`、`0065088`，实测 `f2d4421`）一并收尾：模块解释通过既有工件读取链交付，Controller 把原题与回答配对后传给对应 Agent。代码理解和三个 Agent 的短回答已有真实消费证据；当时风险报告未读、文献翻页和 Coding 上下文缺口转入上述上下文阶段，不能把后续补验倒写为旧提交全绿。见 [ADR-0014](decisions/0014-semantic-handoffs.md)、[分阶段结果](reviews/SEMANTIC_HANDOFFS_ACCEPTANCE.md#verified-closeout)。旧 schema、原报告、失败现场、环境和缓存原样保留。

JSON 格式反馈与编译字段语义修复已验收：产品提交 `8cfd373` 接入既有有界反馈，`dd770f8` 让生成和评审共用字段解释；不新增组件、不改变 schema 6.0 或预算。服务器 828 passed、1 skipped；三次仅编译与两个标准库注入均完成，原始消息和 Session 已复核。见 [最终结果、报告勘误与边界](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md#verified-closeout) 和 [可复跑验收单](reviews/JSON_OUTPUT_ACCEPTANCE.md)。非法输出仍可能发生，完成的是安全有界恢复，不是上游可靠性保证；未重跑完整 GPU 矩阵。

运行期资源主线已完成分阶段验收，公共 schema 为 6.0：调用方不预填数据集，缺少所需资源沿用问答恢复，显式人工等待不消耗 Run 超时。见 [实施记录](reviews/RUNTIME_RESOURCES_PLAN.md)、[ADR-0013](decisions/0013-runtime-resources.md) 和 [服务器验收单](reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。最终产品提交 `f3179e5` 的 §9 原始 trace、Session 与实际指标已于 2026-09-11 复核；用户回答现经共享上下文进入执行 Agent 并影响动作，805 passed、1 skipped。收尾仅同步文档，不改变该产品提交。

历史范围必须分开：`577b8489` 跑完整回归；`d03abee` 补验仍有缺数据先运行的失败；`f3179e5` 做针对性回答上下文补验，不能称为最终提交重跑了完整 GPU 矩阵。两次 schema 错误由 AgentLoop 反馈纠正；资源主线当时未关闭 [JSON 输出专项](reviews/LLM_JSON_OUTPUT_FOLLOWUP.md)，后续独立修复结果见上段，不能以资源补验零解析错误替代其验收。详见 [最终复核记录](reviews/RUNTIME_RESOURCES_PLAN.md#2026-09-11-最终复核与收尾)。

截至文档整理基线 `808e8f1`：接口契约优化 P0–P5 与后续收尾已合入 main，公共 schema 为 5.0。最后一轮模型输出配置验收对应产品代码 `ab5066f`，随后是文档收尾；不要把分阶段验收误写成最终每个提交都重新跑过完整矩阵。

- [接口优化计划与完成情况](reviews/INTERFACE_OPTIMIZATION_PLAN.md)：阶段、范围和提交。
- [任务职责验收](reviews/INTERFACE_SCOPE_ACCEPTANCE.md)：编译职责、review 可见性和执行链。
- [LLM 失败诊断验收](reviews/LLM_DIAGNOSTICS_ACCEPTANCE.md)：逐尝试 trace、空正文和输出截断。
- [模型输出默认配置与验收](reviews/MODEL_OUTPUT_DEFAULTS.md)：依据、取舍和结果；该轮确定性基线为 775 passed、1 skipped，不是无限输出或永久稳定保证。

这些是明确时间点的记录；后续变化形成新记录，不覆盖原失败现场或结论。

## 历史材料怎么用

[L3 基准与自进化调研（2026-09-15）](reviews/L3_BENCHMARKS_AND_SELF_IMPROVEMENT_2026-09-15.md) 对比可借用的外部任务与优化实现；它是下一步测试建议，不表示已接入或已验收。

| 材料 | 保存什么 | 入口 |
|---|---|---|
| ADR | 长期设计取舍、理由和替代方案 | [决策索引](decisions/README.md) |
| 开发历程 | 早期阶段目标、推进与验收 | [DEVELOPMENT_PLAN](DEVELOPMENT_PLAN.md) |
| 审查与计划 | 一次审查发现、修复范围和步骤 | [资料目录](reviews/) |
| 验收记录 | 某提交的复现要求、成功/失败与证据 | [资料目录](reviews/) |

## 保存规则

- 保留历史原文。“当前”“待执行”、旧字段和旧命令属于当时语境，不自动成为当前要求。
- 已接受 ADR 变更时追加新决定并说明取代关系，不把旧理由改成后来才知道的结论。
- 本次只搬移历史文件、修正导航链接、加归档提示。命令、服务器路径和提交号不因整理而改写。
- 新规则落地后更新 current，示例受影响则更新 guides；历史索引不复制完整字段和接口表。
