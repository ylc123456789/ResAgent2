# Tool / Components 整理：实施记录与服务器补验

本轮只调整 Python 实现归属和导入，不改变任务功能、prompt、模型 Tool 契约、预算、schema 10.0 或历史记录。原基线 `main@678b03f`；分支 `refactor/tool-components`。

**当前状态**：`6672376` 的服务器补验与原始证据复核已完成，结果见 [最终复核与收尾](#verified-closeout)。第 1–4 节保留执行前的计划和边界；其中“待执行”是当时状态。

## 1. 本地已完成

| 提交 | 内容 |
|---|---|
| `4cdb725` | 提取普通操作与投影到 Components；合并硬件/镜像、报告/媒体类型、命令诊断等小模块；更新消费者与安装声明 |
| `54fab5c` | Capabilities 按四组聚焦 Tool；文献后端进入 Components；Coding 接回验证工具与命令策略；增加导出/依赖边界检查 |
| 随后的文档提交 | 同步架构、契约、上下文、包导航及本验收单；Runtime 仅更新包说明，不改引擎 |

- 基线 1052 passed / 1 skipped；最终本地 **1057 passed / 1 skipped**，mock E2E completed，diff-check 干净。
- 原 18 种模型工具的名称、完整参数 schema、docstring 与 guidance 指纹不变（`test_tool_surface.py`）。不为了迁移自动重录快照。
- 一次性 AST 核对：原 Capabilities 的 101 个类/函数定义保留逻辑；已有 `_remember` 改为 `remember_source` 并补说明，供两个读取 Tool 共用。
- Components / Capabilities 的普通 wheel 与 editable 构建通过，工具四个子目录均被打包；当前文档 356 个本地链接目标检查通过。
- Runtime 引擎、Contracts、Orchestrator 产品逻辑未改。CLI/E2E 保持独立装配，只更新组件 import。
- 本地 editable 新增 Components；服务器、旧 Run/L3、环境、数据和缓存均未动。下面是**待执行**补验，不能把本地通过写成真实模型已通过。

## 2. 同步与预检

只同步/测试/报告；不改产品代码、prompt、场景目标、预算，不合并/push，不恢复旧 L3。用分支最终 HEAD 建独立干净 checkout，记录完整 SHA；不要只取表中的某个中间提交，不 scp 零散源码。

已有环境只更新本地包，不升级第三方依赖或删缓存：从待测 checkout 根运行（已装好项目依赖时）

```bash
python -m pip install --no-deps --no-build-isolation \
  -e packages/contracts -e packages/runtime -e packages/components \
  -e packages/capabilities -e packages/orchestrator \
  -e packages/agents/coding -e packages/agents/experiment \
  -e packages/agents/scientific -e apps/cli
```

记录切换前后 **9 个包**的 `__file__`：contracts、runtime、components、capabilities、orchestrator、coding、experiment、scientific、cli。都必须指向同一待测 checkout；CLI 可执行入口也核对。旧指针/checkout 原样保留，不未经确认清理。

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
```

焦点：`tests/components/`、`tests/capabilities/`、`tests/coding/test_verification_validity.py`、`tests/e2e/test_tool_surface.py`、`tests/e2e/test_runtime_resources.py`、`apps/cli/tests/test_literature_configuration.py`。不依赖付费模型；唯一可选网络 smoke 的 skip 如实保留。

## 3. 小型真实模型补验

只用 Flash、原生工具、当前默认 Profile/128K，不跑 Pro/GPU/L3 矩阵。每个探针独立 workdir/trace/工作区，所有产物放一个新验收根。脚本只能装配既有公开接口，不重写产品策略、伪造环境审计或替模型执行动作。

执行前汇报并确认成本：Coding/Experiment 各最多 40 次调用、1200 秒；CLI 问答最多 20 次、600 秒；既有 literature 入口最多 60 次、900 秒。暂停恢复共用原 Run/Attempt 剩余额度，不恢复时重给满额。这里是最大执行额度，不是费用保证或含人工等待的总墙钟硬上限。超过上述范围先报告，不扩到绿。

### A. Coding 修改 → 验证 → 完成（经真实 CLI 组合根）

准备新的小 Git 仓库并提交基线：`add.py` 的 add 返回 a-b；`test_add.py` 用 unittest 验证 3 组加法（正数、负数、零）。先证实测试失败，记录文件与 SHA，不预修复。

目标：“修复 add.py 的加法错误，运行 unittest 验证并交付修改结果。”约束仅标准库、不安装第三方包、不做正式训练。CLI 使用 `--max-tasks 4 --max-attempts 1 --max-llm-calls 40 --timeout-seconds 1200`。

核对实际 Coding 做了 read/edit/run_verification/finish，3 个测试通过、patch 真正 a-b→a+b；环境 prepared/certified 来自真实绑定。必要的基础 Python 环境准备可以发生，但不得安装 torch/numpy。检查验证记录绑定当前 revision、正常登记 code_patch/code_change；不能只看模型 summary。

### B. Experiment 执行 + 文件/工件共用读取（小型直调装配）

通过既有 NativeExperimentAgent.invoke 装配，参考 `tests/e2e/test_native_experiment_e2e.py` 的公开输入。新仓库里放标准库 `run.py`，接受一个整数参数并写 `metrics.json={"value": 参数 * 6}`。通过真实 Registry 导入并冻结一个文本工件，其内容给出参数 7；input_artifacts 传真实 Ref，保留 import 来源元数据，不手造 hash/跳过授权。

任务要求按输入工件的参数运行脚本并交付结果；任务正文不提前写答案 42。核对 read_file 看到 run.py、read_artifact 读取已授权工件、audit_env 成功后真实 run_command，得到 42。不得由驱动执行脚本替模型完成；不注入假的 audit。ModuleResult 的 evidence/metrics 对上文件，原 trace/Session 记录完整。若只直调 Agent，没有走 Scheduler 冻结登记，必须明确这个边界。

### C. Scientific + CLI 问答（跨进程）

无工作区、无训练。目标：“请先问我选择 1) Accuracy 或 2) F1；收到回答后只说明选择，不做实验。”run → show 读真实 requested_fields，独立进程通过 `resagent2 answer --field '<实际键>=第二个'` 回答。

应 paused（exit 3）→ completed；最终 F1，键符合 AnswerFieldName，RecordedAnswer 保留原题；沿用同一 Session/Attempt，不能把回答塞回 goal，也不能用 Controller 直答绕过 CLI。

### D. 文献来源 → 工件 → Scientific 阅读（独立 E2E 组合根）

新 workdir，执行 `python -m e2e.real_e2e literature`（设置 REAL_E2E_WORKDIR 与 full trace）。不运行默认 full/code-experiment GPU 场景。

核对后端从 Components 导入，Tool 从 Capabilities 导入；真实来源记录 → literature_search.md → Scientific read_artifact → 有依据的观点，区分检索摘要与全文。若 arXiv 不可用、OpenAlex 成功，记录真实切换；不要求为了验收人为触发 429。

两源均不可用时，报告外部阻断与模型行为，不把暂停写成正常通过。可另用**原样冻结的真实历史记录**接入既有 backend 注入点补验 Tool/模型消费；标注 replay，不宣称证明实时服务恢复。来源节奏、双向切换和错误分类由 Components 的确定性测试覆盖，不对在线服务发限流压力请求。

## 4. 必须核对的证据与停止条件

- full trace 原始 messages/tools/响应：Tool 参数与观测、shared context 中读取内容、真实工具序列；不要只依赖 action_valid 或工具名计数。
- LLM 调用按 call_id 去重后累加 retry_number+1，与 Run/ModuleResult 计量核对；若出现摘要调用一起记账，不混淆 step、HTTP retry 和 Task retry。
- 成功与 schema/JSON 恢复、工具失败、最终失败分别报告；一次失败不覆盖，若诊断重跑必须新目录并并列保留。
- 凭据只运行期读取，不打印或写脚本；full trace/session 权限检查 0700/0600，做定值秘密扫描但不输出秘密。
- 需要第三方大依赖、GPU、扩大预算或修改产品时停止报告。旧暂停 Run 和旧测试现场不动。
- 最后交 MANIFEST：提交/9 包指针、逐项状态、调用计量、关键 call_id、真实 diff/日志/工件、已验证与未触发项。只完成 4 个小探针不宣称全科研 L3 重新验收。

服务器补验通过后再决定合并；本轮没有授权自动 merge/push/deploy。

<a id="verified-closeout"></a>

## 5. 2026-09-17 最终复核与收尾

服务器实测 `667237652719c204e609ff36b392888ecfeb571c`，验收根 `/root/autodl-tmp/e2e-tc-6672376-Wp4xR9/`，报告 `MANIFEST.md`。9 包 editable 指向同一待测 checkout；服务器确定性测试 1057 passed / 1 skipped、mock E2E completed。主开发通过 SSH 只读核对了原始模型请求/返回、Session 工具回执、实际结果和冻结文献；旧现场保留。

| 探针 | 装配 / 调用数 | 复核结果 |
|---|---|---|
| Coding | CLI / 14 | 真实 a-b→a+b，两个 unittest 命令各 3 tests 通过，验证绑定 revision 1；patch/code_change 正常交付 |
| Experiment | 直调 invoke / 7 | 读取真实登记的参数工件 7，执行 run.py 7，磁盘 metrics.json 为 value=42；输出仍为 Agent 候选，未冒称经 Scheduler 冻结 |
| Scientific | CLI / 2 | 原题逐字配对，第二个进入恢复请求，最终 F1；同一 Scientific Session，未创建 Task/Attempt |
| Literature | real_e2e / 6 | 9 次检索、2 次范围读取、finish，最终 supports；检索记录和观点明确限定为摘要级证据 |

29 个逻辑调用按 call_id 去重后，与 retry_number+1 累计均为 29（14/7/2/6）；本轮 JSON/schema 拒绝均为 0。复核的工具批次调用/结果逐项配对，observed_at 顺序正确；trace 目录 0700、文件 0600。服务器报告凭据定值扫描 0 命中。

报告口径更正与边界：

- “并行原生调用”应为模型一次返回多个工具、Runtime **串行执行**。
- 文献读取是 `artifact_sci_154e0729b79d57b3` 的 1–40 行（文件共 173 行）与 `artifact_sci_3998ee48a510cdff` 的 1–60 行（文件共 122 行），两次返回 truncated=false，冻结 hash 均匹配；不是两个完整工件或论文全文。两个窗口分别覆盖 1 / 4 篇完整检索摘要，包含 SENet `1709.01507`。
- 最终引用 6 个检索工件，其中 2 个做过 read_artifact，另外 4 个只经搜索预览观察。现有 observed 语义允许后者，模型也明示部分来源仅见截短预览；不能写成 6 份全文均已阅读或科学因果已独立验证。
- 本轮来源为 arXiv 成功返回，没有真实触发来源切换；切换仍由既有确定性测试覆盖。四探针不替代完整 L3。

收尾调整：四组工具的原实现先移入同目录 `tools.py`，本次再按模型可调用的 Tool 拆到独立模块，`__init__.py` 只显式导出公开 Tool 与输入模型，当前源码链接同步更新。原公开导入和模型工具契约保持不变，schema 10.0 不变；服务器探针对应移动前的 `6672376`，最后这次纯文件组织调整以本地回归和工具指纹复核，不追加付费模型测试。

收尾本地复核（上一阶段）：**1057 passed / 1 skipped**，mock E2E completed，git diff --check 干净；既有 18 种模型工具指纹测试通过。四份 `tools.py` 与 `6672376` 的原实现逐字相同，22 个公开 Tool / 输入模型均显式导出；此次修改涉及的 128 个本地文档链接目标有效。随后按 Tool 拆分的验证见当前提交说明；未改变服务器安装指针。

按 Tool 拆分后的本地复核同样为 **1057 passed / 1 skipped**，mock E2E completed，22 个公开类仍全部可从原有包路径导入；本次只改变实现文件位置和 `__init__.py` 的导出来源，不改变模型可见工具、输入 schema、运行时行为或服务器安装指针。
