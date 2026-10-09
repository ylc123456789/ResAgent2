# 模型实际会看到什么：上下文说明

这份文档回答：**模型每轮收到哪些信息，信息从哪里来，哪些会更新，放不下时怎样处理。** 它描述当前实现。职责分工见 [ARCHITECTURE](ARCHITECTURE.md)，字段和接收规则见 [CONTRACTS](CONTRACTS.md)，修改时的约束见 [DESIGN_PRINCIPLES](DESIGN_PRINCIPLES.md)。

第一次阅读建议按前三节顺序了解共同结构、每轮构造和 Agent 差异；后半用于查读取、压缩、预算、恢复和诊断细节。

<a id="basics"></a>

## 1. 先理解共同的四部分

Scientific、Coding、Experiment 使用同一套上下文框架。区别主要在职责提示、可用工具，以及需要呈现的当前状态。

| 部分 | 模型看到什么 | 怎样变化 |
|---|---|---|
| 固定契约 | Agent 的职责与规则、原生工具协议、实际可调用工具的说明和参数 schema | 每次请求重新提供；不由搜索结果或工具回执改写 |
| 任务需求 | 当前指令，以及这个 Session / Task / Attempt 作用域内累计的 `ask_user` 问答 | 从本次请求重建；用户回答追加后，下一次调用可见 |
| 原生 assistant/tool 历史 | 模型发出的工具调用和一一对应的工具回执 | 持续保存；近期完整回合原样续传，较早完整回合可形成摘要 |
| 当前任务上下文与完整工件索引 | 当前要求、状态和反馈，已读取的材料片段，以及完整的 `artifact_index` | 每轮重新投影；索引完整保留，正文工作集按容量选择 |

**重构和压缩处理的是不同内容。** 每轮重构的是第四部分等当前业务输入，不是把整个 Session 清空重来；压缩的是第三部分中较早的完整工具回合，不是改写任务、裁掉索引或删除工件。

```text
请求、权威状态、Session 事件、已登记工件
                 │
                 └─ 每轮重建 → 任务需求 + 当前状态/材料 + 完整目录

Session 的成对工具回合 → 近期原样续传；较早回合必要时生成交接摘要

两条路径与固定契约一起组成下一次模型请求
```

几个容易混淆的边界：

- 工件目录是“有哪些已授权材料、用哪个 ID 读取”，不是把所有正文每轮塞给模型，也不证明模型读过。
- 工具回执记录“那次调用返回了什么”，当前状态说明“现在是什么”。旧文件读取和旧验证结果不能覆盖后来已检查的状态。
- 同一读取结果可能同时出现在近期工具回执和当前正文工作集中：前者维持原生协议连续性，后者提供当前有界材料；两份都会计入容量。
- `ResearchRequest.context` 只是用户提供的研究背景文本，不是这里讨论的整套模型上下文。

Compiler 使用无状态的 JSON 编译请求，Interpreter 不调用模型；它们不套用上述 Session 历史机制，见 [模块差异](#modules)。

<a id="pipeline"></a>

## 2. 一轮输入怎样组成

### 2.1 先保存事实，再构造模型输入

三个 Agent 的领域 builder 从当前请求、Session 和实际环境绑定生成本轮内容。Loop 补上运行反馈，Composer 统一计量并分配材料空间；这些投影本身不保存第二份业务状态。

| 信息来源 | 本轮用途 | 是否由构造上下文重新执行操作 |
|---|---|---|
| 本次请求 | 指令、授权输入、要求、当前作用域问答 | 否；需要读结构化工件时校验其授权、归属和冻结内容 |
| Session 事件与代码维护的 memory | 原生历史、拒绝反馈、读取片段、命令诊断、工具产物目录 | 否；从已有记录投影 |
| 实际环境绑定 / 验证状态 | 执行 Agent 当前可用环境、认证及验证新鲜度 | 不执行命令；读取工具也使用的同一状态 |
| 登记表与冻结工件 | 材料身份、目录、要求和交接内容 | 不自动读所有正文；按既有入口验证所需内容 |

一轮的实际顺序是：

1. 调用方交付当前指令、授权工件和恢复材料。
2. Loop 确定模块与模型的有效输入上限，为工具 schema 和续传历史预留空间。
3. builder 重建任务、目录、状态、要求和材料；Loop 再加入尚待处理的动作、拒绝反馈及已有历史摘要。
4. Composer 保留必需内容，选择和扩展可伸缩正文，并按完整请求计量。历史压力过大时，可先做一次有界压缩，再重建本轮输入。
5. 客户端请求模型；Loop 保存返回的工具调用，校验后执行，保存对应回执。
6. 下一次请求从更新后的事实重新构造，不累加上一轮的完整领域 prompt。

源码入口：[共享任务/材料/索引投影](../../packages/components/src/resagent2_components/materials.py)、[共享状态与工作集](../../packages/components/src/resagent2_components/context.py)、[AgentLoop](../../packages/runtime/src/resagent2_runtime/loop.py)、[ContextComposer](../../packages/runtime/src/resagent2_runtime/context.py)。

### 2.2 四部分不是四条 API 消息

当前 CLI / real E2E 的三个 Agent 使用原生工具协议。最终发送形状是：

```text
messages:
  system     固定的原生工具使用与状态优先级说明
  assistant  较近一轮的模型内容、reasoning、tool_calls
  tool       每个 call_id 对应的回执
  ...        其余待续传的完整回合
  user       本轮重建的领域上下文
tools:       当前 Agent 的完整工具说明和参数 schema
```

最后一条 `user` 内包含 Agent 职责提示、任务需求、当前材料与状态、工件目录及存在时的历史摘要。职责提示在领域文本中名为 `system` 段，**不是**第一条 API `system` 消息。旧轮次的领域 prompt 不保存在这段 assistant/tool 历史里。

工具说明来自实际注入的 Tool：其说明、`model_guidance` 和输入模型共同生成 `tools`，同一注册表负责派发与参数校验。所有 Agent 客户端必须提供原生 `next_tool_call` 和稳定的协议身份；脚本客户端也返回原生回合。不再接收正文 JSON 动作、注入重复的 `tool_contracts` 文本或生成 `recent_observations` 短历史。未知工具进入有界纠错反馈并保存对应未执行回执，不直接结束整次尝试。Compiler 的结构化输出不属于 Agent 动作协议。

Provider 的 `reasoning_content` 与相应工具回合保存在 Session，近期回合按协议续传；它不进入业务 memory，不作为科研证据。源码见 [原生协议投影](../../packages/runtime/src/resagent2_runtime/tool_calling.py) 与 [客户端](../../packages/runtime/src/resagent2_runtime/llm.py)。

### 2.3 任务、问答与工作交接怎样协调

`request_task_context` 只投影当前 `instruction` 和经过作用域校验的累计用户问答。Scientific 的指令由用户目标、假设、背景和约束组成；Coding / Experiment 接收所分派任务的指令。问答保留原题、回答值、时间和原件 ID，不会在第四部分再复制为 `material_answer`。

`ask_user` 与 `request_work` 都是原生工具调用。历史回执分别说明“问题已发出，尚未回答”或“工作已提出，尚未执行”，不会把提出动作当成结果：

- 用户回答后，调用方将已冻结答案交给原作用域；任务需求显示累计问答。操作批准另由准确的待执行快照与权限检查处理。
- 工作稳定后，Scientific 的本轮上下文显示 `work_feedback` 的事实框和报告，目录并入已授权交付物；原来的 `request_work` 调用仍在历史中。工作结果不追加到用户问答。

Loop 还会按需加入 `runtime_feedback`、`pending_operation` 和 `history_checkpoint`。它们分别说明仍需处理的拒绝、尚未执行的准确操作、旧交互的有损交接。当前事实和回答优先于旧回执与摘要；普通工具的 `ok=False` 回执不一定同时形成 required 拒绝反馈。反馈中的有界 JSON 摘录与其他头尾摘录共用字符裁剪函数，省略标记也计入上限；摘录不是可重新解析的完整 JSON，也不改写原事件。

原生回执保留工具的 `ok`、说明和返回内容，不把内部 `memory_updates` 发给模型。工具先施加的 IO 裁剪不会在历史层恢复，历史层也不另做约 400 字符裁剪。Loop 的拒绝明细另有约 800 字符的预览边界。

用户回答按当前 Task/Attempt 或 Scientific Session 的作用域进入任务需求部分。问答暂停后的续跑保留原作用域和累计回答；失败重试新建 Task Attempt/Session，不自动继承旧问答，必要时重新提问。这是尝试隔离，不是历史压缩或工件删除。

### 2.4 完整索引、分组与正文是三件事

`artifact_index` 是本轮唯一完整的平铺目录：**本次授权输入 ∪ Session 中已记录的工具产物条目**，按真实工件 ID 去重。它保留种类、说明、可选交付名、尝试序号和直接来源关系；有工作分组时补充实际执行状态。它不复制 URI、hash、权限或正文，也不授予新权限。登记表及冻结原件仍是权威来源。

这里的“完整”相对于当前授权和已知范围，不等于暴露整个 Run 的所有内部记录。目录是固定必需内容，不能只挑前几条或截断；完整目录装不下时明确报错。

Scientific 还使用 `ResearchIndex` 对研究材料按初始输入、Scientific 材料和已有 WorkRequest 分组。builder 验证当前目录快照与登记材料一致；模型只收到组名、原工作目标和 `artifact_ids`，条目详情仍只在平铺目录中出现一次。系统交接工件可以在平铺目录中有入口，但不一定属于研究材料分组。

文献和网页工具登记新工件后，将 compact 条目合入 Session 的索引；下一次模型请求即可看到，不需等待一次 `request_work`。新条目先进入平铺目录，工作分组依调用方交付的当前 ResearchIndex 快照呈现。上下文不会因此自动展开正文；需要内容时使用 `read_artifact`。

源码见 [目录投影](../../packages/components/src/resagent2_components/materials.py)、[Scientific 的目录校验](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[ResearchIndex 构建](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py)。

<a id="modules"></a>

## 3. 各模块在共同框架上的差异

表中的“必需”表示该内容不能被 Composer 整段省略。对于可伸缩材料，只保证来源与省略提示等导航框完整，正文仍按空间选择。

<a id="scientific"></a>

### 3.1 Scientific：当前研究目标、证据和交接

Scientific 在同一个 Run 中复用自己的 Session。它以用户目标和明确约束为主线，选择直接工具或委托工作，再综合材料；检索不是每次 Run 的固定第一步。

| 本轮内容 | 来源与呈现 |
|---|---|
| `research` | 当前研究指令与同一 Scientific Session 的累计问答；必需 |
| `artifact_index` | 完整平铺目录及仅引用 ID 的研究分组；必需 |
| `dataset_catalog` | 本次调用解析出的数据集可用性与用法；必需，不是数据正文 |
| 要求与恢复材料 | 校验后的 conclusion_requirements，以及本次交付的非 answer 恢复材料；必需 |
| 工作反馈 | 同源 WorkRecord 的原目标、真实任务状态、问题、未解决项和原件入口；事实框必需，报告正文可伸缩 |
| `artifact_reads` | 本 Session 已读取的工件片段与少量已读来源提示；有记录才出现，正文可伸缩 |

Scientific 没有工作区编辑或实验执行工具，不注入执行环境、工作区权限或实时剩余调用数。工具是否允许执行仍由代码读取结构化权限校验，不靠模型自行记账。

工作反馈保留任务最新已记录报告及原件入口，事实框直接读取配对 WorkRecord，不从报告推断执行状态。旧工作记录和报告仍可按 ID 读取；恢复用户问答不会自动重放旧报告。报告可帮助理解进展和局限，不能代替原始测量，也不能把同一数据衍生出的多份报告算成独立证据。

明确的证据种类和交付要求在要求工件正文中呈现。Scientific 的提示要求把适用于委托工作的明确方法、顺序、授权条件及交付名传进工作目标或约束；最终对照用户需求说明未满足项。是否还需读取、委托或提问由 Scientific 判断。代码检查引用授权、工件身份、冻结内容和明确交付条件，不自动证明论断成立。

访问记录 `observed` / `observation_trace` 只保留实际工具访问事实，不作为提问、委托、引用或完成的前置门槛；下载成功、出现目录项或有读取记录都不证明已读完整篇或正确理解。

源码：[Scientific builder 与提示](../../packages/agents/scientific/src/resagent2_scientific/context.py)、[回合装配](../../packages/agents/scientific/src/resagent2_scientific/agent.py)、[完成检查](../../packages/agents/scientific/src/resagent2_scientific/completion.py)。

<a id="coding"></a>

### 3.2 Coding：代码、当前验证事实和诊断

Coding 的同一套 prompt、工具与 builder 用于分析、修改和验证；写权限允许修改，不要求一定修改。

| 本轮内容 | 来源与呈现 |
|---|---|
| `task` | 任务指令与当前 Task / Attempt 问答；必需 |
| `execution_context` | 当前结构化工作区授权、输出位置、操作权限和命令确认开关；必需 |
| 完整目录、数据集与要求 | 共用索引、数据集视图及校验后的验收/恢复材料；必需，回答不重复入材料 |
| `verification_state` | 代码推导的编辑/验证版本、通过或失败、新鲜度、环境认证与问题；有控制投影时必需 |
| `environment` | 与执行工具同一绑定的当前状态和最近采集事实；有绑定时必需 |
| 读取、命令与目录 | 文件/工件工作集、最近命令诊断、最近一次目录观察；规则见后文 |

验证通过与验证仍有效分开显示：未验证、当前失败、旧版本通过不能互相替代；编辑或环境变化使旧验证失效。模型不能自行填写机器的编辑与验证事实。完成判断依据任务目标，纯分析不强制执行；代码正确性检查也不能冒充正式科研实验。

源码：[Coding builder](../../packages/agents/coding/src/resagent2_coding/context.py)、[验证与完成检查](../../packages/agents/coding/src/resagent2_coding/completion.py)。

<a id="experiment"></a>

### 3.3 Experiment：已有实现、测量与实际执行结果

Experiment 与 Coding 共用任务问答、授权、目录、数据集、环境、读取和命令诊断的投影，没有 Coding 的编辑工具及 `verification_state`。同一套 prompt 用于已有结果分析和新实验执行；调用开始不强制探测硬件、准备环境或执行命令。

命令回执提供实际退出/超时状态和日志摘录，执行记录由真实事件生成。产物正文不会因命令成功自动进入上下文，应通过目录和读取工具检查。实现缺口或真实代码故障携带证据交回 Scientific 安排，不用临时脚本绕过职责边界。失败的探索命令、负科研结果和任务失败是不同判断。

源码：[Experiment builder](../../packages/agents/experiment/src/resagent2_experiment/context.py)、[装配](../../packages/agents/experiment/src/resagent2_experiment/agent.py)、[完成检查](../../packages/agents/experiment/src/resagent2_experiment/completion.py)。

<a id="compiler"></a>

### 3.4 Compiler：每次编译当前工作需求

Compiler 不创建 Session，不接收上述原生历史。它经 `PromptLLMClient` 将职责提示与当前 `compiler_request` 作为必需文本段发送，要求正文 JSON 输出，复用同一容量和执行预算机制。

请求包含当前 WorkRequest、可用执行模块说明、编译草图 schema、剩余任务容量、逻辑工作区及存在时的结构纠错反馈。它不接收全部旧 Task / Run 历史或工具读取工作集。代码物化正式身份并校验；拒绝进入下一版请求，最多两版草图，不另发语义复审。

任务容量是上限，不是凑满目标。Compiler 保留原工作范围和明确要求，仅补必要前置工作；同一 Agent 的提问、检查、准备与执行可留在一个任务内。这些是编译提示，不新增固定研究步骤。

源码：[Compiler](../../packages/orchestrator/src/resagent2_orchestrator/compiler.py)、[PromptLLMClient](../../packages/runtime/src/resagent2_runtime/llm.py)；部署覆盖值见 [CLI](../../apps/cli/README.md#6-模型与上下文预算)。

<a id="interpreter"></a>

### 3.5 Interpreter：组织事实，不生成模型回答

Interpreter 从登记材料构建 ResearchIndex，并从配对 WorkRecord 按任务顺序组织最新已记录报告、状态、错误、警告和原件 ID。历史尝试不冒充当前结果，报告不再经模型转述。

Controller 冻结并交付结果；Scientific 的 builder 校验来源后生成事实框与可伸缩报告。Interpreter 没有 Session、工具循环、日志解释调用或独立模型额度。系统构造反馈或目录不登记为 Scientific 的工具访问。

源码：[Interpreter](../../packages/orchestrator/src/resagent2_orchestrator/interpreter.py)、[共享材料读取](../../packages/components/src/resagent2_components/materials.py)。

<a id="reads"></a>

## 4. 工具返回、读取工作集与搜索材料

### 4.1 一次读取和下一轮可见正文不同

`read_file` / `read_artifact` 先返回一个受 IO 边界限制的片段。`start_char/end_char` 回显请求范围，`truncated` 只表示请求窗口被工具上限裁剪，不表示整份文件是否读完。`next_start_char` 给出实际返回末端在同一所选行范围中的字符位置；该行范围没有余文时为 null。按需连续读取时保持行范围不变，以该位置作为下一段起点。该结果进入 Session 事件及原生回执；下一轮 `workspace_context` 再从记录中选择有界正文工作集。工具返回过的内容不会因此永远全部可见，工作集省略也不会删除原事件或冻结文件。

两个入口仅读严格 UTF-8 文本：无效编码或 NUL 产生可恢复错误，不自动解析二进制，不新增成功读取记录。先检查整份文本，再选范围；缩小窗口不能绕过检查。保留原换行与 Unicode 字符偏移。工件先校验授权、登记来源和整份冻结 hash；二进制工件可以登记及验证存在，但存在不等于返回过正文。

读取窗口先选从 1 开始、两端包含的物理行，再选零基、末端不含的 `start_char/end_char` 字符窗口，最后保留最多 **128000 字符**。字符不是 UTF-8 字节，也不是 tokens。它支持超长单行分段续读，但不是流式读文件；范围越过末尾可返回短结果或空串，不自动换范围。`truncated=False` 只表示所选范围没被这次字符上限裁掉。

工作区文本读写与搜索共用默认 **10 MiB** 文件上限；读取核对大小及实际读取长度，创建/替换核对最终 UTF-8 字节数、编码和 NUL，拒绝时不写入、不增加编辑版本。工件读取没有这项工作区大小上限，完整性检查仍覆盖整份内容。

`search_text` 返回字面子串匹配及行号，当前不形成单独的长期搜索正文段。它报告实际遇到的跳过文件与原因，以及搜索是否不完整；达到结果上限可能尚未访问后续文件。`incomplete=True` 时，零匹配不能证明不存在。具体参数和返回约定见 [文本工具契约](CONTRACTS.md#tools)。

源码：[读取切片](../../packages/components/src/resagent2_components/text.py)、[工件读取](../../packages/components/src/resagent2_components/artifacts.py)、[工作区读取](../../packages/capabilities/src/resagent2_capabilities/workspace/read_file.py)。

### 4.2 工作集保留最近片段与来源线索

文件与工件分别形成 `file_reads` / `artifact_reads`，共用选择机制：

1. 从新到旧挑不同片段，身份包含工具、来源、请求行范围与字符范围；同一来源的不同窗口可以并存，不固定只留六段。
2. 最新片段先装入；最后一个装不下的片段保留头尾并明确标记，其后的旧片段不再选入。
3. 选中后按原始事件顺序从旧到新呈现。原事件及工件不改写。

两组正文各有相同的起始权重，未使用空间可借给其他材料；Scientific 没有文件工作集，工件可使用空余。每组的最小导航框必需，正文弹性分配。`previously_read` 是最多 20 个、合计 600 字符的来源提示，不是完整读史、永久全文目录或研究结论。

`observed_at` 是原事件序号，不是文件版本或时钟。文件片段还会标记记录中较晚的内置编辑、删除或 Coding 实测 Shell 变化；这说明它是改动前的观察。无标记只表示没有记录到这类后续改动，不能证明外部没改文件。冻结工件不使用这类新鲜度标记。

`truncated` 表示这里显示的正文遭到工具或工作集裁剪；额外工作集裁剪另置 `context_truncated=True`。请求范围不等于裁剪后正文的精确覆盖范围，不能从头尾片段推算续读偏移。`next_start_char` 仍是原工具回执的返回末端，不代表工作集头尾片段之间已连续呈现；需要缺口内容时按范围补读。`content_omitted` 表示本轮没放入正文；false 也不代表全部读史已放入。

源码与测试：[共享工作集](../../packages/components/src/resagent2_components/context.py)、[片段选择](../../packages/runtime/src/resagent2_runtime/context.py)、[读取时序测试](../../tests/e2e/test_workspace_read_history.py)。

<a id="commands"></a>

### 4.3 命令诊断和目录也只是有界观察

`command_results` 从原事件选取 `run_verification`、`run_setup`、`run_shell` 各自最近一次带实际结果的观察。失败项先装，再尝试成功项，选中后按原事件顺序展示。它保留命令、退出/超时状态和有界日志尾部；没捕获输出时明确说明，不编造根因。每个尾部最多 2000 字符，裁剪和省略数量标明。该材料导航/省略提示必需，正文参与统一空间分配。

同一工具的新结果只取代投影中的旧诊断，不删除原事件。前置审计失败、无环境或等待批准不算已执行命令，可能仍显示此前诊断；本次阻断应结合最新回执、环境和反馈。诊断不是当前验证状态或科学测量，完整日志仍在原处。

`directory` 来自最近一次 `list_files` 回执，最多呈现 2000 条完整路径，属于可选材料。重构上下文不重扫目录，后来创建的文件不会自动出现在旧清单；旧清单缺项不能证明文件不存在。

源码：[命令与目录投影](../../packages/components/src/resagent2_components/context.py)、[共享投影测试](../../tests/components/test_workspace_context.py)。

<a id="literature"></a>

### 4.4 文献和网页怎样接入同一上下文

两类工具共用“单次回执 → 登记材料 → 完整目录 → 显式读取”的接入方式，没有固定的检索顺序，也不自动获取所有搜索结果的正文。

| 动作 | 保存与返回 | 下一轮怎样出现 |
|---|---|---|
| 文献检索 | 一个查询回执 `literature_search`，每篇一个元信息/完整来源摘要 `literature_paper`；工具返回论文入口及短摘要预览 | 登记条目合入完整目录，短预览保留在本次回执历史；需要完整摘要时读单篇工件 |
| 网页搜索 | 一个 `web_search` 保存本次有界响应的全部规范化标题、URL、snippet；工具只展示少量预览和省略提示 | 同一搜索工件进目录，历史不会自动展开省略结果；需要尾部来源时读搜索工件 |
| 获取论文全文 | 对授权论文 ID，优先复用已冻结 PDF，必要时下载，再分别冻结 `literature_pdf` 和 `literature_fulltext` | 新工件进目录，保留论文 → PDF → 文本的直接来源关系；正文需按范围读取 |
| 获取网页 | `web_fetch` 保存单页 HTML/text 提取文本为 `web_page`，保留合法链接地址、可见文字和 `pre` 空白 | 新网页工件进目录；链接目标没有自动获取，正文需显式读取 |
| 读取工件 | 不创建新工件；返回指定范围内容并记录访问 | 回执进入历史，正文进入有界 `artifact_reads` 工作集 |

文献重复检索按规范化论文身份与元信息快照复用，保留 arXiv 版本；同一身份内容变化保存新快照，不按题名猜测合并、不重写旧原件。全文可复用本 Run 已冻结 PDF/文本，解析失败后再尝试也可保留 PDF。来源关系用于定位同一材料的不同格式，不表示独立证据。

文献工具提供实际来源、执行查询、来源尝试、错误和可用时的完整续页请求；Scientific 自行决定改词、换源、翻页、读取或停止。缺少续页请求不总能证明结果耗尽。网页工具区分成功结果、合法空结果、供应商限制下的部分结果与失败；预览省略和搜索不完整也分开说明。工具完整约定见 [文献与网页契约](CONTRACTS.md#tools)，本文不另维护供应商参数规范。

网页 snippet 可能为空，也只是线索；当前 DeepSeek 响应中的不透明加密内容不解释为摘要，生成的搜索回答不作证据。抓取器不执行 JavaScript，重定向占位页可能没有有效正文；正文不足应保留局限。论文 PDF 由 PyMuPDF4LLM 提取，关闭 OCR，表格、公式、图像可能不完整；下载、解析或目录登记成功均不等于 Scientific 已阅读全文。

CLI 导入的论文元信息和可选本地 PDF 进入同一目录与全文读取链；导入不表示在线检索成功，也不自动读全文、回答当前问题或恢复 Run。只读取了摘要就按摘要层面陈述；失败、空结果、片段省略和外部来源不可用不能混成“资料不存在”。访问日志不承担阅读门禁，引用仍校验授权、身份与冻结内容。

源码：[文献能力](../../packages/capabilities/src/resagent2_capabilities/literature/)、[网页能力](../../packages/capabilities/src/resagent2_capabilities/web.py)、[全文与网页提取组件](../../packages/components/README.md)；粒度和访问日志取舍见 [ADR-0022](../history/decisions/0022-paper-materials-and-access-records.md)。

<a id="compaction"></a>

## 5. 历史压缩：减少续传，不删除原始记录

Session 的原始 `tool_turns` / 事件在磁盘上持续保存。模型下一轮只发送检查点边界后的完整回合，以及已有摘要和最新领域上下文；完整目录、当前任务和用户回答由当前来源重建，不靠摘要恢复。

压缩规则是：

- 完整请求超过有效输入的 **80%**，或必需输入因历史占用而装不下时，才考虑压缩；每轮最多一次，没有较早完整前缀就不做。
- 至少原样保留最新一个完整 turn，再尽量保留总计不超过有效输入 **20%** 的近期回合。每个 turn 的全部调用、回执和 reasoning 不拆开。
- 较早完整回合连同已有摘要发给同一客户端，生成纯文本交接。输入仍受同一容量限制；提示按有效输入的 5% 给出短摘要目标，最多 16384 字符，目标不是另一个拒绝上限。
- 接受后先检查“完整摘要 + 近期历史 + 当前上下文 + schema”能否装下，成功才保存摘要与绝对历史边界并追加审计事件。空摘要、截断响应、失败或仍超限均不推进边界，不靠裁短摘要凑成功。
- 后续从持久边界继续，不重新总结已覆盖的前缀。摘要只帮助定位做过什么、待办和来源，不证明当前文件、验证或科研结论。

摘要也消耗同一调用次数和截止时间；开始前需至少留两次额度供摘要及后续动作。没有 `summarize_history` 的注入客户端仍可运行，真正放不下时明确失败。不自动换模型、扩容、再加摘要纠错或无限重试。

原始记录不删除，当前状态、工件和完成门禁不改写。压缩有损，不承诺保留每个历史细节、消除所有循环或减少每次费用；当前没有分 Agent 的长期记忆、阅读笔记、向量检索或自动磁盘归档。Compiler 不压缩 Session 历史，Interpreter 不调用模型。

源码与测试：[压缩规划](../../packages/runtime/src/resagent2_runtime/compaction.py)、[Loop 检查点提交](../../packages/runtime/src/resagent2_runtime/loop.py)、[压缩测试](../../tests/runtime/test_compaction.py)、[重启测试](../../tests/runtime/test_history_checkpoint.py)。

<a id="budgets"></a>

## 6. 容量与执行预算分别约束什么

### 6.1 先保留完整必需输入，再分配正文

`ContextSection(required=True)` 是不能裁剪的固定内容，例如职责、任务问答、完整索引、要求和拒绝反馈。`ContextMaterial(required=True)` 保证来源与省略提示等最小导航框，正文由纯 `render(chars)` 按空间生成。Composer 不自行剪开 JSON、代码或调用/回执。

必需段按 builder / Loop 提供的顺序排列，职责段先出现；`priority` 不决定它们的显示顺序。可选段按 priority 依次尝试。已选入的材料先按相对 weight 分空间，再按 priority 借用未用完的空间，不能挤掉其他材料已分到的一份，也不强制填满窗口。

| 边界 | 当前规则 |
|---|---|
| 模型可用输入 | ModelProfile 的窗口减预留输出与安全余量；JSON 编译路径还预留输出 schema 说明 |
| 模块输入上限 | 三个 Agent 与 Compiler 默认 256000 tokens，可分别配置；Interpreter 无模型输入额度 |
| 真正计量范围 | 原生路径计完整序列化 `messages + tools`，包含历史、schema 和 JSON 转义；不是只计领域正文 |
| 材料扩展目标 | 整包输入 80% 软水位；必需内容可超过软水位，但不能超过总硬上限 |
| 材料起始权重 | 反馈 / 文件 / 工件 / 诊断 / 目录为 16 / 16 / 16 / 4 / 1；只有选入项参与 |
| 空余借用顺序 | 反馈 100、诊断 96、读取 80、目录 62 |
| 工具 IO | 单次读取最多 128000 字符，是独立边界，不能当输入 tokens 上限 |

Loop 先取模块上限与模型可用容量的较小值，为完整 schema 和当前历史预留空间，再调用 builder；Composer 和客户端仍按完整请求复核。没有模型预算 hook 时使用模块上限，不猜供应商容量。CLI 与 real E2E 各自装配，默认常量同源，不代表 E2E 自动继承 CLI 环境变量。

计量目前是 `ceil(字符数 / 4)` 的确定性粗估，不是供应商 tokenizer；中文等内容也不保证高估。`estimated_tokens` 不等于真实 usage。输出额度另行配置，Provider 的思考和正文可能共享它，不因输入空余自动增大。

软水位留空间给后续工具返回，不保证下一次回执一定装下。至多一次历史压缩后，完整目录、required 输入、schema 或巨大近期回合仍超限，就返回现有 `budget_exhausted` 并保留现场；不静默截断索引、不删半个 pair、不自动暂停或修改上限。

源码与测试：[Composer](../../packages/runtime/src/resagent2_runtime/context.py)、[材料分配测试](../../tests/runtime/test_context_materials.py)、[原生完整请求容量测试](../../tests/e2e/test_native_context_capacity.py)、[Scientific 容量测试](../../tests/e2e/test_scientific_context_capacity.py)。部署配置见 [CLI](../../apps/cli/README.md#6-模型与上下文预算)。

### 6.2 用量控制不靠模型自行记账

Run / Task 的执行预算约束模型请求次数与时间。Agent 动作、Compiler、历史摘要、HTTP 重试和 DeepSeek 托管搜索共享权威用量及截止时间；Tavily 与网页抓取只占时间。嵌套调用只能缩小范围，不能另换钱包。发送前持久占用，暂停恢复不重置，崩溃留下的未知请求不退款。

任务数与尝试数由 ExecutionLimits 控制，step 只记录动作时序。三个 Agent 的当前上下文不自动显示 Run 用量、实时剩余时间或 TaskBudget 数值；Coding / Experiment 的权限和确认开关在 `execution_context`，Scientific 的 `research` 是指令与问答。Compiler 看到剩余任务容量，不拿一份独立模型钱包。

这些控制不表示精确供应商账单、总 token / 货币预算，也不是 Run、Session 与供应商之间的跨系统事务。细节见 [计量契约](CONTRACTS.md#trace) 与 [共享预算](../../packages/runtime/src/resagent2_runtime/budget.py)。

## 7. 刷新与恢复：重建不等于重新执行

### 7.1 不同信息有不同刷新时机

| 信息 | 何时刷新 | 局限 |
|---|---|---|
| 任务问答、完整索引、当前反馈 | 每次构造，从当前请求及 Session 投影 | 不越过授权范围，不自动读全正文 |
| 文件/工件工作集、诊断、目录 | 每次构造选择已有工具记录 | 不重新读磁盘或执行命令；旧观察带时序与局限 |
| 环境状态 | 每次构造读取实际绑定 | 认证只覆盖绑定 Python 身份/版本及 pip，不证明依赖或设备可运行 |
| 环境 information | 获准 prepare/setup、显式或自动 audit 时采集 | 带观察时间，不是实时监控；构造上下文不运行探针 |
| 数据集视图 | 每次 Agent invoke 开始从目录工件解析；回答恢复后重查 | 同次 loop 复用，不在每个 LLM step 扫目录 |
| 工作反馈与研究分组 | Controller 在工作稳定等交接时保存并交付快照 | 历史原件仍可读，不由模型报告推断状态 |

环境信息提供平台、CPU/内存、设备可见性、包版本，以及可取得的 GPU/驱动信息。驱动报告的 CUDA 支持版本不是本环境 Toolkit / 框架构建；框架版本从已安装元信息和可读取字面量取得，不导入框架或初始化设备，不自动判断兼容性。查询失败标为不可用，不推断没有硬件，也不改变基础认证结果。完整包列表保留在回执；重复环境段中的包列表有界并标明省略数量，平台与设备事实不因此剪掉。

prepare/setup 开始前清除信息快照，进程恢复后的新绑定不继承旧快照；未采集时明确说明。运行脚本前使环境认证失效，执行前审计不代表执行后仍认证。执行 Agent 的提示要求按实际需要验证任务所需能力，GPU 工作包括小型设备运算；这不是新增 GPU gate，纯分析不强制探测。

同 Run、同工作区可共用环境。提示要求依赖调整前诊断并通过已有 `ask_user` 获得必要决定，调整后复查受影响能力；当前操作权限与精确命令确认独立生效。自然语言问答不能扩权，旧成功记录不能冒充当前验证。

源码：[环境与工作集投影](../../packages/components/src/resagent2_components/context.py)、[环境信息](../../packages/components/src/resagent2_components/environment_info.py)、[数据集解析](../../packages/components/src/resagent2_components/dataset.py)、[环境行为指引](../../packages/capabilities/src/resagent2_capabilities/environment/guidance.py)。

### 7.2 批次、批准与重启保留什么

原生每轮接受 1–8 个工具调用，整批先做参数与权限预检，再按顺序执行；需要观察前一项结果才能决定下一项时，应等下一轮。`finish` / `ask_user` / `request_work` 必须独占一轮。某项失败时保留已完成项，后续取消并生成未执行回执，不做副作用回滚。

批准不会自动执行工具。Session 的 `pending_operation` 展示准确工具、参数和 action_id，明确尚未执行；模型依据当前答案重发相同动作，代码重验权限与目标、消费结构化批准后才执行。拒绝不执行，批准也不能绕过原权限。

Loop 先保存整批 assistant 调用，每项派发前记录正在执行的 call ID，完成后保存回执。重启时，已完成项保留；正在执行却没有持久回执的项记为结果未知，后续项记为未开始，不自动重放。它防止把不确定副作用当作安全重试，不承诺掉电持久性或 exactly-once。

Session 恢复校验 Run、Agent、任务、尝试及协议身份。原生身份绑定协议版本、API endpoint 和模型，不含 API key；换模型、换 endpoint 或旧正文 JSON Session 均不静默接续。当前 schema 24.0 拒绝旧版本 Run / Session，原始记录保留，不迁移或自动重写。

恢复时，Session 文件的读取、UTF-8 解码、JSON 解析或状态校验失败由存储边界标识，Loop 返回 `failed / contract_error`，不调用模型、不重写原文件。此时没有可用的恢复状态，因此不返回新的 SessionRef。自定义存储的编程错误与权威保存失败仍向上暴露。

Session 是权威续传存储，与可选 trace 独立；关闭 trace 不影响工具历史和 reasoning 持久化。Session 目录 / 文件按 `0700/0600` 管理。权威用量或状态保存失败不能当作成功，trace 写入失败则只告警，不覆盖模型结果。

源码与测试：[Loop](../../packages/runtime/src/resagent2_runtime/loop.py)、[SessionStore](../../packages/runtime/src/resagent2_runtime/store.py)、[原生批次与恢复测试](../../tests/runtime/test_native_tool_calls.py)、[原生断点测试](../../tests/runtime/test_history_checkpoint.py)。

<a id="verification"></a>

## 8. 如何核查信息确实交给了模型

分别检查四层，不能只用最终成功状态替代：

1. **原记录**：请求、工具观察、报告和冻结原件是否包含所需信息。
2. **本轮可见内容**：完整模型请求、included / omitted 清单及实际片段是什么。段已 included 不表示段内全部正文都在。
3. **模型使用**：真实动作是否读了相关材料、是否按答案行动；测试脚本替模型选好范围不证明自主使用。
4. **结果对应**：实际执行和最终论断是否由可见证据支持。访问过也不等于正确理解。

full trace 的原生 `request_text` 是序列化 `messages + tools`，可核对实际输入；assistant 的正文可以为空，动作在 `raw_tool_calls` 中。`action_valid` 只表示候选动作可解析，不证明整批预检、工具执行、完成检查或科研结论有效。摘要请求没有工具动作，不能把它的空 action 字段算成失败。托管搜索 trace 的 HTTP / JSON 成功也不等于搜索可用，应结合工具回执与 Session 观察。

trace 可关闭、只留 hash 元数据或保存完整输入输出。它是可选诊断，写入失败只给不含正文、路径或密钥的告警，不替代 Session、权威预算或科研证据。Session 及 full trace 可能含用户输入、源码、工具参数和 reasoning，应按既有私有边界处理，不能复制到公开记录。格式见 [trace 契约](CONTRACTS.md#trace)。

<a id="maintenance"></a>

## 9. 修改上下文时维护什么

- 先明确事实的权威来源、模型用途和刷新时机，再决定放在任务、目录、当前材料还是协议历史。
- 修改 builder、读取投影、材料选择或预算时，同步本页及对应确定性测试；公开字段仍由 CONTRACTS 维护。
- 区分代码保证、提示期望和模型实测，不把提示要求写成已强制的门禁，也不把一次成功运行当作普遍质量提升。
- 派生导航与当前投影不另存可漂移的业务状态；历史摘要不替代原件、当前状态和完整目录。

设计演变、旧阶段结果和验收证据留在 [history](../history/README.md)；本页只说明当前实现，不维护测试总数或候选能力。原生请求与压缩边界的测试入口见 [Runtime 测试](../../tests/runtime/)，跨模块材料消费见 [E2E 测试](../../tests/e2e/)。
