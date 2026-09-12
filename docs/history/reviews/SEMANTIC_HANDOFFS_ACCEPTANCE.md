# 语义交接验收：模块解释与原题配对

状态：本地验证通过，服务器验收待执行。分支 `fix/semantic-handoffs`，产品提交 `704dbd9`（说明工件）和 `0065088`（原题配对），公共 schema 7.0。同步时应包含后续文档提交，并记录实际 HEAD。设计依据见 [ADR-0014](../decisions/0014-semantic-handoffs.md)。

只验证三处交接：代码理解答案、修改/实验残余风险经 module_report 到达消费者；用户原问题与回答经 RecordedAnswer 到达恢复 Agent。不要顺带调整其它字段、模型、prompt 或预算。

## 本地结果（2026-09-12）

- 在 WSL Ubuntu-D 的 ResAgent2 环境、独立临时 cwd 执行全量：**844 passed, 1 skipped**；跳过项是既有 opt-in literature 网络 smoke。
- `e2e.mock_e2e` completed，`git diff --check` 干净。
- 完整交接测试使用 ScriptedLLMClient 与真实 Controller、Scheduler、原生 Coding/Scientific、JsonRunStore/JsonSessionStore：已注册报告经依赖授权读取后，完整答案和不确定性进入实际上下文；无依赖任务不收到报告。
- 问答测试覆盖 Controller 原题来源、伪造原题不被采用、连续两个“第二个”的题答配对、持久化后重建 Controller/Agent 恢复及 Task/Scientific 作用域。长报告测试经真实 Registry 和 RegisteredArtifactReader 读到默认窗口之外的尾部。
- 这些结果证明确定性数据交接，不证明真实模型一定读取或遵循；下面的服务器真实 LLM 场景尚未执行。

## 1. 同步、隔离与确定性基线

- 同步最终提交到干净 worktree，记录 HEAD、工作树状态和 8 个包的 editable 指针及前值。可使用 git bundle，不 scp 零散源码，不合并或 push。
- 所有新增产物放单一独立根目录：`MANIFEST.md`、`logs/`、`traces/`、`workdirs/`、`ops/`。每次独立运行使用独立 Run ID/目录/trace；同一次问答恢复才共用 Run、Session、Attempt 和 workdir。
- 旧 schema 6.0 及更早的 Run、环境、缓存、数据集和失败现场全部保留，不迁移、不删除；本轮从 schema 7.0 新 Run 开始。检查旧记录拒绝只用副本，并确认读取失败未改写其字节。
- API key 仅运行期加载，不打印、不写入脚本或报告。开启 `RESAGENT2_LLM_TRACE_LEVEL=full` 与独立 `RESAGENT2_LLM_TRACE_DIR`，记录 trace 内实际 model，而不只报告环境变量。

在已有 ResAgent2 环境、独立 cwd 中执行，`repo_dir` 指向本次 checkout：

```bash
PYTHONPATH="$repo_dir" python -m pytest "$repo_dir/tests" "$repo_dir/apps/cli/tests" -q
PYTHONPATH="$repo_dir" python -m e2e.mock_e2e
git -C "$repo_dir" diff --check
```

重点核验新测试覆盖：RecordedAnswer 原题来源/拒绝缺失原题/旧版本拒绝，回答作用域和顺序，三个 context builder，module_report 构造、注册与长单行分页读取，code_understand 无报告缺失，空/非空风险条件，interpreter 用途，以及原指标和验证门槛未放宽。记录实际收集/通过数，不抄旧基线。

## 2. 代码理解答案真正交回上游

新增一个纯标准库小仓库探针：源文件含可核验的函数行为和已知边界，任务仅要求 code_understand 解释行为、不修改代码、不安装依赖。通过生产 Controller/Scheduler/Agent 链发起，不能只调用 finalizer 后把产物手动塞给 Scientific。

核验：

1. Coding 实际 read/search 相关文件，完成 payload 有 answer、uncertainty、evidence_files。
2. 成功结果有已登记的 `kind=module_report`、`media_type=text/markdown` 报告，开头明确解释用途，`## answer`、`## uncertainty`、`## evidence_files` 分别呈现选定内容；不把完整回答缩成通用完成摘要。引用路径与 Coding 实际观察对应。报告可新增展示换行，不要求 Markdown 字节与 payload 原字符串相同；payload 的精确原文必须保持不变。
3. Registry 已冻结正文，ArtifactRef 的 Run/Task/Attempt、producer、hash 正确；不因 code_understand 没有写产品代码而漏注册报告。
4. Scientific work_brief/authorized_artifacts 中能定位报告，原始 prompt 对 module_report 标记解释用途；真实 read_artifact 读取报告正文，后续结论对应实际函数行为并保留相关限制。
5. 若有依赖的子任务，核对 module_report 经既有 input_artifacts 到达、正常授权读取；不得旁路把上游 payload 或私有 Session 直接注入。

只有“报告存在”不能宣称上游已经消费。若模型未读或结论不对应，记录为模型消费缺口并保留现场。

另做一个不调用 LLM 的长文本边界探针：构造超过 8000 字符的单行 answer，在尾部放唯一可核验标记，经真实 build_module_report、Registry 和 read_artifact 路径读取。记录报告物理行范围，确认长行已按最多 1000 字符分行；先读前部，再显式读取尾部所在行，将后一次观察经现有 workspace_context 投影，验证尾部标记实际进入上下文、内容未丢失。原 payload 与构造前逐字相同。不能用“文件存在”代替读取验证，也不能只直接读取磁盘绕过工具。此为确定性构造探针，不能冒充模型自然生成长答案的证据。

## 3. 修改与实验风险按需交付

两个独立标准库探针，复用已有轻量环境，不安装 torch/numpy/pytest：

- Coding：修复一个小函数，用 `python -m unittest discover -s tests` 验证；任务明确要求说明一个真实未覆盖/不支持的边界。检查成功验证、真实 patch/代码工件仍齐全，非空 residual_risks 另进 module_report。
- Experiment：运行标准库脚本，写 `metrics.json={"value":42}`；声明这只是固定输入的冒烟结果、不能代表统计性能。检查真实命令、冻结数值、payload.metrics 与原证据对应；非空 residual_risks 另进 module_report。

两者报告以 `## summary`、`## residual_risks` 呈现该次 finish 选定内容，允许为分页新增展示换行但不删除解释内容；原 payload 原文不改。不把整个 payload、环境状态或原始日志塞进报告。Scientific 读取后应据实表达相关局限，不能将报告作为独立测量佐证。

空风险不应创建空报告，确定性用例需覆盖；真实模型自然产出空风险时可补记，不为凑场景删模型风险。原 gate 仍有效：无变更/无有效验证的 Coding 不因报告成功；无成功实验命令/无本次真实证据的 Experiment 不因报告成功。module_report 不进入 metrics 提取、不代替缺失 expected_artifacts、不更改 delivery_issues 的判断。

## 4. 原题＋短回答：三个 Agent 与跨进程

以 `requested_fields=["answer"]` 或 `["confirmation"]` 的短答案专门验证语义配对，不只用 `preferred_metric=accuracy` 这种仅凭键名即可推断的例子。

- Scientific：让模型先询问一个必须由用户选择的选项；根据它实际提出的原题回答“第二个”或“是”。恢复目标中不要再次写原题或替模型解释答案。最终意见须对应原题中的真实选择。
- Coding：两个行为不同的标准库实现/文件，模型先列出选项再询问；按实际原题回答“第二个”，恢复后读取/处理对应项，不读错项。
- Experiment：标准库脚本具有两个结果不同的模式（如 add 得 5、mul 得 6），先由模型询问再给短答案。按原题实际选项顺序核对命令与 metrics，不能预先假设“第二个”一定是 mul。

至少一项通过完整 Controller 问答和两个独立进程执行，其余若用子 Agent 驱动需如实标明：驱动按正式契约构造系统配对的 RecordedAnswer，不能将其伪称为 Controller 原题来源验证。另做 CLI run→show→answer 冒烟，不新增 question_text 参数。

共同检查：

1. 外部仍提交 UserAnswer（question_id/values/answered_at）。Controller 从当前 PendingQuestion.text 构造 RecordedAnswer；持久化 question_text 与回答前原文精确相同，不取用户改写或旧问题。
2. 回答后 Run 保存完整配对。Scientific/Task 的原作用域不变，两个连续新问题的原题各自对应；错误 QuestionId/缺字段仍拒绝，重复答案不套给下一题。
3. 恢复调用的原始请求里有且仅一个 answers 段，每项同时有 question_text 和 values；上下文仍 required、计入既有预算。不能只检查 Run JSON，或以 Session 内留有原 action 代替模型可见。
4. 同 Run/Task/Attempt/Session 恢复，不新建另一次 Attempt，不重置 LLM 使用量或人工等待记录。
5. 最终行为和产物对应真实回答；“配对正确”“prompt 到达”“模型遵循”“结果正确”分别报告。

## 5. 原有核心回归

在新提交、fresh workdir、full trace 下跑既有场景，不改目标和预算：

- code-experiment：Flash 两次、Pro 一次；修改→验证→正式实验，报告与冻结指标对应。
- repair：Flash 一次；保留真实失败 stderr、修复和重跑；不能把新报告算成训练证据或隐去第一次失败。
- direct、literature：Flash 各一次；分别核对无图完成、真实读取/引用文献。
- ask-start→ask-resume：独立进程、同 workdir；按实际 requested_fields 回答，核对 RecordedAnswer 原题与 Scientific 消费。

GPU 核心回归可按原验收方式复用已有训练环境，务必记录绑定和安装指针；不要为 §2–§4 的轻量探针触发大型依赖安装。若环境缺失需安装大依赖，先报告耗时/前提并等确认，不能换目标掩盖。仅跑标准库探针不能宣称完整 E2E 全过。

## 6. 计量、安全与交付

- 解析错误、schema 校验、HTTP retry、Task Attempt retry 分开统计；按 call_id 去重主记录，再按 attempts 计量实际尝试，与 Run.llm_calls_used 对齐。schema 补充行不另算调用。
- 所有 trace 目录 0700、文件 0600；秘密扫描只报告命中/未命中，不打印匹配值。报告正文、日志、源码和用户输入均可能敏感，不能公开上传。
- 失败现场保留；诊断重跑使用新目录、与原失败并列。说明层和状态层分别判断，不把模型未遵循称为已证明框架回归，也不凭另一个模型通过断言原失败必属随机性。
- 交付 MANIFEST 与逐项结论、确切 HEAD/模型/环境指针、报告工件/原题/原始 LLM prompt/实际结果的路径及 call_id。本轮没有执行的场景标“未执行”，不沿用旧报告凑绿。
- 未经授权不合并、不 push、不恢复或清理旧 checkout、缓存、数据、环境或历史报告。
