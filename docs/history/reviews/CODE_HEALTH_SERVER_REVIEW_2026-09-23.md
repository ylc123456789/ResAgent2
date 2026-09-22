# 代码健康服务器复核与根因修复复测

日期：2026-09-23。分支：`fix/code-health`，尚未合并。上一轮服务器测试 HEAD：`b6258c7535cb721341ae265e976bb4b8f975babd`。本轮产品与测试提交：`9b425f2a6582e375efc0f5dc1e61cee759c0a10b`，schema 仍为 **14.0**。服务器须记录实际测试 HEAD；后续文档提交不等于重新验收。

状态：根因修复与本地验证完成，新的真实模型整链待验收。本文件补充并接替[上一轮测试交接](CODE_HEALTH_TEST_HANDOFF_2026-09-22.md)的当前状态与复测步骤；原报告和失败现场保持原样。当前规范已同步至 [CONTRACTS](../../current/CONTRACTS.md)、[CONTEXT](../../current/CONTEXT.md)、[ARCHITECTURE](../../current/ARCHITECTURE.md)及 [CLI README](../../../apps/cli/README.md)。

## 1. 原始证据复核

服务器证据根：`/root/autodl-tmp/resagent2/runs/code-health-20260922/`。复核范围包括 REPORT.md、results.json、TEST_COMMIT、pytest/mock 日志、两条 Run 的 Run/Session JSON、原始模型 trace 及暂停快照。本轮只读取这些证据，未修改原报告、运行状态或模型记录。

- 九包来源、pip check、确定性回归 **1224 passed / 1 skipped**、mock completed 均通过。
- `run_health_public_01`：budget_exhausted；共 60 次模型请求，批准后 54 次，批准后实际累计 106 次 list_files 工具调用。不能把模型请求数写成工具调用数。
- `run_health_public_02`：删除真实完成、追加工作 revision 2、指标比较正确，最终因 `unsupported scientific artifact kind: data` 失败。
- 两条 Run 的 trace 与持久用量键分别 60/60、29/29 一致，retry 均为 0，无 unknown，call_id:retry_index 唯一。

### 1.1 批准后的操作语义缺口

Run 1 的 Compiler 把“一次递归删除，不逐文件删除”改成了“exactly one recursive delete_path call; do not make additional calls”。首次调用只发出了确认问题；恢复时模型明确认为再调用会违反一次调用限制，并假定系统已经自动执行，继而持续列目录。

已有批准匹配和单次消费逻辑正确，但恢复上下文只提供答案，没有清楚投影“该操作还未执行，批准后仍需提交原工具调用”。这是 Compiler 对约束的错误转译与恢复状态表达缺失共同触发的失败，不能只归因于模型漂移。

### 1.2 Experiment 能力说明陈旧，原报告覆盖范围需勘误

Run 2 的四个 Task（删除目录、询问指标、读取指标、报告与登记证据）实际全部为 `workflow_agent_kind=coding`，Attempt Session.module 均为 coding；Session 目录为 coding 4、experiment 0、scientific 1。29 次调用对应 Coding 20、Scientific 7、Compiler 2。

Compiler 收到的 Experiment 描述仍是“Run an experiment and record its measured metrics and artifacts”。原始推理据此把禁止执行的分析任务交给 Coding，并把剩余任务数误当成应生成的任务数。因此：

- 可以确认已有结果比较与零命令、源文件 hash 不变。
- **不能确认 Experiment 参与，不能把这一轮写成 Experiment 纯分析通过。**
- Run 为删除操作授予了写权限；“文字要求不修改 + hash 不变”证明行为遵循要求，不证明独立的硬只读文件授权。硬权限验证沿用已有确定性测试。

### 1.3 Scientific 已有纠错机制，但检查不完整

Run 2 的倒数第二次响应使用无效 verdict `supported_with_limitations`，已有完成检查成功反馈；下一次模型改为 `supports`，仍把输入证据作为 `ArtifactCandidate(kind="data", path="<已有 artifact id>")` 重新交付。该错误未在完成检查中被发现，离开 AgentLoop 后才由注册层拒绝，导致整个 Run contract_error。

所以问题不是“Scientific 没有任何纠错”，而是可纠正的输出错误越过了完成检查边界。最终注册层拒绝非法工件的行为正确，应保留。

## 2. 根因修复与边界

| 提交 | 改动 | 保持的机制 |
| --- | --- | --- |
| `5f88d4f` | Runtime 从既有 pending_action 生成必需的 pending_operation 上下文，带准确工具/参数和 not_executed；确认回执也标明尚未执行 | 恢复同一 Agent；当前答案决定是否重发；权限复验和执行前单次消费照旧 |
| `9ffc75c` | CLI 与 real E2E 统一引用 Agent 自身能力说明；Experiment 明确支持已有结果分析；Compiler 区分操作次数与工具调用次数、任务容量与任务目标，并把问答留在任务内 | Compiler 仍通过原 JSON 草图编译任务，不增加强制路由或业务模式 |
| `47d1944` | Scientific 完成检查从共享契约派生允许创建的工件种类，提前反馈非法 kind、输入/外来/伪造 Ref 及重复输出 | 沿用原 AgentLoop 的预算与连续失败上限；注册层继续复验身份、hash、磁盘内容 |
| `9b425f2` | 生产公开入口确定性整链加入真实失败中的 data 透传错误，验证反馈、纠正、最终完成和累计计量 | 使用真实 Controller/Compiler/Agents/工具/持久化，仅模型 HTTP 响应固定 |

三个 Agent 仍各只有一个 invoke(AgentRequest) → AgentResult，业务输入仍由 instruction/input_artifacts 表达。没有新增持久字段、兼容层、批准自动执行、额外重试系统或资源管理抽象；schema 保持 14.0。批准已消费而执行结果未知时，不能自动重放。

## 3. 本地验证

环境：WSL Ubuntu-D，ResAgent2 Python 环境，PYTHONNOUSERSITE=1。

```bash
python -m pytest tests apps/cli/tests -q
# 1237 passed, 1 skipped in 32.80s
python -m e2e.mock_e2e
# run=run_golden status=completed artifacts=9
# Coding / Experiment 各 1 个 completed Attempt
git diff --check
# clean
```

新增 13 项 Scientific 回归覆盖错误后纠正、预算/连续错误终止、输入/外来/伪造引用及合法本 Session 工件；删除恢复回归核对真实 HTTP 上下文、批准前目标存在、批准后只删除一次。公开整链回归核对能力说明、第二轮 Experiment、错误 finish 反馈及最终报告。

上述验证证明确定性路径正确，不证明真实模型必定按提示行动；公开整链在同一 Python 进程内重建应用，独立 OS 进程恢复仍由下面的服务器复测验收。本轮没有本地调用真实模型、联网安装依赖或使用 GPU。

## 4. 服务器复测

### 4.1 仓库与环境

仅使用 `/root/autodl-tmp/projects/ResAgent2`，沿用已装环境。测试 AI 不改产品、断言或状态，不合并或推送。先核对本地修改，再更新：

```bash
cd /root/autodl-tmp/projects/ResAgent2
git status --short
git fetch origin
git switch fix/code-health
git pull --ff-only
git merge-base --is-ancestor 9b425f2a6582e375efc0f5dc1e61cee759c0a10b HEAD
conda activate ResAgent2
export PYTHONNOUSERSITE=1
export TEST_EVIDENCE_DIR=$(mktemp -d /root/autodl-tmp/resagent2/runs/code-health-root-fix-20260923-XXXXXX)
mkdir -p "$TEST_EVIDENCE_DIR/logs" "$TEST_EVIDENCE_DIR/probes" "$TEST_EVIDENCE_DIR/cases"
git rev-parse HEAD > "$TEST_EVIDENCE_DIR/TEST_COMMIT"
git status --short > "$TEST_EVIDENCE_DIR/git-status-before.txt"
```

按[原交接 §3](CODE_HEALTH_TEST_HANDOFF_2026-09-22.md#3-固定仓库与测试目录)执行九包导入路径及 pip check 检查，通过后直接测试。本轮无新增依赖，无需 GPU、PyTorch/CUDA 重装或镜像测速；仅导入指针错误时按原交接修正 editable 安装。

按[原交接 §4](CODE_HEALTH_TEST_HANDOFF_2026-09-22.md#4-服务器确定性回归)保留全量 pytest/mock 输出与退出码，新的预期为 **1237 passed / 1 skipped**、mock completed。失败先定位，不继续消耗模型预算。

### 4.2 一条小规模真实模型公开入口整链

沿用[原交接 §5](CODE_HEALTH_TEST_HANDOFF_2026-09-22.md#5-一条真实模型公开入口整链)的公开 CLI、两轮工作、跨进程 Shell/CLI 回答、快照和计量方式。每次新测试使用全新 Run ID、data root 和 trace；预算仍为 max_llm_calls=60、timeout_seconds=1200、max_tasks=4。禁止固定 Scheduler 图或 mock 工具替代整链。

独立小型 Git 场景仓库包含 obsolete/ 内两个待删除文件，以及 metrics.json：`{"baseline": 0.45, "candidate": 0.52}`。保存初始清单/hash。目标文本改为以下明确的副作用约束：

> 请完成两轮顺序工作。第一轮交给 Coding，通过 delete_path 对 obsolete 目录完成一次递归删除，不拆成逐文件删除，执行前等待系统的目标确认。确认请求尚未删除目录；我批准后，同一任务应继续原操作，提交相同工具和参数完成删除。这里的一次是实际删除效果，不限制确认前后为完成该操作所需的工具提交。该工作完成并收到反馈后，再提出第二轮工作交给 Experiment：仅分析现有 metrics.json，不运行命令、不安装依赖、不修改该文件。开始分析前，由 Experiment 在同一任务内询问我这些数值对应的主指标；收到回答后报告 baseline、candidate 及差值，并把原有 metrics.json 作为证据工件交付。最后读取该证据并给出最终报告，明确这是已有测量的比较，没有开展新实验。

示例启动（先保存目标至 goal.txt 并准备 repo）：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$TEST_EVIDENCE_DIR/cases/public-chain/traces"
resagent2 run --run-id run_health_root_fix_01 \
  --workspace "$TEST_EVIDENCE_DIR/cases/public-chain/repo" \
  --goal-file "$TEST_EVIDENCE_DIR/cases/public-chain/goal.txt" \
  --data-root "$TEST_EVIDENCE_DIR/cases/public-chain/data" \
  --no-execute-commands --no-prepare-environment \
  --max-llm-calls 60 --timeout-seconds 1200 --max-tasks 4
```

验收必须根据实际持久状态、原始请求和文件事实：

1. 删除硬确认带精确目标快照，批准前目录存在。在新 OS 进程的 shell 中用 `/show run_health_root_fix_01` 选定 Run，再用 `/answer yes` 批准。恢复后的原始模型请求含 pending_operation、not_executed、准确工具/参数及当前答案；实际删除完成，后续当前上下文不再声称操作待执行。
2. 同一 Coding Task/Attempt/Session 跨进程延续；批准只消费一次。若有额外事实澄清按真实字段回答，保留过程，不将软澄清当成动作批准。
3. Scientific 收到第一轮反馈后提出第二 WorkRequest，workflow revision 至少 2。**第二轮实际 Task.workflow_agent_kind 和 Attempt Session.module 均必须是 experiment**；核对原始 trace，不凭报告文字认定。
4. Experiment 在该任务内问指标。在新 OS 进程执行 `resagent2 answer run_health_root_fix_01 --field <实际字段>=<实际答案> --data-root <本场景 data>`，一次完整填写所有 requested_fields；若问方向，答案为 higher_is_better。保存回答前后快照，Task/Attempt/Session 连续，累计预算不重置。
5. 比较结果 0.45、0.52、差 0.07；零命令/安装，metrics hash 不变，冻结证据内容/hash 与原文件相符。整个 Run 允许 Coding 删除，不能据此报告强制只读文件权限验证通过。
6. Scientific 实际读取 Experiment 证据，最终有效 scientific_opinion 引用原证据 ID，不把输入 data 重新注册为 Scientific 输出；Run completed，final_report 已登记。若模型自然产生错误 finish，保留原反馈与纠正；不要求付费模型故意产生错误，确定性回归已覆盖此路径。
7. trace 与 Run.usage 按所有调用和重试对账，保存独立的失败、中断或预算耗尽轮；不能复用 Run ID 重跑、清空预算或覆盖证据。

测试失败时保留状态与日志并报告实际覆盖范围，不用旧固定 Task 探针替代本次整链。模型未形成 Experiment 任务、未完成批准后操作或未完成最终报告，都属于本项未通过。

### 4.3 交付

交付 TEST_COMMIT、REPORT.md、results.json，以及命令/退出码、测试脚本、每次暂停与回答前后的 Run/Session 原文、answer/action 快照、完整 traces、源文件与冻结工件 hash。报告分别列出来源与依赖、回归、mock、真实整链；区分自动断言、人工复核、未覆盖项。结束记录 HEAD==TEST_COMMIT 和 git-status-after.txt。

原始 `code-health-20260922` 证据不改写；这是一轮新测试。本轮验收目标是公开入口小型整链的可靠完成，不扩大为 GPU 训练或长任务能力声明。
