# 代码审查：统一上下文分支（2026-10-08）

## 范围与证据

本次审查针对 `feat/unified-context` 的 `3341536d7f65a6d88f57aba3a3d588b1a9362469`（schema 24.0）。检查了 Runtime、Components、Orchestrator、三个 Agent、公共契约、当前架构与上下文文档，以及上下文构造、工件登记与读取、网页搜索/抓取、文献工具、工具调用、跨 Agent 交接、预算和恢复路径。对发现的边界用例做了离线复现，并检查已有测试和历史审查记录。

审查阶段本地回归为 **2014 passed / 1 skipped**（51.91 秒），mock E2E 为 `run_golden completed`、13 个工件。服务器上的 schema 24 L3 只证明当时那一次配置和模型轨迹，不能关闭确定性代码问题，也不能推出一般科研质量。审查时没有修改产品代码；用户随后授权修复，实施与验证见文末。

确认 **4 处 P2 和 1 处 P3**，未确认 P0/P1。P2 表示特定输入或运行条件下影响正确性/可靠性的缺陷；P3 是维护问题。审查覆盖主生产路径，并不构成对全部输入和部署环境的形式化证明。

## 已确认问题

### P2：上下文截断在特定预算下会超出上限

位置：`packages/runtime/src/resagent2_runtime/context.py:149-159`。

当 `max_chars` 比截断标记只多 1 个字符时，`tail` 会变成 0，而 Python 的 `text[-0:]` 等于整段文本。例如，100 字符输入在 22 字符预算下返回 122 字符。`recent_tool_snippets` 也使用这个函数，并动态分配剩余预算。用最新 978 字符和较早 100000 字符的两个读取回执复现时，1000 字符总预算产生了 101000 字符正文。材料投影因此会超过 Composer 预留空间，并破坏其二分分配所需的单调性；最终请求仍有整体计量，不能据此断言一定会发送超限请求，但可能造成错误分配或额外的 `ContextBudgetExceeded`。

最小修复是 `tail == 0` 时拼接空字符串，例如 `text[-tail:] if tail else ""`。补充标记长度附近和普通预算的边界测试，验证 `len(result) <= max_chars`，并验证未超限时原文保持不变。现有常规截断测试没有覆盖这个 Python 切片边界。

### P2：可选 trace 写入失败会覆盖模型结果或原始错误

位置：`packages/runtime/src/resagent2_runtime/llm.py:627-632`、`packages/runtime/src/resagent2_runtime/model_request.py:231-251`，以及 `loop.py:461-465` 的 `record_validation` 调用。

两个模型客户端都在 `finally` 中写可选 JSONL trace，但没有隔离写入异常。开启 trace 时，trace 目录不可写、磁盘满或目标路径是普通文件，都可能让成功的模型响应变成 `OSError`；原请求失败时，trace 异常也可能覆盖分类后的供应商错误。`record_validation` 使用同一写入路径，也可能打断无效 action 的纠正流程。架构文档将 trace 定义为可选诊断信息；现有测试没有覆盖诊断存储失败。

离线使用生产客户端和 mock HTTP 已复现：成功请求仍记为 `succeeded`、用量只计一次，但调用方收到 `TRACE_DISK_FULL`；HTTP 400/429 的原错误被 trace 异常覆盖，只留在异常上下文中。没有 monkeypatch 的另一探针把 trace 目录指向已存在的普通文件，同样以 `FileExistsError` 覆盖了成功响应。

最小修复是在可选 trace 的序列化/写盘边界隔离错误，发出有界诊断告警，保留模型响应或原始请求错误。应覆盖成功、请求失败和 validation recovery 三个路径。只隔离诊断存储错误，不能吞业务错误或权威状态保存失败，也不需要增加日志框架。

### P2：网页提取会破坏代码和其他预格式化内容

位置：`packages/components/src/resagent2_components/web.py:218-230`。

`_HTMLTextParser.handle_data` 对所有文本节点都执行 `" ".join(data.split())`。因此 `<pre>` 中的缩进、换行和重复空格会在网页以 `web_page` 工件保存前被压平。离线复现：`<pre><code>for x in xs:\n    print(x)\n</code></pre>` 被提取为 `for x in xs: print(x)`。这会丢掉多行程序和 YAML 等材料中有意义的结构，模型无法从已保存的文本工件恢复原文。

最小修复留在解析器内部：跟踪当前是否处于 `<pre>`（包括嵌套 `<code>`），在其中保留文本和块边界，普通说明文字继续使用现有空白规范化。补充嵌套 pre/code、多个 data 节点、缩进、空行和普通 inline 文本测试。现有测试检查链接和一般文本，没有覆盖预格式化材料；不需要把提取器扩展成浏览器布局引擎。

### P2：Scheduler 记录空异常消息时会再次失败

位置：`packages/orchestrator/src/resagent2_orchestrator/scheduler.py:192-200`、`245-247`、`272-282`。

多个失败路径直接把 `str(error)` 放进要求非空的 `ModuleError.message`。合法 ModulePort 抛出 `RuntimeError()` 时，错误转换本身会触发 Pydantic 校验错误，持久化的 Task/Attempt 仍可能是 `running`，没有结构化的原始失败原因。请求构造和工件接收边界也有相同问题。Controller 已使用正确的现成写法：`str(error) or type(error).__name__`。

用生产 Scheduler、合法 Run/Workflow 和临时存储已复现上述状态。最小修复是在对应边界沿用这个 fallback；为请求构造、invoke 和登记失败补充空消息异常测试，确认 Attempt/Task 正确失败、错误消息非空。现有带消息的失败测试未覆盖此情况。不需要新增异常层级或全局兜底流程。

### P3：系统工件登记中有不可达的文献分支

位置：`packages/orchestrator/src/resagent2_orchestrator/artifacts.py:320-327`。

方法先拒绝所有不在 `SYSTEM_ARTIFACT_KINDS` 中的类型，而 `literature_search` 属于 Scientific 工件类型，后面的同名分支永远不可达。它目前不改变结果，但会误导维护者理解合法登记路径和类型归属。

直接删除死分支即可；不增加登记入口或兼容处理。沿用现有类型测试验证 Scientific 文献登记被接受、系统登记拒绝它；若缺失再补相应断言。

## 设计债务与待讨论取舍

以下项目是维护或性能风险，但本次没有确认违反现有契约，不与上面的 bug 混在一起：

- **工件读取先载入全文，再返回窗口。** `packages/components/src/resagent2_components/artifacts.py:144-170` 的 `read_text` 先读取完整字节串，再检查 hash、严格 UTF-8 解码和切片。128000 字符的返回上限不意味着 IO/内存有界。当前契约未承诺流式读取，`verify()` 已采用有界内存 hash。只有实际大文件压力证明需要时，再改读取方式；必须同时保留完整 hash、严格解码、字符/行窗口和损坏错误语义。
- **DeepSeek 对畸形原生条目整批失败。** `web_deepseek.py:70-127` 的实现与当前明确契约一致。若要保留有效 URL 并返回 partial，应先决定新错误语义，再改代码和测试。本次没有确认一个新的真实畸形条目事件。历史报告中的三次所谓畸形失败，已复核为列表形式的 `max_uses_exceeded`，并在此前修复；不能把旧误称再次当成未修 bug。
- **三个 `_retry_after` 的语义不完全相同。** Runtime 模型请求保留供应商 header 字符串，网页和文献组件解析为各自错误字段所需的秒数。当前不是可以直接合并的同一功能两套逻辑。只有出现明确的语义一致重复时才提取共享函数，不创建通用供应商框架。
- **两个模型客户端有相似的 trace 写盘代码。** 可以共享小型诊断写入函数，但原生 function call 与独立模型请求的协议和错误分类有区别。修 trace 不意味着需要合并整个客户端或另建 transport 层。

## 已核对的架构与误报排除

本次没有确认需要重做核心架构的逻辑断链，也没有确认已删除上下文协议在生产源码中保留第二条主线。以下结论是审查范围内的证据，不是对全部可能输入的证明：

- 原生 assistant/tool 调用在参数校验失败时仍成对保存，拒绝回执让 loop 可以继续；不因模型输出非法参数就破坏协议。
- 四部分上下文、完整 `artifact_index`、作用域隔离和压缩符合当前设计。压缩只改变发送的历史投影，原始工具事件保留，目录从登记事实重建；旧 schema 23 Session 按版本拒绝恢复。
- `Run.artifacts` 是工件登记权威，research index 和 Session 目录是导航视图。源码中没有旧 `research_materials`、`literature_output_artifact_ids` 或 `web_output_artifact_ids` 主线。Controller 管授权，Registry 按传入授权检查来源并冻结内容，目录不授予权限。
- Scheduler 执行任务后保留 Run 为 `RUNNING`、由 Controller 收口，是既有职责分工。Scientific invoke 异常传播进入检查点恢复也有明确设计，不能一概改成吞异常并返回成功。
- Scientific 恢复的幂等身份在生产 Controller 路径包含新的回答或工作反馈身份，本次没有复现答案串线。重复消费、Task/Attempt/Session 作用域和暂停后的授权检查仍应由行为测试约束。
- 三个 Agent、Compiler、独立搜索请求共用 Run 预算。逐请求预扣、失败与 unknown 消耗不退款、客户端自管计量避免双扣，均有行为测试；本次 trace 复现也没有双扣。
- Agent 完成检查、Registry 登记冻结和 Run 最终 gate 检查的是不同事实。相似字段校验不等于重复控制流程：应共享具体事实辅助函数，保留不同职责边界。
- Task 工件槽位身份与外部导入身份约定不同。前者记录实际冻结字节，后者身份含源 hash 并校验 expected hash，不能只因代码形状不同就判为身份错误。
- `build/lib` 的旧文件是生成产物，不是第二条生产实现。可疑重复必须检查入口、import 和消费者，而不是只按同名文件计数。

## 沉淀的通用原则

已将以下经验补入 [DESIGN_PRINCIPLES](../../current/DESIGN_PRINCIPLES.md#5-审查中沉淀的通用规则)。这些规则可用于其他使用工具和持久状态的 Agent 项目，但不要求复制本项目的包或类：

1. **一份权威事实，多种可重建视图。** 登记、状态、授权各有明确所有者；目录、摘要和提示词不能维护另一份可独立修改的权威状态。
2. **协议历史与材料目录各司其职。** 配对历史维持 function call 协议，完整目录维持可发现性，显式读取提供正文；三者不能相互冒充。
3. **测试承诺的实际性质。** “有界”要测实际长度，“完整”要测集合覆盖，“恢复”要测持久状态和身份；不能只检查配置字段。
4. **可选诊断不改变业务结果。** 日志/trace 可以失败并发告警，权威状态保存失败则不能假装成功；两者不能共用不分职责的兜底策略。
5. **错误路径也遵守输出契约。** 先把任意异常转换成合法错误消息，再保存正确失败状态；正常失败和进程中断应区分。
6. **验证分层，修复局部。** 多层检查不同事实是合理的；同一事实规则复用现成 helper，死代码直接删除，不为个别边界问题增加管理器、兼容层或新业务模式。
7. **分清资料、访问记录与科学证据。** 文件已登记或读过，不等于支持结论；模型判断含义，代码保存可核对的事实。
8. **验证结果只覆盖实际测过的范围。** 离线边界、mock 组合和真实研究质量样本互补，不用一次 L3 成功覆盖确定性 bug。

## 验证记录与后续处理

| 验证 | 本轮结果 | 范围 |
| --- | --- | --- |
| `python -m pytest tests apps/cli/tests -q` | 2014 passed / 1 skipped，51.91 秒 | 当前既有回归；尚未包含此次发现的全部边界 |
| `python -m e2e.mock_e2e` | `run_golden completed`，13 工件 | 固定响应下的公开入口组合 |
| 生产代码离线探针 | 复现四处 P2，源码证明一处 P3 不可达 | 未付费、未修改产品源码 |
| 本地 `pip check` | `pdfminer-six 20260107 requires cryptography, which is not installed` | 既有本地依赖差异，未安装或修改环境；不改写服务器此前的 clean 结果 |
| 真实模型/GPU/L3 | 本轮未重跑 | 只参考已保存的 schema 24 验收，不声称本轮重新执行 |

建议先以小范围修改修复四处 P2，顺手删除已确认的死分支，并补上各自的契约/状态边界测试。然后运行受影响专项、全量离线回归和 mock E2E。若上下文投影或网页呈现仍有模型使用质量疑问，再安排定向真实验收；不必机械重跑整套长 L3，也不用付费模型证明切片或错误消息正确。

流式读取、provider 畸形条目 partial 和更大范围客户端抽象单独评估，不作为这五处局部修复的前提。审查完成时只修改审查与原则文档，四处 P2 和一处 P3 尚未修复。后续状态见以下实施记录。

## 修复实施（2026-10-08）

按用户授权继续在 `feat/unified-context` 修复上述五项，schema 保持 **24.0**。修改沿用既有职责和错误流程：

- `_head_tail` 在尾部长度为 0 时不拼接尾部。新增公共 `recent_tool_snippets` 边界测试，覆盖剩余 0/1/20/21/22/23/24/100 字符、最新片段完整保留和原始事件不变。测试在修复前复现 1000→101000，修复后正文总量严格为 1000。
- 两个模型客户端共用 25 行的私有 `_trace.py` 写入函数，只隔离 JSONL 序列化/写盘的 `OSError`、`TypeError`、`ValueError`；告警只含固定文字与异常类型。成功结果、原请求失败和 action 校验恢复都有测试，权威用量保存失败仍传播。未合并模型协议、未增加重试或新控制状态。
- 网页解析器在 `<pre>` 范围缓冲并直接拼接文本，保留缩进、换行、空行、多 data 节点及嵌套 code/span；链接继续按既有格式保留地址。移除 HTML 正文末端的全局 `.strip()`，避免再次丢掉首行缩进和尾换行；补齐自闭合 br、全空白链接标签的精确空白测试。普通文字继续规范化。
- Scheduler 的三处错误边界使用 `str(error).strip() or type(error).__name__`。`NonEmptyStr` 会去除首尾空白，因此同时修正 Controller 的三处相同转换，覆盖空字符串、纯空白和普通异常消息。测试重新打开 `JsonRunStore` 核对失败状态、原始错误、依赖阻塞和 WorkRequest 状态；Scientific invoke 的中断传播机制保持原职责。
- 删除系统登记中不可达的文献特判，沿用唯一类型检查。测试确认被拒路径没有创建冻结工件。

当前契约已补充 trace 写入的诊断边界和 HTML 预格式化规则，设计原则保留本次经验。流式读取、供应商畸形条目策略等待讨论项目没有纳入此次修复。

最终本地验证：**2062 passed / 1 skipped**（55.74 秒），mock `run_golden completed`、13 工件，`git diff --check` 干净。新增 48 个参数化用例覆盖此次边界。产品逻辑与公共 schema 未新增兼容层、业务模式或 Agent。本轮没有执行真实模型、GPU 或服务器 L3，也没有修改已有依赖环境。

### 服务器复核建议

检出 `feat/unified-context` 最新修复提交并冻结实际 SHA，先核对 editable 包均来自源码树，然后从仓库根执行：

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

预期回归 2062 passed / 1 skipped、mock completed/13 工件。四处 P2 的决定性证据已在上述回归中：上下文片段总长度不越界；坏 trace 存储不改变成功/原错误/参数纠正，权威用量保存失败仍抛出；网页代码的空白精确保留；空白异常被转换并持久保存失败状态。不可达登记路径的删除也有类型边界测试。

若要补充真实材料消费，只需一个短网页任务：让 Scientific 阅读官方文档中的多行代码示例，检查冻结 `web_page` 中换行/缩进与读取内容一致。模型自主选择工具，不预设工具顺序。完整 L3 不作为这些确定性修复的门槛，服务器此前的 L3 成功也不能倒写为此修复提交的验收。

上述五项已完成本地修复与验证，保留当前分支，**不合并 main**。服务器复核仍待执行。
