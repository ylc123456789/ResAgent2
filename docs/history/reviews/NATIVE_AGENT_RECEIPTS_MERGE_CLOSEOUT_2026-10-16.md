# 原生工具、上下文与纠正链路：合并收尾

2026-10-16，用户授权检查 `fix/native-agent-receipts`，发现阻断问题先修复，通过后合并到本地 `main`。权限系统的整体设计留到后续独立分支。

## 范围与结论

- 主线基线：`fbf1a00b46892f5c226855824f0bdf2fd4765124`。
- 原开发快照：`87002b6ed9b8f8ca0934774f0ee05dffb1b3a18d`，14 个提交，237 个变更文件。
- 合并前计时修复：`e18ba25e037342e5c38a47bdbfbc1faace0cd46e`。本记录及导航、当前契约说明随后单独提交，不再改变产品代码。
- 公共 schema 为 **27.0**。旧 Run/Session（包括主线的 24.0）明确拒绝恢复，原记录保留，不做兼容迁移；固定契约指纹也可能拒绝同 schema 的旧 Session。

按实际调用链审查权限与问答、打断与恢复、上下文与工件、共享预算及 CLI 装配，结合已有服务器原件复核和本次确定性回归，修复下述问题后未发现尚未解决的合并阻断缺陷。采用范围是当前已有能力，不将模型偶发错误和权限架构后续设计一起扩入本分支。

## 本分支采用的行为

1. 三个 Agent 共用原生工具调用协议，每个已采纳调用保留唯一回执；真实 E2E 使用 CLI 的同一组合根。
2. 四部分上下文中只有第三部分历史可以压缩。第二部分完整保留有来源、有时间顺序的上级需求与业务问答；第四部分是完整授权工件目录。搜索仅返回临时线索，显式 fetch 才登记，读取不新增工件；UUID 表示身份，hash 检查内容和来源快照。
3. Scientific 明确给出结构化 Tasks，Compiler 只匹配 Agent；固定代码原文传递指令、共同约束和控制字段。工作产物按 WorkRequest → Task/Attempt → 原报告与实际工件组织，没有 WorkRequest 总报告。
4. 操作批准绑定准确操作，拒绝时可附原文说明，进入原调用回执；批准不进入任务需求或工件目录。CLI 支持业务问题自由回答。
5. 统一 ask_user 问直接上级，Scientific 统一与真实用户业务交互。子问题进入父方第三部分，父答复进入子方第二部分；父方不能代替用户扩权。普通输入先保存，当前模型调用与已采纳批次完成后在下一模型判断前交 Scientific。
6. 显式打断停止当前执行链，确认停止后暂停；活动旧委派未完成部分取消，完成成果、文件、预算与真实历史保留。恢复 Scientific 原 Session，按需重新委派；不自动回滚文件或复活旧子会话。
7. 正常、暂停和中断沿同一公开工件通道交接真实 execution_record。每次模型尝试显示、占用同一 Run 总预算，包括 Compiler、重试、摘要和收尾；不是各 Agent 的专属额度。

规范以 [CONTEXT](../../current/CONTEXT.md)、[CONTRACTS](../../current/CONTRACTS.md) 和 [DESIGN_PRINCIPLES](../../current/DESIGN_PRINCIPLES.md) 为准。详细实施分别见 [上下文方案](TOOL_CONTEXT_REDESIGN_AGREED_2026-10-11.md)、[层级纠正实施](CORRECTION_ROUTING_IMPLEMENTATION_2026-10-14.md) 和 [交接及总预算实施](FEEDBACK_AND_SHARED_BUDGET_IMPLEMENTATION_2026-10-15.md)。

## 合并前发现并修复的问题

Run 已手动暂停，再次 interrupt 会覆盖第一次暂停起点；恢复时前一段等待被误算为执行时间，可能错误 TIMEOUT。预算投影还没有动态排除当前手动暂停；问答后残留的手动暂停标记可能在后续恢复时重复结算。

修复沿已有 ResearchRun/Controller：当前等待取问题与手动暂停的最早起点；重复打断不重置；answer、resume 和取消相关待答关系共用一次结算。只排除明确 PAUSED 的用户等待，普通停机、真实执行和已发模型请求消耗继续保留。没有增加持久字段、修改 prompt 或放宽授权。

新增三项 fake-clock 回归覆盖重复打断并重建 Controller、根问答与手动暂停重叠、取消升级子问题后继续手动暂停。旧代码 **3 项均失败**，修复后包含在全量通过结果中；原有普通停机计时测试继续通过。

## 本地与服务器证据

| 检查 | 已实际确认的结果及范围 |
| --- | --- |
| 本次修复后本地全量 | **2291 passed / 1 skipped，55.33 秒**；跳过项为 opt-in 联网 smoke。九包均从隔离源码导入；mock completed / 8 工件；pip check clean；diff check 通过 |
| 原 schema 27 验收及严格 L3 复核 | `e94eaea`，全量 2237/1 skip；L3 completed 121/200。开发方追加严格 158 项断言、60 个必要指标比较、独立 calibration 重拟合 T 与固定 T 前向重放。保留初始弱核验无效、文件完整性校验读取 test 字节等勘误；不声称文件级从未读 test |
| 四类真实机制补测 | `e94eaea`，上下级问答、业务待答打断、原子会话普通输入澄清、受管长工具中断四场 completed，独立专项 101/101；保留模型错误、HTTP 重试及原失败证据 |
| feedback-budget 真实短场景 | `87002b6`，服务器 2288/1 skip、mock 8、pip/diff clean；真实 Run completed **31/60**，48 调用/48 回执。Scientific 读取中断 execution_record；partial 4/300、seed 保留、父子进程停止；正文 10/10。开发方实际 SSH 核对 122 public + 17 private 文件全部 hash，原始 trace/账本一致 |
| 原 E40 同条件复跑 | `87002b6`，原目标与完整请求不变，**31/40 completed**，2000 行独立逐条复算通过，45 调用/45 回执。含一次继续完整任务的业务确认、两次精确命令批准；一次 unknown 是被用户主动取消的在途模型请求 |

上述长场景并非本次又重跑。它们的原件已在此前直接服务器复核/测试中检查，本次重新读取本地保留的复核报告；本次只执行确定性合并检查，没有新增付费模型或 L3。

本次隔离夹具首轮因 Windows 挂载点临时文件语义失败，下一轮尚缺 PTY、跨文件系统源码导入过慢，得到 7 failed / 2281 passed / 1 skipped。补齐私有终端、RAM 源码及临时目录后，保持产品和断言不变运行；随后才完成上表中的红/绿复现与全量验证。失败日志保留，未据其添加产品补丁或放宽测试。

服务器证据根：

- 原 schema 27 验收的准确证据根见上述开发方复核报告；`runs/correction-routing-27-supplement-20261015/`（机制补测）。
- `runs/feedback-budget-27-20261015/`。
- `runs/e40-feedback-budget-20261016/`。

这些目录位于服务器 `/root/autodl-tmp/resagent2/` 下。开发方本地复核入口为 `E:/dshWorkSpace/reviews/schema27-acceptance-audit-20261015/SERVER_VERIFIED_REVIEW_2026-10-15.md`、`reviews/schema27-supplement-20261015/SUPPLEMENT_ACCEPTANCE_2026-10-15.md`，以及 `implementation/feedback-budget-20261015/SERVER_ACCEPTANCE_DEVELOPER_REVIEW_2026-10-16.md` 和 `e40-rerun-20261016/E40_REPORT_2026-10-16.md`。私有 Session、全文 trace、批准原文与工作产物不加入产品仓库。

## 保留的限制与下一分支

- 旧 E40 确实预算耗尽，不能算完成；本次 E40 完成只是一个含业务确认的样本，不证明稳定成功率或科研质量普遍改善。原 verifier 把跨 Task 同名输出误判为全 Run 冲突，首轮 29/30 FAIL 保留；按既有 Task/Attempt 作用域补核 10/10，不删除产物制造通过。
- Scientific 曾只在自然指令写工件 ID，却遗漏 `input_artifact_ids`，导致新子 Agent 正确拒绝读取。Compiler 没有丢失已提交字段；后续改善接口使用时仍应保持显式授权，不自动继承父全部材料。模型仍可能填错参数、多交报告或提出多余业务确认。
- 最新公开执行记录与预算改动没有重跑完整 L3；此前 L3 属于 `e94eaea`，不冒称最新提交科研质量验证。新增计时修复是确定性状态逻辑，已复现并全量覆盖，不要求为此重训。
- 取消管理受管前台进程及可追踪后代，不提供 cgroup/OS 沙箱或任意恶意 daemon 的绝对清理保证；文件保留不代表半成品有效。没有自动 Git 回退、任意嵌套委派或任意 Agent 消息总线。
- 下一分支梳理权限原则、策略与交互入口；保留已经有效的准确批准、原文拒绝反馈和业务问答隔离。先形成方案再改，不将整体权限重设计继续堆入本次合并。

## 采用与恢复边界

按用户授权合入本地 main，保留开发分支、分段提交和所有旧证据；本次不推送远端。若后续需要回退代码，须把 schema 27 新 Run 与旧 schema 24 状态分开管理，不能回退代码后强行恢复不兼容记录。合并不会重写或迁移任何已有 Run。

历史实施记录中“服务器未测”“pip 缺项”“分支未合并”保留为当时状态；本记录提供后续验收与采用结论。
