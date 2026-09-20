# Scientific Agent

`ScientificAgent.invoke(AgentRequest) -> AgentResult` 与执行 Agent 使用相同协议。
Scientific 保留一套提示词与动作 schema，Session 归属 Run，不伪造 Task。

工具包括 read_artifact、可注入的 literature_search、request_work、ask_user 和共享 finish。
request_work 需要系统明确授权，其 assessment 与工作正文进入 work_request artifact；
ask_user 的问题进入 question artifact，当前判断保存在 scientific_assessment artifact。
控制信号只引用产物，不复制领域正文。

finish 提交 report 及一个 scientific_opinion JSON artifact。完成检查验证内容格式、
真实观察过的引用、要求的证据 kind，以及失败工作对应的科研限制说明。
observation_trace 由工具记录确定性生成；模型不能自行填报观察历史。
文献检索中途登记的 ArtifactRef 可以同轮读取，并按原 ID 返回。

恢复回答和成对工作反馈从正式快照读取，按照 resume_artifact_ids 投影到每步
必需上下文；材料出现不代表已观察其引用的证据。重复调用使用本 Session 的
持久化结果，重复反馈不重新消耗 LLM 调用。

[interpreter.py](src/resagent2_scientific/interpreter.py) 将工作反馈呈现为科研上下文，
区分解释性报告、失败诊断与原始证据。共享 workspace_context 投影真实 artifact
阅读片段，超出上下文预算时保留分页阅读能力。系统没有新增 Scientific 专用记忆层。
