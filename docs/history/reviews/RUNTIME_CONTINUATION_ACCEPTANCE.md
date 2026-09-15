# 串行原生工具 / 唯一调用预算 / Session 检查点：服务器验收

对应 [开发记录](RUNTIME_CONTINUATION_PLAN.md)；实现边界见 [CONTEXT](../../current/CONTEXT.md#compaction) 和 [CONTRACTS](../../current/CONTRACTS.md#tools)。

**本阶段已完成 f98b6fd 服务器补验与主开发复核：[最终结果、计量勘误与边界](CONTEXT_ALLOCATION_REVIEW.md#verified-closeout)。** 以下保留原验收要求，§7为当时的补验入口，不表示仍需重复付费运行。b4868ec 的旧验收现场原样保留。

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

空摘要、finish_reason=length、异常、重建真正超限、仅剩一次调用、单巨大最新回合的拒绝与不推进边界，用确定性测试作为硬证据；略超摘要生成目标但完整请求能容纳，应完整接受，不当作失败。可选一次标注清楚的空摘要注入复验，不要求逐项付费重跑。

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

<a id="soft-target-followup"></a>

## 7. 摘要软目标与独立工作区补验（b4868ec 之后）

后续统一材料分配已接入同一Composer；测试最新提交时以[上下文分配补验](CONTEXT_ALLOCATION_REVIEW.md#4-服务器补验要求)为本轮入口，沿用本节的fixture隔离、压力与跨进程要求。旧baseline数字和旧workspace_reads段名不作为新提交的断言。

### 7.1 先冻结产品身份和正确的测试输入

用新提交、干净 worktree 和独立验收根；记录实际 SHA、8 包 import 指针、schema 8.0（本次未改公共 schema）。原现场 `/root/autodl-tmp/e2e-rtc-b4868ec-Q3wR7s/` 和旧 L3 不动。不得原地覆盖失败 trace 或清理旧工作区。

每个代码修改探针必须有独立 fixture 工作区，不能只换 outdir/session/run_id，却复用上一探针改过的代码。启动前必须断言并记录：

- fixture 的 Git HEAD 和工作树状态，工作树必须干净；
- add.py 的工作树内容与 HEAD 内容一致，仍是 `return a - b`；
- fixture 文件 hash、实际 workspace.root；
- unittest 在启动前确实暴露加法错误，不能把“测试已通过”的目录当修复任务。

从同一含 bug 的 fixture 提交建立新 clone/worktree 即可，不 reset 旧现场，不通过给模型加提示掩盖输入错误。

### 7.2 本地/服务器确定性检查

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pytest tests/runtime/test_compaction.py tests/runtime/test_history_checkpoint.py tests/runtime/test_native_tool_calls.py -q
git diff --check
```

最终基线看开发记录。重点：8192 额度下，旧目标 1636 字符、实际 1697 字符的非空摘要，若完整请求装得下则保留全文并完成；ASCII/中文都覆盖。真正整包超限返回 budget_exhausted，旧摘要、边界和原始历史不动，已花调用仍入账；空白和 length 响应也不提交检查点。

### 7.3 只补三个小型真实探针

运行前冻结配置，不临时扩预算。模型 Profile 仍为原 1M/256K/1024；不因摘要目标变短而缩小 Provider 输出额度。按 §0 保持小任务预算，不跑 GPU、torch 或全文献矩阵。

1. **Pro 修复重验**：原 goal、Pro、128K、40 calls/600s 均不变，只修正 fixture 隔离。检查真实编辑、unittest、finish，而不是拿 `git diff HEAD` 中其他运行留下的改动当成 Pro 产物。失败时输出每个 call_id 的 latency_ms、attempts、finish_reason、工具与真实 observation，不再用最后一句超时推断“全程没执行工具”。
2. **Flash 8192 压力探针**：同原任务意图，在全新 fixture/Session 中用原 8192 输入配置重跑；不改默认128K。记录每次摘要实际长度与生成目标、完整输入估算、保存边界、后续动作与最终真实文件。自然摘要若没有超目标，标为本次未自然触发；“1697 > 1636 且整包可容纳”的接受分支由上面的确定性测试证明，不反复跑到目标长度。若长摘要使整包真正装不下，仍应明确失败并保留现场，不能视为应强行接收。
3. **压缩后暂停与跨进程恢复**：全新小探针，先确认已发生有效压缩，再 ask_user 暂停；进程退出后由另一进程提交预先约定的答案并继续同 Session。若模型未自然提问，可使用明确标注的单次合法 ask_user 响应注入，后续仍由真实模型继续。核对检查点保存、近期 calls/receipts 配对、原题/答案实际进入当前上下文、原始前缀不重复发送，以及结果对应答案。续跑额度按原调用方语义下发，不能把同 Run 已花调用清零。

旧版已通过的 Flash 小任务、Experiment、普通 Scientific 问答和串行批次不需整套重跑。12000 压力结果可留作旧版对照，不用于覆盖8192失败。

### 7.4 追加勘误与交付

旧 MANIFEST 原件保留，新增勘误文件并引用原始证据：

- Pro 首次读取 add.py（Session event 4）已经是 a+b，而 Flash 首次为 a-b；初始 workspace_snapshot 不同，不能称同条件修复对照。Pro 有21次模型调用且执行过工具，两次测试成功，最后 finish 才遇到派发前超时；不能直接归因“模型600s一直没派发工具”。
- 8192 的摘要长度为1056、1007、1089、1697，并非逐次递增。最后碰旧局部硬上限是真的，但不据此断言压缩必然无限增长。

这些是对 b4868ec 旧现场的纠正，不把新探针成功改写成旧探针通过。新报告分开列产品身份、fixture身份、协议/模型/工具/最终结果四层、调用计量（含摘要及HTTP重试）、0700/0600、密钥扫描和未覆盖项。不要合并main、push或恢复旧L3。
