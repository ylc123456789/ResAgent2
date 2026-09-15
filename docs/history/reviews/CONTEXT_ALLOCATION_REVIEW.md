# 统一上下文分配：实现与补验

## 1. 范围与依据

起点为 `feat/runtime-continuation@827d1b5`。用户要求参考成熟实践、统一共享管理；最终容量不足仍报错，不新增复杂恢复路径。公开schema仍8.0，不改业务契约、Run/Task状态机、原生批次/Session持久化格式、Compiler、模型输入输出默认值或旧L3。

参考一手资料（2026-09-15核对）：

- [Pi的compaction实现说明](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/compaction.md)：在有限窗口中保留近期消息、总结旧历史、保存检查点，并保持工具调用/结果配对。借鉴边界，不照搬它的分支摘要、扩展系统或数字配置。
- [Anthropic的context engineering实践](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：有限上下文应精选材料，长任务可压缩旧历史；压缩有损，需要保留关键决策与未完成工作。此轮不加阅读笔记或多Agent记忆。
- [LangChain的short-term memory说明](https://docs.langchain.com/oss/python/langchain/short-term-memory)：区分持久化历史和模型本轮消息，修剪时维护有效消息关系。本项目不引入LangChain依赖。

“先给起始份额、再按优先级借用”是本项目在已有领域投影上的工程选择，不宣称这些项目都采用同一算法。

## 2. 实际改动

- `ContextSection`仍表示固定正文；新增一个不持久化的`ContextMaterial`描述纯渲染、相对权重与优先级。不在业务请求或Session增加预算字段。
- Composer先装固定段和材料最小导航框，使用完整请求计量。材料先按权重装入，再按priority使用空余；所有候选仍经相同计量函数检查，不直接剪最终JSON。
- 复用80%水位为材料填充目标和压缩触发点，防止“材料填满→压缩→再次填满”的自触发。固定必需输入不受80%拒绝线限制，仍按总硬上限检查。
- `workspace_context`为文件、工件、命令、目录共用此机制。相对权重16/16/4/1沿用原25%/25%/6.25%/1.5625%的相对关系，但不再分配独立硬上限。空余按诊断96、读取80、目录62优先级借用；同优先级保持输入顺序。
- `workspace_reads`拆为`file_reads`、`artifact_reads`以单独参与分配。各段统一为snippets、previously_read、content_omitted；保留读取时序、成功编辑标记与截断语义。旧记录不迁移、不删。
- 总量不足仍走现有至多一次历史压缩及`budget_exhausted`出口；不追加摘要重试、自动问用户、自动扩容或新的记忆框架。

当前规则集中在[CONTEXT §6](../../current/CONTEXT.md#budgets)，接口说明同步[CONTRACTS](../../current/CONTRACTS.md#runtime-context)。

## 3. 本地验收重点

本地全量：**1014 passed, 1 skipped**（相对827d1b5新增12个分配用例）；`python -m e2e.mock_e2e` completed，`git diff --check`干净。初交付时服务器待验，最终结果与复核勘误见[§5](#verified-closeout)。

- 起始份额、另一类材料很短/缺失时借用；先保留各类基本份额，再按优先级借用。
- 固定目标/约束/回答/控制信息原样保留；它们变大时缩减材料，而不是按原比例填材料后错误失败。
- 整包JSON转义、工具schema、历史、标题、材料元数据均计量；ASCII/中文覆盖。填充不自行触发压缩。
- 文件/工件的来源、时序、旧版本标记、源码正文和截断标志仍正确；失败诊断优先，原事件无变化。
- 100%硬上限保持；最小输入、单个巨大协议回合、摘要重建确实超限仍失败，不推进检查点。
- Compiler、普通问答、批次回执、压缩计量及跨进程检查点由全量测试回归。

不再把“某类正文恰好6000/32000字符”当成功条件：检查最终输入实际可容纳的内容、来源和整包上限。一个2048的Coding夹具现在能缩减材料后继续；小额度不必然失败，确实装不下才失败。

## 4. 服务器补验要求

接收本轮最终提交的干净worktree；记录SHA、8包editable指针和输入/输出Profile。旧b4868ec现场及旧L3原样保留，不merge/push。不需要GPU、torch或完整科研矩阵。验收根单独建立，full trace 0700/0600，密钥定值扫描不打印key。

先跑全量pytest、mock_e2e及：

```bash
python -m pytest tests/runtime/test_context_materials.py tests/runtime/test_context.py tests/runtime/test_compaction.py tests/runtime/test_history_checkpoint.py tests/capabilities/test_workspace_context.py tests/e2e/test_native_context_capacity.py tests/e2e/test_scientific_context_capacity.py -q
```

付费小探针沿用[续接验收§7](RUNTIME_CONTINUATION_ACCEPTANCE.md#soft-target-followup)的配置与隔离纪律：

1. **Coding Flash与Pro各一次**：各自独立、干净、确实有加法bug的fixture，128K、40 calls/600s。运行前工作树与HEAD都应a-b、测试应失败；之后看真实编辑、unittest、finish，不把另一运行的补丁当本轮产物。
2. **Experiment Flash标准库一次**：读脚本和一份真实输入工件，再执行得到可复算metrics；不安装torch。确认file_reads/artifact_reads中的正文确实到达模型，答案与结果对应。
3. **8192压力与跨进程问答**：沿用旧压力目标、Profile输出不变、新Session/fixture，确认压缩后暂停再由新进程回答继续。自然未问可按原验收规则注入一次合法ask_user，并明确标记。若没有自然触发，不宣称覆盖；确定性测试是硬边界证据。

每个探针核对：required当前输入、原题/答案不丢；材料body可缩减且来源/省略标记可见；所有完整请求估算不超有效输入；材料填充本身不越80%，但固定/历史部分可超过80%触发原压缩。不把80%当报错线，也不要求每次恰好填满。计量包含摘要和HTTP尝试，步骤按动作计；原始turns/events保留，调用/回执不拆对。

空余额度借用和优先级由上面的确定性测试验证，不要求付费模型刻意产生某个长度或重复跑到绿。最终上下文确实不足、超时或模型任务失败应保留并分类报告，不能扩大额度掩盖。

以上为测试方执行前的要求；开发侧不自行启动服务器付费调用、不恢复旧L3。服务器行为不能由本地单测代替。

<a id="verified-closeout"></a>

## 5. 2026-09-15 最终复核与收尾

服务器实测产品提交为 `f98b6fd`（`feat/runtime-continuation`，schema 8.0），证据根为 `/root/autodl-tmp/e2e-ca-f98b6fd-K9mP2x/`。测试方报告全量 **1014 passed / 1 skipped**、聚焦 **107 passed**、mock E2E completed；主开发经 WSL SSH 只读复核 MANIFEST、驱动、原始模型请求/返回、Session 事件和实际结果。原报告、旧 b4868ec 现场与旧 L3 不改写。

收尾时主开发本地重新执行全量：**1014 passed / 1 skipped**；mock E2E completed，独立临时输出已自动清理；6份修改文档的57处相对文件链接及 `git diff --check` 通过。此次收尾不改产品代码，服务器不需为文档提交重复付费补验。

### 通过内容

- 六个探针均完成：独立 Coding Flash / Pro、Experiment、8192 压力、自然问答恢复、先压缩后暂停的注入补跑。暂停阶段本身为 needs_user_input，只有恢复后的模块结果为 completed，不把暂停当失败或独立完成。
- Pro 的首次读确为 `return a - b`，原生 replace_text 改为 `a + b`，运行 unittest 后 finish；6 次调用、约24秒。不用此结果反推旧非独立夹具超时的原因。
- Experiment 的 `file_reads` 与 `artifact_reads` 正文均到达模型，真实执行 `python run.py` 后 value=60，与输入一致。
- 8192 压力探针压缩3次并生成正确 result.txt；问答补跑在6次压缩后，将一次 finish 响应替换为合法 ask_user（明确标注注入），跨进程恢复读取真实答案，最终 needle_files.txt 为 data_2.txt / data_5.txt / data_7.txt。它验证暂停恢复机制，不证明模型自然选择该提问。
- 所有完整原生/压缩请求按 `ceil(len(request_text)/4)` 重算无失配；主开发复核 trace 目录0700、文件0600。Session权限与密钥定值扫描无命中由测试方报告，收尾不重复读取或记录密钥。

### 对服务器 MANIFEST 的勘误（原件保留，以本节为准）

| 项目 | 原始证据与正确口径 |
|---|---|
| 调用计量 | coding-flash 7、coding-pro 6、exp 6、stress8192 11、resume 两阶段合计12、resume2 17、resume2-cont 4，共 **63个逻辑调用 / 63次HTTP尝试**。每条主记录 retry_number=0、attempts长度=1。补跑暂停前 result.llm_calls=17，恢复后=4，最终 Session.llm_calls_used=21；不是“phase1花21次、其中4次HTTP重试，再加phase2的4次”。摘要调用已包含，不能重复加账。 |
| 暂停检查点 | 补跑第6次压缩为 Session event 53，history_start=8；ask_user 回执为event 59。暂停时累计11个原生turn。history_start=10、tool_turns=14是恢复结束后的状态，不是暂停快照。 |
| 恢复首个请求 | call_id `8aca377416ae42adb6efe46173c211aa` 保留turn索引8–10的 **3个完整工具回合**（run_verification；read_file+git_diff；ask_user），调用ID与原Session逐一匹配，未重发索引0–7。不是“仅1个回合”；首请求同时含原题与 needle_files.txt 答案。 |
| 摘要略超目标 | 单独压力探针的1177/1440/1550确实低于1636，但同为8192输入的补跑真实产生 **1642、1789、1873** 字符摘要（call_id依次 `5aeef57d25574673a0a86d6007142efe`、`0e4f01e6c811470583f90eddd22d2ec4`、`f358fa6348814e18bfa2d97f59aba60b`），提示目标均为1636，finish_reason均stop，检查点保存且继续执行。故“局部超目标、整包仍可容纳”的接受分支已有真实证据，不仅确定性测试覆盖。 |

### 验收边界与后续

- 恢复补跑直接调用子Agent，不经Controller；两阶段各传入40次调用额度。因此已验证Session累计计量和检查点恢复，**没有在此付费探针验证Run剩余额度下发的完整链路**。实际两阶段合计21次，未超原40次；Controller/Scheduler剩余额度规则仍由相应确定性测试覆盖，直接模块调用的每次授额归调用方负责。
- 材料权重/优先级借用、真正整包超限时失败、不推进检查点等硬边界由确定性测试证明。小探针不证明长科研任务永不循环、摘要永不丢信息，也不证明 arXiv 已恢复。
- 本阶段不再改产品代码、不追加付费回归；收尾只同步文档并在用户授权后合并推送。此次分支包含文献平级来源、原生调用、串行回执、预算去重和压缩的前置提交；各阶段历史结果分别保留，不声称 f98b6fd 重跑了全部旧矩阵。
- 服务器 editable 仍指向待测 f98b6fd checkout，不在收尾时切换安装、清理环境或旧工作区。旧 schema 7.0 L3 保持暂停且不迁移；未来科研级验收需另行确认预算，使用当前版本新 Run。
