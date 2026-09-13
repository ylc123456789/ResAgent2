# 上下文现状审查与候选修改清单

日期：2026-09-13。产品基线：`fix/semantic-handoffs @ f2d4421b3a1cf94812b1da36aaf43488c225096c`。

**状态：仅完成文档与分析，等待用户阅读后决定。本文不是已批准的开发计划，不代表以下候选都要实施。** 本轮不改产品代码、prompt、schema、预算、CLI/E2E 装配，不提交合并或推送，不发起真实模型测试。

当前机制的完整参考见 [CONTEXT](../../current/CONTEXT.md)。接口字段仍由 [CONTRACTS](../../current/CONTRACTS.md) 定义。

<a id="summary"></a>

## 1. 先读这一页：现在到底缺什么

不是从“优化字段”跳到一套无关的新架构，而是同一条交接链需要查完整：

1. 模块有没有产出需要的信息？
2. 信息有没有通过接口/工件到达接收方？
3. 接收方怎样让模型看到并理解它？
4. 最终行为和真实结果是否对应？

目前很多信息已经保存在第 1、2 步；缺口主要在第 3 步。不能因为模型没用上，就删字段；也不必把所有结构化数据原样交给模型。

| 共同原因 | 观察项 | 直观问题 | 当前结论 |
|---|---|---|---|
| A. 含义或使用规则不够一致 | [C1 状态与指令](#c1) | “验证后是否修改”看起来像“是否改过代码”；验证用法和允许命令有落差 | 可由代码确认，候选为就地修正表达/派生关系 |
| A. 含义或使用规则不够一致 | [C2 目录时效](#c2) | 旧目录清单看起来像当前目录 | 当前独立目录段确实没有事件号，复用现有时序是候选 |
| A. 含义或使用规则不够一致 | [C4 风险说明的用途](#c4) | 知道报告存在，却认为“不是测量”所以不用看 | 本轮未消费已确认；必须阅读哪些报告还需决定 |
| B. 有记录，但决策信息在展示中丢失 | [C3 失败诊断](#c3) | 多命令结果的预览把真正的错误裁掉 | 有原始请求和后续行为证据，不是执行端没保存 stderr |
| B. 有记录，但决策信息在展示中丢失 | [C5 文献翻页](#c5) | 前页已找到论文，读后页后误以为没找到 | 单次轨迹已确认；不能宣称一条 prompt 能根治 |
| C. 材料单位和额度是否合适 | [C6 预算配置](#c6) | 总输入有容量不代表 6000 字符正文组还能保留材料 | 是设计取舍，尚无测量支持新的最佳数值 |

这六项是分析编号，不是六个新组件。JSON 明确搁置，继续沿用现有有界恢复，不混入本清单。

## 2. 证据范围与不能过度推断的地方

- 本地只读核对了三个 Agent 的 builder/装配/完成检查、Scientific interpreter、共享 workspace_context、Loop/Composer/LLM、文献与文件能力、Compiler、相关测试和现有文档。
- 实例依据此前已读的服务器原始 request/response/reasoning、Session 与冻结工件，根目录为 `/root/autodl-tmp/e2e-output-f2d4421-lqdEcp/`；本轮未重新联网运行模型。对应验收要求见 [语义交接验收单](SEMANTIC_HANDOFFS_ACCEPTANCE.md)。下文保留必要 call_id 供定位，不复制完整私有消息或密钥。
- 代码理解答案的 module_report 读取、原题与短回答配对已有真实消费证据，不因另几项缺口否定它们。
- 某处代码早于本轮存在，不等于所有行为差异都已证明与本轮无关；模型轨迹不能只按“哪个包改了文件”归因。
- 工具正常、上下文包含信息、模型遵循、最终任务完成是不同层次。测试驱动直接指定正确行号不能证明模型会自行找回证据。

<a id="c1"></a>

## 3. C1：当前控制状态和操作说明需要一致

**事实。** Coding 的 `workspace_changed` 比较 edit_revision 与 verification_revision，不是本任务是否修改过工作区。实际失败轨迹中模型多次把 false 理解成“没有改动”。同时，`verification_issue` 会说明环境改变后应重新验证，但 `required_next_action` 还可能因为旧失败结果而指向 inspect_and_fix_verification；两个指引不一定表达同一个当前原因。

另外，MODIFY_PROMPT 提到小型 import smoke，模型使用 `python -c` 被既有 VerificationCommandPolicy 拒绝。不能只增加一条鼓励验证的提示，却不说明受支持的调用方式。

**候选，不是已实现：**

- 给模型可见状态使用准确名称/说明，例如明确“验证后是否又发生编辑”，不增加一个第二来源的工作区布尔值。
- 复用现有验证有效性判据，让 issue 与 next_action 对应同一个未满足条件；不另建状态机。
- 调整用法说明与现有白名单一致；如果需要 import 检查，可说明怎样通过现有受支持测试模块完成，不为此扩大执行权限。

**需要的验证。** 同一条确定性轨迹覆盖“修改 → 验证失败 → 安装依赖 → audit → 需要重新验证 → 验证通过”；另测被建议的验证用法确实符合原权限策略。是否执行这些修改，仍待用户批准。

**定位**：[derive_control_state / _verification_status](../../../packages/agents/coding/src/resagent2_coding/completion.py)、[MODIFY_PROMPT](../../../packages/agents/coding/src/resagent2_coding/context.py)、[命令策略](../../../packages/capabilities/src/resagent2_capabilities/process.py)、[控制测试](../../../tests/coding/test_control_state.py)、[验证有效性](../../../tests/coding/test_verification_validity.py)。

<a id="c2"></a>

## 4. C2：旧目录是历史观察，不是当前磁盘事实

**事实。** `directory` 从最近一次 list_files 事件投影出路径清单。当前独立段没有 observed_at；创建新文件后若没再 list_files，仍展示旧列表。在失败轨迹中，旧列表与成功读取新测试文件的结果并存，模型对此产生疑问。

文件片段已经有 observed_at 和内置编辑标记，不能因此认为目录也有同样提示。directory 当前还是可选段，不是 required；本文的配套文档按实际代码区分了两者。

**候选。** 复用已有事件序号，明确它是某次列目录的结果。是否需要额外标注后续写入，应先看“历史观察”的说明是否已足够；不每轮扫描磁盘，不删除旧事件，不维护一份新的目录状态。

**需要的验证。** 列目录后创建文件：旧清单保留，但不冒充实时；再次列目录后使用新的观察。若只改共享呈现，Coding/Experiment 应使用同一实现。

**定位**：[recent_tool_listing](../../../packages/runtime/src/resagent2_runtime/context.py)、[workspace_context](../../../packages/capabilities/src/resagent2_capabilities/workspace_context.py)、[现有时序测试](../../../tests/e2e/test_workspace_read_history.py)。

<a id="c3"></a>

## 5. C3：失败诊断已保存，但通用预览可能裁掉关键原因

**事实。** `reg-codeexp-flash1` 的 `709b6b022f934ea08d1c85061626802e` 执行一批验证，其中 pytest 缺依赖、py_compile 成功。完整结果保存了错误，但下一轮对整批 value 的约 400 字符头尾预览没有保住 `No module named pytest`。模型先反复查文件；到 `1bcacdf9c13140e49958699c35698e59` 单独运行 pytest 后错误变得可见，随后 `0ead660314164b0eab8ae82a5610b0b9` 安装依赖。

这个过程支持“展示丢失影响诊断”的判断，但不能把整场失败归于这一点。后续安装/audit 已成功，模型仍未重新验证；该场还有格式错误和控制语义问题共同消耗预算。

**候选。** 对命令类结果保留有界的失败状态和错误尾部，再处理低价值预览。优先使用已有结构化执行结果，不按 pytest、训练脚本或某个报错文本写特例；不得把多条完整日志全部塞入 required 段。

共享位置要根据真实消费者决定：普通历史选择仍由 Runtime 负责，命令结果的语义投影可以留在可复用能力层。尚未决定新增函数或具体字段，不预先建立“诊断管理器”。

**需要的验证。** 多命令中间失败、首尾成功；多个失败和长日志；无 stderr/超时情况。检查最终构造文本中关键失败可见、总量有界、原始记录未改，不能只断言某个 summary 字符串存在。

**定位**：[Loop 预览](../../../packages/runtime/src/resagent2_runtime/loop.py)、[RunVerificationTool](../../../packages/capabilities/src/resagent2_capabilities/workspace_tools.py)、[预览测试](../../../tests/runtime/test_observation_previews.py)。

<a id="c4"></a>

## 6. C4：风险报告是可选背景，还是判断结果的重要说明？

**事实。** `experiment-risk-deepseek-v4-flash` 已生成并注册风险报告。Scientific 知道其存在，却只读取 metrics。最终调用 `d5513eaccf4b456aae1e802974b5865f` 明确判断无需阅读 module_report，并在 limitations 说明未将其中的风险纳入判断。

因此，不能说“入口没传到”，也不能把本轮写成“风险正文已被消费”。但本轮的一些烟测局限原先就在 goal/summary 中，最终意见也提及，尚不能证明漏读造成了错误科学结论。需要把这两个结论分开。

**待决定的规则。** “不是独立测量证据”只说明可信来源与用途，不应自动等于“无关或可以忽略”。对于正在评价的结果，什么情况下必须考虑生产方提供的局限？当前只有“Read relevant module_report”的提示，没有确定性逐报告必读门禁。

**候选。** 先明确当前结果及其相关风险说明的阅读期望，使用现有 work_brief、工件入口和提示。暂不强制读取全部历史报告，不增加一份 risk/summary 同步缓存，不让模型无条件相信模块解释。

**需要的验证。** 放入一条与当前结论有关、且只存在于报告正文中的新风险；分别检查入口可见、实际读取、合理考虑。另测无关历史报告不被机械地全部读取。这个用例能区分“真正消费”与“复述任务里原有的限制”。

**定位**：[interpreter](../../../packages/agents/scientific/src/resagent2_scientific/interpreter.py)、[Scientific prompt](../../../packages/agents/scientific/src/resagent2_scientific/context.py)、[module_report](../../../packages/capabilities/src/resagent2_capabilities/module_report.py)、[现有交接测试](../../../tests/e2e/test_semantic_handoffs.py)。

<a id="c5"></a>

## 7. C5：文献能翻页，但没有足够好的定位线索

**事实。** `reg-literature-v3` 已冻结工件 `artifact_sci_1060cd91f3520b53`：14767 字符、132 行，含目标 SENet 的摘要和来源。

- `1ecfe35e47524e42b232b31e65c1e59c` 的原始输入仍有目标摘要，模型明确知道已经找到论文。
- 随后读取 100–400 行尾页，新片段 3867 字符；6000 字符工作集中旧片段只剩 2133 字符，目标内容不再可见。
- `6bec5573f07245b7a2dddee48b6b46b8` 错误判断没找到论文，再次联网检索并遭遇 429；下一调用询问用户。

原始工件没有丢失。首轮其他运行也确实有外部 timeout；这不抵消本次“已找到 → 翻页 → 误以为没找到”的上下文问题。

**不是缺少什么。** 当前已有按行读取、已读来源列表、原始事件顺序及截断提示。模型实际使用了范围读取。因此不应再重复造一个已读 ID 注册表，也不能只说“补一句分段读提示就好”。

**候选，先比较再选择：**

1. 在现有表示中提供更有效的材料定位，让已保存的论文能回读，而不是重复搜索。
2. 对当前“多篇论文信息＋摘要”的材料，比较按完整论文条目组织与按 JSON 行切片；代码仍适合精确行范围，不强求同样语义单位。
3. 与 C6 一起核算是否有足够输入空间保留关键条目，避免为了固定 6000 的数字引入复杂记忆机制。

这些是替代/互补选项，不是全部叠加的既定设计。任何文献特有呈现应靠近文献能力，Runtime 仍保持通用选择和预算；不硬编码论文名称，不改已冻结工件的内容或 hash，不在第一版引入 PDF 解析、向量检索或新的 LLM 总结循环。

**需要的验证。** 构造两篇以上文献：发现前面的目标 → 读取后面的条目 → 能依据保留的线索回读目标，并保持来源正确。现有“驱动直接指定尾部行号”测试可验证可达性，但不足以覆盖这一过程。另测外部服务真实不可用且本地没有所需证据时仍诚实求助。

**定位**：[文献流水线](../../current/CONTEXT.md#literature)、[literature.py](../../../packages/capabilities/src/resagent2_capabilities/literature.py)、[共享工作集](../../../packages/capabilities/src/resagent2_capabilities/workspace_context.py)、[已有范围测试](../../../tests/e2e/test_literature_artifact_windows.py)。

<a id="c6"></a>

## 8. C6：预算需要按用途核算，不先决定扩到多少

**已确认。** 模块输入 8192 tokens、局部正文 6000 字符、单次工具 8000 字符是不同限制。当前选取逻辑不会把 Composer 剩余空间自动借给正文。增大模型窗口或输出额度不能自动解决这个局部裁剪。

**尚未确认。** 6000 对哪些任务足够、Scientific 与 Coding 是否该不同、调整后工具契约和其他必需段的空间是否够用，以及新的延迟/费用影响。已有容量测试证明样例装得下，不证明最佳容量；单次失败也不足以给所有 Agent 统一翻倍。

后续若用户批准预算评估，先用已有请求/观察做确定性重组比较：

- 分别记录固定说明/工具契约/任务与状态/读取正文的占用，注明估算值不是真实 tokenizer usage。
- 区分是单次读取、局部片段选择还是总输入造成的裁剪，记录有多少已知关键材料因此不见。
- 比较保留现额度、适度调整相关模块额度、改变文献条目呈现等少数选项；相同输入、相同目标，不同时改多套规则。
- 明确哪个选择能用更少规则解决已观测问题；随后才用真实模型观察行为和成本，不靠一次绿色结果宣称永久稳定。

不预先承诺动态分配器、自动扩容、精确 tokenizer 或全局改大预算。合理调整配置本身可以很简单；但它不会修复 C1 的误导命名或 C4 的用途判断。

**定位**：[预算现状](../../current/CONTEXT.md#budgets)、[Composer](../../../packages/runtime/src/resagent2_runtime/context.py)、[ModelProfile](../../../packages/runtime/src/resagent2_runtime/llm.py)、[容量测试](../../../tests/e2e/test_native_context_capacity.py)、[Scientific 容量测试](../../../tests/e2e/test_scientific_context_capacity.py)。

<a id="rules"></a>

## 9. 供讨论的修改准则

以下用于选择未来方案，不表示当前所有位置都已满足，也不增加新的公共协议类。

1. **先找信息在哪一层丢失。** 字段缺失、接收方未投影、局部裁剪、模型未使用分别处理，不用新字段掩盖展示问题。
2. **显示含义与真实计算相符。** 当前状态、历史观察、材料入口和正文片段要能区分；不要求每条数据套很多新标签。
3. **解释不是测量，但可能影响判断。** 来源可信度与是否相关是两件事，不以一种布尔值代替全部规则。
4. **共享机制，不强求相同材料单位。** 授权、保存、预算可以共用；代码行、论文条目、失败命令可使用各自最简单的呈现。
5. **先用现有事实和记录。** 若只需事件号或真实 binding 就能说明状态，不创建第二份缓存/状态机。
6. **不默默丢掉完成所需事实。** 在有界预算下保住可行动的诊断或恢复线索；不足时明确表示不完整，而不是装作完整。
7. **预算可调，但要知道调的是哪一层。** 不把固定值当原则，也不把更大上下文当作所有语义问题的解决办法。
8. **同步一条对应关系。** 公开字段改动查 CONTRACTS；投影/刷新/限额改动查 CONTEXT；职责变动才改 ARCHITECTURE。普通表达修正不自动升级成大规模架构重构。

<a id="decision"></a>

## 10. 阅读后再决定的范围

本次交付只完成两步：当前文档与问题归类。下一步需用户决定是否、以及选哪些候选进入实施；本文件不授权自动继续。

若进入实施，建议区分两类工作：

- **较明确的表达/一致性修正**：C1、C2、C3；先检查共享位置和确定性反例，功能、权限与执行记录保持原边界。
- **需要先定阅读规则或比较策略**：C4、C5、C6；先讨论/测量，再选最小方案，不直接把全部报告设为必读或加新记忆系统。

验收按“数据 → 构造的真实上下文 → 模型行为 → 实际结果”分层。真实测试新发现先分类，只有明确阻断已批准目标的才加入当前范围；其他留记录，不因一次失败自动增加补丁。

暂不处理：JSON 专项、自然语言字段删除/合并、旧 schema 迁移、生产级监控、CLI/E2E 组合根重构、资源系统重写和大型文献全文系统。原服务器失败现场保留，不通过修改目标、放宽门禁或重跑到绿覆盖旧结果。

## 11. 本次文档交付检查

- 已新增当前上下文说明与本审查，接入根 README、docs 导航、架构、契约和历史索引；仅 Markdown 有变化。
- 同步纠正旧文档中“目录段为 required”“所有可恢复失败都自动进入持久反馈”的过宽描述，及 value 预览约数；没有改变对应代码行为。
- 检查涉及 7 个 Markdown 文件的 244 个本地文件/锚点链接，均可解析；代码围栏配对正常。
- 运行现有上下文相关确定性测试：runtime 的 context / prompt_client / observation_previews / tool_contracts，capabilities 的 workspace_context，E2E 的 native_context_capacity / scientific_context_capacity / literature_artifact_windows，以及 scientific 的 interpreter、coding 的 control_state，共 81 passed。未增加或修改测试代码，未重跑全量或真实 LLM。
- `git diff --check` 通过。仍停留在原分支，未提交、合并或推送；候选修复待用户决定。

## 12. 随后获批的实施范围（2026-09-13）

以上保留审查时的事实和未决选项。用户阅读后批准最小实现：

- C1/C2：控制投影改名edited_since_verification，下一步与既有验证规则同源；验证prompt不建议被权限拒绝的inline Python；目录观察增加原始事件号及历史性说明。
- C3：capabilities新增纯command_context投影，共享最近命令结果；先选失败及已捕获stdout/stderr，再限长。不新增运行状态、缓存或LLM解释器。
- C4：只加强相关module_report风险阅读提示，不加全部报告必读门禁。
- C5：新文献工件按论文条目排成Markdown；沿用冻结、hash和行读取，不做全文下载、阅读笔记或新的模型总结。
- C6：三个Agent默认128000输入tokens，Compiler仍4096。Loop在builder前计算一次有效额度；阅读材料按50%分配（执行Agent两类各25%），命令诊断1/16，目录1/64。原始工具读取上限提高到共享128000字符。局部限额和总输入计量保持分层，不新增动态分配器。

公共业务schema仍7.0；ContextBuilder显式接收有效预算参数，仓库内装配和测试同步更新，不加双签名兼容。JSON协议、Controller/Scheduler状态机、验证权限和证据门禁未更改；CLI只调整默认值来源，没有组合根重构。

本地全量859 passed、1 skipped，mock E2E completed，diff-check通过。测试含128K下文件/工件各128000字符共存、Scientific两工件共256000字符、模型缩小额度先影响材料、超小额度明确失败、失败在批次中部及历史目录时序。真实模型行为尚待[本轮验收](CONTEXT_128K_ACCEPTANCE.md)，不借用旧服务器结果。
