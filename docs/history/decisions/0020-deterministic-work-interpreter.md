# ADR-0020：Interpreter 固定组织原报告

- 状态：accepted
- 日期：2026-09-28
- 契约：schema 19.0
- 取代关系：取代 [ADR-0017](0017-research-index-and-work-interpreter.md) 中每轮调用 LLM 生成带引用简报的选择；保留 Interpreter 的 Orchestrator 归属、派生科研目录与冻结交接。继续遵守 [ADR-0018](0018-complete-index-and-paired-answers.md) 的完整目录与问答阅读，以及 [ADR-0019](0019-agent-declared-completion.md) 的任务状态与结构验证边界。

## 原因

专业 Agent 已通过统一 report/artifacts 交付结果。必经的 LLM 简报再次转述这些报告，增加调用、丢失细节和误读历史的机会；科学综合本就属于 Scientific。架构的正反向对称不要求两个方向各调用一次模型。

## 决定

保留小接口 `WorkInterpreter.interpret(record: WorkRecord) -> str`，生产与测试共用固定实现。Interpreter 生成科研索引，并按已有 Task 顺序与真实 Attempt 序号组织最新已记录报告。原目标、期望证据、约束、状态、错误、累计警告和产物 ID 都来自已有字段；报告原文不重写，不把缺报告时的任务说明冒充结果。旧尝试仅列编号、状态，正文保留在原 WorkRecord。

`WorkFeedback.report` 替换 brief，删除 WorkBrief/CitedStatement、模型客户端及专用上下文配置。Controller 继续冻结、验证、保存、复用交接；Scientific 接受返回前不消费 WorkRequest。Interpreter 不增加模型用量、不重置预算，Run 时间约束仍生效。

Scientific 上下文使用既有 ContextMaterial/Composer：完整目录与最小事实框必需，原报告正文按共享额度展示，截断或省略显式标记。框从同源 WorkRecord 取事实，不解析 Markdown。长 JSON 字符串可通过 read_artifact 的 start_char/end_char 范围继续读取；物理行号、冻结内容和整体哈希校验不变。

目标、任务、尝试和产物沿用原 WorkRequest/Task/Attempt/Artifact 身份及名称，不建立别名系统、额外关系图或报告存储。Scientific 负责语义综合；不新增科学语义 validation、自动观察、传递观察或全文阅读追踪。

## 代价与验证边界

原报告可能比简报长，依靠已有预算分配与按需读取处理；这不保证模型正确理解每项报告或更低总 token 消耗。报告作为自然语言仍可能出错，固定组织只减少一次转述。

schema 19 不兼容旧 Run 恢复，旧状态与证据原样保留、不迁移。决策记录时已编写针对原文/身份、恢复复用、预算、冻结完整性和长报告窗口的回归用例，当时仅完成静态检查。2026-09-29 后续产品 `f8439c23` 已完成服务器全量回归、mock 与真实 L3；验收范围和剩余限制见[收尾记录](../README.md#code-health-closeout)。这不将一次成功 Run 扩大为所有研究任务的质量保证。
