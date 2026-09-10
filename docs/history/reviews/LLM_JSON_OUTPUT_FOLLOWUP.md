# LLM JSON 输出问题：已复现，待单独调查

记录日期：2026-09-10。观察代码：`fix/runtime-resources @ 577b8489`。
本项不属于资源发现或暂停恢复的实现变更；本轮只记证据，不修改 provider、解析、重试或预算。

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

## 当前处理及边界

- 保持严格解析，不截取第一个 JSON 冒充完整回答，不把 reasoning 或 DSML 当作可执行动作。
- 客户端仍是有界重试；耗尽后返回 retryable 错误，Scheduler 在剩余 Task/Run 预算内决定新 Attempt。失败记录和用量保留。
- `tests/runtime/test_llm_recovery.py`、`test_llm_attempt_trace.py` 已覆盖恢复与计量机制；它们不证明真实 provider 总能返回合法 JSON。
- 本轮不增加额度、自动修 JSON、无限重试或模型专用解析分支。

## 后续调查（未执行）

1. 保留上述现场。用其中一个失败请求做独立、有界复现；核对真实模型/endpoint、消息包装和 json_object 配置，不执行返回的工具、不覆盖历史 Run。
2. 区分纯空白、多个 JSON、夹带文本与 schema 不匹配；同时记录 finish_reason、usage 和每次尝试。不能把所有失败统称为网络波动或输出截断。
3. 根据复现与 provider 的实际协议决定最小修复，再评估是否需要把解析错误变成结构化反馈。当前不预选新框架、原生工具协议迁移或容错抽取方案。

本项从“潜在风险”升级为“已复现、待调查”；资源提示与 CLI 展示收尾不声称修复了它。
