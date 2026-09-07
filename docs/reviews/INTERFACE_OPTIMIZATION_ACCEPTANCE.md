# 接口契约优化：服务器验收单

## 1. 精确代码基线与纪律

待测代码：**a2c6afa2da07b33f864be74c9d6b067403b3ca8f**，分支 `fix/interface-contracts`。本验收单是其后续纯文档提交；服务器请 checkout 上述代码 SHA，不只按分支名猜测版本。

本地已完成：隔离 cwd 全量 **740 passed, 1 skipped**，mock E2E completed，`git diff --check` 干净。服务器尚未验收，不能沿用 schema 4.0 的历史成功记录。

只允许同步、安装指针核验、测试、分析和报告：

- 不修改产品代码、prompt、原 E2E 目标/约束/预算，不自动修 JSON，不重跑到绿后隐藏失败。
- 不合并 main、不 push，不清理旧 checkout、worktree、环境、缓存、数据集或失败现场。
- 使用新 worktree 与单一新产物根；优先 git bundle/fetch 保留 Git 身份，不 scp 零散源码。
- schema 5.0 不续跑旧 4.0 Run；旧记录原样保留。不要拿旧暂停 Run 测恢复。
- full trace 可用于审计原始请求、响应和 provider 返回的 reasoning；报告只摘必要诊断，不输出密钥或大量私人内容。

## 2. 环境及确定性测试

先记录 `git rev-parse HEAD`、`git status --short`、Python 版本和八包源码路径。若需改变 editable 指针，记录变更前后；不要删除/重建已有环境。也可使用明确的 PYTHONPATH 指向八个 src 和仓库，但须逐包验证实际导入指向目标 worktree。

已有服务器环境参考（先验证实际存在，不据此创建或删除）：

- Conda：`/root/miniconda3/bin/conda`；非交互 shell 可先 source `/root/miniconda3/etc/profile.d/conda.sh`。
- 环境名：`ResAgent2`；Python 与本地基线同为 3.12 系列。
- `RESAGENT2_CONDA_EXE=/root/miniconda3/bin/conda`
- `RESAGENT2_ENV_ROOT=/root/autodl-tmp/conda-envs-dev`
- `RESAGENT2_DATASET_ROOT=/root/autodl-tmp/datasets`，核实 catalog.json 与 CIFAR-10 就绪。
- 凭据从已有安全配置加载，不打印、不写进脚本，不开启 shell xtrace。

八个导入包：resagent2_contracts、resagent2_runtime、resagent2_capabilities、resagent2_orchestrator、resagent2_scientific、resagent2_coding、resagent2_experiment、resagent2_cli。

在新验收根的独立 cwd 执行，repo 指向已核验 worktree：

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo"
python -m pytest "$repo/tests" "$repo/apps/cli/tests" -q -p no:cacheprovider
python -m e2e.mock_e2e
git -C "$repo" diff --check
```

应为 740 passed、1 skipped；mock completed。若依赖或数量不符先解释，不改测试凑数。重点文件（已经包含在全量内，可另列结果）：

- tests/orchestrator/test_workflows.py、test_repair_flow.py：空候选、跨轮依赖接收拒绝，合法新轮依赖和旧失败记录保留。
- tests/orchestrator/test_compiler.py、test_controller.py：成功/失败按本次调用计数，typed 模型重验，不读实例历史计数。
- tests/contracts/test_registry.py、tests/orchestrator/test_schema_version.py：五个字段删除，旧版本拒绝且文件字节不变。
- tests/runtime/test_context.py、test_prompt_client.py：最终文本计量；预算前拒绝不调用 provider，计数为零而非复用上次次数。
- tests/e2e/test_composition_adapters.py、test_run_scopes.py、apps/cli/tests/test_cli.py：独立组合根共用适配，同轮读取、跨 Run 隔离。

禁止为了验证升级而读写用户旧 Run；旧文件保全由临时 fixture 验证。

## 3. 真实 E2E：9 次入口调用

所有场景用 full trace。每个独立 Run 使用新 workdir 和 trace 子目录；ask 两个进程必须共用同一 workdir，trace 可追加到同一文件。不修改原场景预算。

| 场景 | 模型 | 次数 | 验收重点 |
|---|---|---:|---|
| code-experiment | deepseek-v4-flash | 3 | 实际实现、验证、训练、JSON 指标与科学意见 |
| code-experiment | deepseek-v4-pro | 1 | 分段读取/编辑及 Compiler 新预算回归 |
| repair | deepseek-v4-flash | 1 | 真实 totla NameError → 修正 → 重跑；两个 WorkRequest |
| direct | deepseek-v4-flash | 1 | completed/inconclusive，无多余任务图 |
| literature | deepseek-v4-flash | 1 | 真实检索、同轮登记读取、内容支持引用 |
| ask-start → ask-resume accuracy | deepseek-v4-flash | 2 个进程 | 非空问题字段、同 Session、答案落盘并采用 |

执行方式沿用原入口：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_MODEL=deepseek-v4-flash
export REAL_E2E_WORKDIR="$accept_root/workdirs/ce-flash-1"
export RESAGENT2_LLM_TRACE_DIR="$accept_root/traces/ce-flash-1"
python -m e2e.real_e2e code-experiment
```

其余按矩阵切换目录、模型与场景。ask-start 完全退出后，再以新进程执行 `python -m e2e.real_e2e ask-resume accuracy`。分别保存 stdout/stderr 与真实退出码，不被管道 tail 的退出码代替。

## 4. 必须看 full trace 原始消息

### 4.1 Compiler / 预算 / 计量（本轮核心）

- 核对 trace.model，不只看环境变量。
- 所有 workflow_compiler draft/review 调用的 included_sections 都包含 system 与 compiler_request，estimated_tokens > 0 且 ≤4096；原始请求有对应正文，不再是裸 prompt + estimated_tokens=0。
- Composer 计量对象是已渲染 Context（含标题/分隔符），不是整个 HTTP 请求。trace.request_text 还附有 Action schema 指令；如从 trace 回算，应去掉末尾实际的 schema 指令再使用同一 estimator，不能直接将整个 request_text 与 4096 比较。
- E2E 仍保留原有无 ModelProfile 配置，只新增 Compiler 的 4096 模块上限。CLI 则保留显式 ModelProfile，适配器使用其容量 hook。不要宣称两入口的全部配置相同。
- budget 拒绝发生在 provider 调用前时可能没有对应 trace 行；结合 Run terminal_error 与 Session/调用记录判断，不能据此说未发生失败。
- Run 的 llm_calls_used 应包含 Scientific、Coding、Experiment、Compiler 的实际尝试；trace 按 call_id 去重主记录，用 retry_number+1 识别 provider 尝试，关联 validation 补充行不重复计数。ask/resume 的 Run 累计值不能再相加。
- 若发生编译失败，检查本次已发生调用仍入账；合法 CompilationError 的 compiler_usage_known=true；未知异常 false 必须单独报告为不完整计量。无失败时记录“真实路径未触发”，确定性测试覆盖不冒充真实故障。
- 读取原始 CompilationDraft/Review 响应及后续 graph：任务只属于本轮，依赖不指向旧 WorkRequest。不存在空图、越权猜任务身份或重复接受的图。

### 4.2 领域链路

- code-experiment：不是只看 completed。检查 SELayer.forward 实际被实现、相对基线有编辑、验证发生在最新修改之后、Experiment 真正 run_command、冻结 metrics.json 的数值与报告对应。对 warnings/delivery_issues 如实单列，不把软警告称为零缺口。
- repair：保留原始 stderr 的 `NameError: totla`，确认修复的是 `totla → total`，重跑测得 accuracy=0.8。历史失败 Task/Attempt 仍在；最终报告 Execution issues 仍记录失败。task 名称变化不作为失败或成功依据；hypothesis 为空时 not_applicable 可以合理，不能仅按 verdict 单词判失败。
- literature：检索得到工件后，同 turn 的 read_artifact 能读到刚登记内容；核对 artifact.run_id、Run 索引、SHA256、实际所读范围和下一 prompt 正文。结论必须由所引内容支持；只有题录/摘要就按摘要层证据报告，不能宣称读过论文全文。
- 新 Session 不再新增 literature_summaries 累计缓存，但短检索预览、完整冻结 JSON、观察 ID、原始事件仍保留；不得把删缓存误判为丢证据。
- ask/resume：requested_fields 非空、answer 确含 accuracy、同 Session 跨进程恢复，最终意见准确记录答案。只有 paused→completed 而答案未采用，不算问答通过。
- 已有读取时序、文件/工件各自6000正文上限、环境 prepared/certified 投影继续正确；若发生循环，定位模型当时看到的原始请求，而非凭“没改该 Agent”判定无回归。

### 4.3 外部故障与失败解释

外部 429/timeout 如实区分服务故障、结构化参数错误、代码错误与模型行为；保留首次失败现场。不同模型成功、单次重跑成功或 action_valid=true，都不能单独证明失败与代码无关。若需诊断重跑，用独立目录并并列报告，不覆盖矩阵结果。

## 5. CLI 独立组合根验收

E2E 通过不替代 CLI。CLI 参数解析和用户交互不改；使用新的显式 --data-root 与 full trace：

1. **问答**：`resagent2 run` 发起“只询问并记录用户评价指标偏好，不跑实验”的请求，预期 exit 3/paused；新进程 `show` 后，按实际 requested_fields 用 `answer --field name=accuracy`，预期 exit 0/completed。同一数据根，答案落盘，非空字段不凭名称猜测。
2. **编译 + 实验**：在新验收子目录用原 E2E 的 `_repo` 准备一份未实现 SE 的 fixture（仅准备仓库，不调用 E2E controller）。通过真正的 `resagent2 run --workspace ... --data-root ...` 执行固定目标：
   `Implement SELayer.forward in train.py, following the existing fixed training protocol on CIFAR-10. Run the script and compare baseline_accuracy and candidate_accuracy from metrics.json.`
   不新增上下文/任务/步数覆盖，使用 CLI 原默认值。保留实际编辑、验证、训练与证据，Compiler trace 包含 compiler_request。若耗时/服务失败也单独报告，不绕过 CLI 调用 controller 凑结果。
3. CLI 数据全部在显式根下；记录仓库内旧 .resagent2 的前后状态，不删除它，也不把历史残留误判为本轮写入。Coding/Experiment 使用同一个 ResourceLayout。

CLI 新输入是本节固定的额外验收案例，不修改原 E2E 场景文本。

## 6. 交付与完成门槛

新产物根建议用 `mktemp -d /root/autodl-tmp/e2e-interface-a2c6afa-XXXXXX`，下设：

```text
MANIFEST.md        # SHA、源码指针、模型、逐次状态/退出码/warnings/缺口
logs/
traces/
workdirs/
ops/               # 同步与分析脚本，绝不写入凭据
```

- Trace 目录 0700、JSONL 文件 0600；秘密扫描只报告命中数量/是否泄漏，禁止打印值。raw_reasoning_text 仅在 provider 返回且 full 档时存在，不要求凭空生成。
- MANIFEST 将科学论断→工件、错误→修复、调用→计量建立可复查的路径/call_id 对应；不要只交“全绿”表。
- 报告初始安装指针、最终指针、worktree 是否干净；如需恢复指针或清理另等用户授权。
- 全部矩阵及 CLI 达标、本地边界测试通过、未隐藏 warnings/未测项，才进入合并复查；本验收不自动授权合并。若失败，先交现场和原始消息分析，不擅改产品代码。
