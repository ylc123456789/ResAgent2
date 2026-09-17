# Tool / Components 整理：实施记录与服务器补验

本轮只调整 Python 实现归属和导入，不改变任务功能、prompt、模型 Tool 契约、预算、schema 10.0 或历史记录。原基线 `main@678b03f`；分支 `refactor/tool-components`。

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
