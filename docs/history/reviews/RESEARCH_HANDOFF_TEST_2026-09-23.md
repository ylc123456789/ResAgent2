# 科研目录与 Interpreter：服务器验收交接

日期：2026-09-23。分支：`fix/code-health`，不合并。测试计划已按用户要求加入必须通过的真实 GPU 训练整链；产品代码和 schema 不变。

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

沿用现有模型配置，不输出密钥。新增 §6 GPU 整链需要可用 CUDA 和已登记 CIFAR-10；其余场景不需要 GPU。先复用现有产品环境和下载缓存，不重装产品环境里的 PyTorch，不修改全局镜像。新的研究 Run 有自己的受管环境，可能需要从缓存安装训练依赖；具体准备见 §6。本轮产品没有新增依赖，先检查九包来源和 pip check：

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

## 6. 真实 GPU 整链：实现候选 → CUDA 训练 → 科研结论

本项必须执行，补齐 §5 没有覆盖的真实环境准备、命令执行、训练产物和数值解释。通过公开 `resagent2 run`，由真实模型驱动 Scientific、Compiler、Coding、Experiment、Interpreter；不用固定任务图或测试方手工执行训练来代替。

复用现有 [real_e2e.py](../../../e2e/real_e2e.py) 的 TRAIN_PY 场景：CIFAR-10 / ResNet18，baseline 已实现，candidate 的 SELayer.forward 待实现。两组分别遍历完整 50000 条训练样本 1 epoch，再在完整 10000 条测试样本上评估，seed=0，优化器和其他配置不变。它是实际训练的系统回归，不是仅做 CUDA 矩阵乘、导入或几个 batch 的 smoke；单 epoch、单 seed 的结果也不能支持普遍有效性或统计显著性结论。

此处只复用实验输入，**不运行 `e2e.real_e2e.run_full`**。后者有固定 Run ID 和独立装配；本项改用唯一 Run ID 与公开 CLI，以覆盖用户实际入口，避免旧环境或状态混入。

### 6.1 正式启动前确认资源

检查脚本与记录只放本次证据目录的 probes/、logs/、cases/，不加入项目，不修改产品或全局镜像。

- 保存 `nvidia-smi`、驱动、设备型号与可用显存；在已确认可用的 CUDA Python 中核对 torch/torchvision 版本、torch.version.cuda、CUDA 可用性，并做一次很小的 GPU 张量操作。这里只证明基础设施，不算正式训练。
- 核对部署的 `RESAGENT2_DATASET_ROOT`、catalog 中 cifar10 的实际目录；通过 torchvision 的 CIFAR10(download=False) 检查 train/test 可读、长度为 50000/10000。保存目录清单和数据校验依据，不下载、不改共享数据集，也不换成合成数据或缩小数据子集。
- 明确记录 `RESAGENT2_ENV_ROOT` / `RESAGENT2_RESOURCE_ROOT` 的实际值并沿用。环境按 run_id + workspace_id 绑定：新 Run 可能需要新环境，旧 Run 中装好 torch 不表示本 Run 已有 torch。不要复用旧 Run ID、伪造 ready 标记、把宿主 Python 冒充受管环境，或手改环境绑定。
- 依赖优先用已有 Conda/pip 缓存或已准备齐全的本地 wheelhouse；预检记录要证明目标 Python 所需包可取得，不能仅凭缓存目录存在判为通过。锁定已验证相互兼容、适配目标 Python 和驱动的 torch/torchvision **实际版本（包括 CUDA 构建后缀）**，不因宽泛的 `torch>=2` 临时拉取新大包。pip 缓存不保证完整可离线安装；使用 wheelhouse 时先确认全部传递依赖齐备，再通过进程级 `PIP_NO_INDEX=1`、`PIP_FIND_LINKS` 传给本轮测试。Conda 基础 Python/pip 的可用性也单独确认，不能只检查 pip 源。
- 如果确需网络，先在正式 Run 外核对目标版本文件和连接；沿用已经可用的部署源，失败再使用已验证可用的源，记录选择与退出码。对本轮进程设置有限的 pip 超时/重试（例如 PIP_TIMEOUT=20、PIP_RETRIES=1）；连接成功不等于大 wheel 已下载完。所需大包尚不可得时先报告资源 BLOCKED，不启动 Run 再耗光预算。不要强装最新版、无界换源重试或改全局 pip 配置。

把准备记录保存为 `cases/gpu-chain/preflight.json`，包括 Python 绝对路径/版本、torch/torchvision/CUDA 版本、GPU、数据规模、依赖来源、相关配置和准备耗时；不包含密钥。仅当资源可用且依赖获取方式明确时启动正式 Run。真实 Run 内仍由现有 prepare_environment、run_setup、audit_env 和执行工具完成绑定与核验，不绕过它们。正式训练前必须再次检查该 Run 实际受管前缀里的 torch/torchvision 和 CUDA；预检使用的另一个 Python 通过检查不能替代这一项。

### 6.2 准备独立实验输入

所有输入位于 `cases/gpu-chain/repo/`。这只是小型实验仓库，不是另一个 ResAgent2 副本。从预检事实填写并导出 GPU_PYTHON_VERSION（major.minor）、GPU_TORCH_VERSION、GPU_TORCHVISION_VERSION；不得猜版本，也不要在日志打印凭据。

从产品仓库根目录执行以下准备代码。它只提取原 TRAIN_PY、写入已确认的依赖版本，不实现 SE，也不预生成 metrics；清单同时保存启动前 metrics 不存在的检查。

```bash
python - <<'PY'
import hashlib
import json
import os
import subprocess
from pathlib import Path
from e2e.real_e2e import TRAIN_PY

case = Path(os.environ['TEST_EVIDENCE_DIR']) / 'cases/gpu-chain'
repo = case / 'repo'
repo.mkdir(parents=True, exist_ok=False)
(repo / 'train.py').write_text(TRAIN_PY, encoding='utf-8')
(repo / 'requirements.txt').write_text(
    f"torch=={os.environ['GPU_TORCH_VERSION']}\n"
    f"torchvision=={os.environ['GPU_TORCHVISION_VERSION']}\n", encoding='utf-8')
(repo / '.python-version').write_text(os.environ['GPU_PYTHON_VERSION'] + '\n')
assert not (repo / 'metrics.json').exists()
subprocess.run(['git', 'init', '-q', str(repo)], check=True)
subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Acceptance',
                '-c', 'user.email=acceptance@example.invalid',
                'commit', '-qm', 'Freeze GPU acceptance input'], check=True)
(case / 'input-manifest.json').write_text(json.dumps({
    'product_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'fixture': 'e2e.real_e2e.TRAIN_PY',
    'initial_metrics_exists': (repo / 'metrics.json').exists(),
    'input_commit': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
    'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (repo / 'train.py', repo / 'requirements.txt', repo / '.python-version')},
}, indent=2), encoding='utf-8')
PY
```

在场景 repo/ 之外保存 goal.txt，内容如下。仅把目标、资源事实和明示约束交给系统，不将评分规则或参考补丁放进工作区：

> 请在当前 CIFAR-10 / ResNet18 项目中完成 SE 候选并与 baseline 比较。train.py 的 SELayer.forward 尚未实现；按其中给定的全局平均池化、两层 FC 与 sigmoid 定义实现通道缩放，其余模型、优化器和数据处理保持不变。随后在 CUDA 上执行 train.py --epochs 1 --seed 0，两组都完成完整 CIFAR-10 训练集的一个 epoch，并在完整测试集评估，不缩小数据、不以 smoke 代替。使用已有 cifar10 数据资源和 requirements.txt 中的依赖，不下载数据。保留原始训练日志、执行记录与 metrics.json，比较两组 accuracy 并给出报告；候选不必胜出，结论必须说明单 epoch、单 seed 的局限。

测试方只准备输入和资源；启动后由被测系统改实验代码、准备环境和正式训练。不要手工补 SE、运行正式训练、填写指标或改输出判据来帮助它通过。

### 6.3 通过真实 CLI 执行

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$TEST_EVIDENCE_DIR/cases/gpu-chain/traces"
export GPU_TEST_RUN_ID="run_gpu_handoff_$(date -u +%Y%m%dT%H%M%S)_$(python -c 'import uuid; print(uuid.uuid4().hex[:8])')"
set -o pipefail
resagent2 run --run-id "$GPU_TEST_RUN_ID" \
  --workspace "$TEST_EVIDENCE_DIR/cases/gpu-chain/repo" \
  --goal-file "$TEST_EVIDENCE_DIR/cases/gpu-chain/goal.txt" \
  --data-root "$TEST_EVIDENCE_DIR/cases/gpu-chain/data" \
  --python-version "$GPU_PYTHON_VERSION" \
  --constraint '只修改本实验工作区；不得修改 ResAgent2、验收脚本或共享数据集。' \
  --max-llm-calls 200 --timeout-seconds 7200 --max-tasks 8 --max-attempts 2 \
  2>&1 | tee "$TEST_EVIDENCE_DIR/cases/gpu-chain/cli-start.log"
printf '%s\n' "${PIPESTATUS[0]}" > "$TEST_EVIDENCE_DIR/cases/gpu-chain/cli-start.exit"
```

本项采用 CLI 默认的命令执行和环境准备授权，不传 §5 的两个 no-* 开关；也不强制每条命令确认，删除硬确认与跨进程问答由 §5 专项覆盖。预算冻结为 200 次模型请求、7200 秒 Run 执行时间、8 个任务、每任务最多 2 次尝试；任务数是上限，不是要求模型生成 8 个任务。安装/训练均计入 Run 时间，人工等待按现有规则处理，测试 AI 不临时扩额。7200 秒不是 GPU 租用计费的硬截止。

遇到 paused，按真实 requested_fields 通过 CLI answer 在同 Run 恢复，保存原题、答案和前后状态。只按预检记录回答环境事实；研究实现和实验决策由系统在既定目标内完成，不提供补丁或推理答案。超出既定数据、权限或预算的请求交回用户。

首次正式执行只运行一遍。失败、循环或资源阻断保留完整现场，报告原因后再决定是否复测；不要自动重跑到绿。训练外部监看由测试脚本完成，退出后停止监看，不向产品加入 GPU 监控代码。

### 6.4 GPU 通过条件

逐项保存证据，不能只看 Run completed、命令退出 0 或 metrics 中一个 device 字段：

1. **真实职责链**：由 CLI 进入；Scientific 提出需求，Compiler 自行拆解，Coding 真实实现 SE，Experiment 执行训练，Interpreter 生成反馈，Scientific 阅读原证据并完成最终报告。不限固定 Task ID、轮数或内部拆分；各次模型调用和实际工具执行可对照。§5 专门验证两轮交接，本项允许一个 WorkRequest 内存在有依赖的 Coding/Experiment 任务，按实际轮数验收目录与简报，不能把两任务写成两轮。
2. **真实 CUDA 与完整训练**：受管环境的绝对 Python、torch/torchvision/CUDA 与审计记录齐全；实际训练命令、cwd、起止时间、退出码和 stdout/stderr 可复核。训练期间保存带时间的 GPU 利用率/显存和进程信息，并与该次训练进程关联；结合冻结源码中模型/张量上 CUDA、执行记录和 metrics.device=cuda 核查，不能用测试方的独立 CUDA smoke 证明 Agent 训练使用了 GPU。两组均走完整 loader，无提前退出、mock 数据、只评估不训练或 CPU 降级。
3. **实现与配方未偷换**：保存初始 commit、最终 diff、执行时对应源码和 hash。确认 SE 做了真正通道缩放，其余配方、数据规模、epochs=1、seed=0 不变；不得把 metrics 改成常量、缩数据或去掉 backward/optimizer.step。包装命令按真实执行链人工复核，不仅以命令字符串认定训练成功。
4. **指标与结论**：baseline_accuracy/candidate_accuracy 都是本次实际测量，有限且在 [0,1]，对应原 metrics.json 和冻结工件，差值及单位一致。候选更低也可通过；Scientific 结论与观察相符并说明单轮单 seed 局限，不要求复现历史 0.4545/0.521 或任何涨点门槛。
5. **新的反向交接真实消费**：对本项每轮检查 work_record、累计 research_index、index_changes 与带引用简报，要求与 §5 相同。特别检查训练指标、源码/补丁、执行记录和限制的引用链，以及 Scientific 的真实原证据阅读；解释器不能凭摘要捏造训练细节。历史目录继续可读。若自然发生失败/修复，失败 Attempt、日志、产物、目录项和消耗也必须保留；不强制制造额外失败。
6. **产物和计量闭环**：final_report 已登记，原指标、源码、执行记录、简报可按原 ID 与 hash 打开；记录模型调用、环境准备、下载、训练、人工等待和总墙钟时间。所有 Agent、Compiler、Interpreter 的请求及重试与本 Run.usage 对账，无借用另一 Run 的旧结果或重置预算。

若设备、数据或依赖在预检阶段不可用，记 GPU BLOCKED；若系统在可用环境中跳过正式训练、悄悄用 CPU、丢证据或输出错误结论，记 FAIL 并定位。一次完整训练闭环通过说明本场景可用，不等于完成长训练、多 seed 研究或证明通用成功率。

## 7. 定向探针：失败材料仍在，解释不掩盖失败

确定性回归已覆盖失败 Attempt 后成功重试的累计目录。额外用真实 Interpreter 检查一次混合结果简报。该探针本身不执行训练或安装，独立于 §6 的真实 GPU 整链。

探针脚本放 probes/。参照 [test_work_interpreter.py](../../../tests/orchestrator/test_work_interpreter.py) 构造小型测试材料：同一任务第一次失败、第二次成功，各有独立冻结报告/数据；失败写明退出错误，成功数据给出 accuracy=0.8，并说明仅一次小样本测量。用正式 ArtifactRegistry、build_research_index、LLMWorkInterpreter 和现有生产模型客户端，分配最多 4 次模型请求的 execution_budget，保存输入、原始输出、trace 和用量。

这是**人工构造执行事实的 Interpreter 探针**，必须在报告标明 fixture=true；不声称发生了真实 Agent 修复或命令执行。不要替换生产 Interpreter 为确定性实现。

检查目录同时保留失败与成功的原 ID、Attempt 编号和真实给定状态；简报忠实区分两次尝试，报告成功值和局限，引用对应已提供材料。只给最终成功、掩盖失败、把小样本当充分科学证据，均不算通过。这个探针使用独立预算，不能混入公开整链 Run 的计量。

## 8. 交付与计量

交付证据根目录、REPORT.md、results.json、TEST_COMMIT。逐项给出 PASS / FAIL / BLOCKED：来源与依赖、回归、mock、真实两轮整链、真实 CUDA 训练整链、失败材料 Interpreter 探针。GPU 资源预检与正式训练分别列结果；只有 CUDA 可用或预检通过，不能把正式训练记为 PASS。记录自动断言、人工语义复核、未覆盖部分，不将“有引用”写成“语义已被代码证明”。

每个独立 Run 对账 logical calls、HTTP 尝试、重试/纠错/压缩和 Run.usage；保留未知占用与中断现场。分清 Compiler 和 Interpreter。所有重跑分开统计，不将 trace 累积和最后一次状态混用。full trace 可能含任务数据，保存于证据目录，不提交产品仓库。

保留所有脚本、命令及退出码、前后 Run/Session 快照、问题与答案、目录/简报/原证据、hash、trace、失败和中断记录。结束时核对 HEAD==TEST_COMMIT、受控文件干净，保存 git-status-after.txt。本轮必须完成 §6 的真实 CUDA 训练；资源不足则如实记录 BLOCKED，不能省略后称整体验收通过。不要求重跑历史 L3、清理服务器目录或修改全局镜像。
