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

本项从“潜在风险”升级为“已复现、待调查”；资源提示与 CLI 展示收尾不声称修复了它。

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

本地结果：2026-09-11 在隔离目录完成全量 **825 passed, 1 skipped**（新增 20 个用例）、mock E2E completed、diff 检查干净。真实模型的纠正效果仍待服务器验收，历史 31 次错误不因确定性测试通过而关闭归因调查。

## 8cfd373 服务器复核与评审提示收尾

证据根：`/root/autodl-tmp/acceptance-json-output/`。已读原始 request/response、注入备份、Session 事件和统计脚本，不只采信报告结论。原报告/日志保留；本节是更正，不把失败改写成通过。

- JSON 机制生效：Coding、Experiment、Scientific 的明确注入调用和下一调用之间只有 `observation(tool=llm)`，没有 action/工具执行事件；下一请求含一个 required runtime_feedback，同 Session/step、新 call_id。Compiler 使用已有 rejection feedback。不能把下一返回能解析等同于字段有效或任务完成。
- Coding 最终失败是五次混合失败：验证失败 → JSON 错误 → 验证命令被拒 → 缺少 torch 的验证失败 → JSON 错误。不是五次连续 JSON 错误。既有通用失败上限正确触发，不提高上限。
- Experiment 注入后恢复并调用训练，因缺少 torch 转入安装，安装超时后任务失败。JSON 恢复与整个任务完成必须分别报告；补验改用新建、明确标注的标准库小任务，不把它冒充原训练场景通过。
- 原脚本只统计 Extra data/Expecting value，漏计三条 Expecting ',' delimiter。218 个唯一调用实际有 **36 次解析错误**（22 Extra data、11 Expecting value、3 缺分隔符），schema 补充行 8、HTTP retry 0。未注入回归的 Flash 是 116 调用/17 解析错误，Pro 是 25 调用/0 解析错误；只描述本轮样本。
- 明确尾随文字注入至少五次：`inject-coding`、`inject-coding-final`、`inject-compiler`、`inject-experiment`、`inject-scientific`，另有早期空白注入混淆。不能按四个模块推算四次注入，更不能据此得出“29 次自然错误全部恢复”。`inject-coding` 中还混有多轮调用，须按实际运行与注入身份拆分。

Pro 首轮 code-experiment 的评审拒绝原文位于 call_id `7f7255f76faf4948a096a41a842f51f9`、`60321234640947229e38280bfa37dd9c`：它要求填 expected_metrics/expected_artifacts（以及代码路径），而代码会清空这些猜测字段，评审提示未解释该语义。属于生成/评审规则传达不一致的既有缺口，不能只归为随机漂移。

修复仅复用已有 `_capability_context`：把原先只给生成端的任务语义/证据字段说明移到共用提示，补充“有意留空、按任务语义评审、仍拒绝真实遗漏”。不新增函数/组件，不改规范化、schema、JSON 恢复、重试上限或预算。新增确定性测试验证两端提示一致、空/猜测字段投影仍正确、真实拒绝仍走原有上限；模型是否遵循需按 [补验单 §5–§6](JSON_OUTPUT_ACCEPTANCE.md#compiler-closeout) 验证。

该小收尾本地结果：编译器专项 **59 passed**，隔离目录全量 **828 passed, 1 skipped**、mock E2E completed、diff 检查干净。真实模型补验待执行，不把首版 8cfd373 的服务器结果移作新提交结果。
