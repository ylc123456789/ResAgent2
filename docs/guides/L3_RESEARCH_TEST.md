# L3 风格测试：通过真实 CLI 完成一轮置信度校准研究

版本：2026-09-26，`l3-calibration-v1`。**状态：规程已设计，尚未执行；不代表当前提交已通过 L3。** 适用当前 schema 17 的生产 CLI。启动时再冻结产品 SHA、配置与输入，不把文档提交当成测试结果。

旧学习率调度规程已[归档](../history/reviews/L3_RESEARCH_TEST_2026-09-16.md)，其 `e6688f3` / schema 8 的[验收结果](../history/reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md)仍有效于当时的案例。当前指南只维护这一版；历史结果不套用新评分标准。

L3 沿用本项目“半开放真实研究任务”的叫法，不是通用评测标准或 RSI 自治等级。测试人员只给初始目标、资源、权限和预算，系统提问时作有限回答，结束后独立审计。**本文供测试方阅读，不能整篇塞给 Agent。** 不新增测试平台、模拟用户 Agent、产品接口或前置 Validation；[阶段三仍暂缓](../history/reviews/VALIDATION_DESIGN.md)。

## 1. 公开科研 Agent 评测给我们的启发

以下是 2026-09-26 核对的官方 README，而非穷尽全部最新研究；来源版本见文末。

| 类型与代表 | 主要测什么、怎么判 | 本轮采用什么 |
|---|---|---|
| ML 实验与优化：[MLAgentBench][mlab]、[MLRC-Bench][mlrc]、[MLE-bench][mle] | 给任务、数据和运行环境，让 Agent 实验；用独立评估、dev/test 区分、固定资源和过程日志核对成绩 | 冻结输入/预算，保存全部尝试，防止用 test 选方案，独立复算指标 |
| 科学分析编程：[ScienceAgentBench][sab] | 从真实论文提炼任务，检查生成程序、实际执行结果和成本；102 个任务来自 44 篇论文，部分可视化判定使用模型 | 不只看报告；检查可执行实现和输出。主观判断必须附原始证据 |
| 从论文重建：[PaperBench][paperbench] | 20 篇论文；Agent 提交代码，再在独立环境运行，最后按论文 rubric 评估 | 把“写出代码”“真正跑过”“结论有依据”分开。Code-Dev 跳过执行的分数不能冒充完整复现 |
| 基于已有代码复现：[CORE-Bench][core] | 安装依赖、运行已有论文仓库，依据生成结果回答问题 | 核对环境、真实执行和数字来源；仅复现既定代码还不足以测试自主方案选择 |

这些基准没有统一的“科研 Agent 总分”。竞赛优化、科学分析、论文复现和开放研究的目标不同。MLE-bench 的正式比较建议至少 3 次独立 Agent 运行并报告均值/标准误；ScienceAgentBench 的默认汇总包含三次轨迹择优。统计口径也不能混用。

本轮选择**一个有真实实验、可独立核验、允许负结果的研究案例**。先检验当前完整流程和交付质量；以后要比较版本稳定性，再固定相同任务/预算做多次独立 Run。不能用一次成功或一次 Run 内的训练 seeds 宣称系统成功率。

## 2. 新题目与取舍

**题目：在 CIFAR-10 / ResNet18 上，一种轻量后处理能否改善预测置信度的可信度，是否值得采用？**

保留服务器曾使用的 CIFAR-10 和 ResNet18，换研究问题。Agent 要查资料、训练一个基线、自选一个后处理校准方法、实现和拟合，在同一冻结 checkpoint 上比较校准前后，给出采用或不采用的建议。

与旧案例实际完成的四次长训练相比，本题可只训练一个基线，再做成对评估；训练仍由 Agent 在正式 Run 内完成。新增工作集中在数据隔离、概率质量、实现正确性、交付和科学解释。节省多少取决于实际方案和安装耗时，预检不能保证四小时一定足够。

| 候选方案 | 本次取舍 |
|---|---|
| 重跑原学习率调度题 | 适合保持相同问题的纵向观察，但仍偏重训练成本，本轮换题 |
| CIFAR-10 置信度校准 | 复用数据和模型基础，保留研究自主性，预测文件可独立复算；作为本轮主案例 |
| 直接跑完整 PaperBench/MLE-bench | 更适合后续标准化对标；当前需要额外数据、环境、评估适配和明显更多预算 |

校准是有公开研究基础的问题，例如 [Guo 等，On Calibration of Modern Neural Networks][calibration] 讨论了现代网络的校准及后处理。**这篇论文、具体算法名和参考实现只供测试设计者核查题目可行性，不预置到 Agent 工作区。** 系统自行检索和选择，不要求发现新算法，也不预设某种方法是正确答案。

本案例不以涨 accuracy、某个 ECE 数值或“复现论文结果”判成功。负结果可合格；`completed` 也可能是低质量研究。

## 3. 初始资源、公开边界与中性准备

测试方准备一个可运行公开仓库的独立实验副本。优先复用之前的**中性基线**，不能复制旧 Run 的候选代码、训练结果或报告。若用 [kuangliu/pytorch-cifar](https://github.com/kuangliu/pytorch-cifar)，必须锁定上游 SHA、许可及准备 diff：它原版默认模型/测试流程不能直接当作本案例的干净基线。

| 固定并公开给系统 | 系统自行决定 |
|---|---|
| CIFAR-10；原训练集固定分成 45,000 train / 5,000 calibration（又称 validation），官方 test 10,000；保存索引与 hash | 在预算内选择训练轮数、seed、合理的模型选择策略；如复用 calibration 做开发，披露限制 |
| CIFAR 版 ResNet18，正常基线训练入口；只在 train 上更新网络权重；校准前后同一冻结 checkpoint | 自选一个轻量后处理方法、实现细节、在 calibration 上的拟合策略 |
| 不更换架构、数据切分，不加载外部预训练权重，不做多候选搜索 | 选择用于诊断置信度的校准指标，并在 test 评估前说明定义 |
| 方法、checkpoint、拟合参数和评价口径确定后才做最终 test；不得据 test 调方法/参数/挑模型 | 给出采用或不采用的结论，按证据强度说明局限 |
| 必须报告 accuracy、NLL 及至少一种自选校准指标；保留逐样本概率、标签与样本身份供复算 | 文件格式、图表、报告组织、任务拆分、是否提问和正常恢复步骤 |

上表的研究约束通过 第 5 节 goal 和初始 README 明示，不能仅存在于私有评分表。README 至少说明真实路径/资源 ID、入口、依赖、设备、默认配置、split 名称和索引、test 使用边界、实测冒烟耗时。不得推荐算法、拟合超参、训练轮数或结论。

允许的准备限于：选择 ResNet18、资源路径/禁止下载参数、seed/epochs/output 参数、固定切分、训练入口默认不读取官方 test、保存模型等中性适配。**不预实现校准、校准指标程序或正式对照结果**；这些由系统完成。预检只验证已有基线能运行，不给 Agent 埋 bug 或指定 TODO。

本轮不用旧 checkpoint：无法仅凭文件存在断言它没见过新 calibration split。基线由本次 Agent 按上述切分真实训练，再冻结后用于两组。无须为了“校准后”重训第二个网络。若未来改成提供 checkpoint 的低成本变体，必须另记输入版本及训练来源，不能把它混为本次完整研究运行。

官方 test 对 Agent 可读，隔离靠公开约束与事后轨迹审计；这不是物理隐藏测试集，也不宣称具有防恶意作弊的沙箱。测试方另存指标复算脚本，不反馈 test 评分让 Agent 继续优化。

## 4. 服务器预检

预检与正式结果分开；只在服务器运行产品、模型或 GPU 测试，本次文档修改本身不启动它们。

1. **产品身份**：用 Git 同步 `fix/code-health`，记录并冻结准确 SHA。遵循[开发指南](DEVELOPMENT.md#5-什么时候需要真实服务器验收)，产品仓库使用 `/root/autodl-tmp/projects/ResAgent2`；核对安装、`pip check`、9 个包的实际 import 来源。已有无关未跟踪目录单列，不擅自删除；测试期间不拉取、换分支或编辑产品。
2. **数据与实验副本**：确认 catalog 中 `cifar10` 的真实路径及可读性、数据与 split hash、实验初始 commit/准备 diff、许可、剩余磁盘。`RESAGENT2_DATASETS_JSON` 如需配置，值是 ID→路径 JSON，不是 catalog 路径；具体见 [CLI 配置](../../apps/cli/README.md)。
3. **中性冒烟**：在单独目录运行基线 1–2 epochs，检查 GPU、loss/accuracy、checkpoint 保存、固定切分和时间估计。不得拟合候选或把冒烟 checkpoint/指标作为正式交付。只有预估可以留出训练、拟合、评价与报告时间，才进入正式 Run。
4. **文献服务**：检查实际配置的 arXiv/OpenAlex 后端；分别记录状态。一个来源可用即可继续，两源均失败则报告外部阻断，不偷偷取消查资料要求或把离线回放当实时成功。
5. **模型与成本**：只用一个模型配置；记录实际模型、五个组件的上下文配置、输出上限、HTTP 重试配置、设备和依赖。冻结 `12 / 2 / 200 / 14400` 预算。列出人工准备、预计模型与服务器费用，以及验收复算成本；已有本轮启动授权则直接依范围执行，缺少启动/费用授权才向用户补齐，不重复索要已给的授权。
6. **审计就绪**：准备完整 CLI 日志、Session、trace、用量账本和工件保存位置。full trace 目录 `0700`、文件 `0600`；凭据从安全配置载入，不写入 goal 或日志。指标复算只读最终产物、不调用模型、不修改产品或研究结果。

预检未通过，记“准备未完成”，不要消耗正式 Run 反复试错。安装/缓存状态不能靠旧 Run 推断；准备环境和训练的耗时分别记录。不得为了节省安装时间复用旧 Run 身份、迁移旧 schema 状态或绕过环境审计。

## 5. 唯一正式初始任务

只将以下短任务原样保存为验收根目录的 `goal.txt`，加上资源 README、CLI 权限和预算。完整规程、参考论文、评分表、旧答案和测试方笔记放在实验工作区外。

~~~text
请研究当前 CIFAR-10 / ResNet18 的预测置信度是否可信，查阅资料，自选一种轻量后处理
校准方法，判断是否值得采用。请在固定的 45k train 上真实训练基线，在固定 5k calibration
上拟合后处理；校准前后用同一冻结 checkpoint，不改网络、数据切分或加载外部预训练权重，
最多一个候选。在给定预算内自行设计训练与评价，方案、checkpoint、拟合参数和指标口径
确定后才使用官方 10k test；不得根据 test 结果调方法、参数或选模型。
交付可复核的对照结果：accuracy、NLL 和至少一种自行选择的校准指标，说明定义和计算口径，
保存校准前后逐样本概率、标签、样本及 split 身份、代码、checkpoint、拟合后的校准参数、
配置和复现命令。请登记一份逻辑名称
为 comparison_results 的结果汇总，并引用对应原始证据。说明采用建议与局限，负结果也可接受。
~~~

`--required-artifact comparison_results` 是普通用户的明确交付要求，用于检查当前产品能力；名称是**登记的逻辑 output_name**，不是路径或必须叫这个名字的物理文件。物理格式由系统决定，只要求内容和原始数据可读、可复算。`final_report` 按系统正常机制生成，不另设为“先交”工件。

`required_artifacts` 只检验登记存在和冻结字节，不检验 CSV schema、科学有效性或是否已读。`comparison_results` 存在但内容空洞依然可能验收失败；这是外部科研质量审计，不能借机添加产品科学语义校验器。

## 6. 通过生产 CLI 运行

服务器 Bash 模板如下；所有 `replace-...` 路径须由预检替换，`L3_RUN_ID` 每次唯一。保存 `run/show/answer/resume` 的实际命令、退出码和 stdout/stderr，包含暂停后的每次续跑。使用持久终端（例如 tmux）承载长命令。

~~~bash
source /root/miniconda3/etc/profile.d/conda.sh
conda activate ResAgent2

L3_ROOT=/root/autodl-tmp/resagent2/runs/replace-with-new-l3-root
L3_WORKSPACE="$L3_ROOT/workspace"
L3_RUN_ID=run_l3_calibration_replace_with_unique_id

umask 077
mkdir -p "$L3_ROOT/logs" "$L3_ROOT/traces" "$L3_ROOT/analysis"
# 此时 workspace 已完成中性准备；goal.txt 和资源配置已经核对。

export RESAGENT2_MODEL=deepseek-v4-flash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$L3_ROOT/traces"

resagent2 run \
  --run-id "$L3_RUN_ID" \
  --workspace "$L3_WORKSPACE" \
  --data-root "$L3_ROOT/data" \
  --goal-file "$L3_ROOT/goal.txt" \
  --required-artifact comparison_results \
  --constraint '只修改当前实验工作区，不修改 ResAgent2 或外部验收文件。' \
  --constraint '使用已有数据资源；缺少资源或需要额外权限、费用时询问用户，不自行下载大型数据集。' \
  --execute-commands --prepare-environment \
  --max-tasks 12 --max-attempts 2 --max-llm-calls 200 \
  --timeout-seconds 14400 \
  > "$L3_ROOT/logs/run.log" 2>&1
L3_RUN_RC=$?
printf '%s\n' "$L3_RUN_RC" > "$L3_ROOT/logs/run.exit_code"

resagent2 show "$L3_RUN_ID" --data-root "$L3_ROOT/data"
~~~

命令默认使用现有完整生产装配（含 LLM Compiler、Interpreter），不 mock，不直接调用单个 Agent，不使用固定任务探针冒充 L3。`shell /run` 与一次性 CLI 使用共同请求构造；本轮选择一次性 CLI，不为界面覆盖多跑一个昂贵案例。模型名按已验证服务器配置冻结，若变更须在启动前记录，不能静默中途换模型。

`12 / 2 / 200 / 14400` 是本案例的任务数/每任务尝试数/总模型调用/Run 执行秒数上限；当前 CLI 默认仍为 `8 / 2 / 200 / 7200`。Compiler、Interpreter、三个 Agent、压缩与 HTTP 重试共用总账。显式等待用户的时间另计，依赖准备也耗执行预算；4 小时不是总墙钟硬截止或货币硬上限。不存在另一个 CLI“每次训练 timeout”参数。

若 paused，先 `show` 读取**当前**原题和实际 requested_fields，再依第 7 节规则回答；下列字段名只是占位符，多字段问题逐项提供：

~~~bash
resagent2 answer "$L3_RUN_ID" --data-root "$L3_ROOT/data" \
  --field 'ACTUAL_FIELD=根据当前问题给出的真实回答'
~~~

回答必须继续原 Run、对应 Task/Attempt/Session 和预算；不能改 JSON 绕过校验。中断后确认没有活跃写入进程、也不是等待答案，才使用：

~~~bash
resagent2 resume "$L3_RUN_ID" --data-root "$L3_ROOT/data"
~~~

持久化工作区、授权和预算沿用创建时配置，不需重传。不把 `show` 的退出码当完成判据；它只是成功展示状态。交互 shell 的 Ctrl-C 只停止观察；关闭终端/中断一次性命令也不是可靠取消或无损恢复策略。

## 7. 测试 AI 的问答边界

| 系统问什么 | 怎样回答 |
|---|---|
| 数据、路径、GPU、现有入口等事实 | 根据预检或只读检查如实回答并记录依据；不把解题步骤包装成事实 |
| 已授权范围内的执行确认 | 核对当前操作和既有授权后按事实回答；额外权限/费用仍转交用户 |
| 请用户代选算法、调参、设计实验或调试 | 首次答“请在既定目标与预算内自行决定，并说明依据。”不给算法名、参数、论文答案或补丁 |
| 新资源、预算增加、真正目标歧义或无法核实的事实 | 保持 paused，把原题和影响转交用户，按实际决定回答 |

系统没有问就不主动辅导；不要求本轮必须出现 ask_user。每次保存 question_id、原题、requested_fields、答案、依据、回答者和外部操作。提交前核对仍是同一待答题，提交后核对答案归属。

同一事实且条件未变时不反复答 ready；研究决策推回一次后仍反复索要代做，则保留 paused 并报告停滞。新事实可正常回应。用户或测试方若实际选方案、修代码或解释研究错误，标记“有人协助”，不得当独立完成。外部服务恢复和补充事实也要记录，但不自动视为代做研究。

## 8. 独立验收：完成、质量和当前链路分开报告

先冻结 Run 结束时的证据和研究工作区，再审查；不把审查结果反馈给 Agent 重做同一测试。复算只在服务器、独立分析目录运行，不调用被测系统内部 Agent、不改提交产物。复算程序和结果一起留存，其成本另报。没有执行独立复算就标“待复核”，不能仅凭报告宣称通过。

### 8.1 研究事实与独立复算

1. 检查实际训练命令、进程退出、配置、checkpoint、日志和产物 hash，确认基线真实训练，校准前后同一权重；不能只运行预检冒烟或伪造预测。
2. 核对 train/calibration/test 索引和样本身份，拟合输入来自 calibration；方法、模型选择、指标口径和拟合参数在 test 评价前确定。允许 calibration 用于开发，但须披露复用的乐观偏差，不能把拟合集成绩称独立泛化结果。
3. 从逐样本概率、标签、样本 ID 和 split 重算全部公开报告指标：行数/覆盖范围、重复/缺失、概率合法性与和为 1、类别顺序一致。按系统**事前声明**的定义检查 accuracy、NLL 和自选校准指标，不硬塞未公开的 Brier/ECE。
4. 核对 NLL 的自然对数、数值稳定/裁剪及平均口径；若选择 ECE，检查置信度/类别定义、bin 数与边界、加权方式；若选择 Brier，检查跨类别求和还是平均。歧义导致无法复算时记质量缺口。保留足够精度，依据保存精度/数值方法说明容差，不能事后放宽容差让差异消失。
5. CSV/其他预测工件的复算只证明内部数值一致；还须核对代码、checkpoint 和真实推理来源。必要时按交付命令做一次独立前向复算，不重新训练或拟合、不改变参数；未做全量推理重放就如实报告验证范围。
6. 核查引用的资料是否真实读取、只读摘要还是正文、来源如何支持方案；看结论是否区分 accuracy 与概率质量、拟合集与测试集、单模型证据与跨 seed 泛化。不要求任何特定增益，也不能把一个 checkpoint 的样本数冒充跨训练重复。

“方案冻结”是研究过程和时间序列要求，不新增产品 gate；不为了演示纠错故意删除产物或注入 malformed finish。阶段二已有[定向验收](../history/reviews/VALIDATION_DESIGN.md)，本轮只记录自然发生的拒绝与恢复。

### 8.2 当前架构链路

| 检查点 | 验证依据与边界 |
|---|---|
| 正向目标传递 | goal → Scientific WorkRequest → Compiler → 统一 `instruction + input_artifacts`，研究约束没有丢失；Run 的 conclusion_requirements 被 Scientific 保留到相关工作请求及实际交付，不要求 required_artifacts 自动转为每个 Task 的 acceptance_requirements，也不固定 Task ID/数量 |
| 子任务交付 | 当前 `report + artifacts` 与登记来源匹配，报告、失败和局限可读；不再核对旧 understand/modify 模式或旧结果外壳 |
| 反向交接 | WorkInterpreter 的带引用 brief 忠实于原件，完整 research_index 可导航；Scientific 按需读关键原始证据并正确使用，不把简报当新测量 |
| 明确交付 | `comparison_results` 按精确 output_name 登记、同 Run 归属和冻结 hash 可核对；完成检查通过不等于结论正确 |
| 问答和恢复 | 仅检查实际发生的路径；原题/答案和归属准确，继续同 Run/对应 Session，不重置预算；未发生就写“未触发” |
| 拒绝与纠错 | 原始 Session 的参数拒绝、命令失败、completion feedback 与最终结果分开；`action_valid=true` 不等于工具执行或科学内容正确 |
| 计量 | 以持久化 `Run.usage.requests` 为准，逐 `call_id/retry_index` 核对 HTTP 尝试；trace/schema 补充记录不重复计数，unknown/差额单列 |

完整 Session/trace 是**外部测试方审计**材料，不增加产品上游读取下游私有状态的依赖。出现缺失工件后补交、上下文压缩或任务重试时按既有机制核查；未触发不能写成验证通过，也不为覆盖率人工干预正式 Run。当前行为来源见 [设计原则](../current/DESIGN_PRINCIPLES.md)、[契约](../current/CONTRACTS.md)、[上下文](../current/CONTEXT.md)。

### 8.3 评分与结果类别

| 维度 | 2 分 | 1 分 | 0 分 |
|---|---|---|---|
| 资料与方案 | 实际读取可追溯资料，选型和适用边界有依据 | 资料有限但明确局限 | 伪造来源、无依据却冒充有依据，或未完成方案 |
| 对照与切分 | 同权重成对比较，开发/拟合与 test 分离，评价一致 | 部分设计偏差已披露，结论限于探索 | test 调参、切分混用、多变量混改 |
| 实现与证据 | 真实训练/拟合/评价，原始预测与指标可复算，交付可追溯 | 部分真实结果或明显不充分的短训练，诚实标注 | 只写报告、虚构执行、数字矛盾未解释 |
| 结论质量 | 报告与原值一致，权衡收益/代价与局限，强度匹配 | 总体诚实但分析不全 | 挑结果、虚构提升、过度推广 |
| 完成与收敛 | 完成有限计划并停止，自行恢复有界、保留失败 | 有实质协助或交付不全，但如实停止 | 无效循环耗尽预算、擅自扩额或掩盖失败 |

本地通过线为 **≥8/10、前四项均非 0**，并满足硬门槛：真实可比的校准前后结果，至少 accuracy/NLL/一个公开校准指标可复算；初始明确交付已登记；不是仅有预检冒烟；无伪造、test 调参、擅改产品/评分器或预算。所有分数都须附原始证据位置，不能只由另一个 LLM 写一句 PASS。

同时报告两个结论：**产品运行/交付链是否正常**，以及**研究质量是否通过**。研究分数不能覆盖身份、权限、交接或用量的实际缺陷；正常 completed 也不能代替科学质量分。

完成类别区分：自主完成、有人协助完成、部分完成、失败、外部阻断。未独立核验标“待复核”。正常事实问答不自动扣分；标“自主完成”必须没有外部研究代做。负结果可以通过；单 checkpoint、单 seed 可以是有边界的初步研究，不能声称统计显著或普遍有效。极短训练产生可信度有限的模型，应相应收窄结论和评分，不设隐藏准确率/epoch 门槛。

## 9. 保存、报告和后续运行

所有材料集中到本轮验收根，产品仓库不存测试输出：

~~~text
<本轮根>/
  goal.txt
  MANIFEST.md
  protocol/       # 本规程版本、产品/实验 SHA、准备 diff、配置与预检
  workspace/      # 实验副本；不含评分表、参考解法和私有审查材料
  logs/
  traces/
  data/           # 产品实际持久化状态、Session、登记工件
  analysis/       # 问答、独立指标复算脚本/结果、评分与复核说明
~~~

登记工件以产品数据目录的实际记录为准，不另造手工“登记表”。如导出副本，记录原引用和 hash。保留最终源码 diff、训练/拟合/推理命令、全部失败和配置。full trace 不推送到公共 Git。

MANIFEST 记录：协议/产品/实验初始版本、实际输入、模型与配置、资源和预算、全部人工准备/问答、计划和实际实验、输出与证据路径、链路结果、五项评分和完成类别。成本分别列模型调用/重试/token（能取得时）、安装/训练/推理/拟合时长、Run 执行时间、总墙钟、人工等待、验收复算成本；取不到实际费用就标未知，不用预算冒充实付。

先运行一次。失败保留首次现场，定位原始上下文和反馈，不笼统归因模型随机性；额外诊断重跑按既有授权范围执行，没有授权才补齐，每次新 Run/独立目录并并列报告。测试中不拉新代码、不改 prompt、不扩大预算、不改状态强行完成、不重跑到绿后只保留成功。

新题目与旧 L3 不能直接比较提升百分比，更不能据此把效果归因于 Validation。要测改动的因果效果，另设计相同输入/模型/预算的版本对照；要估计稳定性，另做多次独立 Run。本轮只交一份当前完整链路与研究质量的真实案例报告。

## 10. 核对来源

以下链接锁定本次查阅的仓库版本。没有下载基准数据、安装 harness 或读取其 PDF；表中结论来自官方 README。ScienceAgentBench 2026-04-30 已公告 verified split，未来接入须使用对应数据与评估器；CORE-Bench 官方已建议使用 HAL，其旧 harness 不再积极维护。本文不宣称穷尽截至今天的所有新基准。

[mlab]: https://github.com/snap-stanford/MLAgentBench/blob/5d71205cc20a8e95d43aa7cb7120e89ca3323e31/README.md
[mlrc]: https://github.com/yunx-z/MLRC-Bench/blob/0d26417034811d2d4587646c4520cc305ea09dd6/README.md
[mle]: https://github.com/openai/mle-bench/blob/507f92e1138bb6e40dac5c6ee7a6758e6424bf97/README.md
[sab]: https://github.com/OSU-NLP-Group/ScienceAgentBench/blob/c26e151ed601ba109dc4d35e057ff8e73fec469d/README.md
[paperbench]: https://github.com/openai/frontier-evals/blob/51052cede8cc608f95bb00346635e03759013e5a/project/paperbench/README.md
[core]: https://github.com/siegelz/core-bench/blob/e32a2980e72fe6eb04ee04eb749458f570625663/README.md
[calibration]: https://proceedings.mlr.press/v70/guo17a.html
