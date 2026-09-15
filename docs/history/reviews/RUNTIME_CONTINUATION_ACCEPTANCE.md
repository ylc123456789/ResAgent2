# 串行原生工具 / 唯一调用预算 / Session 检查点：服务器验收

对应 [开发记录](RUNTIME_CONTINUATION_PLAN.md)；实现边界见 [CONTEXT](../../current/CONTEXT.md#compaction) 和 [CONTRACTS](../../current/CONTRACTS.md#tools)。

## 0. 范围与纪律

- 测试分支 `feat/runtime-continuation`；启动前读取实际 HEAD，冻结 SHA、模型与有效配置，不根据文档猜 SHA。
- 仅同步、测试、分析、报告。不修改产品/prompt/目标/预算，不合并、不 push。旧 L3 和 schema 7.0 Run 原样保持暂停，不恢复、不迁移。
- 本轮不需要 GPU、torch、真实数据集或文献联网。使用标准库小任务，禁止为凑通过安装大依赖或反复重跑到绿。
- 新建单一验收根，保存 MANIFEST、logs、traces、sessions/workdirs、ops；独立干净 worktree，同步用 bundle 或 Git，不 scp 零散源码。
- 8 个 editable 指针记录前后值，不删除旧 checkout/环境/历史。凭据仅运行时加载，不写脚本、不打印。
- 付费请求启动前明确配置和上限。建议普通小任务每个 Run 最多 40 calls / 600s，压缩探针最多 12 calls / 300s；这些是本轮探针配置，不修改产品默认。运行前一次性冻结，失败如实保留。

## 1. 身份和确定性基线

记录 HEAD、git status、8 包 __file__、Python 版本、实际模型、ModelProfile、模块输入上限。确认 schema 为 8.0，TaskBudget 无 max_steps。

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
python -m pytest tests/runtime/test_native_tool_calls.py tests/runtime/test_compaction.py tests/runtime/test_history_checkpoint.py tests/runtime/test_guards.py tests/runtime/test_resume.py tests/orchestrator/test_workflows.py tests/orchestrator/test_controller.py apps/cli/tests/test_shell_render.py -q
```

全量基线以开发记录最终值为准。超过 50 次调用、损坏批次、权限变化、崩溃时未知/未开始区别等已有确定性测试；不为验证这些数字浪费 50 次付费调用。

## 2. 小型真实任务回归

用生产 Agent/Tool/Session 组合，不绕过完成门禁：

1. Coding Flash、Pro 各一次：标准库 add.py 的 a-b 错误，要求修复并跑 unittest；核对真实 diff、验证命令、finish 和产物。goal 保持一句自然语言，不把实现步骤塞给模型。
2. Experiment Flash 一次：标准库脚本输出 metrics.json，核对真实执行与 metrics 一致，不安装 torch/numpy。
3. Scientific 经真实 CLI 问答：run → paused，读取实际 requested_fields，回答预先约定的选项 → 新进程 answer → completed。不用猜字段名，不由测试方临时替用户作研究决策。

明确注入与 CLI 相同的模型窗口/输出预留配置，默认 Agent 输入 128000。记录 Profile 的真实值与 request_max_tokens，不把“无 Profile”报告成生产同配置。Compiler 保持正文 JSON，不加入原生工具或压缩。

分别报告原生格式是否正确、工具是否执行、最终任务是否完成；一次成功不等于从此没有循环。

## 3. 串行批次（真实协议 + 可标注的一次注入）

模型自然返回多工具时直接检查；若没有，不反复诱导刷样本。可在独立探针中仅替换一次响应为合法的 2–3 个只读工具调用，之后仍使用真实模型；明确记为协议注入，不称模型自然生成。

- 数组顺序与 action/observation 顺序一致；每个 call_id 恰有自己的 receipt，value/observed_at 不串项。
- 1 次模型回复、多次工具动作：llm_calls 增 1，step 增实际尝试的动作数，不把工具数量算成模型调用。
- 依赖前项返回值才能决定参数时应下一轮再请求；同批是事先确定参数的串行执行，不是并行或事务。
- 定向非法后项参数：整批零执行、配齐拒绝回执；中途工具失败：前项结果保留，后续记未执行。用标准库、临时文件，非法内容执行次数为 0。
- finish/ask_user/request_work 混入普通调用的批次整批拒绝，不能先写文件再 finish。
- 重启中间项的确定性测试确认：已完成不重放、executing_call_id 对应无回执为未知、后续为未开始。不得以真实破坏性操作验证。

## 4. 压缩和暂停恢复（核心新增，独立探针）

默认 128K 小任务通常不会触发压缩；没有触发应报告“未触发”，不能只检查 helper 存在就声称完成。

使用全新小任务/Session，在驱动装配处单独降低模块输入上限（建议从 8K–16K 中预先选一个能容纳工具 schema 与当前必需段的值），**保持 ModelProfile 的输出额度不变**。这是压力探针，不修改产品默认或已运行场景的预算。

准备数个实际读取的小文件，让真实工具产生多个完整历史回合，每份结果有不同标记；历史需超过输入 80%，而单个近期回合必须能完整保留。若驱动预置历史，必须来自本探针真实工具结果或明确标为合成协议夹具，不能冒充科研证据。不要把一份巨型工具结果塞进去后要求系统切断半个回合。

核对：

1. 摘要前后原始 tool_turns/events 未删除或改写。history_checkpoint 的 history_start 是绝对 turn 边界，至少保留最新完整 turn。
2. 摘要 trace 为纯文本请求，included_sections=[compaction]，不带 tools；action_valid/tool/parsed_action 为 null。输出 reservation 与普通模型调用相同，不临时降到几千 token。
3. 后续原始 request_text 只含检查点后的完整 assistant/tool pairs、恰一个 history_checkpoint 段及当前领域上下文。旧回合的原始 reasoning 不继续整段重放；原记录仍在 Session。
4. 当前回答、任务和验证状态仍由原 context builder 提供，摘要明确不是证据/当前状态，完成门禁没有靠摘要跳过真实操作。
5. 摘要后 ask_user 暂停并保存，用新进程、同 endpoint/model/protocol 恢复；边界与摘要不丢，回答被消费，不重新总结已覆盖原始前缀。如果自然动作没暂停，可单独用明确的协议注入建立这个边界。
6. 继续执行确实完成小任务，不仅“摘要 API 返回成功”。

空摘要、finish_reason=length、异常、过长摘要、重建仍超限、仅剩一次调用、单巨大最新回合的拒绝与不推进边界，用确定性测试作为硬证据；可选一次标注清楚的空摘要注入复验，不要求逐项付费重跑。

## 5. 计量、可观测性、安全

- 按带 model 的主 trace 行、call_id 去重，累计 retry_number+1；不要又加 attempts 长度，不计 schema 补充行。**摘要调用同样入账**，不能过滤 action_valid=null 的行后再核对调用总数。
- 汇总与 ModuleResult.llm_calls / Session.llm_calls_used 对齐；经 Controller 的完整 Run 同时核对 Run.llm_calls_used。直接模块探针没有 Run 总账，不伪造这一检查。
- step 是动作序号，摘要不增加 step；同批工具共享一个模型 call_id，Session 内 call ID 仍各自唯一。
- 原生 request_text 为 {messages,tools}；摘要为 {messages}。分别按完整序列化请求重算 ceil(chars/4)，不是只计领域正文。
- CLI live/trace 能显示串行工具名与 compaction，不必读取 full 原文才能显示活动。
- trace 和 Session 目录 0700、文件 0600；扫描真实密钥定值但不打印密钥。metadata/off 的内容边界由确定性测试覆盖。

Run/Session/Provider 调用不具跨文件事务；异常断电可能在已花调用落入 Run 总账之前中断。不要据正常运行记账精确就声称全场景 exactly-once 或绝对货币硬上限。

## 6. 报告

每个探针写明 SHA、Profile、有效输入、调用/时间预算、是否注入、Session/trace 路径、关键 call_id、结果及失败现场。分开判断：

1. 协议/预算/持久化机制正确；
2. 模型按上下文继续；
3. 工具真实执行正确；
4. 最终任务完成。

正常回归、压力探针、注入不可混算成功率。结尾列出尚未验证的长任务效果；不因为小任务通过自动恢复旧 L3。收口由主开发复核后决定。
