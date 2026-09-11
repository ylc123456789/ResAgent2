# JSON 格式反馈验收

目标：验证坏 JSON 不执行、错误原因确实回到模型、在同 Session/Attempt 内有限恢复，预算与 trace 不失真。此轮不迁移原生 tools、不提高额度、不改业务 prompt。

首版 8cfd373 本地基线（2026-09-11）：独立 cwd、PYTHONPATH 指向仓库，**825 passed, 1 skipped**；mock_e2e completed，git diff --check 干净。相对 main 的 805/1 新增 20 个确定性用例。该版服务器验收已执行，存在实际失败及报告计数偏差；见 [复核更正](LLM_JSON_OUTPUT_FOLLOWUP.md#8cfd373-服务器复核与评审提示收尾)。以下 §1–§4 保留原验收要求；本次提示收尾按 §5–§6 补验，不重复安装大型依赖。

## 1. 同步与确定性检查

使用待验收分支 `fix/json-output-feedback` 的实际提交，新建干净 checkout/worktree，记录 HEAD、8 个包的 editable 指针与前值。不要 scp 零散源码，不合并 main、不清理旧数据。所有新增产物放一个独立验收根目录。

在 ResAgent2 环境、隔离 cwd 下运行（令 repo_dir 指向本次 checkout）：

```bash
PYTHONPATH="$repo_dir" python -m pytest "$repo_dir/tests" "$repo_dir/apps/cli/tests" -q
PYTHONPATH="$repo_dir" python -m e2e.mock_e2e
git -C "$repo_dir" diff --check
```

重点测试：tests/runtime/test_llm_recovery.py、test_llm.py、test_llm_attempt_trace.py；tests/orchestrator/test_compiler.py。持续坏 JSON 的 5 次上限、预算/超时、不执行非法前缀等由确定性测试验证，不用真实模型反复烧预算。

## 2. 真实回归

fresh workdir + full trace，沿用现有模型、目标、预算、数据集和镜像配置：

- code-experiment：Flash 两次、Pro 一次；Coding 改+验证，Experiment 真训练，冻结指标与报告对应。
- repair：Flash 一次；保留最初 stderr，正确修复后重跑，不把重跑成功抹成未失败。
- literature、direct：Flash 各一次；分别核对实际观察/引用和无图直接完成。
- ask-start → ask-resume：两进程、同 workdir，按实际 requested_fields 填答案；同 Session、答案消费正确。
- CLI 独立组合根：问答 run/show/answer 冒烟即可；不因此新装大型训练环境。

失败照实保留；重跑必须新目录并列报告。自然运行未出现坏 JSON，只能证明未复现，不能单靠全绿声称纠错路径生效。

## 3. 定向注入一次坏正文，后续仍用真实 LLM

验收驱动可在 ops/ 中包装测试进程的 urlopen，不能改产品文件、prompt、场景目标或预算。选定 agent 的一次真实成功 HTTP 响应，将交给客户端的 message.content 替换为纯空白或“合法动作 JSON 后跟额外文字”；原响应保存在权限受控的独立文件，并明确标记 injected，不能把注入内容当成 provider 自发失败的证据。保留其它返回元数据，后续调用不再改写。不得输出凭据。

分别覆盖 Coding、Experiment、Scientific（使用相应已有场景）；Compiler draft 可独立运行原编译入口，注入一次坏正文后观察现有重编，不能执行草图任务。每项只注入一次，禁止一直替换到模型过关。

核对原始 prompt、trace、Session、Run：

1. 坏正文对应 action_valid=false、主记录 validation_error 非空，原始正文仅在 full trace；该响应不产生 action/工具执行事件。
2. Agent 下一次调用 included_sections 中恰有一个 runtime_feedback，正文有解析原因及纠正要求；同 Session/Task Attempt，之前已完成操作不被重放。Compiler 则在 compiler_request 的既有 rejection feedback 中出现解析原因，无 AgentLoop/Session。
3. 下一次请求为新 call_id，而非同一主记录内原样 HTTP retry。若发生真实网络重试，仍按每次 attempts 正确记录。
4. 原始模型是否按反馈产出合法动作、恢复后真实结果是否正确，分别报告；没有恢复也保留失败。不要把“反馈正确到达”等同于“模型一定遵循”。
5. Run.llm_calls_used 与去重 call_id 后 attempts 长度求和一致，schema 校验补充行不算新调用。注入是在一次真实 HTTP 返回后替换正文；若使用完全模拟的响应，必须单列模拟次数，不能冒称实际 provider 消耗。

## 4. 汇总与安全

解析错误、schema 错误、HTTP retry、Task Attempt retry 分开统计。检查 trace 0700/0600、秘密扫描，原响应备份同样限制访问。输出 MANIFEST、逐项结果、真实/注入分类、证据路径与 call_id；记录环境安装指针变化，不合并、不删除现场。

<a id="compiler-closeout"></a>

## 5. 生成/评审共用字段语义补验

本次本地基线：**828 passed, 1 skipped**（相对 8cfd373 新增 3 个用例），编译器专项 59 passed，隔离 cwd 的 mock_e2e completed、git diff --check 干净。以下真实补验尚未执行。

新提交仍在 fix/json-output-feedback；同步实际 HEAD、干净 worktree，记录 editable 指针，先重跑 §1 确定性检查。生产变更只有 compiler.py 的共用提示，不改模型、预算、规范化或真实实验脚本。

使用旧 `code-exp-pro-1` 的 WorkRequest（保持目标、证据要求、约束）调用**新代码的 LLMWorkflowCompiler 入口**，Pro 两次、Flash 一次，各独立目录；只生成/校验 WorkflowProposal，不执行任务，不安装 torch。不能直接重发旧 request_text，否则测不到新提示。

逐条检查 full trace：

- draft/review 都且仅一次包含相同的字段说明，解释 expected_metrics/expected_artifacts/suggested_paths 有意留空；review 看见的仍是与物化器一致的 inputs。
- 任务仍覆盖实现 → 正式实验，并带正确依赖；语义要求仍包含基线/候选准确率和工件交付，条件诊断不升级为无条件要求。
- review 不再仅因这三个字段为空而拒绝，不要求猜测路径或键名。若因真实语义遗漏拒绝，保留理由并如实分析；不能屏蔽拒绝、改目标或重跑到绿后丢弃失败。
- 编译产物仍保留空数组；llm_calls 与 trace 的实际尝试一致，输入计量不超模块上限。最多两版 draft 的原有边界不变。

本地字符串断言只证明提示到达，不证明模型一定遵循。未完整重跑 GPU 矩阵不能声称“新提交完整训练回归全过”。

## 6. 轻量注入闭环与旧报告更正

### 6.1 新增两个明确标注的标准库探针

这不是修改原训练用例，而是新建两个小任务隔离 JSON 纠正机制。复用现有 NativeCodingAgent/NativeExperimentAgent 与正常预算，不给它们专用 prompt、额外工具或更高上限：

- Coding：提供一个错误的整数加法函数和 `unittest` 测试，要求修复并验证；不需要 numpy/torch/pytest，使用允许的 `python -m unittest discover -s tests`。最终函数正确、测试通过、修改工件交付。
- Experiment：提供纯标准库脚本，执行后写 `metrics.json={"value":42}`；要求运行并交付指标证据。最终必须真实运行、冻结工件并交付 value=42，不只看 summary。

每个探针在一次真实、正文可解析且**动作及工具参数均校验通过**的响应上追加尾随文字。保存唯一原响应备份，明确记录 probe/run/session/call_id；不通过执行工具来判断原动作是否合法。随后完全放行真实响应。不要用“首次 Extra data”猜测注入身份，也不要覆盖旧备份。

每个探针用全新 workdir **及 trace 目录**，只注入一次。检查错误到达 → 恰一个反馈段 → 同 Session/Attempt → 无非法动作执行 → 最终正确完成，并保留中途 schema/JSON/工具错误。只恢复但最终失败必须分开写；不调高失败上限，不启动大依赖安装来测试格式纠正。

### 6.2 更正原报告，保留原件

在原证据根新增更正报告/统计文件，不覆盖 ACCEPTANCE_REPORT_JSON.md、accounting.txt、原 traces/Session 或注入备份：

1. 用完整解析错误分类（包括缺分隔符），核对已发现的 **36 次**；不能只匹配两种英文报错。其它传输/封装异常、schema 补充行另计，不把所有 validation_error 都无条件当解析错。
2. 按唯一 call_id 计量逻辑调用、逐 attempts 计量 HTTP 尝试；注入必须有确切运行/调用身份，未能确认的早期空白记录单列“混淆/未知”，不能硬归入自然错误。
3. `inject-coding` 的多轮调用按实际 run/session/时间线拆分；缺失的历史文件不能臆造。模型分别计数，混合模型及注入的总数不能作 Flash 自然错误率分母。
4. 将“每次恢复/全部完成”更正为机制是否有效、模型是否纠正、工具是否执行成功、任务是否完成四层结果；Coding 五次混合失败、Experiment 安装超时、Pro 评审拒绝均保留。

交付新提交/测试基线、三次编译探针、两个轻量注入探针、旧报告更正及证据索引；不合并、不删现场。补验不需要再跑大型依赖安装。
