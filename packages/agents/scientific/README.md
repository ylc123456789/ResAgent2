# Scientific Agent

`ScientificAgent.invoke(AgentRequest) -> AgentResult` 与执行 Agent 使用相同协议。
Scientific 使用职责提示与实际注册工具的 schema，Session 归属 Run，不伪造 Task。
以用户目标与明确约束判断材料缺口，选择自己的工具或执行工作，再综合交付。
文献检索不是必经第一步；小批结果供判断阅读、改词、换源、分页或停止，
不规定搜索轮数或论文配额。来源操作细节由 literature_search 的能力说明维护。

工具包括 read_artifact、可注入的 literature_search / fetch_literature_fulltext、
request_work、ask_user 和共享 finish。request_work 需要系统明确授权，
assessment 与工作正文进入 work_request artifact；ask_user 的问题进入 question
artifact，当前判断保存在 scientific_assessment artifact。控制信号只引用产物，
不复制领域正文。

finish 提交 report 及一个 scientific_opinion JSON artifact。完成检查验证格式、
引用工件的授权/归属与冻结完整性、要求的证据 kind，以及失败工作对应的局限。
`required_evidence_kinds` 要求引用 literature_paper 或 literature_fulltext，搜索回执
literature_search 不能代替论文。访问历史不再阻塞提问、委托、引用或完成；
Scientific 仍应根据实际可见材料判断支持程度，缺少关键正文时主动读取或说明限制。

`conclusion_requirements.required_artifacts` 给出用户显式声明的逻辑输出名，精确、
区分大小写地匹配本 Run 已登记的 output_name。缺失时原 CompletionCheck 返回
runtime_feedback，同一 Session 可继续请求工作或提问，共用原预算；存在检查不要求
引用或读取产物，也不评价科学有效性。Scientific 在工作目标或约束中保留明确名称，
让执行 Agent 按同名 output_name 提交。

每篇论文有独立 literature_paper，包含元信息与摘要；需要全文时按论文工件 ID 调用
fetch_literature_fulltext，得到 literature_pdf 与 literature_fulltext 的引用。
检索预览、元信息、摘要、解析正文和原始 PDF 不互相冒称；同 Run 已登记材料可复用。
外部导入论文也经既有授权 input_artifacts 与科研目录交接，来源标为用户导入；
全文工具优先复用导入 PDF，按需解析，不要求为了使用本地材料重新联网检索。
组合根可用 `literature_parser` 注入解析函数；未提供时沿用 Components 的默认解析器。
Scientific 不读取 CLI 环境变量，模型工具不接收超时覆盖参数。
observation_trace 从真实工具访问确定性生成，模型不能自行填报，也不代表读完全文。

业务回答与工作反馈按 resume_artifact_ids 从正式快照投影到必需上下文；操作批准保留原件和授权目录入口，不累积为任务需求。Scientific
消费固定 Interpreter 组织的完整科研目录和本轮原报告，不生成另一份目录或简报。
目录保留原 Artifact ID 及可选直接来源 ID；原文和身份仍以 Registry 为准。
阅读答案不消费批准或改变恢复范围；同一 Session 的持久结果沿原恢复链复用。
共享 workspace_context 投影真实阅读片段，超出上下文预算时保留原件读取入口。
