# 权限反馈、CLI 问答与通用提示词：完整修改方案

日期：2026-10-12。讨论基线：`fix/native-agent-receipts@a60f6b3`，schema 25.0；本轮 L3 的产品冻结提交为 `5d28f74`。

**状态：用户确认后已在同一分支实施，包含崩溃恢复补全的本地回归已完成；服务器真实模型与 L3 尚未执行。** 第一项补齐权限反馈和 CLI 入口；第二项按最新讨论收敛为先优化 Scientific / Compiler 提示词，第三、第四项同样只优化通用提示词。本轮不调整 WorkRequest 契约，也不增加第二套约束输入。原文保留不再承诺由固定代码逐字保证；当前行为仍以代码及 `docs/current` 为准。本轮 schema 升为 26.0，旧 25.0 Run/Session 拒绝恢复，无兼容层；实际开发结果、首次指纹测试失败及修复、后续验收步骤见[实施测试计划](PERMISSION_FEEDBACK_TEST_PLAN_2026-10-12.md)。

四项分别是：①权限反馈和 CLI 回答入口；②Scientific 清楚表达、Compiler 忠实细化；③执行与检查符合任务边界；④用户反馈处理和交接事实准确。第一项补齐产品数据与交互链路，后三项先通过 prompt 尝试改善模型行为，不宣称已经彻底解决。

## 1. 改动目的与已核实事实

目的是让用户反馈准确到达模型，减少任务细化、实施和交接中的语义偏差。保留现有 Scientific / Compiler / Interpreter / Coding / Experiment 分工、统一 Agent 输入输出、原生工具调用和四部分上下文，不借此次个案建立科研专用规则引擎。

本轮已有证据支持以下事实：

- 四次有争议的冒烟请求都是 `run_shell` 的操作批准，不是模型主动发出的业务 `ask_user`。四条均被拒绝，没有执行。
- 模型实际收到原调用对应的第三部分回执：`User declined this operation` 和 `execution_status=not_executed`。详细拒绝理由仅在测试方审计中，没有提交到产品。
- 另有一次业务 `ask_user`，用户选择正式完整实验。该回答已经进入第二部分，模型随后仍提出先做完整流水线的短跑；不能归因为业务回答没有送达。
- Scientific 的两次 WorkRequest 均保留研究边界并描述工作步骤及顺序。Compiler 接收的 WorkRequest 与持久记录相等；四个生成的 Task 指令原样保存，61 次子 Agent 模型请求中的 Task 指令均未丢失或改写。
- 第一版 Coding Task 明确禁止实施阶段评价 test；第二版仍保留冻结后评价 test 的边界，但缺少同样明确的实施阶段限制。Coding 后来将会评价 test 的完整短跑入口写入交接，Experiment 继续采用该建议。

缺少拒绝理由可能放大重复请求，但解释不了第一次产生短跑建议，也没有证据证明它是全部原因。不能把一次模型行为差异直接认定为框架回归，或承诺修改提示词后必然消失。此前验收、研究评分和局限见 [schema 25 验收记录](TOOL_CONTEXT_ACCEPTANCE_2026-10-11.md)。

## 2. 第一项：操作批准可以附带用户说明

### 2.1 决定与说明分开表达

操作批准继续绑定原来的准确操作，包括工具、参数、作用域、工作目录和环境等已有校验事实。恢复时仍核对原操作；用户说明不能隐式扩大许可或替换被批准的命令。

用户拒绝时可以选择性附带原因或说明；没有说明也能拒绝。决定与说明使用独立的数据位置，不把 `yes，因为……` 或 `no，因为……` 当成授权词解析。说明按用户提交的原文保存并传给模型，不由系统改写为另一种业务意图。

被拒绝的操作不执行。原调用的唯一最终工具回执应一起表达：

1. 此操作已被拒绝；
2. 此操作未执行；
3. 用户附带的原文说明（如果有）。

例如，用户拒绝并说明“这条命令会提前评价受限数据；请遵循原任务的正式执行顺序”，模型应在该操作的回执中看到这段原话，而不只看到 generic denial。实现采用 `UserAnswer.comment`，`RecordedAnswer` 保存原文；权限策略只把匹配准确动作的说明放到 `PermissionDecision.user_comment`，Runtime 将其传入 `ToolObservation.user_comment` 并输出在原工具回执顶层，保留原 value 的形状。批准时也可以附说明，说明随实际执行成功或失败返回；派发期间期限中断也保存说明，以 `execution_status=unknown` 保守反映可能部分执行，不重放。不新增另一套授权流程或说明回执。

### 2.2 保持上下文归属

| 信息 | 模型可见位置 | 保存与消费 |
| --- | --- | --- |
| 业务问题和用户回答 | 第二部分任务需求，原文累计保留 | 沿现有业务问答和作用域规则保存 |
| 操作批准或拒绝及附带说明 | 第三部分原操作的工具回执 | 保存供恢复与审计，决定由权限机制消费 |
| 操作批准问答 | 不进入第四部分工件索引 | 不为批准或说明登记工件 |

不把一次批准、拒绝或说明自动转换为长期业务要求。用户若要明确补充长期目标或约束，使用业务反馈入口；当前操作的解释仍进入对应回执。第三部分按现有完整工具回合规则压缩，第一、第二、第四部分的完整性规则不变。

### 2.3 下一步由模型根据反馈判断

**不新增“拒绝后继续调整 / 停止执行”的权限决策字段或选项。** 用户已经明确：是否调整计划、提问或结束，由 LLM 结合任务、拒绝决定和用户说明判断。权限代码负责阻止被拒绝的原操作，不替模型决定业务下一步。

若模型调整命令，仍按既有权限规则检查新的操作；拒绝说明不自动授权替代操作。模型请求业务许可也不能绕过具体命令的权限批准。

这项决定取代此前调研建议中“在批准响应中另外增加继续/停止选择”的候选方向。借鉴外部实现的反馈通道，不照搬其全部状态与交互选项。

## 3. 第一项的 CLI 配套：提供清楚的回答入口

当前业务回答的数据契约已经支持自由字符串；问题选项不是必须命中的枚举。缺口主要在 CLI：交互界面没有清楚展示结构化选项和自由输入方式，用户可能误以为只能回传某个选项标识。

本轮 CLI 目标如下：

- **业务问答**：展示问题、所需字段和已有选项，同时明确提供自行输入答案或补充说明的入口。用户原文进入对应业务回答，不由 CLI 替用户总结或改变含义。
- **操作批准**：展示待执行操作及其绑定信息，明确批准/拒绝入口，并提供可选说明输入；决定与说明分开提交。
- 交互 shell 与非交互 `answer` 命令都应能表达上述内容，并复用同一产品回答、保存与恢复链路，不只把说明写到外围测试脚本的日志里。

不增加第二套选项校验或问答引擎。多字段业务问题仍需按现有契约回答指定字段；自由输入不是向 `values` 随意加入未知字段。业务问题不接受独立 comment，自由补充放在原 values 字段。操作说明使用 `answer --comment`；shell 的 `/answer` 无参数且有待答问题时提供交互入口，具体语法随实现同步到 CLI README。

## 4. 第三、第四项：通用执行、反馈和交接提示词

用户已经确认这两项限于 prompt 优化，要求通用，不能针对本轮数据集、算法或被拒命令加补丁规则。实施时先对照当前 Coding / Experiment 的提示词，改写或合并已有规则，不重复堆叠；模型行为效果仍待真实验收。

### 4.1 第三项：执行与检查必须遵守任务边界

Coding / Experiment 的现有职责和执行前检查仍保留。澄清必要检查也要符合原任务的业务约束；判断命令效果依据真实入口、参数及当前事实，不只看“smoke”“test”“短跑”等名字。运行时间短并不自动说明动作符合任务边界。

不要求每个任务必须增加预检或跑一遍完整流程。按缺失事实和任务需要选择检查；已有材料足够时可以直接完成分析。实现某种执行能力不等于当前 Task 必须执行它，正式研究执行仍按已有职责交给 Experiment。

拟替换或融入已有 Working practices 的通用语义草案：

> Choose checks for the facts needed by the task and keep them within its scope and constraints. Inspect the actual entry point and arguments to understand the operation's effects; its name or short duration does not establish that it is appropriate. Implementing a capability does not by itself request its execution.

### 4.2 第四项：依据反馈调整下一步，交接分清事实与建议

收到操作失败或用户拒绝后，结合原任务、业务回答、工具回执和用户说明重新判断下一步；不把单条命令被拒理解成所有工作被禁止，也不把某条操作获批理解成其他操作自动获批。若缺少决定性信息，沿现有 ask_user 提问；是否提问、调整或结束由模型判断，不新增固定分支和重复次数限制。

Coding 等上游 Agent 交接时分清已执行并观察到的事实、失败或被拒操作、未执行建议及未完成事项。下游把建议作为待判断的信息，核对自身任务和业务回答后再决定是否采用，不能让建议隐式取代任务要求。转述结果前核对影响结论的数字、范围和条件与所引用记录一致，沿已有证据规则表达局限。

拟融入现有 Inputs / Decision principles / Completion and handoff 的通用语义草案：

> Use the actual tool result and user feedback to reassess the next action. An approval or refusal concerns the identified operation; infer neither broader authorization nor a broader prohibition from it alone. In handoffs, distinguish observed results, rejected or unexecuted actions, and proposed next steps. Check recommendations against the current task and user answers before adopting them.

这些是提示词表达的方向，不新增固定验证阶段、Agent 工作模式、科研专用禁令、自动研究判定器或拒绝次数阈值。不要把“有权限执行”和“符合业务任务”混为一谈，也不要求每个拒绝都自动结束任务。

对算法收敛与报告自洽性的观察继续保留在原验收记录中；不据此锁定某个优化器、梯度阈值或报告模板为全项目规则。本轮只针对已有证据改进通用表达，不能凭 prompt 字符串存在就宣布模型质量改善。

## 5. 第二项：先优化 Scientific / Compiler 的提示词

### 5.1 已澄清的职责和接口事实

Scientific 已决定业务工作项与编排意图；Compiler 基于这些输入进行细化、匹配 Agent 并形成可调度 Task。不要把 Scientific 描述成只给一个笼统目标，也不要让 Compiler 自行增加研究目标。

当前 `WorkRequestDraft` 的实际输入是 `objective + expected_evidence + constraints + input_artifact_ids`。Scientific 可以在自然语言中列出步骤，但没有逐项提交独立的 Task 原文和源工作项标识。正式 `CompilationDraft.tasks` 由 Compiler 生成，随后 `Task.instruction` 原样进入 `AgentRequest.instruction`。

用户希望继续保持“一个自然语言指令加必要控制字段”的接口思想，并让 Compiler 基于 Scientific 的输入扩展，使原始意图得到保留。最新讨论决定优先尝试通用 prompt 改进；不先增加接口字段或代码切分阶段。

当前 objective 中的编号可能是一个实验的多个步骤；Coding 则可能需要实现覆盖所有步骤的代码。编号步骤没有稳定 Task 归属，不能由代码机械切成独立任务。有显式工作项接口时可以代码分发，但那不是当前契约，暂无证据要求为此修改接口。

### 5.2 已撤回的建议

不默认把整份 WorkRequest 目标、全部预期证据和其他工作项要求拼给每个子 Agent。那样可能扩大该 Agent 对自身职责的理解，也增加无关上下文。

不新增 `task_context.work_request_constraints` 作为第二套自然语言业务入口。也不让固定代码根据编号、分句或关键词猜测哪些自然语言属于哪个 Task。

### 5.3 Scientific：明确工作、顺序、条件和限制

改写现有“Make the work_request self-contained”附近的规则，说明：

- objective 表达所需工作、必要前提和先后关系；constraints 清楚表达限制及其条件和适用范围，两处保持一致。
- 区分“实现某能力”与“执行某动作”，区分整轮限制和阶段限制，不用模糊简称替代限制的原意。
- 保留用户明确要求，区分它们与 Scientific 自己的科学方案选择；未知实施细节继续交给执行 Agent 判断。
- WorkRequest 自包含；不能把必须传下去的要求只放在 assessment、历史解释或未提供的材料中。

通用语义草案：

> Make each work request self-contained. State the needed work, necessary prerequisites, and ordering in objective; state restrictions with their conditions and scope in constraints, keeping both consistent. Distinguish implementing a capability from executing it. Preserve explicit user requirements and distinguish them from your own scientific choices. Leave unspecified implementation details to the execution Agents.

### 5.4 Compiler：在原意上扩展当前 Task

改写现有“Preserve the work request's objective, evidence needs and constraints”附近的规则。保留最小任务图及当前 Coding / Experiment 分工，不要求逐个流程步骤生成 Task。

- 优先沿用表达关键要求的原句，保留动作、对象、明确数值、输出名、顺序、前提、条件和限制。
- 只补当前 Task 所需的实施说明，不扩大研究目标、授权范围，也不把可选建议变成必须交付。
- 约束的适用范围和执行条件不能因分配给某个 Agent 而被缩小或扩大；实现入口不自动代表要求实施 Agent 执行正式工作。
- 每个 Task 仍用一条 instruction 表达业务要求；控制字段和最终调度结构沿原有契约生成。

通用语义草案：

> Refine the requested work into the smallest suitable tasks without changing the upstream decisions. Prefer original wording for explicit requirements, and preserve their scope, conditions, ordering, and logical output names. Add only the instructions needed for each task and its identified prerequisites; do not broaden permissions, weaken restrictions, or turn optional work into required deliverables.

这两段是模型行为指引，不能由此保证原文逐字保留或完全避免误解。原先讨论的“代码拼接共同 constraints”及“Scientific 显式工作项列表”本轮均不采用。后续只有在实际评估发现 prompt 无法满足明确需求时，才另行讨论接口变化，不预先保留兼容入口。

### 5.5 提示词修改方法

先审查整个现有 prompt，按职责修改或合并对应句子；上述草案用于审阅语义，不要求全部追加为独立段落。角色、工具能力、任务、历史和完整目录的归属不变。保留必要规则的目的，优先用简短、直接且可操作的表达，不靠更强烈的措辞、重复禁令或严格固定流程代替判断。

先用清楚规则验证。如果还需示例，只考虑简短、跨业务的输入输出示例，并检查是否引入无意模式；不默认加入本轮数据集、校准算法、受限文件名或某条命令。本轮调研的实际页面、固定源码和适用边界见 [通用提示词调研](PROMPT_GUIDANCE_RESEARCH_2026-10-12.md)。

## 6. 实施与验证边界

用户已授权在当前分支按本轮收敛范围实施，不自行改成新的任务接口或代码拼接约束；本轮不修改旧 Run、服务器原件或既有验收结果。服务器与新 L3 留给冻结版本验收，不能把既有通过结论套用本轮。

实施后需要验证：

1. 拒绝不会执行原操作；用户说明确实进入原生工具回执，且只有一个最终回执。无说明时仍正常拒绝。
2. 说明没有进入第二部分或工件索引，授权绑定、作用域校验、磁盘回答和恢复仍有效；替代操作仍按既有规则检查。
3. 交互与非交互 CLI 都能提交业务自由文本、权限决定和可选说明，实际模型输入与用户提交原文一致。
4. 提示词修改没有引入案例专用规则、互相矛盾的表达或重复段落。确定性测试证明反馈链路与边界，以及实际装配到请求；不能把提示词关键词断言当成行为通过。
5. 通过已有真实调用入口观察代表性情况：仅分析无需执行、实现入口但不要求正式执行、有明确顺序/阶段边界、拒绝理由到达后的调整、含未执行建议的交接。核对 WorkRequest → Compiler Task → 子 Agent 实际输入与行动，保留错误，评价约束是否保留和实际操作是否合规。
6. 最后沿现有规程在同一冻结版本运行 L3 并独立复算。若要声称相比旧版质量改善，对齐模型配置、输入、环境和预算，考虑重复运行；单轮 L3 只支持该次验收，不能消除模型随机性解释。

代码落地时同步四份 `docs/current`、相关指南和 CLI README。保存的回答结构升为 schema 26.0，旧 Run/Session 不做兼容层或静默迁移。prompt 属于固定契约，变更后必须使用符合新指纹的新 Session，不能往原 L3 Session 中静默替换提示词。开发结果和服务器测试步骤集中在[本轮测试计划](PERMISSION_FEEDBACK_TEST_PLAN_2026-10-12.md)，服务器及 L3 尚未执行，提示词效果不提前判定。

## 7. 依据与定位

- 当前实现：`packages/contracts/src/resagent2_contracts/models.py`；`packages/orchestrator/src/resagent2_orchestrator/compiler.py`、`scheduler.py`；`packages/capabilities/src/resagent2_capabilities/permissions.py`；`packages/components/src/resagent2_components/materials.py`；`apps/cli/src/resagent2_cli/main.py`、`shell.py`、`render.py`；三个 Agent 的上下文和共享循环。
- 既有设计：[设计原则](../../current/DESIGN_PRINCIPLES.md)、[上下文规范](../../current/CONTEXT.md)、[契约](../../current/CONTRACTS.md)。
- 本地只读调查：`E:\dshWorkSpace\implementation\unified-tool-context\server_followup\` 下的 `DENIAL_VISIBILITY_RECHECK_2026-10-12.md`、`QA_AND_PERMISSION_RECHECK_2026-10-12.md`、`L3_CALL_CHAIN_RECHECK_2026-10-12.md` 和 `EXTERNAL_PERMISSION_PRACTICES_2026-10-12.md`。聚焦调用字段仅用于调查，不将完整私有 trace 或 Session 纳入本记录。
- [Claude Code 权限评论](https://code.claude.com/docs/en/permissions#add-a-comment-when-you-answer-a-permission-prompt)与[业务自由输入](https://code.claude.com/docs/en/tools-reference#askuserquestion-tool-behavior)说明；公开 SDK 固定提交 `b6e9d12fe1cc98dde988ab7b7713c1feeee50c6c` 的 [PermissionResultDeny](https://github.com/anthropics/claude-agent-sdk-python/blob/b6e9d12fe1cc98dde988ab7b7713c1feeee50c6c/src/claude_agent_sdk/types.py#L268) 及[实际消息序列化](https://github.com/anthropics/claude-agent-sdk-python/blob/b6e9d12fe1cc98dde988ab7b7713c1feeee50c6c/src/claude_agent_sdk/_internal/query.py#L631)。依据支持独立拒绝说明通道，不代表要照搬其全部行为。
- [Codex App Server](https://developers.openai.com/codex/app-server/)官方协议的批准与用户输入接口作为比较材料；不能据协议表声称所有前端提供同一拒绝评论入口。
