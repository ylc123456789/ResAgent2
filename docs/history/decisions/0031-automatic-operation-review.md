# ADR-0031：无状态准确操作自动审核

- 日期：2026-10-16
- 状态：采用；第二阶段，基于 feat/unified-run-shell 的阶段一提交 7c6c5c0
- 公共 schema：29.0

## 问题

统一 run_shell 后，每段脚本都交人工会增加交互负担。需要让模型判断常规操作是否在已授予范围内，同时保留硬权限、准确操作批准、拒绝说明、共享预算及中断恢复。沙箱是独立后续阶段，本决策不实现它。

## 决定

1. RunPermissions.approval_mode 只选 manual（默认）或 auto_review，子调用沿 Run 继承；删除无效 confirm_commands 字段和 CLI 旗标。
2. 确定性 OperationPermissionPolicy 保持无模型调用。Runtime 对真正准备派发的 ask 调用一次注入 OperationReviewer；preflight 和人工恢复不调用。硬拒绝不可被模型推翻。
3. Components 用 PromptLLMClient 实现无状态 LLMPermissionReviewer，只有 review_operation 输出工具和 allow/ask+reason。没有 Session、读写、联网或执行工具。每次至多一次模型尝试，沿用 Run 总用量、期限、取消和 trace。
4. 固定代码提供准确 ActionSnapshot、当前任务与有序上级要求、根用户原始目标/假设/限制/交付要求与后续业务记录。历史人工批准/拒绝单列，保留来源和原文；旧 yes 不能复用，新的工具调用也不抹掉旧 no。
5. started 与决定事件先持久保存在原 Session，再标记派发。回执投影同一事件，不造新批准库、用户回答或工件。自动 allow 后复验硬授权及原启动配置，执行时仍使用原 prepared 快照。
6. 拿不准、无效返回、容量不足或模型故障回原人工入口；取消、到期、预算耗尽与权威保存失败保持停止语义。未知已执行结果不重放；未派发才标未执行。

## 取舍

审核模型提供语义判断，代码负责授权、准确身份、持久化和执行。它可能误判，不提供 OS 隔离，也不能证明研究质量提升。审核看不到引用脚本的正文时应转人工，实际自动化率需要真实模型短场景测量，不把离线用例通过当作该比例。默认 manual 保留原审批行为。

Run 与 Session 继续各自原子保存，不承诺跨存储/外部副作用事务。旧 schema（包括 28.0）不迁移、旧证据不改写。实现和本地验收记录见 [第二阶段记录](../reviews/AUTO_REVIEW_STAGE2_2026-10-16.md)。
