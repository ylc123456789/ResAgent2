# 代码健康修复：本地收尾与服务器测试交接

日期：2026-09-22。实现分支：`fix/code-health`，从已合并的 `main@5fe2c7f` 派生，**本轮尚未合并**。产品与回归测试基线：`51c32381088e5b7d7face36d5e4586f53563aaa5`，schema **14.0**。后续文档提交不表示重新进行过产品验收；服务器记录实际测试 HEAD。

后续状态（2026-09-23）：本轮服务器回归与 mock 通过，两条真实模型整链均未完成；原始证据复核、报告勘误、根因修复及新版复测步骤见[服务器复核与根因修复复测](CODE_HEALTH_SERVER_REVIEW_2026-09-23.md)。以下保留原交接基线和操作记录；新复测以该文档为准。

原交接状态：三个实施阶段及本地确定性验证完成；真实模型服务器复测待执行。原始问题及复现见[主线代码健康审查](MAIN_CODE_HEALTH_REVIEW_2026-09-22.md)，当前行为以[契约](../../current/CONTRACTS.md)、[架构](../../current/ARCHITECTURE.md)、[模型上下文](../../current/CONTEXT.md)与[CLI 使用说明](../../../apps/cli/README.md)为准。

## 1. 本轮做了什么

| 范围 | 最终行为 | 主要提交 |
| --- | --- | --- |
| F1：问答入口 | Shell 与一次性 CLI 共用答案参数解析及组装；恢复只使用已保存的工作区授权，删除无效的恢复时补工作区入口 | `d05c357` |
| F2：安装环境 | pip 安装统一执行绑定环境的绝对 Python；拒绝其他解释器路径、改变安装目标的长短参数及缩写 | `e179c35` |
| F3：追加图与绑定失败 | Proposal/Patch 共用图校验；真实上游产物缺失/歧义写入不可重试的 failed Attempt，不遗留零 Attempt 的 pending 任务 | `91ce3c2` |
| F4：失败传播 | 按拓扑顺序传播依赖失败；工作轮任务全部真正终态才 stable，反馈与持久状态一致 | `91ce3c2` |
| F5：超时诊断 | 诊断 Git 失败保留原 AgentResult、Session、用量和已有工件；记录 diagnostic_patch_error，不把已修改任务自动重试 | `8ffa244` |
| 恢复监看 | Shell answer/resume 等待本次执行者完成，避免读到旧 paused 快照就提前返回 | `1bb35ec` |
| 确定遗留清理 | 删除无消费者的答案索引、辅助段、计数、通用快照包装、硬件/镜像辅助函数与手动 retry_task；测试专用 Tool 移入测试夹具 | `11d5adb`、`0161090`、`af1eed8`、`2571490` |
| 完整入口回归 | CLI → Scientific → Compiler → Coding 确认 → Shell 恢复 → 追加 Experiment → CLI 回答 → 引用证据并完成 | `51c3238` |

架构保持：三个 Agent 仍各只有 `invoke(AgentRequest) -> AgentResult`；业务输入仍为 instruction/input_artifacts，输出为 report/artifacts。Controller、Compiler、Scheduler 的职责、追加工作流程、单次批准、共享 Run 预算及权限收窄规则不变。没有新 Agent 模式、兼容层、AgentBase 或资源管理框架。

删除 `ResearchRun.answer_task_ids` 改变了持久模型，按既有版本规则升至 schema 14；RecordedAnswer 与冻结 answer 工件继续保存答案作用域。schema 13 及更早的 Run **不能用新版恢复**，旧 state/session/trace 保留，不迁移或清理。

Coding 只保留恢复和差异交付真正使用的 GitBaseline；Experiment 不再为纯分析生成无消费者的全工作区快照。显式环境清理维护 API 保留。Runtime 的注入客户端路径与 Compiler 的 JSON 协议保留，不把不同职责强行改成同一种传输。

数据集、环境与依赖缓存设计不变。pip 配置、镜像与缓存继续由部署管理；本次仅约束 run_setup 命令的解释器与目标参数。部署配置和项目安装脚本仍属于可信输入，这不是 OS 沙箱，也不承诺拦截任意安装脚本的宿主副作用。

## 2. 本地已完成的验证及边界

环境：WSL Ubuntu-D，Python 3.12，Conda 环境 ResAgent2，`PYTHONNOUSERSITE=1`。

```bash
python -m pytest tests apps/cli/tests -q
# 1224 passed, 1 skipped in 30.27s

python -m e2e.mock_e2e
# run=run_golden status=completed artifacts=9
# Coding / Experiment 各 1 个 completed Attempt，最终报告已登记

git diff --check
# clean
```

全量回归包括新增的 [test_public_native_lifecycle.py](../../../tests/e2e/test_public_native_lifecycle.py)：只替换 HTTP send_request 响应，使用真实生产装配、三个 Agent、Compiler、权限判断、文件工具、Git、Run/Session 持久化。测试实际删除目录、读取并冻结 metrics、保存两份回答、恢复同一 Attempt/Session、完成两轮工作并核对共享用量。

该测试在同一 Python 进程内多次重建应用，**不是独立进程重启验收，也不是真实模型测试**。本轮本地没有联网安装依赖、调用模型或运行 GPU。阶段性总数曾更高，最终因删除专测旧代码的测试而减少；保留路径的断言没有放宽，新增整链回归已计入 1224。

确定性负向覆盖包括：安装目标绕过、非法追加图、上游产物缺失/歧义、倒序 DAG 失败、修改后超时/Git 诊断失败、工作区授权不能在恢复时扩展。服务器不需要再用付费模型制造这些确定性错误。

## 3. 固定仓库与测试目录

继续只用 **`/root/autodl-tmp/projects/ResAgent2`** 管理产品代码，不建立新克隆、product 副本或 worktree。测试 AI 不改产品、不放宽断言、不合并或推送。

```bash
cd /root/autodl-tmp/projects/ResAgent2
git status --short
# 如有受版本控制的本地修改，先核对；不要自动 reset、clean 或 stash。
git fetch origin
git switch fix/code-health
git pull --ff-only
git merge-base --is-ancestor 51c32381088e5b7d7face36d5e4586f53563aaa5 HEAD
git log -1 --oneline

conda activate ResAgent2
export PYTHONNOUSERSITE=1
mkdir -p /root/autodl-tmp/resagent2/runs
export TEST_EVIDENCE_DIR=$(mktemp -d /root/autodl-tmp/resagent2/runs/code-health-20260922-XXXXXX)
mkdir -p "$TEST_EVIDENCE_DIR/logs" "$TEST_EVIDENCE_DIR/probes" "$TEST_EVIDENCE_DIR/cases"
git rev-parse HEAD > "$TEST_EVIDENCE_DIR/TEST_COMMIT"
git status --short > "$TEST_EVIDENCE_DIR/git-status-before.txt"
```

测试期间不拉取、切分支或修改可编辑安装。测试脚本统一放 probes/，小型输入仓库放 cases/，原始日志和证据放本轮目录。删除只能作用于场景目录，不对产品仓库操作。每次重跑创建新的 run_id、data root 和 trace 目录；问答恢复才复用同一 Run。旧失败、暂停和中断现场不覆盖。

沿用已安装的 ResAgent2 环境，不重装 PyTorch/CUDA，不改变全局镜像。先检查九包来源和依赖：

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

两项通过就直接测试。仅当 editable 指针错误或包未安装时，在该仓库执行：

```bash
python -m pip install --no-deps --no-build-isolation \
  -e packages/contracts -e packages/runtime -e packages/components \
  -e packages/capabilities -e packages/orchestrator \
  -e packages/agents/coding -e packages/agents/experiment \
  -e packages/agents/scientific -e apps/cli
```

之后重做来源检查和 pip check；命令成功不等于依赖齐全。本轮没有新增依赖，若环境已损坏，只补实际缺项。确需下载时，先用一次性 pip 参数做小文件预检并保存源、耗时和退出码；镜像预检属于运维，不加入项目，也不无界重试。

## 4. 服务器确定性回归

```bash
set -o pipefail
python -m pytest tests apps/cli/tests -q --tb=short 2>&1 | tee "$TEST_EVIDENCE_DIR/logs/pytest.txt"
printf '%s\n' "${PIPESTATUS[0]}" > "$TEST_EVIDENCE_DIR/logs/pytest.exit"

python -m e2e.mock_e2e 2>&1 | tee "$TEST_EVIDENCE_DIR/logs/mock-e2e.txt"
printf '%s\n' "${PIPESTATUS[0]}" > "$TEST_EVIDENCE_DIR/logs/mock-e2e.exit"
```

预期 1224 passed / 1 skipped、mock completed。出现平台差异保留原文和理由，不改测试或凑数量。任一项失败先定位，不在失败基线上继续消耗模型/GPU。

## 5. 一条真实模型公开入口整链

本轮重点是生产入口、跨进程回答和第二轮工作，不需要 GPU、训练、依赖安装或文献联网。使用已有模型配置，记录 model/API endpoint（不记密钥），启用 full trace。不要用固定 Scheduler 图、脚本客户端或替换工具执行冒充本项。

准备独立小型 Git 仓库，例如 `cases/public-chain/repo/`：

- obsolete/ 内两个普通文件，作为递归删除目标；
- metrics.json：`{"baseline": 0.45, "candidate": 0.52}`；
- 保存初始文件清单、内容 hash，执行 git init；不要复制 ResAgent2 产品代码。

将下列自然语言目标写入本场景 goal.txt：

> 请完成两轮顺序工作。第一轮交给 Coding，通过 delete_path 一次递归删除 obsolete 目录，不拆成逐文件删除，并等待系统的目标确认。该工作完成并收到反馈之后，再提出第二轮工作交给 Experiment：仅分析现有 metrics.json，不运行命令、不安装依赖、不修改该文件。开始分析前，让 Experiment 询问我这些数值对应的主指标；收到回答后报告 baseline、candidate 及差值，并把原有 metrics.json 作为证据工件交付。最后读取该证据并给出最终报告，明确这是已有测量的比较，没有开展新实验。

示例启动参数（目标及路径来自本轮目录）：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$TEST_EVIDENCE_DIR/cases/public-chain/traces"
resagent2 run --run-id run_health_public_01 \
  --workspace "$TEST_EVIDENCE_DIR/cases/public-chain/repo" \
  --goal-file "$TEST_EVIDENCE_DIR/cases/public-chain/goal.txt" \
  --data-root "$TEST_EVIDENCE_DIR/cases/public-chain/data" \
  --no-execute-commands --no-prepare-environment \
  --max-llm-calls 60 --timeout-seconds 1200 --max-tasks 4
```

以实际 pending question 为准，保留每次命令的原始输出和退出码；`3` 是正常 paused，不等于失败。第一轮涉及写授权，整个源目录不是强制只读；Experiment 阶段“仅分析”需以实际 hash 与无命令记录验收。

恢复和验收顺序：

1. 到达 Coding 的删除硬确认时，保存 Run JSON、相关 Session 原文、pending_question/action、目标文件清单及 hash。确认 action.tool=delete_path 且快照准确，批准前目录仍在。
2. 让启动命令退出，在**新 OS 进程**执行 `resagent2 shell --data-root <本场景 data>`，输入 `/show run_health_public_01` 选定 Run，再按真实字段执行 `/answer yes`。只批准本场景准确目标。保存 shell 会话；监看应跟随本次执行者，不因旧 paused 快照提前宣称结束。
3. 到达 Experiment 的主指标问题后，保存第二次 Run/Session 快照；确认删除已经完成，Scientific 创建了第二个 WorkRequest，Workflow revision 至少 2。退出 shell，再在新 OS 进程执行 `resagent2 answer run_health_public_01 --field <实际字段>=accuracy --data-root <本场景 data>`。
4. 最终 Run completed；三个 Agent 和 Compiler 均真实参与。Coding 删除前后、Experiment 回答前后的 Task/Attempt/Session 身份保持连续；没有通过新 Attempt 代替问答恢复，预算占用累计不重置。
5. 指标仍为 0.45 / 0.52，差值 0.07；原 metrics 文件 hash 不变，无命令执行、安装或 execution_record。冻结 metrics 的内容/hash 对应源文件；Scientific 实际读取该工件，最终意见引用它，final_report 已登记，并说明没有新实验。
6. 每次目标问答都有完整 answer 工件快照和作用域；计量用每个独立 Run 的原始 trace、HTTP 尝试与 Run.usage 对账，保存全部轮次。

如模型先问额外澄清，只按事实回答、保留原文；不手改任务图或状态，不把未到达目标路径报告为通过。若达到预算或出现循环，保留 FAIL/BLOCKED 及现场，诊断后另开新 Run；不能在同一 Run 中重置预算、覆盖 trace 或放宽断言。模型未按要求形成两个工作轮，说明本条整链未通过，不由旧固定 Task 探针替代。

## 6. 证据与最终交付

交付证据根目录、REPORT.md、results.json、TEST_COMMIT。报告至少逐项列出：

- 安装来源与依赖检查；
- 全量确定性回归；
- mock E2E；
- 真实模型公开入口整链，单列删除确认、跨进程 Shell/CLI 恢复、追加工作、分析与最终引用、计量。

完整保存测试脚本、命令及退出码、每次暂停/回答前后的 Run 和 Session 原文、问题/答案与动作快照、真实 trace、目录和文件 hash、冻结工件及所有失败/中断记录。只保存最终态不足以证明中途没有变化。保留新增确定性补测的独立标记，不混同真实模型证据。

结束时核对 HEAD==TEST_COMMIT、受版本控制的文件干净，保存 git-status-after.txt。汇总用 PASS / FAIL / BLOCKED，并区分自动断言、人工复核和未覆盖项。本轮待完成的是上述小规模整链验收，不要求重跑历史 CUDA 训练或重新整理服务器目录。

