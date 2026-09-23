# 科研目录与 Interpreter：服务器验收交接

日期：2026-09-23。分支：`fix/code-health`，不合并。

产品提交：`b31648d14793c690a642446d6e38da5bb81b7e53`。产品及专项回归基线：`42efaa1653f160cbb7aec888d9a426739818d38b`。后续文档提交不改变产品代码；服务器记录实际测试 HEAD。当前 schema 为 **15.0**。

## 1. 本轮范围与不变的规则

Run.artifacts 仍是唯一产物登记表。Interpreter 的固定代码把登记材料按初始输入、科研产出、各轮原始研究目标组成 research_index。目录沿用原 Artifact ID，不保存另一份路径、hash 或授权；旧目录快照保持可读。

每轮工作稳定后，Controller 保存完整 work_record；Interpreter 的 LLM 阅读实际材料，产生每条带引用的简报；Controller 保存 work_feedback 和本轮目录变化，再交给 Scientific。Scientific 仍需通过 read_artifact 查看原证据，目录、简报及 Interpreter 的阅读都不算它已经阅读原证据。

三个 Agent 仍只有 invoke(AgentRequest) → AgentResult。Interpreter 是注入的普通组件，没有 Session、工具循环或独立预算。原权限、批准消费、任务重试和最终验收规则不变；本轮不重做 validation 分层。引用可追溯不保证模型解释一定正确，真实模型简报仍需人工对照原证据检查。

当前说明见 [CONTRACTS](../../current/CONTRACTS.md#interpreter)、[CONTEXT](../../current/CONTEXT.md)、[ADR-0017](../decisions/0017-research-index-and-work-interpreter.md)。

## 2. 本地验证与边界

```text
python -m pytest tests apps/cli/tests -q --tb=short
1269 passed, 1 skipped in 32.80s

python -m e2e.mock_e2e
run=run_golden status=completed artifacts=13
Coding / Experiment 各 1 个 completed Attempt，final_report 已登记

git diff --check
通过
```

新回归覆盖：失败尝试的材料保留、最终报告分类、目录增量、历史目录链接、原证据 hash/作用域、非法引用纠正、二进制未读引用拒绝、窗口截断、共享预算、最后额度保存、超时和交付恢复。

公开 CLI 整链使用真实装配，仅替换 HTTP 响应；在同一 Python 进程内重建应用。恢复边界测试使用磁盘存储和模拟崩溃异常。两者都不能当作真实模型或实际 OS 进程重启的证据。本地没有运行真实模型、GPU、网络安装。

## 3. 只使用服务器现有主仓库

产品仓库固定为 `/root/autodl-tmp/projects/ResAgent2`，不创建新克隆、product 副本或 worktree。测试 AI 不修改产品代码和断言，不合并或推送。

```bash
cd /root/autodl-tmp/projects/ResAgent2
git status --short
# 有受控文件改动先核对，不自动 reset、clean 或 stash。
git fetch origin
git switch fix/code-health
git pull --ff-only
git merge-base --is-ancestor 42efaa1653f160cbb7aec888d9a426739818d38b HEAD

conda activate ResAgent2
export PYTHONNOUSERSITE=1
mkdir -p /root/autodl-tmp/resagent2/runs
export TEST_EVIDENCE_DIR=$(mktemp -d /root/autodl-tmp/resagent2/runs/research-handoff-20260923-XXXXXX)
mkdir -p "$TEST_EVIDENCE_DIR/logs" "$TEST_EVIDENCE_DIR/probes" "$TEST_EVIDENCE_DIR/cases"
git rev-parse HEAD > "$TEST_EVIDENCE_DIR/TEST_COMMIT"
git status --short > "$TEST_EVIDENCE_DIR/git-status-before.txt"
```

schema 14 及更早的 Run 不支持恢复。使用全新测试 Run 和 data root；旧 state、Session、trace 原样保留。测试期间不拉取或切分支。

沿用现有模型配置，不输出密钥。不需要 GPU、PyTorch 重装或全局镜像调整。本轮没有新增依赖；先检查九包来源和 pip check：

```bash
python - <<'PY' > "$TEST_EVIDENCE_DIR/logs/import-paths.txt"
import importlib
import sys
from pathlib import Path
root = Path('/root/autodl-tmp/projects/ResAgent2').resolve()
print('Python:', sys.executable)
for name in ('contracts', 'runtime', 'components', 'capabilities', 'orchestrator',
             'coding', 'experiment', 'scientific', 'cli'):
    module = importlib.import_module('resagent2_' + name)
    path = Path(module.__file__).resolve()
    print(name, path)
    assert path.is_relative_to(root), (name, path)
PY
python -m pip check > "$TEST_EVIDENCE_DIR/logs/pip-check.txt" 2>&1
```

两项通过就直接测试。仅当 editable 指针错误或缺少本地包时，从主仓库执行：

```bash
python -m pip install --no-deps --no-build-isolation \
  -e packages/contracts -e packages/runtime -e packages/components \
  -e packages/capabilities -e packages/orchestrator \
  -e packages/agents/coding -e packages/agents/experiment \
  -e packages/agents/scientific -e apps/cli
```

之后重新检查导入和依赖。若有外部依赖缺项，记录实际缺项再处理；不要让安装进入无界重试。镜像预检是运维工作，不加进产品。

## 4. 确定性回归与 mock E2E

```bash
set -o pipefail
python -m pytest tests apps/cli/tests -q --tb=short 2>&1 | tee "$TEST_EVIDENCE_DIR/logs/pytest.txt"
printf '%s\n' "${PIPESTATUS[0]}" > "$TEST_EVIDENCE_DIR/logs/pytest.exit"
python -m e2e.mock_e2e 2>&1 | tee "$TEST_EVIDENCE_DIR/logs/mock-e2e.txt"
printf '%s\n' "${PIPESTATUS[0]}" > "$TEST_EVIDENCE_DIR/logs/mock-e2e.exit"
```

预期 1269 passed / 1 skipped，mock completed、artifacts=13。有差异保留原始输出并定位，不修改断言凑数。此项失败时先停止付费模型测试。

## 5. 真实模型：两轮交接与跨进程恢复

使用现有模型与真实 CLI，不固定 Scientific/Compiler 输出、不替换工具执行。启用 full trace，保存模型名与 API endpoint，不保存密钥。

在 `cases/public-chain/repo/` 创建独立小型 Git 场景仓库（不是产品副本）：obsolete/ 有两个普通文件；metrics.json 内容为 `{"baseline":0.45,"candidate":0.52}`。保存初始文件清单及 hash，并执行 git init。

将以下目标写入场景 goal.txt：

> 请完成两轮顺序工作。第一轮请 Coding 通过 delete_path 一次递归删除 obsolete 目录，等待系统的准确目标确认。收到本轮反馈后，再提出第二轮工作：请 Experiment 仅分析现有 metrics.json，不执行命令、不安装依赖、不修改文件；分析前询问我这些数值对应的主指标。收到答案后，报告 baseline、candidate 和差值，并把原 metrics.json 作为证据工件交付。最后阅读原证据并给出科研意见与最终报告，明确这里只比较已有测量，没有新实验。

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$TEST_EVIDENCE_DIR/cases/public-chain/traces"
resagent2 run --run-id run_research_handoff_01 \
  --workspace "$TEST_EVIDENCE_DIR/cases/public-chain/repo" \
  --goal-file "$TEST_EVIDENCE_DIR/cases/public-chain/goal.txt" \
  --data-root "$TEST_EVIDENCE_DIR/cases/public-chain/data" \
  --no-execute-commands --no-prepare-environment \
  --max-llm-calls 80 --timeout-seconds 1200 --max-tasks 4
```

保存每条命令的输出和退出码；3 表示正常 paused。每次暂停、回答前后保存 Run/Session 原文与当前工件。不要只保存最终态。

1. 删除硬确认时，核对 action.tool=delete_path、目录快照准确、目标仍存在。退出启动进程。
2. 在新 OS 进程运行 `resagent2 shell --data-root <场景 data>`，用 `/show run_research_handoff_01` 选定 Run，再按实际字段 `/answer yes`。只批准本场景目标，保存完整 shell 会话。
3. Experiment 询问指标后，保存快照并退出 shell。在新 OS 进程执行 `resagent2 answer run_research_handoff_01 --field <实际字段>=accuracy --data-root <场景 data>`。如有多个字段，逐一按真实问题回答，不更改字段名或猜填。
4. 验证 Run completed；至少两个顺序 WorkRequest，三个 Agent、Compiler、Interpreter 均真实参与；回答前后同一 Task/Attempt/Session 延续，预算不重置。纯分析阶段无命令与安装；metrics 内容和 hash 不变，差值为 0.07。

本轮新增验收点：

- 每个已交付工作轮各有冻结 work_record、research_index、work_feedback；feedback_refs 与实际 WorkRequest 一一对应。目录条目使用原 Artifact ID，来源分组对应原始工作目标。
- 第二轮目录累计保留第一轮条目；index_changes 不重复第一轮已有且未变化的条目。最终目录包含 final_report，位于科研产出组，不在初始输入组。
- 简报每条非空且带引用；引用可解析到同 Run 已登记材料。对照该次 Interpreter 的 source_windows/完整 work_record，人工检查数字、成功与失败、限制都忠于原文；不把模块解释当测量，不从文件名猜内容。
- Scientific 的默认上下文有 research_materials 目录入口和当前 material_<feedback_id> 中的变化、简报；没有同时自动展开旧 work_brief、完整 work_record 和整份授权登记表。模型主动读取原记录后出现执行细节是正常行为。
- 原 metrics 工件被 Scientific 实际 read_artifact，最终意见引用原 ID；仅 Interpreter 阅读、目录列名或简报引用不产生 Scientific 的阅读记录。用 trace、observation_trace 和 Run 状态核对。
- 使用正式 RegisteredArtifactReader，按已保存 Run 的授权引用只读打开第一轮 feedback、它链接的旧 index、其中的原工件，核对内容和 hash。这个离线检查不算 Scientific 模型读取证据。
- Interpreter 调用记录的 agent 为 work_interpreter。正常每轮一次草稿；如结构纠正最多再一版，HTTP 重试按实际计量。所有调用进入同一 Run.usage。已保存反馈因回答或恢复而重新读取时，不应再次调用 Interpreter。

不要硬编码模型表述、额外澄清次数或总调用数。若模型未走到两轮目标路径，或证据不足，记录失败/未覆盖，不用确定性探针替代本项。重跑必须使用新 run_id、data root 和 trace；保留旧轮次。

## 6. 定向探针：失败材料仍在，解释不掩盖失败

确定性回归已覆盖失败 Attempt 后成功重试的累计目录。额外用真实 Interpreter 检查一次混合结果简报，不必再跑训练或安装。

探针脚本放 probes/。参照 [test_work_interpreter.py](../../../tests/orchestrator/test_work_interpreter.py) 构造小型测试材料：同一任务第一次失败、第二次成功，各有独立冻结报告/数据；失败写明退出错误，成功数据给出 accuracy=0.8，并说明仅一次小样本测量。用正式 ArtifactRegistry、build_research_index、LLMWorkInterpreter 和现有生产模型客户端，分配最多 4 次模型请求的 execution_budget，保存输入、原始输出、trace 和用量。

这是**人工构造执行事实的 Interpreter 探针**，必须在报告标明 fixture=true；不声称发生了真实 Agent 修复或命令执行。不要替换生产 Interpreter 为确定性实现。

检查目录同时保留失败与成功的原 ID、Attempt 编号和真实给定状态；简报忠实区分两次尝试，报告成功值和局限，引用对应已提供材料。只给最终成功、掩盖失败、把小样本当充分科学证据，均不算通过。这个探针使用独立预算，不能混入公开整链 Run 的计量。

## 7. 交付与计量

交付证据根目录、REPORT.md、results.json、TEST_COMMIT。逐项给出 PASS / FAIL / BLOCKED：来源与依赖、回归、mock、真实两轮整链、失败材料 Interpreter 探针。记录自动断言、人工语义复核、未覆盖部分，不将“有引用”写成“语义已被代码证明”。

每个独立 Run 对账 logical calls、HTTP 尝试、重试/纠错/压缩和 Run.usage；保留未知占用与中断现场。分清 Compiler 和 Interpreter。所有重跑分开统计，不将 trace 累积和最后一次状态混用。full trace 可能含任务数据，保存于证据目录，不提交产品仓库。

保留所有脚本、命令及退出码、前后 Run/Session 快照、问题与答案、目录/简报/原证据、hash、trace、失败和中断记录。结束时核对 HEAD==TEST_COMMIT、受控文件干净，保存 git-status-after.txt。本轮不需要重跑 CUDA、清理服务器目录或修改镜像。
