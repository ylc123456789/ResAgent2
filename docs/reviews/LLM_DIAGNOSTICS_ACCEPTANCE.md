# LLM 失败诊断补验收（无需训练）

## 1. 版本、目的与边界

待测代码：**`da00fe6d248c6bf6c1ed37a2b65619b2070f9242`**，分支 `fix/interface-contracts`。后续验收单提交只改文档。本地 **761 passed, 1 skipped**，mock E2E completed，`git diff --check` 干净。

本轮只改共享 OpenAI-compatible 客户端的诊断捕获/投影，以及非法响应外壳的受控错误处理。没有模型名、测试场景或任务类型特判；未改 Agent prompt、任务图、状态机、输入/输出配置、三次重试策略、CLI/E2E 装配或 schema 5.0。

目的有两个，分开验收：

1. **代码保证**：失败 JSON 也保留 provider 的结束原因/用量；每次 HTTP 尝试可分别审计，调用账本不变，metadata 不外泄正文。
2. **现场诊断**：查清上一轮 CLI 空 JSON 是否与输出额度有关。只有真实返回 `finish_reason=length` 等证据才能支持该判断；单测模拟和重跑成功均不能证明历史原因。

纪律：只同步/测试/分析/报告；不改产品代码、prompt、原测试目标，不合并或 push，不清理旧工作树/环境/缓存/数据集/现场。仍用 bundle/fetch + 新干净 worktree，核对确切 HEAD 与实际 import 路径。安装指针若变化记录前后。产物放单一新根的 `logs/ traces/ ops/`，全程 full trace，凭据只运行期加载、不打印。不调用训练，不创建受管 Conda 任务环境。

## 2. 确定性复验

在隔离 cwd，使用已有 ResAgent2 Python 环境，显式 `PYTHONPATH="$repo"`、`PYTHONDONTWRITEBYTECODE=1`：

```bash
python -m pytest "$repo/tests" "$repo/apps/cli/tests" -q -p no:cacheprovider
python -m e2e.mock_e2e
git -C "$repo" diff --check
```

预期 761 passed, 1 skipped。重点文件 `tests/runtime/test_llm_attempt_trace.py` 有 12 个测试：空 content+length+reasoning，坏 JSON 后成功，失败后网络/超时/5xx/4xx 不借用旧响应，metadata 的嵌套内容边界，off 不写文件，可选元数据缺失，以及非法响应外壳。原 retry/loop/Compiler/CLI 计数测试也全部通过，不必人工构造新的失败 Run。

## 3. 精确失败请求重放：默认输出额度两次

来源只读：

- 文件：`/root/autodl-tmp/e2e-scope-0d611a7-X9BKAu/traces/cli-ce/llm_traces.jsonl`
- 逻辑调用：`52f8e485448e4707adbf9fabda9fe7e1`
- 历史现象：末次最终 content 空，reasoning 21,221 字符；前三次逐次内容/结束原因/用量不可恢复，不能补写到历史记录。

把以下 Python 保存到**本轮产物根** `ops/replay_compiler.py`（不是产品仓库）。它复用 CLI 当前 `_client()` 配置，原样保留历史编译 Context，只拆下客户端原本会追加的同款 schema 指令，再由同一个客户端追加一次。只做 provider 调用和 CompilationDraft schema 校验，**不调用 Controller、不执行工作图，也不等同完整 Compiler/E2E 验收**。

```python
import json
from pathlib import Path
from pydantic import ValidationError
from resagent2_cli.composition import _client
from resagent2_orchestrator.compiler import CompilationDraft
from resagent2_runtime import ComposedContext, ContextComposer

source = Path("/root/autodl-tmp/e2e-scope-0d611a7-X9BKAu/traces/cli-ce/llm_traces.jsonl")
rows = [json.loads(line) for line in source.read_text().splitlines()]
record = next(row for row in rows if row.get("call_id") ==
              "52f8e485448e4707adbf9fabda9fe7e1" and "model" in row)
client = _client()
suffix = "\n\n" + client._action_instruction(CompilationDraft)
assert record["request_text"].endswith(suffix), "schema differs; stop, do not rewrite the prompt"
text = record["request_text"][:-len(suffix)]
context = ComposedContext(
    text=text, included_sections=record["included_sections"],
    omitted_sections=record["omitted_sections"],
    estimated_tokens=ContextComposer.estimate_tokens(text),
)
assert context.estimated_tokens == record["estimated_tokens"]
assert context.estimated_tokens <= client.context_budget(CompilationDraft, 4096)
assert client.model == record["model"] == "deepseek-v4-flash"
assert client.trace_level == "full" and client.trace_dir is not None
assert not (client.trace_dir / "llm_traces.jsonl").exists(), "use a new trace directory"
client.set_trace_context(
    agent="workflow_compiler", run_id="run_compiler_trace_probe",
    work_request_id="work_probe", step="draft",
)
try:
    candidate = client.next_action(context, CompilationDraft)
    draft = CompilationDraft.model_validate(candidate)
except ValidationError as error:
    client.record_validation(str(error))
    print("draft_schema_failed", "llm_attempts=", client.last_attempts)
    raise SystemExit(1)
except RuntimeError as error:
    print("provider_call_failed", type(error).__name__, "llm_attempts=", client.last_attempts)
    raise SystemExit(1)
else:
    print("draft_valid", "tasks=", len(draft.tasks), "llm_attempts=", client.last_attempts)
```

固定 `RESAGENT2_MODEL=deepseek-v4-flash`、`RESAGENT2_CONTEXT_WINDOW=65536`、`RESAGENT2_RESERVED_OUTPUT_TOKENS=4096`、`RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS=1024`，API base/key 配置保持原验收部署值。配置值可记录，凭据值不可记录。启动两次独立进程，每次不同 trace 路径：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$accept_root/traces/compiler-4096-1"
python "$accept_root/ops/replay_compiler.py"
# 第二次仅更换为 compiler-4096-2；分别记录 rc，不用重跑覆盖失败。
```

读每次 JSONL 原始 request/response/reasoning，报告：

- `request_max_tokens=4096`，输入正文与原记录相同、计量相同；没有多拼一遍 schema。
- 每次逻辑调用只有一条带 model 的主记录；`len(attempts) == retry_number + 1 == client.last_attempts`，attempts 内 retry_number 从 0 连续递增。关联 schema 校验行不是额外调用。
- 按尝试分别列 `finish_reason`、`usage`（含 provider 给出的 reasoning tokens）、content/reasoning 字符数、解析结果。不能从末次内容推断前次内容。
- 顶层 finish_reason/usage/response 等于最后一次尝试；若 provider 没给值保持 null。不要把多个 attempts 的用量混称为顶层末次 usage，也不能把 logical 行的 retry_number+1 和 attempts 长度重复相加。
- 若这次没有重试，真实运行只能证明一次成功捕获；三次失败保存和防混淆由确定性测试覆盖，不必反复调用直到失败。

## 4. 仅在观察到 length 时的一次输出额度对照

若 §3 任一尝试真实 `finish_reason=length`，允许一次单变量诊断：只把已有 `RESAGENT2_RESERVED_OUTPUT_TOKENS` 改为 `16384`，使用新目录 `compiler-16384-1` 重放同一请求。其余输入、模型、窗口、schema、重试次数不变；这只是诊断性配置对照，不修改产品默认值，也不代替 4096 组结果。最多这一组，不逐档无限调高、不重跑到绿。

如果 §3 未出现 length，则跳过该对照：空 content+stop/其它原因如实报告；缺少结束原因/usage 记为未知；全成功记为未复现。不得据此宣称历史原因已排除。

若对照从 length/无 JSON 变为 stop/合法 JSON，说明现有输出配置不足是有证据支持的解释；报告所需额度和代价，交给主开发方决定是否调整默认/部署配置。不要自行改 input 上限、关闭 thinking 或加入自动修 JSON。

## 5. CLI 冒烟与最终报告

还原 §3 的默认配置后，只补一组 CLI 纯问答：沿用上一轮明确“用户选择指标后只记录、不执行工作”的目标，fresh data-root/run-id，run→paused(exit 3)、show→0、读取真实 requested_fields、answer accuracy→completed(exit 0)。不传 workspace，不跑训练；若产生异常 work request，停止并报告，不顺便进入环境搭建。完整 E2E 9/9 的职责验收已经在 0d611a7 完成，本轮不要求重跑训练矩阵。

最终报告分开写：

1. 新代码/测试 SHA、import 指针、761/1 与 mock 结果。
2. 4096 两次重放原始结果，条件触发的 16384 对照（或跳过原因），CLI 问答结果。
3. 每次 call_id、模型、输出限制、finish_reason、usage、空 content 是否存在、attempt 计数是否对应。思考内容是诊断线索，结束原因/用量才是判断输出截断的直接信号。
4. 0700/0600、不打印秘密的凭据扫描结果；所有旧现场保持不变。
5. 已确认、仍未知、是否建议调整配置三者分开。**代码诊断通过不等于历史根因已证实，更不等于已授权合并。** 验收后只交报告，不合并/push。
