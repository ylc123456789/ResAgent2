# ResAgent2 Validation 修改方案

状态：阶段 0 已完成源码盘点；阶段 1 本地与服务器验收完成，原始证据已独立复核通过。阶段 2 源码已实现，schema 17.0，等待服务器验收；阶段 3 尚未实现。本轮未运行任何测试。

本文定义 Validation 的职责、边界、分阶段改动和验收方式。2026-09-25 开始按阶段实施；各阶段的代码与测试结果记录在本文末尾。保持 Agent 调用模式和 Scientific、Compiler、Interpreter、Scheduler、子 Agent 的职责。

## 1. 目标

Validation 的目标是确认系统能否安全、真实、可追溯地接受一个任务结果。

Validation 不负责证明科研结论正确，也不负责替 Agent 或用户解决问题。

固定代码只检查能够直接确认的事实：

- 数据结构和协议是否正确；
- Run、Task、Attempt、Session 和 Artifact 是否对应；
- 路径、权限、工作区和哈希是否正确；
- 执行记录是否真实完整；
- 状态转换是否合法；
- 用户或 Compiler 明确声明的产物要求是否满足。

以下内容暂不由固定 Validation 判断：

- Coding 是否真正解决了自然语言问题；
- Experiment 的实验设计是否合理；
- 结果是否足以支持科学结论；
- Scientific 的判断是否客观正确；
- 报告是否对复杂证据作出了正确的专业解释。

这些内容由 Agent 基于实际证据判断，并通过报告、Artifact 引用和限制说明表达。

## 2. 编译器类比

ResAgent2 中的 Agent 任务可以按下面的流程理解：

```text
自然语言任务
    |
    v
Scientific / Compiler 翻译
    |
    v
WorkRequest 和可执行任务
    |
    v
运行前检查
    |
    v
Agent 执行
    |
    v
产物登记
    |
    v
运行后检查
    |
    v
接受结果或报告问题
```

运行前 Validation 类似编译器的语法、类型和链接检查。发现任务协议、依赖或权限错误时，不启动本次 Agent 执行。

运行后 Validation 类似执行后的结果检查。Agent 可能已经运行，但如果结果不符合系统协议，就不能把它标记为合法完成。

这个类比不表示固定代码能够验证科学语义。固定代码只验证协议、来源、状态和可观察事实。

## 3. 现有架构中的职责

| 模块 | Validation 相关职责 |
| --- | --- |
| Scientific | 负责科研目标、任务规划和科学判断 |
| Compiler | 把 Scientific 已提出的 WorkRequest 翻译成可执行任务；保留原目标和约束 |
| Coding / Experiment | 执行任务并提交结果与候选产物 |
| ArtifactRegistry | 登记产物，检查归属、路径、哈希和可读性 |
| Runtime | 执行权限、工作区、预算和工具调用守卫 |
| Scheduler / Controller | 调用 Validation，推进、恢复、暂停或终止流程 |
| Interpreter | 整理执行结果和证据，不负责替代 Validation |

不新增每个 Agent 自己的 Validator，也不新增第二种 Agent 调用模式。所有 Agent 仍然使用统一的自然语言任务和运行上下文。

## 4. 统一诊断格式

Validation 只需要一种统一的诊断格式。实现时应优先复用现有 Contracts 和错误处理机制，不建立平行异常体系。

建议包含以下字段：

```text
code       必填，机器可识别的稳定错误码
message    必填，给 Agent、用户和日志看的说明
subject    可选，问题涉及的文件、Artifact、Task 或其他对象
refs       可选，相关 Run、Task、Attempt 或 Artifact 引用
```

`subject` 不是必填字段。文件不存在时可以写 `metrics.json`，但状态转换错误可能没有单一主体，此时省略 `subject`，使用 `refs` 说明范围。

Validation 本身不返回 `retryable`、`fatal` 等调度策略。是否恢复、重试、暂停或终止由 Scheduler 根据现有流程和错误码决定，避免把流程策略写进公共检查器。

空诊断表示通过。一个结果可以包含多个诊断，但每个诊断应说明一个具体问题。

## 5. 明确要求的最小实现（阶段 2，源码已实现、待服务器验收）

`ConclusionRequirements` 是 Controller 创建的 **Run 级最终要求**。schema 17.0 在它与 `ResearchRequest` 中增加 `required_artifacts: list[OutputName]`，默认空；CLI `run` 与交互 shell `/run` 共用可重复的 `--required-artifact NAME`。Controller 创建 Run 时冻结要求，不从 goal、context、constraints 或文件名推断要求。

要求仅精确、区分大小写地匹配同一 Run 已登记 `ArtifactRef.output_name`。`OutputName` 为 1–128 个 ASCII 字母、数字、下划线、点或连字符，以字母开头；它不是路径、文件名、kind 或 metadata。例如要求 `metrics.json` 时，磁盘同名文件或 `output_name="Metrics.json"` 都不满足；必须登记 `output_name="metrics.json"`。重复要求只表达一次存在要求；不同登记产物可以同名，所有匹配文件都须通过冻结 hash 校验。

Scientific 的 CompletionCheck 与 Registry 复用 Components 的 `missing_required_artifacts`，通过 `RegisteredArtifactReader` 检查授权、Run 归属和冻结字节，不绕过登记表扫描工作区。只有存在要求，不新增观察或引用要求；原 `required_evidence_kinds` 的观察与引用规则独立生效。机器存在检查不写入 Scientific 的观察记录。

Scientific 缺失交付时沿已有 runtime_feedback 继续同一 Session，可 request_work 补交或 ask_user。其自己的合法命名 finish 候选可以先提议，接收端必须实际登记并独立复验，最终 gate 才认可交付。损坏的登记工件保持原拒绝路径，不转换成可接受结果或普通缺失。

要求不进入 WorkRequest/Task 字段，不代替已有 `TaskAcceptanceSpec`。Scientific 负责在工作目标或约束中保留用户明确的名称，Compiler 保持原目标和约束，执行 Agent 按该 output_name 提交；固定代码不解析自然语言，统一 Agent 入口仍为 `instruction + input_artifacts`。

## 6. 分阶段修改

### 阶段 0：梳理现有检查

目标是确认当前行为和调用边界，不改变运行结果。

工作内容：

1. 列出 Contracts、Runtime、ArtifactRegistry、Scheduler、Controller 和各完成入口中的现有检查。
2. 标记每个检查属于运行前守卫、运行后检查、Artifact 事实检查或业务语义判断。
3. 找出重复实现、不同入口行为不一致和没有测试覆盖的检查。
4. 为关键现有行为补确定性测试。
5. 更新项目内 Validation 设计说明，保持术语和错误边界一致。

这一阶段不把所有代码搬进一个大 Validator，也不为了统一命名而大范围重构。

### 阶段 1：Agent 完成后的固定 Validation

实际已有两道不同职责的完成边界，无须新增入口：

- 原生 Agent 的 `AgentLoop → CompletionCheck.evaluate`：在 Session 完成前检查候选输出。可修正的提交错误通过已有 `CompletionDecision` 返回，原 Session 继续。
- 接收端 `receive_artifacts / check_acceptance / ScientificCompletionValidator`：验证公开结果、冻结工件、检查最终状态。仍独立检查可替换 ModulePort，不能信任模型或前一道检查已经通过。

统一指的是相同事实使用同一规则，不是把不同层的职责塞进一个函数。优先提取 ArtifactRegistry 已有的文件来源解析到 Components，由 Agent 完成检查与登记共同使用。Runtime 不反向导入 Components。

第一版只检查：

- 返回结果结构；
- Run、Task、Attempt、Session 的一致性；
- 候选 Artifact 是否存在；
- Artifact 是否属于当前 Run；
- 路径是否在授权工作区；
- 哈希是否能验证；
- 报告引用是否指向已登记 Artifact；
- 完成状态转换是否合法。

Artifact 的登记、哈希和读取继续由 ArtifactRegistry 负责，完成 Validation 只调用公共接口，不复制一套文件系统逻辑。

处理顺序：

```text
LLM 提交 finish 候选
    → CompletionCheck 检查
    → 可修正错误：runtime_feedback，原 Session 继续
    → 通过：返回统一 AgentResult
    → 接收端校验、登记、检查已有明确要求
    → 通过后推进 Task / Run 完成状态
```

两道边界间文件仍可能变化，所以登记层继续检查。接收端拒绝时保留已有证据并沿既有错误路径处理，不为了纠错另开一个 Agent 调用模式。

检查失败时保留 Attempt、trace、失败产物和诊断，不把任务标记为合法完成。恢复、重试、暂停和终止继续使用现有 Scheduler 机制。

### 阶段 2：`required_artifacts` 检查

在 Run 级最终完成边界接入 `conclusion_requirements.required_artifacts` 的明确存在要求。源码已按 §5 实现，schema 升为 17.0；本轮验收尚未执行。

检查必须通过 ArtifactRegistry 查询同一 Run 的登记结果，不直接绕过登记表访问文件系统。这样可以保证要求、索引、登记表和实际读取范围一致。

缺少要求产物时返回：

```text
code: required_artifact_missing
message: required artifact was not produced
subject: metrics.json
```

该结果不能把 Run 标记为完成，但不删除已经产生的失败材料。Agent 或用户负责决定补交产物、修改任务或结束任务。

这一阶段只验证产物存在和可登记，不验证产物内容的科学含义。

### 阶段 3：新的运行前 Validation

完成后的检查稳定后，再补充统一的运行前检查：

- WorkRequest 结构是否可执行；
- 明确要求的输入 Artifact 是否存在；
- Task、Attempt、Session 和 Run 是否一致；
- 权限、工作区和资源范围是否允许；
- 任务依赖是否满足；
- 工具和参数是否符合协议。

运行前检查失败时，不启动本次 Agent 执行，只返回诊断。现有权限、预算、路径和命令确认守卫在所有阶段继续生效，不等待本阶段才启用。

## 7. 失败处理边界

Validation 发现问题后只报告问题，不替 Agent 或用户解决问题。

需要区分以下情况：

- 命令真实执行但返回非零退出码：这是一次真实失败执行，应保留记录；
- 缺少明确要求的产物：结果不能完成，返回可处理诊断；
- Artifact 归属错误、路径越权、哈希不匹配或身份错误：结果不能接受，保留现场并使用现有致命错误路径；
- 科学结论可能不充分：不由固定 Validation 判定，由 Scientific 在报告中表达 `supports`、`refutes` 或 `inconclusive`。

不要为每种失败新增一个 Run 状态。优先复用现有的错误、暂停、恢复和终止机制。

## 8. 不应做的修改

本方案明确不做以下事情：

- 不增加 LLM Validator 作为当前必经步骤；
- 不让固定代码判断科研结论是否正确；
- 不为 Coding、Experiment、Scientific 分别实现三套 Validation；
- 不让 Validation 直接修改 Run、Artifact 或用户答案；
- 不让 Validation 自己决定重试策略；
- 不把自然语言要求复制成另一套 Agent 输入协议；
- 不保留新旧两套检查路径作为兼容层；
- 不为了验证而放宽权限、预算或批准消费规则。

## 9. 测试计划

### 阶段 0 测试

- 现有完成路径行为保持不变；
- 现有权限、预算、批准和恢复测试继续通过；
- 各检查入口和错误处理路径有确定性覆盖。

### 阶段 1 测试

- 合法结果通过；
- 结构错误被拒绝；
- Task、Attempt、Session 或 Run 归属错误被拒绝；
- Artifact 缺失、越权、哈希变化和错误引用被拒绝；
- 非零退出码的真实失败记录被保留；
- Validation 失败时不会错误推进完成状态。

### 阶段 2 测试

- CLI 与 shell 显式输入、Contracts 往返和 Controller 冻结一致；schema 16 及更早 Run 拒绝恢复且原记录保留；
- 精确 output_name 已登记时通过，缺失或仅文件名相同则返回明确诊断；大小写、重复要求与同名多登记规则一致；
- Scientific 缺失时收到 runtime_feedback，在同一 Run/Session 内请求工作、补交并完成，预算和身份不重置；
- 原生候选先提议、接收端实际登记；可替换 ModulePort 不能绕过最终 gate；
- 要求只匹配同一 Run 的授权登记产物，损坏的冻结内容被拒绝；
- 产物登记表、索引和读取入口保持一致；存在检查不增加观察记录；
- 不要求时不额外推断文件名，原 evidence kind 的观察/引用规则独立通过回归。

本轮全部测试在服务器由专门测试 AI 执行，包括全量 pytest、mock、真实 CLI 整链及 Scientific 缺失反馈定向探针。

### 阶段 3 测试

- 运行前非法任务不会启动 Agent 工具；
- 缺少输入 Artifact 时不会创建真实执行记录；
- 权限、工作区和预算守卫仍然有效；
- 合法任务的调用模式和状态流程不变。

每个阶段先跑确定性测试和 mock E2E，再按改动范围选择真实模型定向探针或整链，具体以该阶段验收计划为准。阶段 1 采用两个真实 Native Agent 的固定任务探针，不要求 Scientific 自规划整链。GPU 测试只在公共调度或资源检查代码实际改动后加入，不把 GPU 训练作为纯协议改动的必要前置条件。

## 10. 分段提交建议

按以下顺序提交，保持每次提交可独立审查：

1. `document validation boundaries and characterize current behavior`
2. `add shared post-completion validation`
3. `validate required artifacts from conclusion requirements`
4. `add preflight task validation`

每次提交都删除被替代的重复路径，不保留旧接口兼容层。全部阶段通过回归和服务器验收后，再考虑合并目标分支。

## 11. 完成标准

本方案完成后，系统应满足：

- 每个 Agent 仍只有一种调用和工作模式；
- 所有 Agent 继续使用已有 CompletionCheck；共享事实检查只有一份实现，接收端按职责重新校验；
- Validation 只验证协议、事实、归属、状态和明确要求；
- `conclusion_requirements` 只增加最小的产物存在检查；
- 运行前和运行后错误都能被清楚报告；
- Agent 或用户负责解决问题，Validation 不替代业务判断；
- 失败证据、产物索引、登记表和读取权限保持一致；
- 不出现重复 Validator、兼容层或同一功能的多套逻辑。

## 12. 阶段 0 源码盘点（2026-09-25）

基线：`fix/code-health@8159404`，schema 16.0。

| 现有检查 | 所在代码 | 保留职责 |
| --- | --- | --- |
| 请求、结果、状态字段组合 | contracts/models.py | 结构和跨字段一致性 |
| 工具参数、预算、批准、反馈、Session | runtime/loop.py 与共享权限组件 | 原执行与恢复守卫 |
| Coding / Experiment / Scientific 完成判断 | agents/*/completion.py | 已有 CompletionCheck，分别读取专业事实 |
| 候选文件来源、冻结和哈希 | orchestrator/artifacts.py | 工件登记权威 |
| 结果归属、部分工件保留、明确 Task 要求 | orchestrator/handoffs.py、scheduler.py | 接收检查，通过后再完成 Task |
| 最终观点、引用、任务终态 | orchestrator/completion.py、controller.py | Run 最终完成，不能证明语义正确 |

实际发现：

1. Experiment 对缺失候选文件直接抛异常，AgentLoop 因而终止 Session，绕过已有可修正反馈。
2. Coding 不检查候选文件，类似错误直到登记阶段才结束 Attempt。
3. Experiment 和 Registry 各自解析候选文件，规则存在两处实现。
4. Scientific 已有可修正反馈；输出名唯一性可与任务 Agent 共享。
5. Experiment 返回失败执行记录时，如果错误候选排在前面，增量登记可能先失败，尚未保存执行记录。应先交付真实执行记录，保留原错误。
6. 原文把 conclusion requirements 当作 Task 字段不准确；已按实际 Run 级绑定修正，阶段 1 不扩展要求接口。

阶段 1 范围：共享候选文件和输出名事实检查、接回现有反馈、保留失败执行记录。已有 acceptance policy、科学观点检查、运行前守卫保持各自职责。先完成本地与服务器定向验收，再推进后续要求和运行前阶段。


## 13. 阶段 1 实施结果（2026-09-25）

- Components 新增普通候选文件解析函数，直接提取 Registry 原规则；Registry 与 Coding/Experiment 完成检查共同使用。
- 三个 Agent 共用输出名唯一性检查。局部候选错误使用内部 ArtifactCandidateError（code、message、可选 subject），沿现有 CompletionDecision/report 反馈，无新对外 schema。
- 删除 Experiment 自有的候选路径分支；Coding 接入同样检查并接收现有 output_dir。
- Experiment 的真实执行记录先于模型候选登记，避免后续缺文件时丢失已发生的失败执行。
- 未新增 Agent 模式、循环、Validator 服务或兼容层；未修改 Scheduler/Controller 状态机、TaskAcceptanceSpec 或 ConclusionRequirements。
- 已同步 ARCHITECTURE、CONTRACTS、CONTEXT、DESIGN_PRINCIPLES。schema 保持 16.0。
- 服务器定向测试见 [阶段 1 复测说明](VALIDATION_PHASE1_TEST_2026-09-25.md)。阶段 2、3 尚未实现。

本地验证：最终全量 `python -m pytest tests apps/cli/tests -q` 为 **1291 passed / 1 skipped**；`python -m e2e.mock_e2e` 为 completed、13 工件、final_report 已登记；`git diff --check` 通过。新增原生反馈链测试 10 项、执行记录落盘测试 1 项，原有缺文件测试改为核对拒绝和具体诊断。独立 diff 审查未发现阻断问题。未运行真实模型或服务器/GPU，本结果不代替服务器验收。


## 14. 阶段 1 验收收尾（2026-09-25）

测试方报告 `8fc0e79` 服务器全量 **1291 passed / 1 skipped**、mock completed（13 工件）、两个真实 Native Agent 的完成候选反馈探针 **40/40 PASS**，各 3 次请求。两条均真实经历错名提交、收到反馈、同 Task/Attempt/Session 纠正并登记原件；零命令、文件未变、逐条计量一致。

报告与阶段 1 计划范围一致。首次收尾因 SSH 认证失败仅核对了报告；随后使用用户指定身份连接成功，已独立读取原始 trace、Session、脚本、账本与冻结工件，并补核事件顺序、计量唯一性和 Git 初始字节。原始证据支持通过，阶段 1 收尾，无新增必补测试。验证器的弱断言及离线补核见 [独立复核](VALIDATION_PHASE1_TEST_2026-09-25.md#independent-review)。

后续阶段保持原边界：阶段 2 只做 Run 级 conclusion_requirements 的明确产物存在检查，先落实精确匹配语义与输入来源，再同步生产者、消费者及 schema；阶段 3 再整理新的运行前检查。任务入口、LLM 的语义职责和已有权限/预算/恢复流程不因此改变。


## 15. 阶段 2 实施与待验收（2026-09-25）

- `ResearchRequest` 和 `ConclusionRequirements` 新增默认空的 `required_artifacts: list[OutputName]`，Controller 冻结要求；CLI/shell 使用同源的可重复参数。
- Components 提供共享授权登记输出查询，Scientific CompletionCheck 与 Registry 复用；最终 gate 经 Registry 再检查实际交付，缺失诊断含稳定 code/message/subject。
- 精确区分大小写的 output_name 存在要求独立于证据 kind 的观察/引用要求；无要求不从自然语言推断。Scientific 同 Session 反馈可继续请求工作或提问，沿用原预算、授权和身份。
- 当前公共 schema 为 17.0；旧 schema 16 及更早 Run 不支持恢复，不迁移或改写历史状态。§12–14 的 schema 16.0 与阶段 1 验收数值是历史事实，保持原样。
- 已同步现行接口、上下文、架构和 CLI 文档。阶段 3 新的运行前 Validation 尚未实现。

**验证状态：待服务器验收。** 用户指定所有测试在服务器上由专门 AI 执行；本轮本地仅源码/文档编辑与静态审查，没有运行 pytest、mock、真实模型或其他测试。服务器尚未开启，当前地址与 SSH 端口尚未提供；没有本轮测试结果，也没有验收完成结论。工具 schema 指纹须在服务器受控生成、比较并审查，不能以自动刷新基线或放宽断言掩盖差异。
