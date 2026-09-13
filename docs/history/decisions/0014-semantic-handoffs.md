# ADR-0014：解释工件与原题配对的语义交接

- 日期：2026-09-12
- 状态：accepted；`fix/semantic-handoffs` 实现，已完成语义交接验收；暴露的上下文消费缺口经后续独立阶段修正，见文末验证记录。
- 局部补充 ADR-0011 的结果交接、ADR-0012/0013 的问答恢复；原状态机、资源作用域、预算和证据门槛不变。

## 问题

代码理解 answer/uncertainty 与修改、实验的 residual_risks 留在领域 payload 中；Scheduler 保存了它们，但后续 Agent 的授权读取路径不直接解释 payload。仅把通用 summary 交给 Scientific 会漏掉完整答案或风险。

用户回答虽然已经进入 required answers 上下文，但原问题只留在已清除的 PendingQuestion 或 Agent 私有事件里。回答“是”“第二个”时，仅有 question_id 和 values 不足以恢复语义；Controller 不应读私有 Session 猜原题。

## 决定

1. capabilities 增加一个 `build_module_report(details)` 纯函数，产生 `kind=module_report`、`path=module_report.md`、`media_type=text/markdown` 的 ArtifactCandidate。用字段标题组织选定解释，开头的用途说明与摘要明确其不是独立验证或测量证据；不做 IO，不调用 LLM，不序列化整个结果对象。
2. code_understand 完成后总是交付 answer、uncertainty、evidence_files；code_modify/experiment_run 仅在 residual_risks 非空时交付 summary 与风险列表。原 payload 和通用 summary 保留。
3. 解释沿既有 Registry → 依赖授权/Scientific 授权 → read_artifact 路径传递。interpreter 标记解释用途，不把报告当成原始代码、验证、运行日志或测量结果。报告在原完成检查之后追加，不满足缺失的实验交付项、不参加 metrics 推导。
4. 用户入口仍接收 UserAnswer。新增系统内部 `RecordedAnswer(UserAnswer)`，必填 question_text；Controller 校验当前问题后，只从 PendingQuestion.text 配对，不让调用方提供或覆盖原题。
5. Run 和内部 Scientific/Module 请求保存、传递 RecordedAnswer，继续使用原有作用域、顺序、幂等和 required answers 上下文；不新增问答状态机、不把整个 Session 放回提示。
6. 公共 schema 升到 7.0。旧 6.0 及更早 Run 原样保留、不迁移、不恢复；Session 顶层结构不变不等于承诺旧 Run 可以续跑。

报告采用 Markdown 而非缩进 JSON，是因为长 answer 即使放进缩进 JSON，字符串内部仍可能占一条超长物理行，无法靠既有行范围读取取回后部。可读报告将长物理行按 1000 字符分行，不删除解释内容；展示换行后的字节不承诺与原 payload 相同，精确原文仍保留在 payload，工件 hash 对应实际报告字节。不新增分页协议、不改变 reader 或扩大预算。

## 不选的方案与边界

- 不把 CodeUnderstandResult.answer 改成一条短 summary，不静默丢 uncertainty 或风险。
- 不让 Orchestrator 解析各领域 payload，不新增报告 Agent、风险管理器或第二套注册协议。
- 不让模型/用户回填原问题，不通过“最近一次 ask_user”去猜所有历史答案的对应关系。
- 不顺带删 ScientificFinish.summary、QuestionDraft.reason 或 hypothesis，不合并其它自然语言字段。
- 工件冻结保证可追溯，不保证模块解释科学正确；问答配对保证上下文到达，不保证模型一定遵循。

## 验证

验收检查分开判定：确定性投影/来源校验、真实 prompt 可见、模型如何消费、最终结果是否对应。语义交接的标准库探针、含糊短答案和核心回归已执行，结果及失败边界见 [分阶段验收记录](../reviews/SEMANTIC_HANDOFFS_ACCEPTANCE.md#verified-closeout)。后续上下文阶段只加强相关报告提示、调整共享呈现与额度，不改变本 ADR 的报告交付和原题来源规则；其风险消费及文献补验见 [最终记录](../reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout)，不沿用后轮通过数宣称旧提交全绿。
