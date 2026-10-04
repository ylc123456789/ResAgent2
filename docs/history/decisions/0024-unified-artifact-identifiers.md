# ADR-0024：统一工件 ID 的外观与稳定编码

状态：accepted。日期：2026-10-04。

## 问题

Task 产物原来用 Task/Attempt/序号拼接 ID，导入、Scientific、系统材料和最终报告各用不同外观。最长合法 TaskId 会使拼接的 ArtifactId 超长；只对超长输入截断并补 hash 又引入长短两种命名形式。

用户确认采用统一 artifact_ 前缀加完整 SHA256。文件名、内部工件编号与可选 output_name 各有职责，来源关系已经有结构字段，不应再依赖可读长编号解析。

## 决定

ArtifactRegistry 的五个登记入口共用一个私有函数：对规范 JSON [登记类别, 稳定身份] 计算完整 SHA256，生成 `artifact_<64位小写十六进制hash>`。对象键排序固定，登记类别参与计算以隔开不同身份域。ID 总长 73 字符，无超长 fallback、来源专用前缀或最终报告特例。

统一外观不改变各入口原有身份语义：

| 入口 | 稳定身份 |
|---|---|
| Task | TaskId、Attempt、产物序号；同槽位内容变化仍拒绝 |
| 导入 | 文件内容 hash、kind、来源 metadata；调用者文件名不参与 |
| Scientific | Session、内容 hash、候选描述 |
| 系统 | kind、Run/Session/Task/Attempt 作用域、内容 hash |
| 最终报告 | 同 Run 的固定最终报告槽位；内容变化仍拒绝 |

实际磁盘绝对路径不参与 ID 计算。冻结路径本身包含 ID，因此不能反过来作为 ID 的计算输入；原文件路径也不能区分历史快照，并会让改名改变导入身份。Ref.sha256 继续校验文件字节，与 ID 的身份编码分别承担原职责。

Task/Attempt、kind、summary、来源链、文件名、URI 和 output_name 继续从已有字段取得。Run.artifacts 是唯一登记表，科研目录、工作反馈、材料读取和引用都沿用同一个实际登记 ID。不新增编号服务、计数账本、数据库、映射注册器或跨 Run 去重机制。

## 恢复与版本

编号算法改变会影响冻结目录及中断后重复登记，公共 schema 升为 23.0；沿用已有版本拒绝规则，旧 schema 22 及更早的 Run 不支持恢复。原 state/session/trace 和冻结文件保留，不重写、迁移或自动清理；旧 Run 需要以原冻结版本完成，或者在新版本创建新 Run。

Runtime AgentState 的独立解析能力不等于旧 Run 的业务恢复支持，本次不改变通用 Session 存储协议、不添加兼容编号算法。

本 ADR 取代既有 Task 拼接、分来源 ID 外观及 0997b51 的超长 fallback；保留各 ADR 的工件归属、冻结、来源、访问日志及交接职责。

## 验证

WSL Ubuntu-D 全量：1686 passed / 1 skipped；mock run_golden completed，13 工件。新增 11 个回归实例，覆盖五入口统一格式和跨 Registry 重建、类别身份隔离、metadata 键顺序无关、同任务槽位内容变化拒绝，以及旧 schema22拒绝且不改写原文件。既有改名导入、失败保留旧证据和最长任务身份回归继续通过。

公开原生生命周期的完整登记表同时校验统一格式、key/ref一致和所有 Attempt/科研索引引用可定位；不预猜生成 ID。工具 schema逐个检查只发生 const/default 22.0→23.0，更新四个对应指纹，无 prompt 或工具参数功能变更。

未执行真实模型、GPU 或服务器 L3；测试交接见 [schema23 L3](../reviews/L3_SCHEMA23_HANDOFF_2026-10-04.md)。
