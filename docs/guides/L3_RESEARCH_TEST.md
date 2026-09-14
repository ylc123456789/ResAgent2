# L3 风格测试：在真实仓库里完成一次小型研究

状态：**测试规程已编写，尚未执行，不能写成验收通过。** 对照产品基线 `47f6231`；命令和预算规则见 [CLI README](../../apps/cli/README.md)。

这里的 L3 沿用旧 ResAgent 的“半开放真实任务”叫法，不是通用评测标准，也不是 RSI 论文里的 L3 自治等级。第一版只做一个任务，不新增测试平台、Agent、Provider 或产品接口。

## 1. 要测什么

给系统一个可运行的公开训练仓库、已准备的数据和有限预算，让它自己完成：查资料、选一个方案、实现、对照实验、解释结果、停止。测试人员只准备基础设施和核查证据，不替系统选候选方法或修研究代码。

与现有 code-experiment E2E 的区别：不埋一个指定 TODO、不预置唯一正确补丁、不规定内部任务图；检查的是多步研究能否闭环。现有确定性测试和小 E2E 继续保留，L3 不取代它们。

借鉴旧项目的学习率调度案例，但去掉“某方法一定涨点才算成功”的判定。**负结果可以是合格研究；completed 也可能是不合格研究。** 一个案例不能证明通用科研能力。

## 2. 冻结第一版任务边界

| 项目 | 本案例约定 |
|---|---|
| 数据 | CIFAR-10，使用已有资源库；不自动下载，不悄悄切换 CIFAR-100 |
| 仓库 | 一个真实公开 CIFAR 训练仓库的独立副本，运行前锁定 URL、提交 SHA 和许可 |
| 模型 | CIFAR 版 ResNet18；不是 ImageNet 输入头；不得改网络结构 |
| 研究变量 | cosine 基线与系统自行选择的一个更简单学习率调度；不同时换优化器、平均权重或数据增强 |
| 固定配方 | SGD，初始 LR 0.1、momentum 0.9、weight decay 5e-4、batch size 128；两组相同的裁剪/翻转/归一化 |
| 训练 | 每组 50 epochs；cosine 的 T_max=50、eta_min=0；候选的完整公式/参数在正式运行前固定 |
| 数据切分 | 官方训练集分成固定的 45,000 train / 5,000 validation，split seed=20260915；保存索引与 hash |
| 重复 | 两组使用配对 seeds 11、22、33，共 6 条正式训练记录；相同 seed 的初始化/数据顺序设置一致 |
| 评价 | 每条记录取第 50 epoch 的 validation top-1 accuracy；不挑最好 seed 或最好 epoch；报告每条值、均值、标准差、配对差值 |
| 测试集 | 不参与方案/参数/检查点选择；方案与六份最终检查点冻结后，才各评一次官方 test 集，单独报告 |
| 收敛 | 六条有效记录齐全即分析并停止；不得因候选没赢而追加候选、seed 或 epochs |

这是一个有限训练预算下的比较，不是原论文完整复现，也不预设准确率门槛。三个 seeds 只能提供初步波动信息，不能自动得出“等价”“显著优于”或可跨数据集推广的结论。随机 seed 不保证 GPU 逐 bit 确定性，记录设备和确定性设置。

数据来源：[CIFAR 官方页面](https://www.cs.toronto.edu/~kriz/cifar.html)。选 CIFAR-10 是为了复用当前服务器资源，**不是把旧项目的 CIFAR-100 结果迁移过来**。

## 3. 预检：先确认能跑，再启动计分 Run

测试人员先交付下面的预检记录；这一步未通过就停止，不消耗正式研究预算反复试错。

1. **产品身份**：干净 checkout、Git SHA、8 个包的实际 import 路径、CLI 安装位置；不得用零散 scp 源码制造不明版本。
2. **数据就绪**：资源库 catalog 已登记 `cifar10`，目录和数据内容可读；记录校验和。`RESAGENT2_DATASETS_JSON` 是运行时 ID→路径的 JSON，不是 catalog 文件路径。实际缺资源时沿用 ask_user，不能只回答“ready”骗过检查。见 [资源约定](../current/CONTRACTS.md#resources)。
3. **实验仓库**：建议候选 [kuangliu/pytorch-cifar](https://github.com/kuangliu/pytorch-cifar)，但它当前 `main.py` 默认是 SimpleDLA、200 epochs、逐 epoch 测试，**不能原样当作本协议的 ResNet18 基线**。启动前必须锁 SHA，核对许可与源码，不依赖浮动 master 的行为。
4. **独立准备 diff**：允许在实验副本里做中性准备：选 ResNet18，数据路径/禁止下载，seed/epochs 参数，固定 train/validation split，日志/metrics/检查点输出，移开逐 epoch test。保留 upstream SHA、准备 commit/diff 和文件 hash。不得预实现候选调度、塞入文献结论或修改 ResAgent2。准备后的仓库能运行已有 cosine；系统仍需自行选并实现候选。
5. **小冒烟**：只跑 cosine 的 1–2 epochs，检查数据、GPU、loss/accuracy、输出落点，并估计单 epoch 时间。冒烟独立目录，不进入六条正式结果，也不证明科学结论。若 6×50 epochs 明显超预算，先报告并与用户确定一个新协议版本，不在正式 Run 中偷偷减量。
6. **成本确认**：第一轮只用一个 Flash 配置，不同时启动 Pro 矩阵。记录模型、上下文/输出限制、设备、依赖版本与费用上限。下节示例给 8 小时 Run 预算；它不是费用上限。若用户未确认可接受成本，预检后等待确认再运行。
7. **外部服务**：单独检查文献检索可用性；测试人员不提前给系统候选答案。若 arXiv 不可用，记录外部阻断；不能静默换为回放后宣称真实检索通过。

预检允许的工程准备本身也算人工投入，计入报告，不能包装成系统自主完成。若换仓库或配方，发布新案例版本，不与本版合并评分。没有可运行且协议一致的基线时，本测试仍是“准备未完成”，不是开箱即跑的 benchmark。

新 Run 的环境身份可能触发重新安装 torch；包下载缓存不等于已有环境。分别记录安装和训练耗时，不复用旧 Run ID 伪装独立试验，不删旧环境来凑干净。环境由系统管理，测试方不要绕过审核手工改产品环境状态。

## 4. 给系统的任务文本

预检完成后，将以下文本原样存为验收根目录下的 UTF-8 `goal.txt`。将准备后的仓库 README 与预检协议一起冻结；README 只说明真实入口/参数，不包含候选答案。

```text
请在这个已准备好的 CIFAR-10 / ResNet18 仓库里做一次有限预算的研究：
查阅并实际读取相关来源，自行选择一个比现有 cosine 调度更简单的学习率调度，
比较它在本协议下的效果，解释是否值得采用。不要假设候选一定优于 cosine。

只改变学习率随 epoch 的变化方式，不换模型、优化器、增强、batch size 或训练轮数。
固定 SGD(lr=0.1, momentum=0.9, weight_decay=5e-4)、batch=128、50 epochs；
cosine 使用 T_max=50、eta_min=0。沿用仓库冻结的 45000/5000 train/validation
切分及索引（split seed=20260915）。两组配对 seeds=11,22,33。

先说明候选的来源、公式、参数与“更简单”的依据，再实现并验证；正式结果出来后不换候选。
文献只读到摘要就明确说摘要，不能装作读过全文；摘要不足以确定公式时不要编造来源。
系统可以提出实现，但需明确区分来源主张与自行设计。

完成两组各三个 seeds 的正式训练，保存每次命令、配置、日志、检查点和可机器读取指标，
每次使用不同输出目录。正式指标取第50 epoch的 validation accuracy，不能挑最好epoch/seed。
六个最终检查点和方案冻结后才各评一次官方 test 集，test 不用于调参或重新选方案。
报告逐seed结果、均值/标准差、配对差值及局限；负结果或证据不足也要诚实报告。

六条有效正式训练齐全后分析并停止，不追加候选或扩实验规模。发生技术失败可在现有
预算内诊断重试，失败记录不得覆盖；不能把失败训练混入有效结果或把重试藏起来。
缺少所需数据或访问权限请询问用户，不能自行下载大型数据集或更换数据。
只改当前实验工作区，不改 ResAgent2、本评测协议或用于验收的脚本。
```

不指定 `code_modify`/`experiment_run` 的内部任务 ID、任务数量或动作顺序。外部评分要看实际职责和产物；不能靠提示模型输出某个验收字段凑分。

## 5. 从真实 CLI 启动

以下是服务器 Bash 命令模板。所有 `replace-...` 路径必须替换为预检确认的绝对路径；不要粘贴后直接跑。凭据从现有安全配置加载，不写进命令、goal、脚本或日志。

```bash
source /root/miniconda3/etc/profile.d/conda.sh
conda activate ResAgent2

# ROOT 为专属于本次测试的新目录；不得使用旧验收根或项目源码目录。
ROOT=/root/autodl-tmp/replace-with-new-l3-root
WORKSPACE=/root/autodl-tmp/replace-with-prepared-experiment-copy
RUN_ID=run_l3_schedule_001

export RESAGENT2_MODEL=deepseek-v4-flash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$ROOT/traces"

resagent2 run \
  --run-id "$RUN_ID" \
  --workspace "$WORKSPACE" \
  --data-root "$ROOT/data" \
  --goal-file "$ROOT/goal.txt" \
  --max-tasks 12 --max-attempts 2 --max-llm-calls 400 \
  --timeout-seconds 28800

resagent2 show "$RUN_ID" --data-root "$ROOT/data"
```

已有资源环境变量按服务器实际配置保留并记录，不能在缺 catalog 时编造路径。不要为了本轮评分改模型、上下文、工具权限、任务预算或产品 prompt。

`12 / 2 / 400 / 28800` 是本案例运行前显式冻结的预算，不是产品默认值；当前 CLI 默认是 `8 / 2 / 200 / 7200`。正式运行中不临时提高这些额度。

这里 `--timeout-seconds` 是整个 Run 的预算，执行工具使用当时剩余额度；**没有独立的 CLI“每次训练 timeout”参数**。LLM socket timeout 与 Run timeout 也不是一回事。依赖安装可能耗掉很大部分时间；暂停等待用户的时间单独计量。并不提供全 Run 货币硬预算或任意时刻精确抢占承诺。见 [预算与执行代码](../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py) 和 [进程执行](../../packages/capabilities/src/resagent2_capabilities/process.py)。

若 paused：先用 show 读取原题及实际 `requested_fields`，只回答真实资源/权限事实。不替 Agent 选择研究方案或写补丁。回答格式见下；`ACTUAL_FIELD` 是占位符，不是固定字段名。

```bash
resagent2 answer "$RUN_ID" --data-root "$ROOT/data" \
  --workspace "$WORKSPACE" \
  --field 'ACTUAL_FIELD=真实回答'
```

中断后先核对是否还有写入进程；`resume` 不能与原进程同时推进，也不会替 paused 状态制造答案。CLI 的 Ctrl-C/关闭终端不是本测试认可的可靠取消或无损恢复策略。

核实没有活跃执行进程、且不是等待用户答案时，才使用：

```bash
resagent2 resume "$RUN_ID" --data-root "$ROOT/data" \
  --workspace "$WORKSPACE"
```

answer/resume 重传同一工作区，是为了兼容尚未物化工作区就暂停的正常路径；不是换一个工作区继续同一 Run。

## 6. 如何验收

不要仅看 exit code。分别报告 **Run 状态、研究交付、模型行为、外部依赖**，然后评分。

| 维度 | 2 分 | 1 分 | 0 分 |
|---|---|---|---|
| 资料与方案 | 实际读取可追溯来源，方案/来源/摘要边界说明清楚 | 资料有限但明确局限，没有伪造 | 无依据且冒充有证据，或未完成选方案 |
| 对照设计 | 只改调度，配方/split/seed冻结，无 test 调参 | 有可解释且明确记录的偏差，仅能作探索结果 | 多变量混改、test泄漏、事后改标准 |
| 实现与证据 | 候选代码可核对，六条真实训练和冻结指标齐全 | 部分真实结果，缺口诚实 | 只写报告、不执行，或证据矛盾未解释 |
| 结论质量 | 逐seed与汇总对应原始值，负结果/风险正确表达 | 总体诚实但分析不全 | 挑结果、虚构增益、将初步结果说成定论 |
| 完成与收敛 | 达到预定交付即停止；失败处理有界且保留现场 | 人工干预后结束并如实标注 | 重复扩图/训练、无效循环耗尽预算或掩盖失败 |

建议通过线为 **≥8/10 且前四项均非 0**，同时满足硬门槛：六条训练+终期 test 全交付、无伪造、无 test 调参、无擅自修改产品/评分器、预算未擅自增大。分数是本地验收约定，不是来自官方 benchmark。资料服务不可用导致 paused：记“外部阻断、未完成”，不打成全绿，也不单凭此判框架回归。

人工查看下列原始证据，不使用另一个 LLM 的一句“通过”作最终评分：

- **规划/职责**：真实 Compiler 请求与输出、工作请求及依赖；Coding 实现/验证，Experiment 正式训练。允许合理任务拆分，不断言固定 task ID。
- **是否真做**：原始 action、工具 observation、真实进程返回值、输出文件及 hash。模型 reasoning 可以帮助定位意图，但不是执行证据。
- **数字链路**：逐 seed 原始指标 → 冻结 artifacts → Scientific 读取 → 最终 statement/limitations。分别核对 baseline/candidate、validation/test、比例/百分数单位。
- **信息交接**：相关 module_report/residual_risks 是否读取和解释；只读 metrics 不代表已理解实验局限。见 [上下文](../current/CONTEXT.md) 与 [接口契约](../current/CONTRACTS.md)。
- **停不下来时**：定位首次重复动作前后的完整 request/response，检查看到了什么、缺了什么；不得仅以“Flash 随机性”归因。保留所有失败，不重跑到绿覆盖首次结果。
- **计量**：按主 trace 的 call_id 去重，累加每条 `retry_number + 1`，与 Run 的 `llm_calls_used` 比较；schema 补充记录不重复计数。分别报告 JSON/schema/HTTP/Task Attempt 层，沿用已有恢复机制，不借测试另修 JSON。
- **安全**：trace 目录 0700、文件 0600；用实际密钥定值检查泄漏但不打印密钥，不把 full trace 发布到公共 Git。

自然发生失败时核查修复链，不额外埋 bug。本轮先正式跑一次；失败要先解释，诊断重跑使用新身份/目录并并列报告。三个训练 seeds 是同一研究里的统计重复，**不是三次独立 Agent 系统成功率样本**。

## 7. 交付与下一步

所有文件放在一个明确命名的验收根里：`MANIFEST.md`、`protocol/`（本协议版本、goal、预检、仓库SHA及准备diff）、`logs/`、`traces/`、`data/`、`analysis/`。不散落 E 盘、项目根目录或共享数据集目录。实验工作区路径单独登记；保留最终 diff、命令和正式结果。

MANIFEST 至少列：产品/基线提交、模型配置、人工准备/干预、资源与费用、Run状态、每条训练/失败记录、五项评分及证据路径、最终结论、外部阻断、可复现命令。记录总墙钟时间、安装/训练耗时和人工等待，不把它们混成“模型速度”。

测试方不合并 main、不修改产品、不删除旧数据/环境/失败现场。一次性同步包与脚本用完后列清单，在确认报告可复现后再精确清理；不要清空整个验收根。通过后再考虑加入 [外部任务基准](../history/reviews/L3_BENCHMARKS_AND_SELF_IMPROVEMENT_2026-09-15.md)，本轮不加自动优化器。
