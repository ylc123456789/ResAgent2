# 三组自然语言字段精简：实施与小型验收

日期：2026-09-16。对照基线：`f32f2e6`（schema 8.0）。
产品提交：`c03115b`，分支 `refactor/semantic-field-slimming`，schema **9.0**。

状态：阶段验收通过并完成文档收尾。schema 9.0 的三组精简与 schema **10.0** 的共享答案键修复均已复核；最终产品提交 `3476222`，服务器实测 HEAD `ee7d821`。最终结果、测试驱动偏差与后续边界见 [§7](#verified-closeout)。§1–§6 保留各阶段实现/验收要求，不把后续结果倒写成旧版全绿；本次收尾不再改产品行为。

## 1. 这次究竟删什么

| 范围 | 删除的填写项 | 信息放在哪里 |
|---|---|---|
| Scientific 完成 | ScientificFinish.summary | 模型只写 opinion；完成检查通过后，Runtime summary 直接取 opinion.statement |
| 三个 Agent 提问 | AskUserToolInput / AskUserInput / QuestionDraft.reason | 问题 text 包含回答所需背景；requested_fields、原题配对和恢复作用域不变 |
| Compiler 图级说明 | CompilationDraft.summary/rationale、WorkflowProposal.summary/compilation_rationale、WorkflowPatch.reason | WorkRequest 表达本轮目的；任务保留 goal、constraints、typed inputs 和 depends_on |

不是删除所有自然语言，也不是让用户失去可读说明。Coding/Experiment 的结果 summary、Artifact 摘要、工具 receipt、诊断 reason、CompilationReview.issues 与纠错反馈全部保留。ResearchRequest.goal/hypothesis/context/constraints、证据要求、指标身份和环境安装策略不在本轮范围内。

实现只用现有类和调用链：

- Scientific AskUserInput 继承 Runtime 的 AskUserToolInput，仅增加 assessment；text 的字段描述经既有原生工具 schema 送给三个 Agent。
- Experiment 的代码生成确认问题也把“已启用运行前确认”放进用户可见的正文，不丢掉原 reason 的背景。
- Compiler 仍是 JSON-only 草案 → 确定性物化/校验 → 语义评审 → 有界纠错。没有删除评审，没有新推理提示、新模型调用或替代说明对象。
- 不改状态机、Session、预算、上下文分配和完成门禁。CLI 与 E2E 仍是独立组合根，只同步 mock 输入。

公开模型删除字段，所以升级 schema 9.0。旧 8.0 Run 不续跑、不迁移；旧文件原样保留。没有剥离旧字段或 extra-ignore 兼容层，误传旧字段由现有校验/反馈处理。单独 Session 的解析不等于支持旧 Run 恢复，见 [版本边界](../../current/CONTRACTS.md#schema)。

当前规范：[接口与契约](../../current/CONTRACTS.md)、[上下文](../../current/CONTEXT.md)。本记录补充此前 [语义交接阶段](SEMANTIC_HANDOFFS_ACCEPTANCE.md)，不改写旧验收结论。

## 2. 本地已验证

- 全量：**1027 passed, 1 skipped**；相对基线 1015/1 增加 12 个测试实例。
- `python -m e2e.mock_e2e`：completed，最终报告生成；`git diff --check` 干净。
- Scientific 不填 summary 也可完成，运行时摘要与 opinion.statement 一致；多填旧 summary 被拒。
- 两种原生 ask_user schema 共用正文背景说明；无 reason 的问题可暂停并保留 assessment，requested_fields 仍必填非空。现有答案配对、作用域与恢复测试继续通过。
- 仅 tasks 的草案仍经过 draft/review，Proposal/Patch 的任务目标、约束、依赖和 instructions 保留；旧图级字段被拒，空图/非法依赖等原门禁继续通过。
- schema 8.0 Run 加载失败后原文件字节不变。

相关测试：
[字段边界](../../../tests/contracts/test_models.py)、
[Scientific 工具](../../../tests/scientific/test_scientific_agent.py)、
[完成检查](../../../tests/scientific/test_completion_evidence.py)、
[Compiler](../../../tests/orchestrator/test_compiler.py)、
[旧记录只读](../../../tests/orchestrator/test_persistence_and_artifacts.py)。

## 3. 交给测试 AI：小范围真实验收

### 3.1 纪律、身份与基线

只同步、测试、分析、报告。不改产品/prompt/目标/额度，不合并、不 push，不恢复旧 L3；不下载数据集或安装 torch，不执行生成的研究任务图。

新版本同步此分支实际 HEAD，记录产品提交与文档提交；旧对照固定 f32f2e6。两版独立干净 worktree、独立工作目录/trace。运行前核验 8 包 import 路径与模型/Profile；记录安装指针，不静默修改旧环境。每版用自己的模型类从相同业务输入新建对象，不能把旧 schema JSON 强行改版本号加载。

在新版本安装对应 editable 包后，隔离 cwd 运行（把路径换成实际 checkout）：

```bash
cd /tmp
PYTHONPATH=/path/to/checkout python -m pytest /path/to/checkout/tests /path/to/checkout/apps/cli/tests -q
PYTHONPATH=/path/to/checkout python -m e2e.mock_e2e
git -C /path/to/checkout diff --check
```

预期 1027 passed / 1 skipped。新 Run 与新 Session 从零开始，旧报告/trace 保留。真实调用需按已有纪律先确认费用；下面是小探针，不是完整 L3。

### 3.2 Compiler 新旧对照（重点）

冻结下列两份 WorkRequest 的业务内容、registry、workspace descriptor、模型/Profile 和预算；每版均通过既有 PromptLLMClient → LLMWorkflowCompiler 正常入口编译，不能手写替代评审或只校验预制 JSON：

1. **新图**：已有项目缺少 add(a,b) 实现。实现加法并进行单元验证，再由实验任务运行已有脚本采集结果文件；不得改数据划分，失败时保留日志。应先 code_modify 后 experiment_run，带成功依赖；“失败时留日志”不应变成预先安排的修复任务。
2. **追加修复**：上一轮实验已因变量 totla 拼写错误失败。修正并验证，再重新运行采集证据。current 包含上一轮失败任务，只生成新的修复/重跑任务，不引用旧 Task 作为新任务依赖、不覆盖旧历史。

Flash：两例 × 新旧版本，各一次。Pro：新图 × 新旧版本，各一次。共 6 个只编译探针，正常每个 draft/review 两次调用；单探针 remaining_calls=4，保留已有有界纠错和真实尝试计量，不循环重跑到绿。输入上限两版均 128000；使用相同明确记录的 ModelProfile/输出额度，勿顺带调整 thinking。

逐份保存请求、草案、评审、物化结果与调用账本。不能用删除字段后字节不相等来判失败，也不能只看“合法 JSON”判通过：

- 目标/证据条件没有遗漏，任务分工和依赖正确，约束落在对应任务上，没有凭空扩张范围。
- 新版草案仅含 tasks，工具/提示/schema 不要求旧字段；Proposal/Patch 也没有旧图级说明。
- review 确实看到了 WorkRequest、任务 goal/constraints/inputs/依赖；issues/拒绝纠错仍有效。
- 计量与 trace 对齐，分别报告格式合法、语义质量、纠错次数；不承诺两版生成相同图或相同调用数。

若新版出现目标遗漏或评审退化，保留失败并回报，先决定是否保留 rationale；不要加 prompt 补丁、暗扩预算或反复重跑掩盖结果。有限样本通过只支持这次精简的采用，不证明永久等效。

### 3.3 提问与完成（只测新版本）

用既有 Agent/Controller 入口，小型标准库夹具，三种问答各一次；回答读实际 requested_fields，不猜字段名。单个小任务 max_llm_calls=20、timeout_seconds=1200，不改变全局配置：

- Scientific：未给定偏好，在 Accuracy/F1 中询问，跨进程回答“第二个”。CLI run → show → answer 一并冒烟，最终意见对应 F1。
- Coding：未给定待解释文件，在 helper_a.py/helper_b.py 中询问；回答“第二个”，读取并解释 helper_b。无需代码改动/GPU。
- Experiment：未给定计算模式，在 add/mul 中询问；回答“第二个”，真实运行标准库脚本得到乘法结果 6，不是加法结果 5。

核对原始 messages/tools：ask_user 参数没有 reason、问题 text 给用户足够背景、requested_fields 非空；Scientific 仍带 assessment。问题正文原样进入 PendingQuestion/RecordedAnswer，恢复沿用原 Session；回答确实影响行动，不能以“恢复接口返回成功”代替验证。

另跑一次 Scientific direct（不要求额外证据）：finish 只提交 opinion，没有 summary；CLI/最终报告仍能读到同一结论。Runtime 摘要派生已由上述确定性测试核对；它是 Loop 返回的 ModuleResult.summary，不是 ScientificCompletedResult 或 Session 的独立顶层字段，不要求从持久化文件找一个不存在的 summary。建议同为 20 调用/1200s 上限，不强制模型用满。

无需重新进行 GPU code-experiment/完整 L3。若这组小探针出现真实产品缺陷，再按证据决定扩大范围。

### 3.4 报告

单独验收根保存 MANIFEST、logs、full traces、workdirs、ops。原始消息可读但不公开泄露；沿用 trace 0700/0600 与凭据扫描，不把 key 写进命令/脚本/报告。

报告分开写：确定性契约是否正确、模型是否按新字段行动、工具是否真实执行、任务是否完成。保留第一次失败与所有纠错，区分外部故障与产品问题；不以一次成功证明“模型漂移永久消除”。本轮没有文献/网络检索或训练依赖，出现额外安装应先检查测试装配。

## 4. schema 9.0 服务器复核与覆盖缺口

实测 HEAD `5a2488b`，证据根 `/root/autodl-tmp/e2e-sfs-5a2488b-S7kP3x/`。原 MANIFEST、失败夹具和 trace 保留；以下是开发方对原始调用/Session 的复核，不是重新执行验收。

- 确定性 1027 passed / 1 skipped；Compiler 新旧六探针通过，draft/review 均实际执行。三组字段精简成立；不据此推断永久生成等效。
- Scientific、Experiment 的真实 ask_user 无 reason；Scientific finish 只交 opinion。原题配对与按“第二个”行动可见。
- **Coding 自己提问尚未覆盖**：成功的 coding-qa3 是 Scientific 先提问，再派发 Coding 读取 helper_b。证明了上游答复传递，但不能当作 Coding ask_user → pause → resume 的直接证据。
- **既存 CLI 字段名缺陷**：Experiment 生成 `selected option letter/name: 1 = "add" or 2 = "mul"` 作为键。CLI 按第一个等号切 NAME=VALUE，无法提交该键；当时测试改用 Controller 字典回答，故未覆盖 CLI 闭环。它不是 schema 9.0 引入，但也不能写成“无产品问题”。
- direct 探针实际先因 supports 缺证据被拒，再检索、读取、完成；证明 finish 无 summary，不证明“无检索直接完成”。不放松证据门禁来凑 direct。
- 精确计量为 **60 个逻辑调用 / 60 个唯一 call_id / 60 次尝试**，含保留的夹具迭代。coding-qa3 中一次 Scientific request_work 原生参数 JSON 的 Extra data 经现有反馈恢复，不记为零格式错误。

## 5. schema 10.0 小修复与本地结果

产品提交 `3476222`。只改三个产品文件：contracts 的 models/导出与 Runtime tools；不改变 Controller、CLI 分隔规则、Agent 状态机、Session 或上下文分配。

共享 `AnswerFieldName` 约束为 `^[A-Za-z][A-Za-z0-9_]{0,63}$`：如 mode、file_choice。问题正文承载选项/背景，回答值仍可含中文、空格和等号。QuestionDraft、PendingQuestion、UserAnswer/RecordedAnswer 与 Runtime 的 AskUserToolInput 复用一个类型；Scientific 继续继承共享工具输入。

说明与规则直接进入原生工具 schema；坏键在执行前校验失败，沿用现有有界反馈，不静默改名、不增加转义或兼容层。因为新规则会拒绝原先合法的键，升级 schema 10.0，旧 9.0 及更早 Run 原样保留、不续跑、不改版本号。

本地验证：

- **1052 passed, 1 skipped**；mock_e2e completed；diff-check 干净。
- 25 个新增测试实例覆盖合法键 round-trip、非法键、旧版文件不改写、共享工具 schema、原生坏键反馈后暂停/同 Session 续接，以及 CLI/shell 的中文和含等号答案。
- 真实模型能否一次生成正确键、Coding 是否自己提问，仍由 §6 补验，不用确定性测试替代。

测试入口：[共享键](../../../tests/contracts/test_answer_fields.py)、[原生恢复](../../../tests/runtime/test_native_tool_calls.py)、[CLI](../../../apps/cli/tests/test_cli.py)、[shell](../../../apps/cli/tests/test_shell_parser.py)。当前定义见 [问答契约](../../current/CONTRACTS.md#questions)。

<a id="answer-field-acceptance"></a>

## 6. 交给测试 AI：本次最小补验

### 6.1 预检与边界

同步本分支**最新 HEAD**（包含产品 `3476222` 和后续文档提交），新干净 worktree，记录 8 包 editable 的前后指针。用 schema 10.0 新 Run/Session/trace，不加载或修改旧 Run；旧 L3 不动。沿用 §3.1 的本地命令，预期 **1052 passed / 1 skipped**，mock_e2e completed。

只测下面三个小型问答，**不重跑 Compiler 新旧矩阵、GPU、L3 或文献检索**。Flash，原生工具、现有 128K/Profile 配置；每探针包括暂停/恢复累计不超过 20 次调用、1200s 有效运行时间。模块驱动恢复时扣除已用额度，不重新发一份全额预算。真实调用前按既有纪律确认费用；次数是调用上限，不是货币费用硬上限。

准备独立、已 git init 并提交的标准库夹具，WorkspaceGrant/授权/输出目录使用现有正规装配。不改产品、prompt、模型配置或任务目标，不临时补安装大依赖；保留所有失败和纠错，不循环重跑到绿。

### 6.2 三个探针（各一次）

1. **Coding 自己提问（补齐上轮遗漏）**：通过现有 `NativeCodingAgent.invoke(ModuleTaskRequest)`，code_understand、JsonSessionStore、真实 LLM。参照 [Coding 夹具](../../../tests/coding/test_agent.py) 与 [恢复测试](../../../tests/e2e/test_native_coding_e2e.py)，不经 Scientific 代问。任务是“先请用户选择要解释的文件：1）helper_a.py；2）helper_b.py。收到选择后只读取所选文件并说明返回值。”两文件分别返回 111/222。必须看到 coding-understand 的自然 ask_user，text 保留选项，requested_fields 为合法键。按实际键回答“第二个”，由测试驱动按调用方职责配对原题/RecordedAnswer；新进程复用同 run/task/attempt/parent_session_id 恢复，只读取 helper_b，答案对应 222。不要把答案塞进 goal 冒充 answers 路径。
2. **Experiment + CLI（修复原始缺陷）**：复用上一轮标准库 compute.py（add=5，mul=6）的完整 Controller/Scheduler 夹具，从新 Run 开始。要求 Experiment 在运行前询问模式，必须由 experiment-run 自然提问；Scientific 代问只能算旁路，不记覆盖。用真实 `resagent2 show` 读取 Fields，再通过 `resagent2 answer --field '实际键=第二个'` 回答，**不得改用 Controller 直答来绕过 CLI**。原题清楚列出 add/mul，恢复同 Session，真实执行 mul 并冻结结果 6。如出现既有运行前确认，单独按实际字段回答 yes 并记账，不删确认。
3. **Scientific + CLI 冒烟**：目标为澄清用户偏好（Accuracy/F1），不要求论证科学命题；自然 ask_user 后，通过 CLI 用实际键回答“第二个”，同 Session 恢复。最终意见对应 F1；finish 只有 opinion，不要求 summary，不绕过证据门禁强求 supports。原生 schema 含共享键规则，Scientific 仍保留 assessment。

三者逐项核对：工具 schema 的 pattern/说明实际到达；合法键不含空格或等号、长度不超过 64；ask_user 无 reason、text 自包含；原题逐字配对，答案实际影响行为。**不要强制模型生成某个固定键**，应使用它实际声明的合法键。

### 6.3 错误反馈与交付

坏键有界恢复已由本地原生调用测试覆盖。若本轮自然出现非法键，单独追踪“校验拒绝 → 负 receipt → 下一调用的单个 runtime_feedback → 改正后才暂停”，不能静默修键；自然未触发时明确报告“真实恢复未触发，本地确定性覆盖”。无需为了制造错误再跑一轮付费矩阵。

新验收根保存 MANIFEST、原始 messages/tools/返回、Session 与结果、驱动和账本。精确核对逻辑调用、HTTP 尝试、预算；Schema 拒绝与 JSON 解析失败分开统计，任务完成与机制正确分开报告。trace 0700/0600，凭据定值扫描零命中。未满足指定提问方或改走旁路时报告覆盖缺口；未合并、未 push，由开发方复核后收口。

<a id="verified-closeout"></a>

## 7. 最终复核与收尾（2026-09-16）

本轮产品 `3476222`，实测 `ee7d821`，schema **10.0**。服务器 checkout `/root/autodl-tmp/projects/ResAgent2-sfs10-ee7d821` 身份与干净状态已核对；证据根为 `/root/autodl-tmp/e2e-sfs10-ee7d821-T4kP7v/`。开发方只读检查了原始请求的工具 schema、模型工具调用、恢复答案、Session、命令回执与冻结结果，并非只采信 MANIFEST。

### 7.1 已确认的结果

服务器确定性 **1052 passed / 1 skipped**，mock_e2e completed，与本地基线一致；本轮三探针均完成：

| 探针 | 原始消息与真实行为 | 调用 |
|---|---|---|
| Coding 自己提问 | coding-understand 自然询问 file_choice；两进程间通过 answers/RecordedAnswer 与 parent_session_id 恢复；只 read_file helper_b.py，说明返回 222 | 4 |
| Experiment + CLI | experiment-run 自然询问 mode；CLI 回答“第二个”；真实执行 python compute.py mul，exit 0，stdout 为 mode=mul result=6；冻结 result.json 与工作区原文件字节一致 | 15（Scientific 3 + Compiler 2 + Experiment 10） |
| Scientific + CLI | metric_choice=“第二个”映射 F1；finish 仅 opinion，verdict=not_applicable，无检索或实验；未擅自把偏好确认写成科学证据 | 2 |

共 **21 个逻辑调用 = 21 个唯一 call_id = 21 次 HTTP 尝试**，无 HTTP retry、JSON 解析失败或 schema 拒绝。19 次原生 Agent 请求的 ask_user schema 均实际携带相同 pattern 与 machine-readable 说明；另 2 次为原有 JSON-only Compiler 调用。

三者均无 ask_user.reason，Scientific 仍有 assessment。问题正文与答案逐字配对，回答不是偷偷塞进 goal。Coding 的同一 Session 有 8 个事件；Experiment **自身** Session 有 22 个事件、10 次模型调用，任务始终为同一 Attempt；Scientific 偏好探针同一 Session 有 4 个事件。不能只用 Experiment Run 中 Scientific Session 的复用来代替检查执行 Agent。

关键原始证据在相应 `traces/<探针>/llm_traces.jsonl`：

- Coding ask_user：`aaa2e6326b224636b858f04ac2eb531c`；随后 read_file：`b7245a741a3246259afaca9700f85954`。
- Experiment ask_user：`c144f70460124a009aa4e7fe6be6b307`；真实计算命令：`97773621b8534eccacaf5641f766ed7b`。
- Scientific 最终 finish：`7e5d5f158125493691fc3e50008e6100`。

本轮 trace 目录 0700、文件 0600 已核对；凭据定值扫描零命中依据服务器验收记录。没有复制或公开密钥、改写旧记录、恢复旧 L3，亦未重跑 GPU/Pro/Compiler 新旧矩阵。

### 7.2 报告说明与测试驱动偏差

1. **总量与单探针上限分开**：“≤20”是每个探针的上限，不是三探针合计。本轮实际分别 4/15/2，总量为 21。
2. **Coding 驱动没有扣减恢复额度**：`ops/probe1_coding.py` 的 answer 阶段重新 build_request，仍给完整 20 次调用/1200 秒，未按 §6.1 扣除第一阶段已用部分。实际两阶段各 2 次调用，未超支，问答/原题配对/同 Session 恢复证据仍成立；不能据此声称驱动的累计额度扣减已验证。这是测试装配偏差，不是本次 AnswerFieldName 修复或生产 Controller/Scheduler 的回归。

模块直调的调用方负责下发本次剩余额度；复用此驱动前应保存首阶段实际消耗，恢复时下发剩余调用/有效运行时间，并用确定性测试验证不能重置额度。保留原驱动和本轮现场，另建修正版；**本轮无需为此重新跑付费模型**，也不把驱动缺口变成产品预算重构。

另明确字段口径：精简的是 ScientificFinish 顶层 summary；Experiment 的 result.summary 仍存在，本轮真实 finish 也包含它。不要把“顶层无 summary”误写成所有结果摘要均已删除。

### 7.3 接受结论与保留边界

三组字段精简与共享答案键修复已获足够的分阶段证据，可以收口；本次只同步文档、提交并合入主线，不再追加产品代码或付费验收。schema 9.0 的 Compiler 新旧对照与 schema 10.0 的三问答结果分开保留，不宣称最终提交重新执行过完整科研 L3。

坏键有界恢复在本轮真实调用中没有触发，仍是**确定性测试覆盖**；一次全部生成合法键不证明模型永不违规。既有校验/有界反馈继续生效，不引入自动改名、额外 prompt、CLI 转义或兼容层。旧 9.0 及更早 Run 不恢复、不迁移，旧 state/session/trace 与原 MANIFEST 全部原样保留。Scientific 对未明确的 F1 平均方式所作默认理解已写入 limitations，不能把它当作用户确认。
