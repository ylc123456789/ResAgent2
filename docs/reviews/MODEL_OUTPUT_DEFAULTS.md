# 模型输出默认配置：按模型容量留余量，不逐级试探

## 决定（2026-09-07）

CLI 当前默认面向 DeepSeek V4，采用以下部署配置：

| 配置 | 旧默认 | 新默认 | 含义 |
|---|---:|---:|---|
| RESAGENT2_CONTEXT_WINDOW | 65536 | 1000000 | 模型总容量声明，不是实际填入长度 |
| RESAGENT2_RESERVED_OUTPUT_TOKENS | 4096 | 256000 | 同时用作 max_tokens 请求上限及输入计算中的输出预留 |
| RESAGENT2_LLM_TIMEOUT_SECONDS | 未暴露，客户端默认 120 | 600 | 沿用 urlopen 的网络等待参数，允许部署覆盖 |
| 各 Agent 输入上限 | 8192 | 8192 | 不变 |
| Compiler 输入上限 | 4096 | 4096 | 不变，含 draft/review |
| 安全余量 | 1024 | 1024 | 不变 |

不改 Run/Task 预算、重试次数、thinking 设置、prompt、工具或 schema 5.0。生产改动仅在 `apps/cli/src/resagent2_cli/composition.py`：两个既有默认值和一个网络参数透传。所有 CLI 客户端共用 `_client()`，不逐 Agent 打补丁，不引入模型注册表、远程能力发现、自动升档或 JSON 修补。环境变量仍优先；换用其他模型需配置符合其真实容量与输出限制的值，runtime 不按模型名分支。独立 E2E/程序化客户端的配置不隐式改变。

## 依据与边界

查阅日期 2026-09-07；下列是当时官方文档/源码，部署升级后应重新核验，不把数字当永久标准：

- [DeepSeek 模型规格](https://api-docs.deepseek.com/quick_start/pricing/)：V4 总容量 1M、最大输出 384K。采用 1M 容量声明，模块自己的小输入上限仍保留。
- [DeepSeek Harness 官方适配器](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/README.md)：默认输出额度 256000、容量 1000000，并允许部署/模型/请求覆盖。本项目借鉴明确、宽裕、可配置的额度策略，不搬入其插件或流式架构。
- [DeepSeek 官方 Pi 配置示例](https://github.com/deepseek-ai/awesome-deepseek-agent/blob/main/docs/pi_mono.md)：V4 模型条目声明 contextWindow=1000000、maxTokens=384000。此处是模型声明，不能冒充 Pi 每次请求的实际默认值；也不是小样本找到“刚够用”的预算。
- [Anthropic Python SDK 超时说明](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python#timeouts)：默认十分钟，并提示长非流式请求的网络风险。本项目取 600 秒作为可配置的网络等待余量；urllib 与其 SDK/DeepSeek Harness 流式 idle timeout 的语义并不相同，不宣称完整等价。

真实重放中四次 length 的 completion_tokens=reasoning_tokens=4096，最终 content 为空；16384 对照成功，但实际只用 3421 tokens。这证实小额度会截断，**不证明 16384 是必要或足够的阈值**，更不能恢复旧版未记录的历史 finish_reason。按用户要求，不继续 8K→16K→32K 试探；直接选择已有官方实现使用的宽裕额度，而非本项目发明一个经验阈值。

256000 是生成上限，不是要求模型用满；[官方计费](https://api-docs.deepseek.com/quick_start/pricing/)依据实际输入/输出 tokens。增大上限仍会允许更长思考和更高消费，不承诺成本/延迟不变，也不能保证任意任务永不截断。系统目前没有全 Run 的货币或总输出 token 硬预算，本次不新增这类机制。

600 秒传给 `urlopen(timeout=...)`，不是整次逻辑调用/Run 的严格 wall-clock deadline；重试和持续收到数据都会影响总耗时。Run/Task 的时限检查仍位于既有同步执行边界，本次没有实现可抢占取消。有限等待、有限尝试、真实 trace 保留原样；网络故障、长思考和输出截断必须按证据分开报告。

## 本地验证

新增 14 个配置测试覆盖：五类 action/draft/review 的输入上限不扩大；Flash/Pro wire 都发送 256000、timeout=600；自定义模型/小容量/输出/timeout 可覆盖；非法组合在网络前拒绝；实际 CLI adapter + LLMWorkflowCompiler 的 draft/review 全链在新默认下计量、trace、两次消费一致。全量 775 passed, 1 skipped，mock E2E completed。

确定性测试不调用真实服务。服务器补验收只检查新默认下的完整编译及 CLI 问答，不跑训练、不创建受管环境。上一轮诊断结果见 [LLM 诊断验收单](LLM_DIAGNOSTICS_ACCEPTANCE.md)，其 4096/16384 是历史对照配置，不是现在的默认值。

## 验收结论与保留观察（2026-09-07）

**验收通过，用户已授权收尾合并推送。** 服务器待测代码 `ab5066f55f4d8fa941ee66d51829631d8a033dcd`，干净 worktree `/root/autodl-tmp/projects/ResAgent2-ab5066f`；八包 editable 指向该 checkout。产物完整保留在 `/root/autodl-tmp/e2e-output-ab5066f-Sl6yzC/`。本次收尾只改文档，不移动服务器安装指针、不删除历史现场。

- 本地与服务器全量确定性测试均为 **775 passed, 1 skipped**，mock E2E completed，diff-check 干净。
- 真实完整 Compiler：Flash ×3、Pro ×1 均通过 draft→review；图均为 code_modify（实现并验证）→ experiment_run（正式训练与指标），保留依赖。此次仅编译，不执行这些任务。
- CLI 纯问答：run 暂停（exit 3）→ show（0）→ answer（0）；实际 requested_fields=["answer"]，最终意见明确记录 accuracy。
- 从默认配置加载 1000000 / 256000 / 1024 / 600；所有实际请求 max_tokens=256000，Compiler 输入估算为 1112–1289，未扩大 4096 模块输入上限。所有尝试 finish_reason=stop，未观测到 length。
- 四次编译分别消费 2/2/3/2 次请求；连同问答共 **10 次逻辑调用、11 次 HTTP 尝试**。与 trace 的 retry_number+1 及 CompilationResult.llm_calls 对应，未重复计数。驱动不保存 Run，旧 Run 字节未变；不宣称本次探针更新过 Run 总账。
- 五个 trace 目录/文件权限 0700/0600；验收方凭据扫描 NONE，主开发方另行只读复核原始请求、响应、尝试记录及权限。

### 非阻断观察：额度宽裕仍有一次 stop / 空正文

不能把本轮记为“零错误”或“所有空响应已根除”。`traces/compile-flash-3/llm_traces.jsonl` 的 call_id `e0e0910c5b5f48abacf0f48fa332949b`：

| 尝试 | finish_reason | completion / reasoning tokens | 正文与处理 |
|---|---|---|---|
| 0 | stop | 11023 / 11023 | 原始 content 为空；JSON 解析受控失败，记录 Expecting value |
| 1 | stop | 9622 / 8970 | 合法任务草图；既有有界重试恢复，随后 review 通过 |

本次上限为 256000、响应结束原因是 stop，**不支持继续归因为 4096 额度截断，也不支持继续加额度**。客户端在 strip/JSON 解析前保存的原始 content 已为空，不是本地工作集裁剪；为什么服务在此处停止且不返回正文，现有证据不能确定。第二次成功说明本次恢复有效，不证明以后相同请求必然成功。

按当前范围保留现有最多三次、受剩余调用预算约束的重试及逐次诊断，不新增 JSON 修补、自动升档或 Provider 分支。若后续在额度未耗尽时连续复现 stop/空正文并耗尽重试，再携完整 trace 单独诊断。更高额度仍可能增加实际成本/延迟，且没有全 Run 的货币/总输出 token 硬预算；这些边界保持不变。

以下保留原验收方法以便复现；其中“不合并/push”是服务器测试角色的操作边界，不代表验收仍未完成。

## 服务器收尾验收

代码基线 **`ab5066f55f4d8fa941ee66d51829631d8a033dcd`**，`fix/interface-contracts`；后续文档提交不改变待测代码。沿用前轮纪律：新干净 worktree、核验八包 import 指针并记录前后、隔离 cwd 跑 775/1 与 mock、单一新产物根、全 full trace、权限 0700/0600、不打印凭据，不清理旧工作树/环境/数据集，不合并/push。

### 配置预检

先加载现有凭据，再在**本轮进程环境**移除这些覆盖（不改 .bashrc 或持久部署配置）：RESAGENT2_CONTEXT_WINDOW、RESAGENT2_RESERVED_OUTPUT_TOKENS、RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS、RESAGENT2_LLM_TIMEOUT_SECONDS，以及四个 RESAGENT2_<COMPONENT>_CONTEXT_TOKENS。读取 `_model_profile()` / `_client().timeout_seconds` / `_component_context_limit()`，确认使用代码默认 1000000 / 256000 / 1024 / 600 和 8192/4096，而不是用 export 新值掩盖默认值错误。保留正确 API base/key 引用，模型按矩阵选择。

### 真实矩阵（无训练）

1. **完整 Compiler：Flash ×3、Pro ×1**。每次新进程、新 trace 子目录，用 CLI 的 `_compiler_client()`、`_registry()` 和真实 `LLMWorkflowCompiler.compile()`。读取下面旧 Run 的 WorkRequest/预算/逻辑 workspace 描述，不重新改写目标或简化 schema。必须包含 draft→review；不是只校验一个原始 draft。
2. **CLI 纯问答 ×1**：默认 Flash，无 workspace，沿用“只询问并记录指标偏好，不执行工作”的目标，fresh data-root，run→paused(3)、show→0、读取实际字段、answer accuracy→completed(0)。不启动训练或环境搭建。

可将以下驱动保存于本轮产物根 `ops/compile_default.py`。代码不创建 Controller，不执行任何 Task，也不写回旧 Run：

```python
from pathlib import Path
from resagent2_cli import composition
from resagent2_contracts import WorkspaceDescriptor
from resagent2_orchestrator import ResearchRun, LLMWorkflowCompiler, CompilationError

source = Path("/root/autodl-tmp/e2e-scope-0d611a7-X9BKAu/workdirs/cli-ce/state/run_cli_ce_1.json")
original = source.read_bytes()
run = ResearchRun.model_validate_json(original)
assert run.workflow is None and len(run.work_requests) == 1
profile = composition._model_profile()
assert (profile.context_window, profile.reserved_output_tokens,
        profile.safety_margin_tokens) == (1000000, 256000, 1024)
client = composition._client()
assert client.timeout_seconds == 600
assert client.trace_level == "full" and client.trace_dir is not None
assert not (client.trace_dir / "llm_traces.jsonl").exists(), "use a fresh trace directory"
assert client.model in {"deepseek-v4-flash", "deepseek-v4-pro"}
limit = composition._component_context_limit("compiler")
assert limit == 4096
compiler = LLMWorkflowCompiler(composition._compiler_client(max_context_tokens=limit))
try:
    result = compiler.compile(
        run.work_requests[0], current=None, registry=composition._registry(),
        budget=run.request.budget,
        workspaces=[WorkspaceDescriptor(workspace_id=w.workspace_id,
                                        source_kind=w.source.source_kind)
                    for w in run.workspaces.values()],
        remaining_calls=run.request.budget.max_llm_calls - run.llm_calls_used,
    )
    print("compiled", "llm_calls=", result.llm_calls)
    print(result.output.model_dump_json(indent=2))
except CompilationError as error:
    print("compilation_failed", "llm_calls=", error.llm_calls, str(error))
    raise SystemExit(1)
finally:
    assert source.read_bytes() == original, "historical run must remain unchanged"
```

### 必看证据与停止条件

- 编译和 CLI 所有实际请求 `request_max_tokens=256000`；Flash/Pro model 字段正确；网络 timeout 预检为 600。
- Compiler draft/review 都有 system+compiler_request，estimated_tokens≤4096；各 Agent 的输入默认仍为 8192。总容量 1M 不能当作实际输入长度。
- 实际图正确保留“修改并验证 → 实验”的职责和依赖；不能只以 JSON 可解析为通过。
- 每次尝试分别列 finish_reason、completion_tokens、reasoning_tokens、正文长度及延迟，读取原始消息核验；没有 length 才能说本次未截断。若有 length/timeout/坏 JSON，保留第一次失败并报告，**不再临时调大额度、改 thinking/prompt 或重跑到绿**。
- CompilationResult/CompilationError.llm_calls 与主 trace 行的 retry_number+1 求和对应；attempts 不重复加账。驱动不保存 Run，不能宣称它更新过历史 Run 总账。
- 问答必须实际消费 accuracy；凭据扫描只输出是否命中；目录 0700、文件 0600。
- 报告包含 SHA、安装指针、原始失败/成功、call_id、配置实值、结果和所有警告。有限样本通过不宣称永久稳定；本轮无需重跑训练矩阵。验收后只报告，不合并/push。
