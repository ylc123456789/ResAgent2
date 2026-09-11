# LLM JSON 输出问题：历史证据与有界格式纠错

记录日期：2026-09-10。观察代码：`fix/runtime-resources @ 577b8489`。
最初仅记录证据，不属于资源发现或暂停恢复的实现变更。下文保留各轮事实；2026-09-11 的独立修复见末节，不把历史失败改写为已通过。

## 已确认的事实

服务器证据根：`/root/autodl-tmp/acceptance-runtime-resources/`。
核对 `traces/<场景>/llm_traces.jsonl` 中的原始 response、reasoning、逐次 attempts，以及 `workdirs/<场景>/state/` 中的任务 Attempt。

八个回归场景（ask-start/resume 按同一 Run 合计，不含 section4/5 探针）：

| 指标 | 核对值 |
|---|---|
| HTTP 尝试总数 | 192，逐逻辑 call_id 去重后累加 attempts 长度，与各 Run.llm_calls_used 一致 |
| JSON 解析失败 | 31：17 次 Extra data，14 次 Expecting value |
| 失败响应内容 | 纯空白；JSON 后夹带说明文字、第二个动作或 DSML 工具标记；部分是不完整的工具标记片段 |
| 上述 31 次 finish_reason | 全为 stop，不是 length |
| 影响 | 两个 Task 的第一个 Attempt 因客户端三次解析失败而失败，后由 Scheduler 的第二个 Attempt 完成 |
| 最终 Run | 八个均 completed；最终成功不抵消前面的协议错误 |

31 次失败均来自本轮 Flash 场景；Pro 的一个 code-experiment 场景为 18 次尝试、零解析失败。样本不用于推断模型永久优劣或一般故障率。JSON 已解析但 action schema 不匹配的补充行另计，不包含在这 31 次中。

两个可直接定位的失败逻辑调用：

| trace 子目录 | call_id | 实际失败 |
|---|---|---|
| ce-flash-1 | 7ee5ed7985dd48d4a23f1d6f7e975e16 | Experiment：三次分别夹带 DSML、纯空白、JSON 后跟 “Let me continue.” |
| ce-flash-2 | e356674e2afe411bbdea815034ee5670 | Coding：第一次 JSON 后夹带 DSML，后两次纯空白 |

这些 E2E 记录的 `request_max_tokens` 为 null（未显式发送 max_tokens），不能冒称它们使用了 CLI 的显式输出额度。失败尝试的 completion 用量很小，且结束原因是 stop；目前没有输出额度耗尽的证据。

该提交的 OpenAICompatibleClient 已请求 `response_format={"type":"json_object"}`、`temperature=0`，但收到的 content 仍出现上述内容。只能确认返回内容未遵守本地 JSON 动作协议；仅凭 trace 不能判定具体是模型、provider 服务或其他协议适配环节的根因，也不能断言由资源改动引入。

## 577b8489 时的处理及边界

- 保持严格解析，不截取第一个 JSON 冒充完整回答，不把 reasoning 或 DSML 当作可执行动作。
- 客户端仍是有界重试；耗尽后返回 retryable 错误，Scheduler 在剩余 Task/Run 预算内决定新 Attempt。失败记录和用量保留。
- `tests/runtime/test_llm_recovery.py`、`test_llm_attempt_trace.py` 已覆盖恢复与计量机制；它们不证明真实 provider 总能返回合法 JSON。
- 本轮不增加额度、自动修 JSON、无限重试或模型专用解析分支。

## d03abee 小收尾补验观察

`/root/autodl-tmp/acceptance-closeout/` 的三个子 Agent trace 共 24 个逻辑调用、25 行记录；一行是 schema 校验补充记录（不是新调用）。本轮 JSON 解析错误为 0，另有一次 `extra_forbidden` schema 错误并恢复。未复现不等于 577b8489 的 31 次失败已修复；后续用户回答上下文补齐也不修改 provider、JSON 解析、重试或额度。

## f3179e5 用户回答上下文补验观察

`/root/autodl-tmp/acceptance-answers/` 中五份 trace 为 **32 个逻辑调用、32 次 HTTP 尝试、34 行记录**。JSON 解析失败 0，schema 校验补充行 2，客户端重试和 Task Attempt 重试 0。两次错误是动作顶层多出 result 或 opinion/summary；AgentLoop 将拒绝详情放入 runtime_feedback，下一次逻辑调用改成 arguments 下的正确结构后恢复，不能称为“客户端重试修好了 JSON”。

可定位的原始失败：choice-coding `1d20c9ad926346898fc91531644a108b`、sci-smoke `6fb3e144f4ba42f5ba3f8a04bb490fe7`。这轮只修改回答上下文，未修改解析、协议或重试；上述 schema 错误与此前 31 次解析错误分开跟踪。资源主线收尾不关闭本专项。

## 最初的后续调查计划

1. 保留上述现场。用其中一个失败请求做独立、有界复现；核对真实模型/endpoint、消息包装和 json_object 配置，不执行返回的工具、不覆盖历史 Run。
2. 区分纯空白、多个 JSON、夹带文本与 schema 不匹配；同时记录 finish_reason、usage 和每次尝试。不能把所有失败统称为网络波动或输出截断。
3. 根据复现与 provider 的实际协议决定最小修复，再评估是否需要把解析错误变成结构化反馈。当前不预选新框架、原生工具协议迁移或容错抽取方案。

当时本项从“潜在风险”升级为“已复现、待调查”；资源提示与 CLI 展示收尾不声称修复了它。后续格式反馈实现及其验收见下文，不能倒推上游输出异常的归因已解决。

## 2026-09-11：共享格式反馈修复

结论分两层：历史正文确实违反 JSON 协议；不能仅凭客户端记录断定是模型权重、服务端输出约束还是协议转换的问题。DeepSeek 官方承认 JSON Output 偶发空内容，但这不能替代对本项目每次失败的证据分析。

本次使用已有机制补齐恢复，不增加框架、错误类型、预算或模型专用分支：

- 客户端只将模型正文的 `JSONDecodeError` 直接交还调用方；网络及响应封装错误保留原有重试。
- AgentLoop 将其接入既有 required runtime_feedback，与 schema 错误共用连续失败上限 5、调用预算和超时；保持同 Session/Attempt。不给坏动作副作用，不自动修补/抽取 JSON。
- Compiler 共用该客户端，所以 draft/review 的 JSON 错误也进入现有一次重编反馈；不导入 runtime、不增加 Loop。
- trace 仍保留原始正文、reasoning、finish_reason、usage 和每次 HTTP 尝试。纠正是新 call_id；不把解析失败记作 schema 补充行，不重复计量。
- 不迁移原生工具调用，不提高输出额度，不宣称解决上游模型输出可靠性。当前 wire schema 仍为 6.0，历史 Run/Session/trace 不修改。

确定性覆盖：空白、夹带文字/工具标记、多个 JSON；无非法动作执行；同 Attempt 保留已完成工作；网络重试后遇坏 JSON 的精确计量；JSON/schema 混合错误共用上限；预算/超时；另一个最小客户端；Compiler draft/review 的有限纠正。服务器真实验收按 [JSON_OUTPUT_ACCEPTANCE.md](JSON_OUTPUT_ACCEPTANCE.md) 执行，未执行前不宣称真实模型已恢复。

参考（借鉴边界，不引入依赖）：[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[PI 参数校验与错误反馈](https://github.com/badlogic/pi-mono/blob/main/packages/agent/src/agent-loop.ts)、[PydanticAI 有界校验反馈](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/)。

首版本地结果：2026-09-11 在隔离目录完成全量 **825 passed, 1 skipped**（新增 20 个用例）、mock E2E completed、diff 检查干净。当时真实模型的纠正效果待服务器验收；后续结果见下文，历史 31 次错误不因测试通过而关闭归因调查。

## 8cfd373 服务器复核与评审提示收尾

证据根：`/root/autodl-tmp/acceptance-json-output/`。已读原始 request/response、注入备份、Session 事件和统计脚本，不只采信报告结论。原报告/日志保留；本节是更正，不把失败改写成通过。

- JSON 机制生效：Coding、Experiment、Scientific 的明确注入调用和下一调用之间只有 `observation(tool=llm)`，没有 action/工具执行事件；下一请求含一个 required runtime_feedback，同 Session/step、新 call_id。Compiler 使用已有 rejection feedback。不能把下一返回能解析等同于字段有效或任务完成。
- Coding 最终失败是五次混合失败：验证失败 → JSON 错误 → 验证命令被拒 → 缺少 torch 的验证失败 → JSON 错误。不是五次连续 JSON 错误。既有通用失败上限正确触发，不提高上限。
- Experiment 注入后恢复并调用训练，因缺少 torch 转入安装，安装超时后任务失败。JSON 恢复与整个任务完成必须分别报告；补验改用新建、明确标注的标准库小任务，不把它冒充原训练场景通过。
- 原脚本只统计 Extra data/Expecting value，漏计三条 Expecting ',' delimiter。218 个唯一调用实际有 **36 次解析错误**（22 Extra data、11 Expecting value、3 缺分隔符），schema 补充行 8、HTTP retry 0。未注入回归的 Flash 是 116 调用/17 解析错误，Pro 是 25 调用/0 解析错误；只描述本轮样本。
- 明确尾随文字注入至少五次：`inject-coding`、`inject-coding-final`、`inject-compiler`、`inject-experiment`、`inject-scientific`，另有早期空白注入混淆。不能按四个模块推算四次注入，更不能据此得出“29 次自然错误全部恢复”。`inject-coding` 中还混有多轮调用，须按实际运行与注入身份拆分。

Pro 首轮 code-experiment 的评审拒绝原文位于 call_id `7f7255f76faf4948a096a41a842f51f9`、`60321234640947229e38280bfa37dd9c`：它要求填 expected_metrics/expected_artifacts（以及代码路径），而代码会清空这些猜测字段，评审提示未解释该语义。属于生成/评审规则传达不一致的既有缺口，不能只归为随机漂移。

修复仅复用已有 `_capability_context`：把原先只给生成端的任务语义/证据字段说明移到共用提示，补充“有意留空、按任务语义评审、仍拒绝真实遗漏”。不新增函数/组件，不改规范化、schema、JSON 恢复、重试上限或预算。新增确定性测试验证两端提示一致、空/猜测字段投影仍正确、真实拒绝仍走原有上限；模型是否遵循需按 [补验单 §5–§6](JSON_OUTPUT_ACCEPTANCE.md#compiler-closeout) 验证。

该小收尾本地结果：编译器专项 **59 passed**，隔离目录全量 **828 passed, 1 skipped**、mock E2E completed、diff 检查干净。对应产品提交 `dd770f8` 的真实补验已执行并复核，见下节；不把首版 8cfd373 的服务器结果移作新提交结果。

<a id="verified-closeout"></a>

## 2026-09-11：dd770f8 补验复核与收尾

**结论：共享格式反馈与编译字段语义修复验收完成；上游偶发非法输出仍是已知限制。** 本节依据原始 request/response、Session 事件、执行日志及注入驱动复核，不只采信验收摘要。收尾仅更新文档，不修改产品代码、schema 6.0、预算或失败上限。

证据根：`/root/autodl-tmp/e2e-output-dd770f8-N3zmuh/`；服务器干净 worktree：`/root/autodl-tmp/projects/ResAgent2-dd770f8`。8 包 editable 指针已由验收方核对。服务器确定性基线 **828 passed, 1 skipped**，mock E2E completed、diff 检查干净。

| 补验 | 原始证据与结果 |
|---|---|
| 仅编译，Pro 两次、Flash 一次 | 三份 WorkflowProposal 均为 code_modify → experiment_run 并带依赖；draft/review 各含一份共用字段说明，评审均接受。三个精确字段仍为空，语义指标要求与失败时才提供日志的条件保留；每次 2 次调用、2 次 HTTP 尝试，estimated_tokens 1223–1942，结束原因 stop。 |
| Coding 标准库注入 | `01fdc9fe900d4711a74fbfc1e0a9efe8` 的尾随文字被拒；下一调用 `974959ef7f244ba496126b3010f0a1b4` 同 Session/step、恰一个 runtime_feedback。坏输出只有 llm 失败观测，无 action/工具执行；Attempt 保持 1。最终 add.py 改为加法，unittest 三项及 py_compile 通过；Session 用量 = 11 次调用 = 11 次 HTTP 尝试。 |
| Experiment 标准库注入 | `1e3cda746b19407c8e2acc5c856a4c3b` 被拒后，`a8d0fccdccb441268c150d4a1e73ddd2` 在同 Session/step 收到一个反馈段，无非法执行，Attempt 保持 1。随后真实执行 python run.py，冻结并交付 metrics.json，metrics.value=42；Session 用量 = 9 次调用 = 9 次 HTTP 尝试。 |

Coding 另有一次自然缺分隔符错误 `9953f6d0551c425da44a775a84fd59a1`，原文缺少动作对象末尾大括号；下一调用经同一路径纠正。不能把本次完成写成模型不再产生坏 JSON。五个 trace 目录/文件权限经复核为 0700/0600；验收方秘密扫描为零命中。

### 旧报告的最终勘误

旧证据根 `/root/autodl-tmp/acceptance-json-output/` 的 `CORRECTION_REPORT_JSON.md` 已更正总数为 **36 次解析错误**，但表格与文字仍有以下三处矛盾；以本节复核为准，原始 trace、Session、报告和注入备份不覆盖：

1. `inject-coding-final` 的结束原因不是“连续七次坏 JSON”。其 Session 事件 43/44/46/48/49 是验证失败、JSON 错误、验证命令被拒、验证失败、JSON 错误，五次混合失败触发既有上限。
2. `inject-experiment` 不是“未执行训练命令”。事件记录显示已调用 `python train.py`，因缺少 torch 在导入阶段失败；随后安装依赖超时。命令被尝试、训练未实际开展、任务最终失败应分开陈述。
3. 早期空白调用 `64481b95d54a4151b165dfc4c5c2482e`（01:53:33 UTC）的来源仍未确认。现存注入脚本修改于 02:00:48，现存 Coding 原响应备份也更晚，不能据后来的“仅对合法 JSON 注入”代码证明早期未注入。因此不能把 11 条空白响应全部认定为自然响应；该调用单列未知，36 次解析错误总数不变。

收尾时服务器 SSH 拒绝连接，未重写服务器更正报告或 MANIFEST；本节将最终勘误随代码版本归档，后续读取服务器旧报告须同时参考本节。这不影响此前已完成的原始证据复核。

### 验收边界与剩余风险

- `8cfd373` 的回归、混合失败和安装超时原样保留；`dd770f8` 只做三次编译与两个轻量注入补验，没有重跑完整 GPU 矩阵。
- Agent 探针直接核对 Session.llm_calls_used，不冒称这些独立探针创建了 ResearchRun；Run 总账由首版回归及确定性测试覆盖。
- 三次评审接受只证明本轮遵循提示，不保证未来永不误判。JSON/schema/工具失败仍共用有界恢复；耗尽时明确失败，不自动修 JSON、不提高上限。
- 本实现项可以收尾；若后续仍频繁出现非法正文，应保留原始请求/响应再调查 provider 协议，不据此新增模型专用补丁或无限重试。
