# schema 23 自然科研 L3 测试交接（2026-10-04）

请先读现行[置信度校准 L3 规程](../../guides/L3_RESEARCH_TEST.md)。
本文件是本轮差异与启动检查，不代替完整规程，也不要交给 Scientific 当科研输入。

## 先确认本次测试的产品版本

本轮 `refactor/literature-foundation` 已修复审查确认的七组问题，并统一所有登记入口为 artifact_ 加完整 SHA256。公共 schema 为 23.0；分支未合并。[审查与修复](CODE_REVIEW_BASELINE_2026-10-04.md)保留原阶段事实，[ADR-0024](../decisions/0024-unified-artifact-identifiers.md)记录编号决定及本轮验证。

同步本轮最终交接 SHA，核对 HEAD 后把它写进 protocol/MANIFEST；不要使用旧 361e9fc 或 0997b51 作为本轮验收版本。旧 schema22 Run 不支持用新版本恢复，旧材料保留，新测试使用新 Run。测试 AI 不擅自修改产品、prompt、测试断言或预算；正式 Run 启动后不切换版本。

服务器使用已有授权目标：`ssh -p 26089 root@connect.cqa1.seetacloud.com`。
私钥通过现有安全配置使用，不写到仓库、测试报告或日志。
产品仓库统一为 `/root/autodl-tmp/projects/ResAgent2`。
每轮新 Run、新证据目录，例如 `/root/autodl-tmp/resagent2/runs/l3-schema23-20261004/`，不要覆盖历史材料。

## 预检

按现行规程检查产品 HEAD、工作区、九包 editable 实际 import 指针、依赖、数据、GPU、磁盘和文献来源：

~~~bash
conda activate ResAgent2
cd /root/autodl-tmp/projects/ResAgent2
git rev-parse HEAD
git status --short
python -m pip check
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
~~~

统一编号后的 WSL 全量基线为 **1686 passed / 1 skipped**，mock **completed / 13 工件**（原审查为 1641 passed）。服务器必须独立执行并保存实际日志，不能复制本地数字；出现数量差异先核对版本、测试收集和安装指针。

WSL 本地有额外 pdfminer-six 缺 cryptography，且 editable 安装元数据未刷新；服务器独立核对当前安装，不照搬本地环境。
同步产品后，若依赖声明发生变化，按 environment.yml 同步项目环境，不能只保证 import 指针却跳过安装元数据/依赖检查。

确认真实 CUDA 运算，不能只依据 nvidia-smi 或 torch 安装标签。
中性基线在独立目录做 1–2 epoch 冒烟；冒烟模型、旧校准代码、旧结果及已知算法答案不能成为正式输入。
arXiv/OpenAlex 分开预检和记录；一个来源可用即可继续。两源都失败，按现行规程报告外部阻断，不悄悄取消查资料要求。

## 正式运行

- 使用生产 CLI 的完整 Scientific → Compiler → Coding/Experiment → 固定 Interpreter → Scientific 链路。
- 原样使用现行规程第 5 节 goal。保留 CIFAR-10 / ResNet18、45k/5k/10k、同 checkpoint、最多一个轻量后处理候选和 test 前冻结等公开约束。
- 保留 `--required-artifact comparison_results` 和 `12 / 2 / 200 / 14400` 的任务/尝试/调用/执行时间预算。
- 训练轮数、方法和拟合策略由系统自主决定；不强行固定温度缩放、200 epochs、精度或正向增益。
- 模型、endpoint、四个模块上下文设置和输出上限在启动前固定；full trace、所有问答/审批/拒绝、命令退出码、Session 和预算总账保留。
- 仅有限回答系统当前问题和已有授权范围内的操作审批，不代选算法、不改实验代码、不代调参数。
- 使用持久终端和现行规程第 6 节命令。出现失败保留首次现场，不重跑到绿只交最后一次。

## 已修复边界与 L3 的关系

本轮新增确定性回归验证重复导入的 URI/旧证据保留、长 TaskId 登记、绑定解释器、后置诊断与部分批次回执保留、工作输入 ID 同 Session 纠错，以及 Git 来源/submodule 边界。
不需要向自然 L3 故意注入这些错误来刷覆盖；服务器先跑全量回归即可复验它们。
常规 L3 使用普通 Git 仓库根；COPY linked worktree 和可读范围内 submodule 当前明确不支持。

全量回归已覆盖统一格式、重建 Registry 后稳定引用、改名导入、来源区分、最长 TaskId 和旧版本拒绝。L3 对实际发生的工件核对统一格式、登记表/索引/引用一致；按 kind、summary、归属和来源字段识别材料，不按 hash 猜含义，不要求重新制造编号边界错误。

L3 重点仍是自然科研的职责分配、真实 GPU、环境决策、材料依据、预算、交付与独立复算。
PDF 解析 120 秒、在线来源可用性、工作区大文件读取限制及无 OS 沙箱没有在本轮改变；它们是已记录边界，不冒充已修或已由新 L3 验证。

## 本版新增文献核对

现有完整科研质量审计之外，针对实际发生的文献行为核对：

1. `literature_search` 是查询回执；`literature_paper` 是单篇题录与摘要；不能把回执数量当论文数量或独立证据数量。
2. 实际取得全文时，独立重算 PDF/fulltext 的冻结 hash，核对 paper → PDF → fulltext 的直接来源 ID 与统一研究索引。
3. 报告说明依据摘要还是正文。出现全文独有的方法/实验细节时，回查实际返回的片段、页码和解析正文；访问记录只能辅助审计，不参与运行门禁，也不代替阅读事实。
4. 当前解析上限仍为 120 秒；记录真实超时、失败、提问和局限，不现场加大解析上限，不写成“≥14 页必失败”。
5. 外部导入若自然发生，记材料的来源、题录真实性、PDF 字节和人工介入，区分在线检索与补充材料。不要预塞参考校准论文或强迫导入来刷覆盖率。
6. 未发生导入、解析失败、审批或压缩时，写“未触发/未覆盖”，既有专项验收另列，不冒充本次 L3 覆盖。

## 验收与交付

按现行规程独立复算 accuracy、NLL 和系统自选的校准指标，核对逐样本概率/标签/样本身份、checkpoint、切分、test 使用次数及冻结时序。
没有重放全部推理就说明范围；数值复算一致不能单独证明真实训练或泛化结论。

分别报告：
- 产品运行/交付链是否正常；
- 科研质量评分及完成类别；
- 文献新链路哪些被本 Run 实际覆盖；
- 所有人工准备、问答、审批、恢复及未覆盖项。

全部判定脚本失败时非零退出，不能仅 print FAILED。
MANIFEST 覆盖本轮保存的普通证据文件，记录相对路径和 SHA256，排除清单及说明自身以避免循环，并实际核对一次。
保留初始/最终版本、日志、trace、Run/Session、登记工件、独立复算脚本/输出以及失败记录；不提交凭据或 full trace 到公共 Git。

本轮可建立当前完整链路的案例基线，不能凭单次成功宣称比旧 prompt 更好或整个框架具备通用科研成功率。
