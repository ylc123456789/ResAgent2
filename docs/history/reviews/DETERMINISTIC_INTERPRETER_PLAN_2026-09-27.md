# Interpreter 固定代码化修改方案

状态：2026-09-28 已按本方案实现，待服务器验收。方案日期：2026-09-27；2026-09-28 补充身份对齐与实施记录。下文保留设计时的方案表述，现行规范见 docs/current。
方案核对基线：`fix/code-health@81a85d3`，当时公共 schema 18.0；本次实现升级为 19.0。服务器关闭，测试仍待执行。

## 1. 目标与范围

保留 Interpreter 在 Orchestrator 中的反向交接职责，把它改为完全由固定代码实现的模块：

1. 从已有登记材料与执行事实生成、更新科研索引。
2. 按本轮 WorkRequest 组织各任务已经保存的报告、状态和原件入口。
3. 把这部分结果交回 Controller，由现有 Scientific 上下文链路呈现。

取消每轮必经的 LLM 二次简报。Scientific 综合各任务结果并作科学判断；Coding、Experiment 继续通过统一 `report + artifacts` 交付专业解释和证据。

本次不扩展“读过”机制：沿用现有实际正文读取与引用检查，不增加“输入展示即自动观察”、引用传递或全文覆盖追踪。不新增科学语义 validation、产物关系图、任务类型分支或上下文预算系统。

## 2. 设计依据与职责

依据：[设计原则](../../current/DESIGN_PRINCIPLES.md)、[架构](../../current/ARCHITECTURE.md)、[契约](../../current/CONTRACTS.md)、[上下文](../../current/CONTEXT.md)。

现有原则要求反向交接保留成果、未完成项、失败、警告、局限及证据入口，也明确“对称性不要求……各调用一次 LLM”。本方案保留双向交接和模块边界，调整 ADR-0017 中每轮用 LLM 解释材料的实现选择。

| 模块 | 修改后的职责 |
| --- | --- |
| Compiler | 用 LLM 将研究需求转为任务，沿用现有结构校验 |
| Scheduler | 执行任务、接收专业 Agent 结果、保存真实状态和登记产物 |
| Controller | 收集并冻结本轮工作记录，调用 Interpreter，保存与交付反馈，管理 Run 和恢复 |
| Interpreter | 固定代码生成科研索引，组织本轮已记录报告，不推断科学意义 |
| Scientific context / Components | 校验并呈现交接材料，接入现有上下文分配，不再做一次语义总结 |
| Scientific LLM | 综合报告，决定需要补读或补做什么，形成科学判断 |

Interpreter 不修改 Run、不维护 Session、不调度任务、不读取子 Agent 私有 memory，不导入具体 Agent。Scientific 和 Components 不反向导入 Orchestrator；交接继续走公共契约和冻结工件。

## 3. 最小数据调整

复用现有事实来源：

- `Run.artifacts`：唯一产物登记来源；索引是可重建导航。
- `WorkRecord.previous_work_request`：原工作目标、期望证据和约束。
- `WorkRecord.work_outcome.tasks`：本轮任务最终状态、错误、任务级累计警告和产物入口。
- `WorkRecord.attempts`：每个 Attempt 当前保存的报告、状态和产物。
- `WorkRecord.unresolved_task_outcomes`：仍未解决的任务事实，包括此前轮次。

其中 `WorkAttemptRecord.summary` 来自已保存的 `attempt.report`。同一 Attempt 问答恢复时会更新报告，不能把它描述为每次 invoke 的完整历史；接收失败时系统也可能附加诊断文字。本方案原样使用已记录报告，不恢复不存在的历史。

建议契约只作下面的替换：

```text
WorkFeedback:
    run_id
    work_request_id
    session_id
    work_record_artifact_id
    index_artifact_id
    report: str                 # 替代 brief: WorkBrief
```

`report` 是 Interpreter 固定生成的展示正文，能从 WorkRecord 重建，不作为新的状态或测量权威。已有身份及来源引用继续保留。

删除仅为模型二次解释服务的 `WorkBrief`、`CitedStatement` 及其导出。不新增逐任务报告模型、科学判定字段或独立报告存储。不修改 AgentRequest / AgentResult 的统一业务字段。

## 4. Interpreter 的固定行为

### 4.1 科研索引

保留 `build_research_index` 的现有行为：按初始材料、Scientific 材料和原 WorkRequest 组织完整累计目录；保留原 Artifact ID、摘要、输出名、尝试和执行状态。

保留来源、尝试绑定及成对问答的结构检查。索引不复制文件路径、hash 或权限，不扫描未登记文件，不把反馈、目录自身递归收入目录。持久化和当前索引指针仍由 Controller 管理。

### 4.2 工作报告

将当前仅供测试占位的 `DeterministicWorkInterpreter` 实现为生产使用的固定组装器；保留现有小型 `WorkInterpreter` 注入边界，简化为接收已配对的 `WorkRecord` 并返回正文字符串。CLI、真实 E2E 和测试使用同一生产实现；删除 LLM 实现，不增加策略选择开关。

组装规则：

1. 按 `work_outcome.tasks` 的既有稳定顺序组织任务。
2. 通过明确的 task_id 绑定该任务的 Attempt，按 attempt_number 找到最新尝试。使用其已保存报告原文，不根据报告关键词猜状态。
3. 有尝试但报告为空时，明确显示“没有已记录报告”；没有尝试的依赖阻塞任务显示“未执行，无 Agent 报告”。
4. 不直接把 `WorkTaskOutcome.summary` 当作原报告：当前无报告时该字段会回退到任务 instruction，不能冒充执行结果。
5. 最新报告只呈现一次。旧尝试保留序号、实际状态和读取 WorkRecord 的入口，旧报告正文保留在原记录中，不全部再次复制成当前结果。
6. 当前任务状态、错误和警告来自结构字段。警告是任务级累计信息，不归到某次尝试，不因后续成功就自动宣布解除。
7. 附上已有产物 ID；完整名称、种类和摘要由已有科研索引提供，不再复制一份目录。

固定标题、来源标识、排序和明确的缺失说明可以由代码生成。任务报告中的科学解释和局限逐字保留；代码不抽取“有效结论”，不合并不同测量，不判断任务语义是否完成或假设是否成立。

例如，任务最终为 completed、历史尝试曾 failed 时，两项事实分别呈现；不自动生成“已解决所有问题”。如果 Agent 报告与结构状态不一致，两者保留，由 Scientific 理解，不能用文字覆盖状态。

解释结果之间的关系仍由 Scientific 完成。Interpreter 不再遍历实验文件或命令记录正文来重新生成解释；实际需要时由 Scientific 经现有工具读取。

### 4.3 专业报告的使用约定

在 Coding / Experiment 已有 report 提示中简洁说明：交接应让未参与执行的 Scientific 理解实际成果、必要条件、剩余问题和相关产物。复用现有自然语言字段，不增加统一科研模板或报告语义校验。

Scientific 的提示改为：当前工作反馈是子 Agent 已记录报告及执行事实的固定组织，可用于理解进展、选择下一步；需要报告未覆盖的细节、处理冲突或核查具体证据时，再读相关原件。删除仅针对 LLM 简报的用法说明及笼统要求反复回读的表述。

任何结构化证据引用仍须满足第 7 节原有观察规则；“可用于理解进展”不表示取得所提及原件的已读资格。

### 4.4 与正向需求、科研索引统一身份和命名

同一个研究目标、任务、尝试或产物，在正向 WorkRequest、科研索引和工作反馈中必须使用同一组已有身份及名称来源。Interpreter 负责组织信息，不再创造一套简称、编号或改写后的目标名称。

| 对象 | 身份与名称来源 | 汇总中的规则 |
| --- | --- | --- |
| 本轮工作 | 原 `work_request_id` 和 `WorkRequestDraft.objective` | 目标原文与科研索引该组的 title 相同；期望证据和约束直接取原请求，不重写为另一个任务目标 |
| 任务 | 既有 `task_id` | 报告、状态、产物及历史尝试始终绑定同一任务；不用另编的“任务 A / 任务 B”作为身份，也不从报告猜任务名称或执行角色 |
| 尝试 | `task_id + attempt_number` | 当前与历史明确标出真实尝试序号；每个任务的“第 1 次”分别属于自己的任务，不以全局位置替代 |
| 产物 | 已登记 `artifact_id`；有 `output_name` 时沿用精确原值 | 汇总与索引使用相同 Artifact ID；若展示名称或摘要，直接取登记字段，不另起“最终结果”等别名 |
| 历史记录 | 原 WorkRequest、Task、Attempt 和 Artifact 的既有绑定 | 不把旧轮次产物归到当前目标，不用当前位置或同名判断它属于哪个任务 |

`output_name` 是逻辑交付名称，不是全局唯一身份：不同任务、尝试可能交付同名产物，必须用 Artifact ID 和原归属区分。两个 WorkRequest 即使 objective 文字相同，也不能合并为一轮工作。状态随尝试和时间变化时保留各自记录，不为追求文字一致把旧失败改成当前成功。

报告中的产物入口通过 Artifact ID 与完整科研索引直接对上；不要求在结果部分重复整份目录或给索引新增一套任务层级。对于同时出现在当前结果和未解决项中的任务，用相同身份关联，不生成第二个主体。

这条规则约束系统生成的标题、分组、来源与引用，也在现有 Agent 报告提示中明确：引用已提供材料时沿用已知名称/ID；新产物沿用实际提交的 output_name，登记前不编造尚未分配的 Artifact ID。报告正文仍原样交付，不用字符串替换、关键词匹配或额外模型调用来“统一术语”。固定代码只检查明确的身份与来源匹配；自然语言含义是否一致仍由 Scientific 理解。

Compiler 生成的 task.instruction 是对子需求的具体化，与父 WorkRequest.objective 文字不同是正常的；对齐指同一主体沿用同一身份与名称，不要求不同层级的目标使用同一段文字。

无需增加别名字典、名称注册器或新的语义校验。原“读过”规则保持第 7 节的范围，不因这些身份对齐而自动标记任何原件。

## 5. 上下文呈现与长报告

完整科研索引继续作为 required 内容交给 Scientific，不改成增量、top-K 或另一套语义检索。

当前 work_feedback 的 brief 是 required 固定全文。替换为多个原报告后，不应继续把全部正文无条件放进 required 固定段。复用现有 `ContextMaterial` 和 `ContextComposer`：

- 保留最小必需框：原工作目标与约束、本轮任务状态、错误、任务级警告、跨轮未解决项、原件及产物入口。字段从同源冻结 WorkRecord 直接取得，不解析报告字符串。
- 反馈 `report` 正文作为可伸缩材料，按 Scientific 当前请求的实际剩余额度展示。
- 放不下时明确显示正文被省略或截断，并保留全部任务的状态导航和读取入口。不静默丢失内容、不生成新的 LLM 摘要。
- 展示端只做预算下的文本窗口，不解析 Interpreter 生成的 Markdown 来重建任务。
- 最小必需框本身放不下时，沿用现有显式超预算失败；不自动扩容。
- 冻结反馈和原 WorkRecord 保存完整内容。可沿既有按行读取机制展开；生成的长文本沿用现有有界行呈现能力，避免单个超长物理行不可续读。

Components 只负责已定义字段的校验、导航框及预算呈现，报告的任务组织和正文生成仍归 Interpreter。不引入新的共享管理器或缓存。

本轮结果只在原工作交接时呈现。回答恢复和重启沿用当前交付机制，不在每步重放所有历史报告；旧记录仍通过索引和授权原件可达。

## 6. Controller、装配与恢复

沿用现有顺序：

```text
任务稳定
→ Controller 冻结 WorkRecord
→ Interpreter 生成/更新索引并组织 report
→ Controller 冻结 WorkFeedback、保存 feedback_refs
→ 原 Scientific invoke / Session 接收本轮交接
```

索引和报告的实际登记仍由 Controller 完成。已保存反馈在重启时复用；目录按现有规则刷新。只有 Scientific 返回被接受后才消费 WorkRequest，不能因为固定代码生成了 report 就提前消费。

删除 Interpreter 专用的模型调用、两次草稿、模型引用纠错、trace 接线及模型额度消耗。保留 Run 的时间边界、其他模块预算和已有用量账本；不得重置预算或给历史请求退款。

固定组装所需的记录、身份或引用不一致时，明确失败，不回退到 LLM，也不返回空的“成功简报”。保留 Run/Session/WorkRequest 配对、引用授权和冻结内容完整性检查；机器侧未解决任务仍从 WorkRecord 取得，不从 report 提取。

组合根删除 `_interpreter_client`、Interpreter 的 `PromptLLMClient` 和 `RESAGENT2_INTERPRETER_CONTEXT_TOKENS` 配置消费。统一装配固定实现；Compiler 的模型与上下文设置保持原职责。

## 7. “读过”与 validation 的范围

本轮保持当前简单实现，不建立新观察协议：

- Scientific 的 observed 仍来自现有成功的正文读取工具记录，以及文献检索返回的已观察检索结果工件。
- 目录、反馈正文及报告中的原件 ID，不自动使这些原件成为 observed；不把 Interpreter 在后台处理记录算成 Scientific 阅读。
- 要在 assessment / opinion 中引用某个原件，仍须按现有规则读取它的确切正文。读过一个原文窗口不等于全文已读，不新增全文覆盖或行级状态管理。
- 文献检索结果被观察，不等于论文全文被阅读。
- 读取工件仍检查授权、Run 归属和冻结 hash；引用、required evidence kinds、required artifacts 及完成检查保持原边界。

本方案不让固定代码判定科学支持程度，不改变阶段三前置 validation 暂缓的决定，也不因为任务报告写了“成功”而覆盖实际状态。

## 8. 修改范围与版本

| 位置 | 拟修改内容 |
| --- | --- |
| `packages/orchestrator/.../interpreter.py` | 保留科研索引；生产化固定 report 组装；删除 LLM 类、源文件窗口预读、专用执行窗口和模型纠错 |
| `packages/orchestrator/.../controller.py` | 保留记录冻结和反馈检查点；调用固定 Interpreter；删除其模型预算包装和 brief 专用校验 |
| `packages/contracts/.../models.py` 与导出 | WorkFeedback 改为 report；删除 WorkBrief / CitedStatement；统一更新生产者和消费者 |
| `packages/components/.../materials.py` | 读取新反馈字段；同源原记录的最小事实框；report 接入既有弹性材料 |
| `packages/agents/scientific/.../context.py` | 更新交接用法说明；保留证据阅读与引用约束 |
| Coding / Experiment 现有报告提示 | 小幅明确交接用途，不增加字段或新的完成判据 |
| `apps/cli`、`e2e/real_e2e.py` | 固定实现装配，删除 Interpreter 模型客户端及专用上下文配置 |
| 相关测试与 fixture | 移除模型简报假设，改验确定性内容、恢复和实际上下文边界 |

`Scientific` 对 `WorkRecord` 的机器事实读取、完成检查和 observed 更新无需改造成新机制。

公共字段发生不兼容变化：若按当前 schema 18.0 基线实施，升级到 **19.0**，同步工具 schema 指纹及版本断言。遵守现有旧 schema 不兼容恢复的规则：保留旧 Run、Session、trace 和证据，不迁移、不删除、不留新旧两条生产路径。

实施时追加 ADR，明确取代 ADR-0017 的“LLM 简报”选择，保留 ADR-0018 的完整索引与成对问答规则。同步四份 current 文档、Orchestrator README、CLI 配置说明及受影响指南；历史记录不改写成新行为。本方案阶段不提前修改 current 的实现描述。

## 9. 实施顺序与验收

1. 修改契约和固定 Interpreter，同步 Controller 与两个组合根，清理废弃模型路径。
2. 接通反馈呈现及长报告的既有预算机制，调整必要的报告/阅读提示。
3. 同步测试、版本、工具面指纹和当前文档，在服务器完成检查。

服务器关闭期间不执行项目测试。服务器恢复后，验证重点为：

- 单任务、多任务、失败后重试成功、未执行阻塞、同 Attempt 问答恢复：报告与来源对应，旧失败不冒充当前结果，任务级警告及跨轮未解决项不丢失。
- 对齐检查覆盖相同目标文字的不同 WorkRequest、不同任务/尝试的同名 output_name：目标标题与索引一致，报告/产物/历史均按原 ID 绑定；不同主体不合并，同一主体不另命名。不存在或归属不一致的引用继续被结构检查拒绝。
- 生产固定 Interpreter 零 LLM 调用；保存反馈后中断恢复不重新消费工作或预算；完整索引与问答入口保持。
- 小上下文中保留必需框，正文省略可见且能按原件入口补读；最小框装不下明确失败。
- 仅被目录或报告提及的原件仍不能直接引用；实际读取后的既有引用链通过。
- 运行受影响的契约、Interpreter、Controller、Scientific、CLI 配置与边界测试，再做全量回归和现有 mock 整链。

模型可见内容发生变化，之后用固定材料做小型真实 Scientific 验收，观察能否利用报告继续工作、是否漏掉限制、是否正确按需补读，以及实际输入与调用成本。无需为这一步重新训练。完整 L3 可在定向验收通过后评估研究质量，不能把固定测试通过当作问题五已全部解决。

## 10. 完成标准

- Interpreter 保留为 Orchestrator 的反向交接模块，唯一生产实现为固定代码。
- 索引完整可追溯；正向需求、索引和反馈共用已有身份与名称；报告按来源和当前结果组织，已记录正文不被二次语义改写。
- Controller、Registry、Session 和共享上下文机制各保留原职责。
- 无额外 LLM 总结、任务数量分支、新报告框架、自动观察或科学语义校验。
- 新行为通过对应检查与真实消费验收后，才在当前文档中记为已实现。


## 2026-09-28 实施记录

- 固定 Interpreter 接收已验证的 WorkRecord，组织最新原报告及真实状态/历史入口；生产 CLI、real E2E 与测试统一装配，删除 LLM 实现和专用配置。
- WorkFeedback.report 替换 brief，schema 19；不保留旧模型或旧 Run 恢复兼容路径。
- 工作反馈采用必需事实框与弹性正文，沿用 ContextMaterial/Composer；索引保持完整，观察与科学语义 validation 范围未扩展。
- 实施时发现 JSON 会把报告换行转义为一个长物理行。为保持原文和原行号，采用通用 read_artifact 字符窗口参数 start_char/end_char，而非重写文件或制造虚拟行：先选物理行，再选零基、末端不含的字符范围，原完整 hash 校验不变。
- 已补充回归用例并同步现行文档。静态核对覆盖 Python AST、差异格式、旧符号及工具 schema：四个控制工具仅版本常量变化，read_artifact 增加两个字符范围参数及说明，其余工具指纹一致。
- 未执行 pytest、mock、真实模型或服务器测试；恢复、整链和小上下文容量表现仍须服务器确认。历史测试结果不代表 schema 19 验收。
