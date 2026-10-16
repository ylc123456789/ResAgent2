# 中断交接、共享预算与工具说明修正（2026-10-15）

在 `fix/native-agent-receipts` 上继续开发，基线 `e94eaea9ff440e7b701708f59e8322c4b317e694`。本轮处理实际信息缺口，不改变 Compiler 纯路由、四部分上下文、公共 Agent IO、权限或预算分配原则。

## 修改

1. Coding/Experiment 从自己的完整原生工具回执生成同一种 `execution_record`，覆盖正常、失败、中断、业务问答及让出暂停。验证批次被取消时保留前序真实结果。没有执行的命令不造记录；停止未确认仍如实报告。
2. 执行快照通过原有 `AgentResult.artifacts → Scheduler/Registry → Task 报告 → 完整目录` 交接。中断通知附公开工件 ID；不读取子 Session、不调用 LLM 改述事实、不增加总体报告或索引层。相同来源与冻结内容的同 Attempt 快照经完整性复验后复用；新增实际执行保留新快照。文件保留不代表半成品已验证。
3. 每次模型发送前在第三部分显示同一 Run 总账的总调用上限、已用、剩余和剩余执行时间。明确包含 Scientific、Compiler、所有子 Agent、后续委派和最终交付，不是当前 Agent 的专属额度。数字对应本次占用前；重试重新刷新，失败和已发送取消调用不退款。独立调用明确标注 invocation 范围。
4. 移除第二部分静态 budget 投影，内部收紧限制继续生效。动态预算不持久化为另一份历史或账本。重试与摘要请求重新计量完整输入，trace 的各 attempt 保留对应请求；准备超时未发送的输入不能覆盖最后一次已发送请求的诊断。
5. 五个控制工具的单独调用规则、工具说明和错误反馈共用 `CONTROL_TOOLS`。错误点名实际工具、说明整批未执行及如何重试。Scientific 获得已声明的逻辑工作区列表；`request_work` 说明与单个默认、多个必选规则一致。Scientific 的 verdict 合法值由原枚举生成，并与执行 status 区分。路由互斥与 resume_work 已清楚，未另加提示。

## 兼容与边界

公共 schema 保持 27.0：没有新增持久字段、状态或业务协议。内部 ComposedContext 的预算字符串只是可重建投影。Session 指纹现在覆盖实际 system 内容与工具说明；旧固定契约的 Session 明确拒绝恢复，不能把“同 schema”视为“可续跑”。旧证据不迁移或重写。

没有增加预算、为某个 Agent 预留额度或添加自动收尾策略；不保证模型不再扩展工作或一定在预算内完成。没有恢复已经废弃的子任务、自动重放命令或回滚文件。第一、第二、第四部分仍完整保留，只有第三部分可压缩。

## 验证

本轮完成预算、原生调用、执行记录、登记恢复及父方公开交接的定向验证，并作独立审阅。最终在原 WSL 源码树 `/home/cyl/ResAgent2` 使用既有 ResAgent2 环境验证：

- `python -m pytest tests apps/cli/tests -q`：**2288 passed / 1 skipped，61.22 秒**。
- `python -m e2e.mock_e2e`：completed，8 件工件，Coding/Experiment 均一次 Attempt 完成。
- `git diff --check`：通过。
- 独立审阅定向验证 106 项通过；预算专项 7 项通过，含重试准备后超时与首请求未派发边界。审阅发现的 trace 错配已修并新增回归。
- `pip check`：rc=1，既有 `pdfminer-six 20260107` 缺 `cryptography`；未修改无关部署依赖，不把这项写成通过。

本地实施及原始日志目录为 `E:/dshWorkSpace/implementation/feedback-budget-20261015/`，保留 `implementation.patch`、`followup.patch`、首次全量失败和最终全量/mock 日志，以及 `acceptance/` 短场景脚本。真实服务器验收尚未运行，不以离线结果代替。

首次全量回归为 2278 passed / 10 failed / 1 skipped：9 项旧测试只构造 event、未构造实际原生回执，1 项 Coding 工件集合未包含新增的真实 execution_record。按原测试意图修正 fixture 和工件证据断言，没有添加旧 event 兼容路径或放宽产品检查。此前镜像定向中的两项严格 HTTP 时间测试在原 WSL 源码树全量中通过，未修改超时阈值。

服务器 `ssh -p 14225 root@connect.cqa1.seetacloud.com` 的只读预检返回 `Connection refused`，未修改服务器产品或旧证据。短验收脚本已在本地准备，要求新证据目录、冻结提交和真实 CLI 装配；场景覆盖完成任务保留、父子进程中断、执行记录读取及恢复前后预算连续性，不需要重训 L3。
