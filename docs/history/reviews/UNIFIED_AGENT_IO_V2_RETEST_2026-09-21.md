# Unified Agent IO V2：修复与定向复测

后续独立验收见 [2026-09-21 复测复核记录](UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md)：主要产品流程通过，repair 自动判定按既定规则人工复核通过，旧答案拒绝的原始材料待补。以下保留当时的复测交接内容。

日期：2026-09-21。开发分支：`refactor/unified-agent-entry`。本轮未合并或推送。

## 已修复与本地验证

- `a7937ee`：Experiment 完成判定和编排执行验收共用 `latest_command_results`。同一 Attempt 按 argv 保留每个命令最后一次结果；仅同命令成功重试才能消除它的失败。环境诊断、不同参数的 smoke 命令不能覆盖训练失败。全部原始命令记录继续保留，纯分析任务不要求执行。
- `543a348`：完整 GPU E2E 的任务上限从 2 调整到 4，保留 200 次调用/3600 秒预算。取消恰好两任务的假设，加强实际结果验收：真实训练命令、同一成功 Attempt 的原始 `metrics.json`、hash、两组有限且合法的 accuracy、epochs=1、seed=0、device=cuda，以及 Scientific 实际读取和引用。
- repair 必须保留真实失败训练 Attempt、实际补丁、成功重跑命令和冻结 accuracy=0.8；仅有 completed 或 work_request 数量不算通过。
- 本地全量回归：**1071 passed，1 skipped**。跳过项仍为显式启用的真实网络文献 smoke。服务器旧 repair 的三个原始命令回执已离线重放，新逻辑保留 `python train.py` exit 1，三份原始记录不变。

新版本尚未执行真实模型/GPU复测。本文给独立测试 AI 执行，开发 AI 随后查看原始证据验收。精确测试提交取交接包 `PRODUCT_COMMIT`，不得仍测 `76576ec`。

## 旧报告需要补正

旧证据目录：`/root/autodl-tmp/unified-io-v2-76576ec-evidence`。原始记录保留，补正写入新报告，不覆盖旧失败或偷偷更改旧 Run：

- `results.json` 实际列出 10 PASS、2 FAIL、1 BLOCKED，并非 12 PASS。
- 旧 GPU Run 仅完成 CUDA smoke，没有完整 baseline/candidate 训练指标。
- 被终止的第二轮有 11 个 trace 调用，持久化 Run 仅计 3 次；旧汇总未包含该轮。记录差额及未返回的 Agent 调用，不宣称全部精确对账，更不能靠修改计数消除差额。
- 命令确认只覆盖一个命令。软确认加硬确认是对同一命令的两次提问，不等价于验证第二个不同命令需要重新授权。
- pip 日志显示 torch 554.6 MB 用时约 21:05 下载完成，torchvision 7.4 MB 及若干依赖也完成；末尾开始下载 cudnn 553.1 MB，随后人工终止。只能确认部分包下载成功，不能确认整次安装完成，也不能据此断言镜像完全不可达或产品死锁。

## 镜像建议

本轮从同一服务器比较了阿里 HTTPS/HTTP、清华、腾讯、华为、中科大、豆瓣、北外、上海交大、南大和 PyPI，共 11 个端点。均能取得相同完整 `six==1.17.0` 小包。相同 torch wheel 的前 4 MiB 采样，华为约 23.60/57.83 MiB/s，腾讯 8.07/10.06，清华 5.06/10.13；阿里约 0.08-0.11 MiB/s，因采样截止只收到部分范围。完整范围的 hash 一致。豆瓣实际跳到腾讯。

建议仅在本轮测试 shell 使用：

```bash
export PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple/
```

备选为 `https://mirrors.cloud.tencent.com/pypi/simple/`、`https://pypi.tuna.tsinghua.edu.cn/simple/`。不改全局 pip/conda 配置，不禁用 TLS。记录安装进程日志中实际使用的 index，防止 Conda 环境自身配置覆盖父进程设置。若需试备选，以一次有明确原因的新尝试记录，不循环换源掩盖失败。

详细方法与 JSON 在交接目录 `MIRROR_RESULTS.md`、`mirror_benchmark*.json`。本轮未安装软件、未改服务器镜像配置；短时采样不保证完整大包持续带宽，更不证明 CUDA 依赖安装成功。

## 1. 隔离安装

服务器：`ssh -p 26089 root@connect.cqa1.seetacloud.com`。以下在服务器 Bash 执行：

```bash
HANDOFF=/root/autodl-tmp/unified-io-v2-retest-20260921
cd "$HANDOFF"
sha256sum -c SHA256SUMS
TARGET=$(cat PRODUCT_COMMIT)
EVIDENCE=$(mktemp -d /root/autodl-tmp/unified-io-v2-retest-XXXXXX)
export EVIDENCE
mkdir -p "$EVIDENCE/ops" "$EVIDENCE/cases" "$EVIDENCE/probes" "$EVIDENCE/logs"
git clone "$HANDOFF/resagent2-retest.bundle" "$EVIDENCE/product"
cd "$EVIDENCE/product"
git checkout --detach "$TARGET"
git rev-parse HEAD > "$EVIDENCE/ops/product-sha.txt"
git status --porcelain=v1 > "$EVIDENCE/ops/product-status-before.txt"
/root/miniconda3/bin/conda create -y --prefix "$EVIDENCE/driver-env" \
  --clone /root/autodl-tmp/unified-io-v2-76576ec-evidence/driver-env
PY="$EVIDENCE/driver-env/bin/python"
export PY PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH
"$PY" -m pip install --no-deps \
  -e ./packages/contracts -e ./packages/runtime -e ./packages/components \
  -e ./packages/capabilities -e ./packages/orchestrator \
  -e ./packages/agents/coding -e ./packages/agents/experiment \
  -e ./packages/agents/scientific -e ./apps/cli
"$PY" -m pip check
"$PY" -m pip list --editable
```

安装命令、输出和返回码全部落盘；任一步失败应先处理。按原交接的 import-check 对九包实际 `__file__` 逐一断言位于本轮 `product/`；不能只看 pip list。保持产品工作树干净，不修改产品或放宽断言。

## 2. 回归及环境预检

```bash
cd "$EVIDENCE/product"
"$PY" -m pytest tests apps/cli/tests -q -p no:cacheprovider --tb=line \
  > "$EVIDENCE/logs/pytest.txt" 2>&1
rc=$?
printf '%s\n' "$rc" > "$EVIDENCE/logs/pytest.exitcode"
"$PY" -m resagent2_cli --help > "$EVIDENCE/logs/cli-help.txt" 2>&1
```

预期 **1071 passed，1 skipped**。保留 stdout/stderr、退出码、依赖版本和非敏感有效模型配置。凭据继续使用原授权配置，不输出密钥或整个环境变量表。

训练前检查真正的实验环境，不能拿 driver-env 的 CUDA 检查代替。每场景使用独立的 `RESAGENT2_ENV_ROOT`；经公开 `EnvironmentManager.prefix(run_id=..., workspace_id="ws_main")` 取得受管前缀，再准备环境。

- 已有健康 CUDA 环境可作为环境 fixture clone 到新的前缀，通过公开 `EnvironmentManager.prepare` 正常校验绑定。记录来源、版本和安装日志；不要复用旧 Run/Session 文件或污染旧实验环境。
- 不凭旧报告假定环境健康。至少检查 torch/torchvision 可 import、版本兼容、`torch.cuda.is_available()`、真实 CUDA 张量运算以及 pip check。
- 缺依赖时在该实际前缀完成安装并记录 exit code，然后再启动真实模型流程。使用上面的进程级镜像；torch/torchvision 的 CUDA 版本按驱动兼容性选取并记录，不把普通源名字等同于某种 CUDA 构建。
- 数据继续来自 `/root/autodl-tmp/datasets` 已登记的 cifar10；检查内容完整性，不让 Agent 静默替换数据。
- 安装慢时观察日志增长与进程状态。若人工终止，记录已下载内容、实际时长及退出原因；不能把未等到结果自动归因死锁。

## 3. 定向真实 E2E

沿用原模型/API配置，开启 full trace。每个场景使用全新 workdir；不 resume 旧失败 Run，不扩大 200 次调用/3600 秒预算。

可复用如下 Bash 函数；额外保存调用命令与有效预算：

```bash
run_stage() {
  local case_name="$1" stage="$2"
  shift 2
  local case_dir="$EVIDENCE/cases/$case_name"
  mkdir -p "$case_dir/logs" "$case_dir/traces"
  date -u +%FT%TZ > "$case_dir/logs/$stage.started"
  (
    cd "$EVIDENCE/product" || exit 1
    export REAL_E2E_WORKDIR="$case_dir/workdir"
    export RESAGENT2_DATA_ROOT="$case_dir/workdir/data"
    export RESAGENT2_RESOURCE_ROOT="$case_dir/resources"
    export RESAGENT2_ENV_ROOT="$case_dir/resources/envs"
    export RESAGENT2_DATASET_ROOT=/root/autodl-tmp/datasets
    export RESAGENT2_CONDA_EXE=/root/miniconda3/bin/conda
    export RESAGENT2_LLM_TRACE_LEVEL=full
    export RESAGENT2_LLM_TRACE_DIR="$case_dir/traces"
    export PIP_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple/
    "$PY" -m e2e.real_e2e "$stage" "$@"
  ) > "$case_dir/logs/$stage.stdout" 2> "$case_dir/logs/$stage.stderr"
  local rc=$?
  printf '%s\n' "$rc" > "$case_dir/logs/$stage.exitcode"
  date -u +%FT%TZ > "$case_dir/logs/$stage.ended"
  return "$rc"
}
run_stage repair-01 repair
run_stage code-experiment-01 code-experiment
```

按第 2 节在上述同一 case 的 env_root 中预先准备对应环境：repair 的 run_id 为 `run_repair`，GPU 场景为 `run_full_real`，workspace_id 都为 `ws_main`。函数不代替预检。

验收：

1. repair：真实 `python train.py` 失败保留为 failed Attempt；后续成功诊断不能消掉它。Coding 最小修复 `totla -> total`，新 Experiment 成功重跑，原始 metrics accuracy=0.8。报告说明该值是测试夹具常量，非 CIFAR 实验指标。
2. code-experiment：允许实现、smoke、正式训练分开占用最多 4 个任务。必须完整跑 baseline/candidate 两组，保留原始 metrics.json、CUDA 设备、epochs=1、seed=0、执行命令/日志、源代码 diff/hash、冻结指标工件和 Scientific 读取/引用。无需 SE 比 baseline 更好。smoke 通过、CPU 完成或只有报告数字不能通过。
3. 若模型又超出预算，保留实际拆工和错误，报告 FAIL；不要改预算或手动编造 metrics。新 E2E 判定识别直接 Python 运行 train.py（支持常见解释器选项），复杂包装方式需人工复核，不能改运行记录让谓词通过。

## 4. 补测第二命令确认

独立脚本放 `$EVIDENCE/probes/`，复用旧 probe5 的公开 API 装配，但固定一个 Experiment Task，要求顺序执行 `python measure.py --tag first` 和 `python measure.py --tag second`，写两个不同 marker。Scientific/Experiment 用真实模型，可用公开 DeterministicWorkflowCompiler 固定任务图并在报告声明。

- 通过 `confirm_before_experiment=True` 开启确认；不得直接设置 `experiment_confirmed=True` 或修改 Run JSON。
- 第一次硬门批准前 first/second marker 均不存在；按实际 pending_question 字段通过 `ResearchController.answer_question` 回答。
- 第一条成功后，第二条必须再次触发绑定精确命令的硬确认，且 second marker 仍不存在。软确认与硬确认单独记录。
- 第二题出现时重交第一题答案，应拒绝且持久 Run/Session 不改变；再回答第二题。每次答题用新进程、相同 Task/Attempt/Session。
- 最后两个真实命令均 exit 0，两份执行记录分别匹配批准命令，两个 marker 内容正确。记录每次暂停的快照，不能反复写同一个 `confirm.json` 覆盖中间状态。
- 预算最多 1 个 Task、1 个 Attempt、80 次逻辑调用、1800 秒。真实模型没有按预期触发时据实 FAIL/BLOCKED，不能换 scripted Agent 宣称真实测试通过。

原 probe1/2/3 可对新 checkout 各重跑一次以确认纯分析仍不被强制执行；尤其 Experiment 只读已有结果应 completed 且 command_count=0。所有探针完整保存实际 AgentRequest/AgentResult、输入 fixtures 和权限。

## 5. 提交验收证据

保存 Run JSON、全部 Session、登记工件/原文件及 hash、完整 trace、执行 stdout/stderr/返回码、脚本、初始/最终源码 diff、实际环境和人工干预。原始失败轮次保留。

计量包含全部场景、失败及中断轮次。只将带 model 的 trace 主记录视为调用，再按 call_id 去重；schema 补充行单列。HTTP 尝试按主记录 `retry_number + 1`，不能再叠加 attempts 长度；缺 usage 记 unknown。分别列 trace 实际消费与 Run 已持久化计量，解释中断中尚未返回的 Agent 消费，不能宣称差额不存在。

产出 `REPORT.md` 和 `results.json`，逐项 PASS/FAIL/BLOCKED，数量从实际项目计算。最后提供：**服务器证据根目录、报告路径、完整测试 SHA、各项结论及未完成原因**。开发 AI 将独立复核后决定是否通过；本轮不合并或推送。
