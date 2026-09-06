# Scientific 工件上下文与检索职责：验收单

分支：`fix/contract-foundations`。`392e312` 的服务器 **9 次正常入口 + 1 次持续 HTTP 429 注入已核验**，见 §5。后续多行 JSON 小补丁代码提交为 `8bcf0e2e899e85cd03be764c05c1c06bc134f8c9`，本地隔离 cwd 全量 **703 passed、1 skipped**，mock E2E completed，`git diff --check` 干净；服务器新 workdir 单次 literature **rc=0、completed、artifacts=2**，新增 2 项确定性测试通过。已核真实尾部范围内容进入下一 prompt、工件 hash、trace 权限和安装指针，详见 §6；新旧提交的验收范围分别保留。

## 1. 本轮边界

- Scientific 复用已有 `workspace_context(state)`，只使用工件片段，不传 EnvironmentBinding。工件正文从成功读取的 Session events 投影，总共最多 6000 字符、required，沿用来源/行范围、事件顺序、截断与已读来源索引。
- 删除 `read_artifact_summaries` 正文前缀缓存。没有额外摘要调用、长期记忆、自动重读或第二个内容存储；原始历史、冻结工件和 trace 不删除。
- Scientific Native/CLI 默认总输入上限 8192 tokens；Compiler 4096，Coding/Experiment 8192。显式配置及模型可用容量仍是硬上限，不因缺内容自动扩容。
- Scientific prompt 明确检索/读取/判断是自有职责，timeout/429 经已有重试失败不能成为派代码/实验任务的理由；需要材料或恢复决定时走既有 ask_user。未修改 Compiler review、状态机或 required evidence 判据，**提示词不是确定性路由保证**。
- `replace_text` 的唯一匹配是每次调用要求，不限制一个任务只能修改一次。未改编辑算法。
- 后续小补丁仅将 `ArtifactRegistry.register_scientific` 生成的 JSON 按 `indent=2` 序列化再冻结，便于对结构化文献列表按行读取；新工件的 SHA256 基于新字节计算，不改旧工件，不在 reader 中改写冻结内容。

## 2. 本地确定性检查

使用已有环境，在隔离 cwd 运行，避免往仓库写测试状态。服务器路径按实际安装调整，先核验 editable 指向待测提交。

```bash
repo=/home/cyl/ResAgent2
check_dir=$(mktemp -d /tmp/resagent2-scientific-context-check.XXXXXX)
cd "$check_dir"
export PYTHONPATH="$repo"
/home/cyl/miniconda3/envs/ResAgent2/bin/python -m pytest \
  "$repo/tests" "$repo/apps/cli/tests" -q
/home/cyl/miniconda3/envs/ResAgent2/bin/python -m e2e.mock_e2e
git -C "$repo" diff --check
```

重点不是只看片段函数返回值：用真实 ScientificAgent/AgentLoop/ReadArtifactTool 与实际文件验证，中部行范围正文出现在下一次传给客户端的 prompt；多个 Artifact 的正文总量不超过 6000；required 工作集不能因可选 section 淘汰而整包消失。默认预算下装入实际 prompt、tool contracts、feedback、授权目录和正文；显式小预算装不下时，在模型调用前明确返回预算错误。确认旧 `read_artifact_summaries` 不再作为生产数据来源，原始 Session 事件不变。

ScriptedLLM 可以确定性验证数据通路，但不能证明真实模型遵循检索故障提示或形成正确科学结论。

### 本地发现的既有估算边界（另项跟踪，本轮未修改）

`ContextComposer` 当前以各 section 正文的字符估算入账，未计入最终拼接的标题与分隔符。一份显式 4096 的容量样本分段估算为 4070，而对最终全文用同一估算器重算为 4115；这不是供应商 tokenizer 的实测值。本轮没有更换预算算法或取消模型安全余量，不能把模块 token 上限解释为精确 tokenizer 计数保证。容量回归保留最终全文估算断言：默认 8192 与显式 5000 均容纳实际工作集，1024 在 LLM 调用前明确失败。此边界需独立修正与回归，不与本轮“正文整体省略”的修复混为一项，也不因测试通过而隐去。

## 3. 服务器执行纪律与场景

只同步、测试、分析和报告：不改产品代码、prompt、原场景目标或预算，不合并 main，不删环境、缓存、数据集和失败现场。所有新产物收在单一的新验收根目录，正常场景与故障注入分别建 fresh workdir；记录 warm/cold 环境，不覆盖历史现场。

1. Git pull 或 bundle 同步到干净 checkout/worktree；记录 SHA、状态、8 个 editable 包的 `__file__`。如果改变安装指针，报告前后位置。
2. 全场景启用 `RESAGENT2_LLM_TRACE_LEVEL=full`，每场景独立 `RESAGENT2_LLM_TRACE_DIR` 与 `REAL_E2E_WORKDIR`；保留实际模型名、request_text、raw_response_text、供应商返回时的 raw_reasoning_text、Session、Run、冻结工件和 stdout/stderr。不打印密钥。
3. 默认 Flash：正常 `literature` 固定 **3 次**、`code-experiment` **1 次**、`repair` **1 次**、`direct` **1 次**、`ask-start` → 退出进程 → `ask-resume accuracy` **1 组**；V4-Pro `code-experiment` **1 次**回归。不以重跑到绿覆盖失败。
4. 另做下面的持续 HTTP 429 故障注入 **1 次**，保留真实 LLM，不改原 literature 目标、约束、required evidence 或预算。

现有入口是 `python -m e2e.real_e2e <场景>`；ask-start 和 ask-resume 使用同一个 workdir、两个独立进程。沿用原有模型配置覆盖方式，核对 trace.model，而不是只改报告名称。

### 3.1 正常 literature：验收引用内容，不只看 completed

逐次对照原始请求/响应和冻结文件：

- 模型实际执行 literature_search，工件来源可追溯；最终引用指向本 Run 的已登记、已观察工件。标题、搜索词和短预览不能代替支持结论的内容。
- 把最终 statement 的关键论断与所引工件中的具体内容逐项对应，记录 ArtifactId、路径/行范围与 call_id。工件若只有题录与摘要，报告其证据范围，不能把它称为读过论文全文。发现错引或“结论看似合理但所引工件无关”单独判未通过。
- 每次成功 read_artifact 后，核对下一次 Scientific 请求的 `workspace_reads.artifact_snippets`：artifact_id、start_line/end_line、observed_at 与实际 Session 观察对应；不得用原工件前 2000 字符替代实际所选范围。若读了中部范围，核对其中真实内容进入下一 prompt，而不是只保留一个“读过”的 ID。
- 读取工作集在 included_sections 内，且没有被整体作为可选内容丢弃；正文合计不超过 6000。预算仍可能截断或淘汰较旧片段，要求的是规则正确，不是所有工件全文永久常驻。
- 若正常运行未出现中部行范围读取，如实记“未触发”；不能据此声称真实模型已经验证分段行为。本地 Native 通路测试负责确定性覆盖；若另做真实模型范围诊断，使用独立测试驱动、真实冻结工件和单独 workdir，记录专用输入，不修改原正常场景。
- Scientific 上下文不出现 environment 绑定，也没有执行工具；旧 `read_artifact_summaries` section 不再进入请求。

### 3.2 持续 HTTP 429：仅对检索后端注入故障

在验收根目录的 `ops/` 保存独立测试驱动，不修改仓库。驱动可用 `unittest.mock.patch` 仅替换 `resagent2_capabilities.literature.urlopen`，持续抛出 HTTPError(429)，然后通过 `runpy.run_module("e2e.real_e2e", run_name="__main__")` 运行原 `literature` 入口。保留 ArxivLiteratureBackend 原本的重试与退避；不要 mock LLM、Scientific 动作、Compiler、Controller 或最终判据，不额外增加 retry/预算。

核验真实模型收到的是工具已有重试耗尽后的 timeout/429 诊断。期望随后通过现有 ask_user 说明真实服务故障，请用户提供材料、决定等待或确认恢复；问题应有非空 requested_fields，状态落盘为 PAUSED。不得创建替代检索的 code/experiment WorkRequest/Task，也不得把服务失败解释为“没有相关文献”或凭空引用工件完成。

**退出码要分开解释**：现有 `literature` 脚本只对“完成且引用文献工件”返回 0，因此故障注入下正确 PAUSED 也会返回 1。此项按 Run/Session/原始动作验收，不修改脚本让它变绿；不要把故障场景称为“正常 literature 通过”。若仍 request_work 绕路、无意义重试至耗尽或伪造结论，保留现场，判该恢复行为未通过。若模型按规则失败退出而未绕路，也单独报告，不能冒充已验证 ask_user 暂停路径。

一个故障注入样例通过只能说明该次真实行为符合要求，不证明 Prompt 对所有模型/运行都有确定性约束。

### 3.3 其余回归

- 黄金链：Coding 实际编辑/验证，Experiment 实际执行，Scientific 的论断与 JSON 指标/工件一致。保留 warnings，不把 completed_with_warnings 当作完全交付。
- repair：原始 stderr 的真实 typo → Scientific 诊断 → Coding 修正 → 重跑证据 → 最终报告。检查诊断取自错误/工件，而非仅凭标题或 summary 猜测；报告保留 Execution issues。
- ask/resume：非空答案落盘、跨进程同 Session 恢复，最终意见准确记录回答；不得只凭 paused/completed 两个状态宣称答案被采用。
- 编辑约束：若发生多次 replace_text，逐次核对唯一匹配与成功记录；未触发则记未触发，不强迫模型为了证明提示词而多改代码。
- trace 权限目录 0700、文件 0600。凭据扫描只报告是否命中，不展示疑似密钥值。

## 4. 交付与收尾门槛

交付 MANIFEST（SHA、安装指针、逐次结果、正常/注入区分）、原始 trace、Session/Run、冻结工件及分析脚本。至少列出：关键科学论断→工件内容的对应、中部读取→下一 prompt 的证据、required section/预算结果、429 后实际控制动作、回归真实指标和已知缺口。

本地绿、action_valid=true、一个 completed 或不同模型通过都不能代替上述证据。未做项明确未做；不根据测试 AI 的“模型漂移”标签跳过原始消息检查。具体已验证范围以 §5 记录为准，新补丁未验证范围见 §6；push/合并另行决定。

## 5. 392e312 服务器审计记录

产物根：`/root/autodl-tmp/e2e-context-392e312-20260906/`。已对照 full trace 的原始请求/响应、Session、冻结工件和 Run，不只复述测试报告。9 次正常入口包括：Flash literature ×3、code-experiment、repair、direct、ask-start/ask-resume 两个进程，以及 V4-Pro code-experiment；均达到相应场景结果。另有 1 次持续 429 故障注入，按正确暂停而非 completed 判定。

| 审计项 | 已核证据与结论 |
|---|---|
| literature ×3 的结论依据 | 三次最终 Scientific 请求的工作集均可见完整 SENet 摘要，关键结论与摘要内容相关；这是摘要层证据，不代表读取了论文全文。对应 final call_id：`a20a623660c742c79c0c610d419888c4`、`0851f25e0be64c26a0dce74bcb0bc177`、`b00df6ede3c7442298f9f25b24cf3139`。 |
| 持续 HTTP 429 | search call `538b9ca0b8854b15ad5f21fb2e37650b` 后，`ef650affc5914a73bda9361ec19be15e` 调 ask_user；Run 正确 PAUSED，无替代检索的 WorkRequest/代码/实验 Task。后台实际 **3 次 HTTP attempts = 首次请求 + 2 次 retry**，不是首次之外又重试 3 次；也不能把后台次数当成 LLM 调用次数。 |
| ask-start / ask-resume | 原始 trace 分别 **1 次 / 1 次 LLM 调用，共 2 次**。暂停→新进程提交 accuracy→完成，答案得到记录；恢复后 Run 的累计数不能再与首阶段重复相加。 |
| repair verdict | final call `e051223df6e849f7bb71da0f05b702d6` 的任务是修复并运行脚本，`hypothesis=null`；在真实修复/重跑完成的前提下，`not_applicable` 是合理的科学假设判定，不应仅因它不是 supports 就归为模型漂移。 |

这些结果支持 Scientific 工件工作集及检索故障职责提示在这批运行中生效，但不证明所有模型与所有运行都必然服从 Prompt，也不消除下面的序列化缺口。§2 中标题/分隔符未计入预算的已知问题继续单独跟踪，本轮未修改。

## 6. 自产单行 JSON：确认缺陷与小补丁验收

原测试报告把“工件是单行 JSON”判断为非 bug，不成立。自产文献列表可能整体序列化成超过 8000 字符的一行：read_artifact 读第 1 行仍只能返回受上限约束的前缀，读第 2 行以后则为空，后文根本无法用现有行范围接口取回。工作集的 6000 字符额度不是这个问题的唯一来源；增加上下文不会让不存在的第 2 行出现。旧现场 `artifact_sci_1d6c597a030934be` 保留作例证，不能把空范围读取当作成功读取中部内容。

最小修复放在生产端：register_scientific 用 `indent=2` 生成多行 JSON，保持原 JSON 数据含义，先生成新字节再计算 hash 并冻结。reader 的 Run 授权、整文件 SHA256 检查、行范围接口和 8000 字符工具上限均不改变，旧工件与其 hash 不改写。该修复针对结构化文献列表整体挤成一行，不声称能任意分页一个本身超过上限的超长字符串字段。

新增确定性验收链必须使用实际登记与读取组件：

1. 构造总序列化长度 **>8000 字符** 的文献工件，在列表尾部放一个可定位的真实内容标记；通过真实 register_scientific 生成并冻结，不直接手写“理想的多行文件”代替生产端。
2. 校验新工件可被 JSON 解析且数据等价、包含多行、ArtifactRef.sha256 等于实际冻结字节的 hash。
3. 默认读取受限后，定位尾部内容所在的真实行，用 read_artifact 的 start_line/end_line 读取，断言内容**非空且确实包含尾部标记**、范围未截断；越过文件末尾得到空串不能算通过。
4. 有界范围正文应能进入 Scientific 下一次实际构造的模型请求；保留已有 Native/Session 工作集回归。这里的确定性客户端只验证通路，不冒充真实模型选择了正确范围。

本次小补丁代码提交：`8bcf0e2e899e85cd03be764c05c1c06bc134f8c9`。本地隔离 cwd 全量 **703 passed、1 skipped**，mock E2E completed，`git diff --check` 干净。服务器已在新根 `/root/autodl-tmp/e2e-literature-8bcf0e2-20260906/` 跑该提交的单次真实 literature，日志确认 **rc=0、run status=completed、artifacts=2**；新增 2 项确定性回归也在服务器通过。

此次真实 LLM 验收用时约 **80 秒**，**4 次调用、0 次客户端重试**。原始 trace 对应链路是：literature_search → 默认 read_artifact 返回 8000 字符并标截断 → read_artifact 请求 **[100:200]** → finish。新冻结 JSON 共 **15183 字符、137 行**，SHA256 与 ArtifactRef 匹配；范围读取返回从第 100 行到文件末尾的 **3285 字符**，非空且 `truncated=false`，不是超出文件行数后的空读。

该范围包含末尾 SENet 的完整摘要、`paper_id=1709.01507`、`2.251%` 与约 `25%` 的错误率相对降低描述。下一次 Scientific 请求的 required `workspace_reads` 保留了这份范围正文，工件正文池合计 6000 字符；没有用旧前缀缓存替代。最终 call_id 为 `2fcf649ebc1a4b56990950e1eb950401`，verdict=supports，与可见摘要相关；limitations 明确只据摘要、没有读论文全文。该次结果验证了“自产多行工件→实际范围读取→下一 prompt→有依据的结论”，不是仅验证 completed 状态。

trace 目录/文件权限为 **0700/0600**，凭据扫描未发现泄漏。8 个包的源码路径断言均指向新待测 worktree，新 worktree 干净；原 editable 安装仍指向 392e312，没有重新绑定或清理旧现场。原始记录与分析收在上述新产物根目录。

旧 392e312 的单行工件和三次正常 literature 不计入新格式验收；8bcf0e2 这次单独运行也不冒充将此前所有场景再跑了一遍。标题/分隔符预算开销仍按 §2 作为独立已知项保留，不因本次通过而隐去。
