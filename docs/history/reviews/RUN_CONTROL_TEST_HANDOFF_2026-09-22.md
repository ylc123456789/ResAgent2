# Run control schema 13 本地验收与服务器测试交接

日期：2026-09-22。状态：首轮服务器验收发现两处产品缺陷及证据偏差，本文件用于修复后的定向复测。原轮次和勘误见[服务器复核记录](RUN_CONTROL_SERVER_REVIEW_2026-09-22.md)。

## 1. 锁定版本与分支

- 实现分支：`refactor/run-control`，直接从未合并的 `refactor/unified-agent-entry` / `c0add70` 派生。
- 修复产品基线：`602ffeef597ba86a4bfa4de3c958caba9dddb9de`，schema `13.0`。测试使用分支最新提交，并记录实际 HEAD；产品基线必须是 HEAD 的祖先，不切到 detached HEAD。
- 分支推送到 `origin/refactor/run-control`，未合并。原服务器验收对应 `29d3ab80`；下面的本地结果属于新修复，真实模型复测仍待执行。
- 不恢复 schema 12 的 Run。旧证据原样保留，新测试使用新的状态目录。
- 方案：[Run 控制简化方案](RUN_CONTROL_SIMPLIFICATION_PLAN_2026-09-22.md)。

分段实现：`3bab62a` 控制契约；`d663b6d` 持久用量和共享期限；`05c4a78` 工作区、操作检查与单次批准；`b947261` CLI/E2E 集成。后续修复：`5221835` 补 Coding 删除动作；修复产品基线补命令批准后的执行前环境核验。

### 固定代码目录与更新方式

服务器开发和测试统一使用 **`/root/autodl-tmp/projects/ResAgent2`**。版本由该仓库的 Git 分支和提交管理，不按测试轮次复制整个项目，不再创建 `ResAgent2-<提交号>`、`product/` 或新 worktree。

本文件是唯一正式交接文档，保存在项目 `docs/history/reviews/`。服务器从远端更新同一仓库；以下替代此前的 bundle 克隆和独立 checkout 步骤：

```bash
cd /root/autodl-tmp/projects/ResAgent2
git status --short
git fetch origin
git switch refactor/run-control
git pull --ff-only
git merge-base --is-ancestor 602ffeef597ba86a4bfa4de3c958caba9dddb9de HEAD
git log -1 --oneline
```

服务器已经建立本地跟踪分支，后续更新直接在该分支执行 `git pull --ff-only`。开始正式测试前确认受版本控制的文件没有本地修改；发现修改先核对，不自动覆盖、清理或 stash。未跟踪的 Notebook checkpoint 等旧文件不要当作本轮产物。

同一轮测试期间保持代码提交和可编辑安装不变，不并行拉取、切分支或修改产品。开发有新提交时先结束当前轮次，再更新并建立下一轮证据目录。已经生成的 bundle 只是历史传输产物，本轮不再使用。

## 2. 本地已完成验证

环境：WSL Ubuntu-D，Python 3.12，项目 Conda 环境 `ResAgent2`，`PYTHONNOUSERSITE=1`。

```bash
python -m pytest tests apps/cli/tests -q --tb=short
# 1153 passed, 1 skipped
python -m pip check
# No broken requirements found.
git diff --check
```

验证包括：

- 发送前保存占用；保存失败不调用；恢复后 unknown 不退款；返回结果不重复计数；Scientific 工具登记的工件不会被用量保存覆盖。
- Compiler、纠错、压缩与客户端重试共用余额；最后一次调用得到非法草稿时，以 budget_exhausted 停止。
- 嵌套调用不能刷新期限；慢 HTTP body 被取消并关闭连接；慢 DNS 不延迟请求取消的返回。
- 受控进程及另起进程组的子进程在超时后终止；下一条命令不能重新获得完整超时。
- Run 创建即固定工作区与权限；后来修改解析器配置或调用方模型不能扩展已经保存的授权。
- 空允许列表拒绝、排除优先、子授权只能收紧；受限工作区不能通过批准取得任意脚本执行权限。
- 文本删除、单文件/空目录删除、递归删除确认、目标变化失效、符号链接和受保护目录检查。
- Coding 原生工具动作清单与 schema 一致；普通删除实际到达工具，递归删除可在重建 Agent 后用原问题答案恢复。
- Coding/Experiment 命令批准后重建环境绑定，在执行前真正核验；失败不执行，检查通过后生成命令产物，不再因认证丢失循环。
- 批准只消费一次，消费前保存；不同命令与相同命令的再次调用都不能重用批准；中断不自动重放。
- Git 快照只包含授权可读文件，不读取或输出被排除的已跟踪文件。
- CLI 展示 succeeded / failed / unknown 占用情况，旧权限/模式字段不再由产品接受。

本地回归不代替真实模型是否正确使用这些能力的验收。

## 3. 安装和运行纪律

所有产品代码都从上述固定目录运行。本轮测试 AI 只测试，不改产品、不放宽断言、不合并或推送；正常开发仍可在该仓库进行，须与测试错开。测试脚本、状态和产物统一放入已有的 `/root/autodl-tmp/resagent2/runs/` 管理目录，每轮创建一个证据子目录：

```bash
cd /root/autodl-tmp/projects/ResAgent2
conda activate ResAgent2
export PYTHONNOUSERSITE=1
mkdir -p /root/autodl-tmp/resagent2/runs
export TEST_EVIDENCE_DIR=$(mktemp -d /root/autodl-tmp/resagent2/runs/run-control-fix-20260922-XXXXXX)
mkdir -p "$TEST_EVIDENCE_DIR/logs" "$TEST_EVIDENCE_DIR/probes" "$TEST_EVIDENCE_DIR/cases"
git rev-parse HEAD > "$TEST_EVIDENCE_DIR/TEST_COMMIT"
git status --short > "$TEST_EVIDENCE_DIR/git-status.txt"
```

恢复测试终端时，将 `TEST_EVIDENCE_DIR` 设回本轮已有绝对路径；新轮次才新建目录。补测脚本放 `probes/`，场景工作目录放 `cases/`；这些是测试输入与产物，不是 ResAgent2 产品副本。尤其删除测试只能作用于场景小仓库，不能把产品仓库当作待删除目标。每次失败后重跑都使用新的 run_id 及独立状态、Session、trace 子目录，保留上次原始状态；正常问答恢复才沿用同一 Run，不能清空状态后复用原标识和 trace。

沿用现有 `ResAgent2` Conda 环境（Python 3.12），复用既有数据集、训练环境与缓存，不为每轮测试新建环境或重装 PyTorch/CUDA。上一轮已将 9 包 editable 安装到固定主仓库，且 httpx 0.28.1 / pip check 通过。本修复没有新增依赖，先执行下方导入检查及 pip check；二者通过就直接测试，无需重装。

仅当导入仍指向历史目录、包未安装或环境已变化时，从主仓库刷新可编辑安装：

```bash
cd /root/autodl-tmp/projects/ResAgent2
python --version
python -m pip check
python -m pip install --no-deps --no-build-isolation \
  -e packages/contracts -e packages/runtime -e packages/components \
  -e packages/capabilities -e packages/orchestrator \
  -e packages/agents/coding -e packages/agents/experiment \
  -e packages/agents/scientific -e apps/cli
python -m pip check
```

若缺少依赖，只补缺少项后重查。不得因 `--no-deps` 完成就认定安装通过。保存 Python 路径、`pip check` 原文；若实际安装了包，另存安装命令。导入检查保存到 `logs/import-paths.txt`：

```bash
python - <<'PY' > "$TEST_EVIDENCE_DIR/logs/import-paths.txt"
import importlib
from pathlib import Path
import sys

root = Path('/root/autodl-tmp/projects/ResAgent2').resolve()
print('Python:', sys.executable)
for name in ('contracts', 'runtime', 'components', 'capabilities', 'orchestrator',
             'coding', 'experiment', 'scientific', 'cli'):
    module = importlib.import_module('resagent2_' + name)
    path = Path(module.__file__).resolve()
    print(name, path)
    assert path.is_relative_to(root), f'{name} still imports from another checkout: {path}'
PY
cat "$TEST_EVIDENCE_DIR/logs/import-paths.txt"
```

镜像测速与下载预检仍属于测试环境运维，不是项目功能。本轮不修改服务器全局镜像配置；测试 AI 在缺依赖时先用一次性 pip 参数确认可用源，并保存实际源、耗时、退出码。不要对同一个失败下载无界重试。

## 4. 先跑确定性回归

在 `/root/autodl-tmp/projects/ResAgent2` 执行第 2 节的全量回归，保存原始输出和退出码到本轮 `logs/`。预期 `1153 passed, 1 skipped`，若因平台出现不同 skip，保留理由并单独报告，不改断言。版本以本轮 `TEST_COMMIT` 为准；相对上述产品基线如果已有产品变动，需要更新验收基线，不能仍声称只增加了文档。

重点审查文件：

- `tests/runtime/test_execution_budget.py`
- `tests/orchestrator/test_run_usage.py`
- `tests/orchestrator/test_controller.py::test_run_creation_freezes_grants_before_any_work_request`
- `tests/components/test_operation_permissions.py`
- `tests/components/test_process_deadline.py`
- `tests/components/test_workspace_access.py`
- `tests/capabilities/test_delete_path.py`
- `tests/coding/test_delete_invoke.py`
- `tests/e2e/test_command_approval_resume.py`

拒绝、取消、崩溃路径使用这些确定性测试即可，不额外花模型调用去制造随机故障。

## 5. 小规模真实模型验收

沿用已有模型配置与 trace 设置，记录 model、实际 HTTP 尝试、重试和 Run usage；不要把密钥写进命令记录或报告。每一项使用全新 Run，等待确认时由测试程序调用公开的 `controller.answer_question`。本次必须复测原 item1/2/3/4/6；item5 和 item7 已有原提交的通过证据，不必花模型或 GPU 再跑，报告标明未在新提交复测即可。

这些是能力探针：用小型固定任务明确 Coding/Experiment 角色及输入工件，可在测试脚本中通过正常 Task/Controller 入口固定任务图，以排除 Scientific 拆任务的随机性；Agent 本身仍使用真实模型和公开 invoke，不 mock 执行工具或环境审计。另列确定性测试时必须明确标记，不充作真实模型结果。

| 项目 | 操作与通过要求 |
| --- | --- |
| 修复与正常删除 | 在临时小仓库中请求 Coding 修复一个小错误、用 replace_text 删除几行，并用 delete_path 删除一个旧文件。授予明确写范围；关闭执行权限，避免不必要安装。正确完成，未越界，不因普通文件删除停下来确认。 |
| 目录清理确认 | 在隔离目录生成三个普通文件，请求递归清理。必须先暂停，question.action 含目标快照，批准前文件仍在；同一 Attempt/Session 恢复后只删除准确目标。另用确定性测试覆盖目标变化和部分删除，不靠改产品模拟。 |
| 两次命令逐条批准（item3） | 固定 Experiment Task，全读写可信工作区，execute_commands=true、prepare_environment=true、confirm_commands=true。先有 prepared 环境；准备操作自身可能需要批准，单独留存，不能充作两个目标命令。标准库脚本用两个不同参数生成 marker。首条目标命令批准前两个 marker 均不存在；第一次批准只产生第一个；第二条目标命令单独批准才产生第二个。保存结构化 action、两份命令问题/答案、每次前后 marker 快照及真实执行记录。批准恢复时自动核验环境成功后应实际执行，不依赖反复 audit_env 问答。 |
| 陈旧答案拒绝（item4） | 第二条目标命令 pending 时，经公开 answer_question 重交第一条命令答案。保存原始脚本、原始异常输出、拒绝前后 Run JSON 和全部 Session 原始字节快照以及 usage；必须拒绝且字节级不变，第二题仍 pending。随后提交正确第二题答案继续。拒绝调用不能触发模型。若未到达第二题，报告该路径未覆盖，不能仅凭最终态声明 PASS。 |
| 只读分析（item6） | 固定 Experiment Task 或直接 NativeExperimentAgent.invoke，给两个已有 metrics 工件和只读源目录，关闭执行/环境准备权限。必须保存实际 Experiment Session、输出工件及结果；command_count=0，源文件 hash 不变，正确比较数值。Coding/Scientific 输出或空 Session 列表不能代替 Experiment 的通过证据。 |

新脚本使用 schema 13：`RunBudget` 只有模型次数与时间；任务数/尝试数放在 `ExecutionLimits`；`permissions` 必填；工作区用 `WorkspaceAccess`；命令逐次确认用 `confirm_commands`。不要复制旧模式字段或旧全局确认开关。

原 item5 小预算终止和 item7 CUDA 短进程集成已通过，范围见复核记录。本次没有修改进程封装，无需重跑 GPU 或完整训练；两个 marker 的命令足够验证批准与执行恢复。若环境核验确实失败，保存真实审计结果并排查环境，不跳过核验。

既有完整流程可按需运行，不作为所有探针的前置：

```bash
cd /root/autodl-tmp/projects/ResAgent2
REAL_E2E_WORKDIR="$TEST_EVIDENCE_DIR/cases/direct" python -m e2e.real_e2e direct
REAL_E2E_WORKDIR="$TEST_EVIDENCE_DIR/cases/repair" python -m e2e.real_e2e repair
```

repair 的自动谓词仍按直接训练命令识别。复杂包装命令可能导致谓词不通过，应保留 FAIL 与原始命令，另报人工语义复核结论；不要自动把人工通过改成脚本 PASS。新规则可能对内联代码要求确认，这是预期控制行为，应通过公开问答继续；不能把暂停误报成执行失败。

## 6. 证据交付和判定

证据目录至少保存：

- REPORT.md、results.json、TEST_COMMIT（实际测试 HEAD）、git-status.txt、安装与 pytest 原始日志及导入路径检查；
- 每一探针脚本、Run JSON、Session JSON、所有 question/answer、完整 trace、命令日志和实际工件；
- 拒绝和目录确认前后原始快照，不只保留最终态摘要；快照需要实际落盘，不能只在脚本内存中比较；
- 所有失败、重试、中断和补测。新补测单列，不能覆盖原失败记录。

逐项报告 PASS / FAIL / BLOCKED，并分开“程序谓词”和“人工语义复核”。逐项 result.json、汇总 results.json、REPORT.md 必须一致；计数按实际项目行数计算。未达到目标路径的探针不能把空集合检查当通过。计量对账以每个独立 Run 的请求占用为准：每个 call_id/retry_index 唯一；正常完成时与该 Run 的 trace 对账；崩溃或保存后未发送时可能出现 unknown 或 trace 差额，逐条解释，不删记录。占用数不冒充供应商账单。

结束时再次核对 HEAD 与 `TEST_COMMIT` 一致、受版本控制的产品文件没有新增修改。历史证据保留原路径；旧项目目录的清理另行处理，不是本轮测试前置条件。

已知实现边界：同一 Run 串行、单执行者；批准消费与外部副作用不构成事务；宿主可信脚本执行不是 OS 沙箱。HTTP 取消不等待系统 DNS 线程，尚在 libc 中的解析可能后台结束，但其结果不会恢复已取消请求；本地取消也不保证供应商停止计费。
