# Scientific 工件上下文与检索职责：验收单

分支：`fix/contract-foundations`。测试前记录待测提交完整 SHA 和安装指针；不使用父提交结果代替。本地隔离 cwd 全量 **701 passed、1 skipped**，mock E2E completed，`git diff --check` 干净。新增 4 项实际 Scientific 上下文/容量测试和 3 项 Prompt 静态回归；**本轮服务器尚未验收**。

## 1. 本轮边界

- Scientific 复用已有 `workspace_context(state)`，只使用工件片段，不传 EnvironmentBinding。工件正文从成功读取的 Session events 投影，总共最多 6000 字符、required，沿用来源/行范围、事件顺序、截断与已读来源索引。
- 删除 `read_artifact_summaries` 正文前缀缓存。没有额外摘要调用、长期记忆、自动重读或第二个内容存储；原始历史、冻结工件和 trace 不删除。
- Scientific Native/CLI 默认总输入上限 8192 tokens；Compiler 4096，Coding/Experiment 8192。显式配置及模型可用容量仍是硬上限，不因缺内容自动扩容。
- Scientific prompt 明确检索/读取/判断是自有职责，timeout/429 经已有重试失败不能成为派代码/实验任务的理由；需要材料或恢复决定时走既有 ask_user。未修改 Compiler review、状态机或 required evidence 判据，**提示词不是确定性路由保证**。
- `replace_text` 的唯一匹配是每次调用要求，不限制一个任务只能修改一次。未改编辑算法。

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

本地绿、action_valid=true、一个 completed 或不同模型通过都不能代替上述证据。未做项明确未做；不根据测试 AI 的“模型漂移”标签跳过原始消息检查。确认完成后再另行决定 push/合并，当前文档不代表服务器已验收。
