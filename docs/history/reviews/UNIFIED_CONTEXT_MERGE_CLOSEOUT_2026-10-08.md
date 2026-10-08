# 网页能力与统一上下文：主线采用与验收汇总

2026-10-08，用户确认新版设计原则，并授权最后复查通过后合并 `feat/unified-context`。本记录汇总采用范围与证据，补充历史测试交接的后续状态，不改写原阶段报告。

## 采用范围与版本

- 主线原基线：`0e5bb969c909fae2c4ceb0247907e9d0e21bee9c`。
- 最后确认的开发快照：`20ce00f3da5d752fafd304516f70acb0b0922846`。本次随后仅补充文档导航与本收尾记录，不改变产品代码。
- 网页工具及来源保留、partial 回执：实现与验收见 [WEB_TOOLS](WEB_TOOLS_2026-10-06.md)、[WEB_QUALITY](WEB_QUALITY_2026-10-06.md)。
- 统一上下文：`8d313d9`，直接基于联网搜索完成版 `8f9d725`。废弃的 `d23cf44` 研究材料生命周期实验不在此分支中。
- 审查修复：`85598bb`；服务器最终验收快照 `fbd5ee8`。从该快照到本次采用版本，packages/apps/tests/e2e 均无代码差异。
- 公共 schema 为 24.0；旧 Run/Session 不兼容恢复，原件保留，不迁移、不重写。

本次采用保留分段提交和开发分支，不合并其他分支，不删除服务器材料或实验环境。四份 current 文档解释当前事实；下文的实测范围不扩大为通用成功率或科研质量保证。

## 统一上下文服务器三场景

以下根据用户提供的测试方汇报整理，本次收尾没有重跑真实模型，也没有再次登录服务器独立复算。

测试快照 `3341536`，服务器回归 **2014 passed / 1 skipped**、mock 13 工件、pip/diff clean；schema 24、9 个 editable 包来自源码树，旧 schema 23 Session 被拒绝恢复。

| 场景 | 报告结果与关键覆盖 |
| --- | --- |
| A：三 Agent 问答 | completed/supports，75 工件；Scientific 同作用域两次问答累计保留，任务作用域隔离；request_work 为普通工具；协议 11/11 配对；目录 71 条；真实 CIFAR-10 GPU 校准实验完成 |
| B：材料与目录 | completed/supports，22 工件；目录为授权输入与工具产物并集，分组只引用 ID；论文来源链和网页重定向元数据正确；模型区分全文、网页和搜索线索，对占位页不编造 |
| C：受控容量与压缩 | completed，47 工件；48000 输入额度下真实触发三次压缩；原工具历史保留，目录 40 条完整重建，压缩后协议 5/5 配对；必需输入超限明确失败 |

测试方报告冻结 hash **144/144** 一致，MANIFEST 为 183 public + 9 private。Coding/Experiment 的问答覆盖为命令批准，不据此称任意自然语言问答形态均已真实覆盖。

证据根：`/root/autodl-tmp/resagent2/runs/unified-context-3341536-20261007/`。报告在 protocol，复算在 analysis，完整 trace 与 Session 仅按服务器私有边界保留。抓取不执行 JS，PyTorch 的“Redirecting…”占位正文是已记录的提取限制。

## schema 24 校准 L3

测试方随后用新版完成 L3，证据根为 `/root/autodl-tmp/resagent2/runs/l3-schema24-20261008/`，报告在 protocol、复算在 analysis；MANIFEST 为 181 public + 4 private。用户汇报未列出独立的完整冻结 SHA，本记录不另行推定。

报告完成类别为自主完成。Scientific Session 材料目录为 14 条，最终模型目录 99 条，通过 6 次 read_artifact 取得证据后形成结论；本轮 53 次调用，旧记录 65 次，1 次完成候选校验失败后恢复；压缩未触发。13 次人工介入包括一次环境策略问题与 12 次命令审批，未代选算法、调参或修码。

测试方报告未发现上下文丢失、材料混淆或错误结论，并保留 MCE 变差、单 seed/单 checkpoint 等局限。模型本轮自选 100 epochs，train acc 1.0、test acc 0.8818，出现过拟合；文献全文未获取。这些行为与质量边界保留，不能把模型目录结构正确直接等同于研究策略最优。

上述是不同模型轨迹与旧 L3 记录的观察比较，未做控制条件下的新旧重复 A/B，不据调用数下降或一次完成宣称普遍质量提升。后续五项确定性修复没有重跑完整 L3，使用下节回归与真实材料消费验收。

## 五项审查修复及真实网页消费

完整问题、最小修复和测试方汇报见 [CODE_REVIEW](CODE_REVIEW_2026-10-08.md#服务器验收与收尾2026-10-08)。

服务器冻结 `fbd5ee8`：**2062 passed / 1 skipped**，mock completed/13 工件，pip/diff clean；9/9 源码 import 和 241 个定向用例通过。修复覆盖截断边界、可选 trace 失败、网页预格式化文本、空白异常转换与不可达分支清理。

真实网页 Run `run_web_material_fbd5ee8_20261008` completed，5 次模型调用、一次抓取，未使用 GPU。关闭搜索 provider 后，Scientific 抓取 Python 官方 JSON 页面并显式读取；冻结正文、读取回执、最终观点和报告中的多行示例一致，测试方报告 **9/9 PASS**。首版校验基准记错字典键顺序，核对原始 HTML 后纠正；被测工件未改，首轮失败及原因保留。

证据根：

- `/root/autodl-tmp/resagent2/runs/unified-context-fbd5ee8-20261008/`，回归报告与 5 文件清单。
- `/root/autodl-tmp/resagent2/runs/web-material-fbd5ee8-20261008/`，真实材料消费与 27 文件清单。

此真实场景证明该网页的抓取、冻结、读取和输出链，不新增搜索质量、JS 页面或通用提取成功率结论。

## 本次合并前复查

在开发快照 `20ce00f` 使用 Ubuntu-D `/home/cyl/miniconda3/envs/ResAgent2/bin/python`：

| 检查 | 本次实际结果 |
| --- | --- |
| `python -m pytest tests apps/cli/tests -q` | **2062 passed / 1 skipped**，52.80 秒 |
| `python -m e2e.mock_e2e` | `run_golden completed`，13 工件，Coding/Experiment 各一次完成 |
| `git diff --check` | 通过 |
| 文档与实现 | 四份 current 独立交叉复核通过；链接、原锚点与图示检查通过 |
| 产品一致性 | packages/apps/tests/e2e 与服务器验收基线 `fbd5ee8` 无差异 |
| `python -m pip check` | 本地既有 `pdfminer-six 20260107 requires cryptography, which is not installed`；未改环境，服务器验收为 clean |

合并前确认工作区干净、远端 main 是当前分支祖先，无需改写产品或解决代码冲突。收尾同步根 README 的 schema 24、网页与上下文入口，修正文档导航旧原则组织和 PDF 超时说明，补全上述验收入口。没有重跑付费模型、GPU 或完整服务器 L3，没有新增产品行为。
