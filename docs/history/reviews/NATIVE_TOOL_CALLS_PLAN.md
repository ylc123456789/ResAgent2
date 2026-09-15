# 原生工具调用：实现与阶段边界

基线 `fa711a0`；分支 `feat/native-tool-calls`；公共 schema 7.0 不变。
本地实现、文档及确定性回归已完成，真实模型效果待按[小型验收单](NATIVE_TOOL_CALLS_ACCEPTANCE.md)核验，不宣称旧 L3 问题已消失。

## 实现范围

1. **同一套 Tool，两种明确用途。** 三个 Agent 的生产客户端通过原生 `tools` 发送既有 `Tool.input_model` 完整 schema；模型的调用经现有 AgentAction、权限、参数校验和完成检查执行。Compiler 仍通过 `next_action` 生成/评审任务图 JSON。没有为三个 Agent 分别打补丁，也没有正文 JSON/DSML 兜底执行器。
2. **Session 管协议配对。** `tool_turns` 保存模型返回的调用、可选 reasoning 和工具回执；先保存调用再执行，回执随原有事件路径保存。进程重启发现缺回执，只记录结果未知，不自动重放副作用。它不保证跨文件事务或掉电 exactly-once。
3. **保留领域上下文。** 原生请求为固定协议说明 + 已配对历史 + 当前重建的领域 Context。历史不再剪成400字符预览，也不累计旧完整 prompt；已有工作集、状态语义和完成门禁保留。历史/schema/当前文本共用128K总输入额度，包含序列化转义，超限明确失败；没有新压缩、长期记忆或预算扩容机制。
4. **恢复身份明确。** Session 的 `tool_protocol_key` 固定协议与配置身份；生产客户端用协议、endpoint、model 的 hash，不含密钥。恢复拒绝 JSON↔原生切换及换模型/服务，不做旧记录迁移。旧 Run 和冻结证据保留，新的测试使用新 Run。
5. **调试与执行记录分开。** trace 档位只控制 trace；Session 的协议续传仍需保存模型返回的 reasoning，目录/文件为0700/0600。full trace 新增 `raw_tool_calls`；CLI `/trace` 同步显示，其他 CLI 装配和命令不改。

独立的 `tool_calling.py` 只放 schema、消息、回执和预算投影；HTTP/重试沿用同一客户端实现，调度业务与科研输入输出契约不改。没有并行工具执行、强制结束、自动修 JSON 或提高50步上限。

## 本地核验

- 全量：`python -m pytest tests apps/cli/tests -q` → **946 passed, 1 skipped**。
- mock E2E → `run_golden completed`；本次临时产物已清理。`git diff --check` 与10份修改文档的相对文件链接检查通过。
- 新原生测试31项：原生schema、正文不执行、多调用整批拒绝、权限、完成拒绝、问答/工作请求续跑、reasoning续传、跨Session隔离、进程中断、协议身份拒绝、输入预算及trace档位。
- 旧 JSON-only 注入客户端测试明确保留；Scheduler 的同 Attempt 格式恢复/新 Attempt 传输重试同时覆盖 JSON 与原生客户端。
- CLI 增两项原生 trace 展示测试；Controller、Scheduler、三个 Agent 的业务实现未变。

这证明执行与恢复边界，不证明真实模型一定不循环。首轮只做标准库 Coding 对照、Scientific 问答、Experiment 小脚本，不重跑 GPU/L3，不动旧暂停现场。

## 取舍与下一步

- 原生参数仍可能生成错误；沿用有限反馈，不无限重试。
- 历史与当前工作集可能有重复内容，都计入总预算；首版不另做智能去重或压缩。长期会话超限时如实失败，再根据真实样本决定是否需要改进。
- 保留现有 profile 的参数示例，原生 schema 是执行参数的事实来源；本轮不顺带重写三个 Agent 的业务提示。
- 原生 reasoning 仅用于协议续传，不是业务证据，也不汇入领域 memory。

参考：[DeepSeek thinking/tool continuation](https://api-docs.deepseek.com/guides/thinking_mode/)、[DeepSeek tools](https://api-docs.deepseek.com/guides/tool_calls/)。当前代码与接口见 [CONTEXT](../../current/CONTEXT.md)、[Runtime](../../../packages/runtime/README.md)。
