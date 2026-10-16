# Scientific Agent

`ScientificAgent.invoke(AgentRequest) -> AgentResult` 与执行 Agent 使用相同协议。
Scientific 使用职责提示与实际注册工具的 schema，Session 归属 Run，不伪造 Task。
以用户目标与明确约束判断材料缺口，选择自己的工具或执行工作，再综合交付。
文献检索不是必经第一步；小批结果供判断阅读、改词、换源、分页或停止，
不规定搜索轮数或论文配额。来源操作细节由 literature_search 的能力说明维护。

request_work、ask_user 和共享 finish 都使用原生工具调用。request_work 需要明确授权，
运行时先保存稳定工作 ID 和调用来源，再返回接收回执并暂停；ask_user 同样先保存问题 ID。
控制请求直接持久化，不产生 work_request/question/scientific_assessment 工件。
恢复时只消费对应控制请求的结果，旧接收回执不被后来的结果改写。

finish 提交 report 及一个 scientific_opinion JSON artifact。完成检查验证格式、
引用工件的授权/归属与冻结完整性、要求的证据 kind，以及失败工作对应的局限。
`required_evidence_kinds` 要求引用 literature_paper 或 literature_fulltext，临时搜索列表
不能代替论文。访问历史不阻塞提问、委托、引用或完成；
Scientific 仍应根据实际可见材料判断支持程度，缺少关键正文时主动读取或说明限制。

`conclusion_requirements.required_artifacts` 给出用户显式声明的逻辑输出名，精确、
区分大小写地匹配本 Run 已登记的 output_name。缺失时原 CompletionCheck 返回
对应 finish 拒绝回执，同一 Session 可继续请求工作或提问，共用原预算；存在检查不要求
引用或读取产物，也不评价科学有效性。Scientific 在工作目标或约束中保留明确名称，
让执行 Agent 按同名 output_name 提交。

搜索仅返回临时文献候选；选定材料后用 fetch_literature(source) 保存 literature_paper，
并尽可能获得原始 PDF 与提取全文，各自使用独立工件 ID。source 来自搜索回执的精确
fetch_source，或 artifact:<已授权论文工件 ID>。检索预览、摘要、正文和原始 PDF 不互相冒称。
外部导入论文也经既有授权 input_artifacts 与科研目录交接，来源标为用户导入；
全文工具优先复用导入 PDF，按需解析，不要求为了使用本地材料重新联网检索。
组合根可用 `literature_parser` 注入解析函数；未提供时沿用 Components 的默认解析器。
Scientific 不读取 CLI 环境变量，模型工具不接收超时覆盖参数。
observation_trace 从真实工具访问确定性生成，模型不能自行填报，也不代表读完全文。

完整目录由已授权 Ref 与 WorkResult 导航事实重建，不生成 ResearchIndex 快照。
目录保留原 Artifact ID、来源关系、Task 与 Attempt、原报告与实际产物；报告正文按需读取。
业务问答完整进入任务需求，操作批准不进入任务需求；两者均不创建答案工件。
同一 Session 的持久结果沿原恢复链复用，固定 prompt/tools 或原生协议变化则拒绝恢复。

当前接入规则：文献和网页搜索只返回临时线索；fetch_literature(source)/web_fetch 显式保存材料，read_artifact 按需读取。工具回执只进原生历史，实际 Ref 增量由共享 Loop 摄入完整目录。工作结果按 WorkRequest → Task → 原报告和实际产物展开，不生成总体报告。业务问答直接保存并累计，操作批准续执行原调用。规则以 [CONTEXT](../../../docs/current/CONTEXT.md) 和 [CONTRACTS](../../../docs/current/CONTRACTS.md) 为准。
