# 模型实际会看到什么：上下文说明

三个 Agent 使用同一套上下文。Scientific 负责研究判断，Coding 和 Experiment 负责专业执行；它们的差别主要是身份提示、工具和领域完成检查，不是另一套记忆架构。本文从模型输入讲起，再说明工具、工件、压缩和恢复。职责见 [ARCHITECTURE](ARCHITECTURE.md)，字段见 [CONTRACTS](CONTRACTS.md)，取舍见 [DESIGN_PRINCIPLES](DESIGN_PRINCIPLES.md)。当前 schema 为 **26.0**。

<a id="basics"></a>

## 1. 四部分各负责什么

| 部分 | 内容 | 是否允许从模型输入中压缩或省略 |
| --- | --- | --- |
| 固定契约 | Agent 身份、系统规则、实际启用工具的说明和 schema | 不允许；同一 Session 保持稳定 |
| 任务需求 | 初始指令、必要控制事实、本作用域累计的完整业务问答 | 不允许；业务回答追加保留 |
| 原生 assistant/tool 历史 | 工具调用、直接返回内容、错误反馈和必要历史摘要 | 只有这一部分可以压缩较早的完整回合 |
| 完整工件索引 | 当前 Agent 已授权的全部登记材料，以及 WorkRequest → Task → 实际产物导航 | 不允许选择或截断；不包含正文工作集 |

“完整”指当前 Agent 的授权范围，不是整个 Run 的全部内部记录。索引说明材料在哪里；工具读取提供材料内容；读取过不证明内容支持结论。

每轮重建第二、第四部分，使用最新已保存的事实。重建不会重新执行搜索、读取文件或命令。第三部分连续保存近期原生回合，过长时只改变较早回合的模型投影；系统的原始事件和回合仍保留。第一、第二、第四部分以及必要近期回合装不下时明确失败，不能偷偷删除索引条目或任务要求。

<a id="pipeline"></a>

## 2. 实际发送给模型的形状

```text
messages:
  system     固定原生协议规则 + Agent 身份和规则
  user       完整任务需求、初始控制事实和累计业务问答
  assistant  存在时的较早工具历史摘要（属于第三部分）
  assistant  原生模型内容、reasoning、tool_calls
  tool       对应 call_id 的唯一回执
  ...        其余近期完整回合
  user       完整工件索引
tools:       本 Session 实际启用的固定工具 schema
```

四部分是职责划分，不强制对应四条 API 消息。Agent 身份是真正的 system 内容；工具 schema 只通过原生 tools 参数提供，不再复制一段工具契约文本。工具注册表同时负责向模型展示、参数校验和派发。

Session 保存协议身份及固定契约指纹。指纹涵盖身份 prompt 和实际工具 schema；恢复时更换模型、endpoint、身份或工具集合会明确拒绝。API key 不进入指纹。供应商 reasoning 与对应原生回合一起保存，不作为科学证据。

源码：[消息投影](../../packages/runtime/src/resagent2_runtime/tool_calling.py)、[循环](../../packages/runtime/src/resagent2_runtime/loop.py)、[Composer](../../packages/runtime/src/resagent2_runtime/context.py)。

<a id="questions"></a>

## 3. 任务需求和问答

第二部分来自 `AgentRequest.instruction`、`task_context` 和 `user_answers`，保留必要的初始工作区、环境约束、权限、数据集及明确验收要求。控制数值由代码执行，模型不能通过自然语言扩权或重置预算。

业务 `ask_user` 先返回已接收的问题 ID，保存直接控制记录并暂停。用户回答后，完整原题、字段、选项、回答值和来源保存在 Run；调用方将同作用域的累计业务问答交给 Agent，下一次模型输入完整呈现。选项只是回答提示，用户可在原字段自行输入，包括补充说明；业务问题不使用独立的 comment。问答不生成 question/answer 工件，也不占目录。

Scientific 问答属于同一 Scientific Session；Coding/Experiment 问答属于同一 Task、Attempt 和 Session。暂停恢复继续原尝试；失败 retry 建立新 Attempt，不自动继承旧尝试问答，必要时重新提问。尚未确认接收的回答不能在恢复失败后提前消失。

操作批准也使用暂停、回答入口，但有不同用途。它绑定准确工具、参数、目录、环境和作用域，权限策略消费它；决定及可选 comment 不进入第二部分，也不生成工件。comment 按原文保存在答案中，仅随匹配动作的原工具回执以顶层 `user_comment` 交给模型，原业务 value 不变：拒绝同时说明 `execution_status=not_executed`，批准说明则随实际执行成功或失败返回。说明本身不授予权限或改变任务，模型结合它与已有需求决定后续行动。

原操作的 native call 保持待回执，恢复后重新检查权限与快照，执行或拒绝后为原调用保存唯一最终回执，不追加另一条说明回执。无需模型重新发一次命令。批准在副作用前持久消费；批准后派发期间期限中断，原回执仍保留说明，以 `execution_status=unknown` 表示可能存在部分执行，不能写成拒绝时的 not_executed，也不自动重放。快照变化后的动作重新询问，不套用旧动作的说明。CLI 的 `answer --comment` 和 shell 的 `/answer` 交互入口沿同一回答链路提交，完整使用方式见 [CLI README](../../apps/cli/README.md)。

<a id="index"></a>

## 4. 完整目录怎样组织

`artifact_index` 每轮由授权输入、Session 已接收工具产物 ID 和官方登记 Ref 重建。Run 登记表是工件事实的权威来源；Session 保存已接收的身份，不保存第二份可以独立漂移的元数据目录。缺失已接收 Ref 明确报错，不静默漏项。

```text
文献材料
  论文元信息、原 PDF、提取全文：各自 ID、说明和直接来源关系
网页材料
  已获取页面：ID、说明、类型
工作产物
  WorkRequest ID、原目标、终态
    Task ID、任务需求、状态、错误、各次 Attempt 事实
      原始报告工件 + 实际交付物条目
其他材料
  其余合法授权工件
```

这些标题是同一份目录的展示结构，不生成分类工件、索引工件或第二套组 ID。所有实际条目直接展开，不必先读取 index 文件才能取得材料 ID。

每条包含 ID、kind、media_type、说明、可选 output_name、真实生产者与 Task/Attempt/Session、直接来源关系。不复制正文、物理 URI、hash 或权限。完整目录不授权新材料。类型内保持首次接入顺序，未知合法类型仍列出。

每个工件身份只占一个条目。复用已有材料不复制或改挂到新的生产任务；新的工作通过已有 ID 表达引用关系。实际输出按真实生产 Task 放置，各次失败 Attempt 的报告仍保留。尚未执行的依赖阻塞任务只有真实导航状态，不伪造 Agent 报告；编译失败且没有 Task 时显示真实错误与“未创建任务”。

WorkRequest 没有总体报告；每个真实终态 Attempt 的报告和实际产物直接列在 Task 下。报告按原字符冻结，不再选摘、汇总或自动折行。预执行失败报告明确标为运行时来源、Agent 未启动，不能冒充专业 Agent 的工作。

源码：[共同目录投影](../../packages/components/src/resagent2_components/materials.py)、[无状态结果整理](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py)。

<a id="reads"></a>
<a id="literature"></a>

## 5. 工具结果如何进入上下文

| 调用 | 第三部分 | 第四部分 |
| --- | --- | --- |
| 文献/网页搜索 | 本次去重后的候选、来源、状态及完整返回信息 | 不变，不产生工件 |
| 文献/网页 fetch | 结果说明、实际工件 ID、失败或部分成功事实 | 并入真正已登记的材料 |
| read_file / read_artifact | 所选正文与实际范围、续读信息 | 不变，不产生新工件 |
| request_work | 立即返回 accepted 和 work_request_id 后暂停 | 工作结束后展开对应 Task 报告和实际产物 |
| 业务 ask_user | 已接收的问题 ID | 不变；回答加入第二部分 |
| 操作批准 | 原调用等待，执行/拒绝后保存唯一回执，包含可选用户说明 | 不变 |

搜索只提供临时线索。旧工具历史压缩后，模型可能看不到这些搜索内容；模型需要长期保留的材料应显式 fetch。工具说明明确这个边界。搜索成功、部分、空结果或失败都不登记搜索工件。

`fetch_literature(source)` 接收明确 `arxiv:`、`doi:`、`openalex:` 或授权 `artifact:` 论文来源，按精确身份查元信息、获取 PDF、提取页码标注全文；不按模糊标题猜论文。元信息成功但下载失败、PDF 成功但提取失败时，实际登记材料仍进入目录，回执说明缺口。网页 `web_fetch` 冻结 Markdown 正文并保留来源；同源同解析器同正文快照可在授权范围复用，正文变化保留新工件。本次获取时间不写入正文破坏快照复用。

去重在三处有不同含义：搜索内部去掉本次重复候选；获取边界按准确来源与内容快照判断能否复用；目录的 artifact_id 唯一是身份约束，不负责再猜资源是否相同。复用必须校验原冻结内容，不能跨 Run 或凭猜到的 ID 扩权。

读取工具共用“选行 → 字符窗口 → 单次返回上限”。行号从 1 开始，端点包含；字符从 0 开始，末端不包含，相对于所选行段。回执保留 `total_lines`、`selected_chars`、实际续读位置 `next_start_char` 和 `truncated`。连续读取保持行范围不变，使用 next_start_char；truncated=False 只说明请求窗口未被上限裁剪，不证明整份文件读完。严格 UTF-8/NUL 检查和原换行保留不变。

正文只出现在读取回执，不再投影为第四部分工作集。历史压缩可能移除精确正文；原件和完整目录仍在，需要时重新按范围读取。PDF/checkpoint 等二进制工件也完整列出，但 read_artifact 不自动解析它们；PDF 正文通过关联全文工件读取。

<a id="modules"></a>
<a id="scientific"></a>
<a id="coding"></a>
<a id="experiment"></a>
<a id="compiler"></a>
<a id="interpreter"></a>

## 6. 专业差异保持在职责内

Scientific 的工具用于获取材料、提问、委托和形成意见；它不直接编辑工作区或训练。Coding 用同一套工具完成分析、修改和验证；Experiment 分析已有结果或执行测量。它们没有显式工作模式。

动态环境、命令诊断、读取和完成拒绝由对应工具回执提供；代码仍保存验证新鲜度和环境认证事实，并据真实事实检查 finish。它们不成为另一个第四部分状态工作集。完成拒绝先得到判断，再保存本次 finish 的唯一 accepted/rejected 回执；不会先写成功再修改旧回执。

Compiler 是无状态结构化编译调用，不创建 Agent Session。Scientific 提示词要求工作需求自包含，区分实现能力与执行动作，明确条件、顺序及限制范围；Compiler 提示词要求沿关键原句细化，保留约束含义并匹配职责，不从自然语言编号机械切 Task。子 Agent 的提示词要求按实际入口和任务边界选择检查，依据回执和用户反馈调整行动，交接区分观察事实与未执行建议。这些指引不新增接口字段或强制工作阶段，也不保证模型必然遵循。Interpreter 不调用模型，只把真实任务状态与 Ref 组织成 WorkResult，不拼接总体报告。

<a id="budgets"></a>
<a id="compaction"></a>

## 7. 只压缩原生工具历史

压缩以完整 assistant/tool 回合为边界。不能留下孤立 tool 消息，也不能删除一个批次中的半组调用。较早回合形成摘要和 history_start 检查点，近期回合原样续传；原始 tool_turns、事件、reasoning 和冻结材料不删除。

摘要属于第三部分，不提升为用户需求、工件或系统规则；它是有损交接，不保证保留每个精确数字、源码或搜索线索。第二、第四部分从权威记录完整重建，不从摘要猜回。读取重要原件是取得精确内容的途径。

容量按有效模型输入上限、Agent 配置、tools、完整消息、摘要和历史共同计量。字符估算不是供应商 tokenizer。软水位为后续回执留空间；至多一次有界压缩仍超限时明确返回容量错误，保留现场。完整目录不能截断，第一、第二、第四部分没有正文配额、优先级筛选或压缩路径。

摘要请求与其他模型请求共享 Run 账本、调用上限和期限；发送前保存占用，失败/未知不退款。等待用户暂停不重置预算。

<a id="recovery"></a>

## 8. 恢复保留哪些保证

运行时先保存原生调用，再记录正在执行的 call ID，随后保存结果回执。批准操作的匹配说明与 call ID 一起保存在回合的 `executing_user_comment`，回执保存后清除；执行中崩溃后，原说明仍附回原调用的未知结果回执，不重放操作或重新授权。该字段是恢复元数据，不是新控制流程或长期任务需求。普通中断调用结果未知时不重放，批次尾部未开始项明确 not_executed；已完成项保留。合法的待批准操作优先按其原调用恢复，不被普通中断清理误取消。终态与原始 AgentResult 一起保存；结果交回前中断，重启仍返回原结果，不重新调用模型或工具。

控制交接按 source Session、原始 turn 与 native call ID 定位，防止不同 Session 重用供应商 call ID 时串线。先保存直接控制事实再交给 Orchestrator；重复接收只确认同一请求，不创建新问题或新 WorkRequest。Accepted 回执完成后不再回写为工作完成；工作完成通过第四部分导航呈现。

工件登记先分配统一 UUID 并保存恢复记录，再冻结和登记；可续接的原交付恢复使用相同 ID 和字节。Scientific 返回产物后，Session 保存实际授权 ID 和唯一回执，再确认清理 Run 中待接收登记；确认中断只补确认，不重写回执。只分配身份但未冻结的网络调用不能自动重发，保留中断回执并清理未完成分配；已冻结文件损坏则报错，不能当作未产生材料跳过。初始化导入先保存声明和进度，恢复先补完导入再调用模型。Compiler 返回的原任务图先落盘，再冻结任务要求，接收中断不重新编译。

RunStore 和 SessionStore 分别原子保存快照，不构成跨存储事务，也不保证网络请求或命令恰好执行一次。授权内冻结登记可恢复，不等于可以安全重放所有外部操作。schema 26.0 明确拒绝旧 Run/Session，包括 25.0，不迁移或兼容；旧证据原样保留。

<a id="verification"></a>

## 9. 如何核对模型实际看到的内容

先查原始请求与工具回执，再查 full trace 中真实 `messages + tools`，最后查模型动作和交付物。目录完整不证明模型读取，读取记录不证明理解，最终 completed 不代替逐项证据。

验收应覆盖：搜索无新工件；fetch 部分成功和复用；第三部分正文不重复进入第四部分；完整目录在压缩前后不丢项；业务回答累计、批准及说明隔离；原调用配对；编译零 Task 失败和任务失败报告；登记/接收中断后的同身份恢复。工具上下文基础检查见 [schema 25 测试计划](../history/reviews/TOOL_CONTEXT_IMPLEMENTATION_TEST_PLAN_2026-10-11.md)，本轮权限说明、CLI 与提示词检查见 [schema 26 测试计划](../history/reviews/PERMISSION_FEEDBACK_TEST_PLAN_2026-10-12.md)。
