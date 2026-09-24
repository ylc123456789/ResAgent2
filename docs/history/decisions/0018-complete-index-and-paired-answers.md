# ADR-0018：完整科研目录与成对问答阅读

状态：accepted。日期：2026-09-24。

## 要解决的问题

Scientific 每轮只默认收到目录入口和变化，需要另行读取完整目录才能发现较早材料。用户答案已经保存为包含原题的 answer 工件，但子任务答案被 Scientific 读取范围和科研目录排除，导致已确认的事实无法追溯。目录、引用和读取范围不能各自采用不一致的过滤规则。

## 决定

每轮 WorkRequest 的结果交接提供两部分：本轮带引用简报、更新后的完整科研目录正文。简报仍由 Interpreter 的 LLM 说明本轮工作、结果、失败和局限，不改为整个 Run 的累计总结。目录仍由固定代码生成，沿用原 artifact ID，只做导航；文件正文按需通过现有 read_artifact 读取。

Run.artifacts 保持唯一登记表，负责原件身份、位置、来源和 hash。索引与读取使用一致的登记来源；索引里的每个条目都必须有登记原件且可由 Scientific 读取，简报引用必须能在索引中找到。文件缺失、归属不符或内容校验失败须明确报错。登记表不作为另一份材料清单展示给模型。

成对 RecordedAnswer 直接收入目录：子任务问答按原 Task/Attempt 归入所属工作，Scientific 问答归入 scientific 组。本轮子任务问答同时可供 Interpreter 阅读和引用。答案仍是 Controller 配对保存的用户信息，不伪装为子 Agent 自报产物，不复制问题或建立新的问答库。

WorkFeedback 保留完整目录的引用和本轮 brief，删除 index_changes。Scientific 上下文在 research_materials 展示完整目录，避免同时展开第二份目录。历史目录仍保留，当前指针和反馈保存、消费规则沿用原实现。完整目录是必需上下文，容量不足明确失败，不静默退回增量或删历史条目。

## 取代范围与不变的边界

取代 ADR-0017 的“默认仅给目录入口与变化”选择，并修正当时实现中排除成对问答的材料范围。保留其唯一登记表、普通注入的 Interpreter、每轮带引用简报、共享预算和机器事实检查。

schema 升到 16.0；删除旧字段和差异计算，不提供兼容路径、不迁移旧 Run。Scientific 的其他上下文、工具和调用模式不变。ask_user 路由、Task/Attempt/Session 恢复、操作批准和单次消费不变：阅读历史答案不代表消费答案，也不能用一条批准执行其他动作。validation 的分层与失败处理仍单独讨论。

## 代价与验证

每轮完整目录比增量占用更多上下文，但不重复注入所有原件正文，也不增加 Interpreter 调用。验证两轮交接后的历史可见性、子任务问答原件读取、目录/登记/引用一致性，以及公开 CLI 回答后的原任务恢复。执行和证据要求见[服务器验收计划](../reviews/COMPLETE_INDEX_QA_TEST_2026-09-24.md)。当前规则见 [CONTRACTS](../../current/CONTRACTS.md#interpreter) 和 [CONTEXT](../../current/CONTEXT.md#scientific)。
