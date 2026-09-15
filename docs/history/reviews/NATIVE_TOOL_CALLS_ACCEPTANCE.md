# 原生工具调用：小型服务器验收

状态：本地946 passed / 1 skipped；**尚未做本版真实模型验收**。基线 `fa711a0`，待测分支 `feat/native-tool-calls`；执行前记录该分支实际提交 SHA，不能只记分支名。[实现范围](NATIVE_TOOL_CALLS_PLAN.md)。

## 1. 纪律与预检

- 只同步、测试、分析和报告；不改产品代码/prompt/预算，不合并、不push、不修研究代码。
- 使用 Git bundle + 干净独立 worktree，记录 SHA、8 包 import 路径和 editable 指针前后值。不 scp 零散源码，不复用旧 Run/Session。
- **旧 L3 保持暂停且原样保留**：不回答、不resume、不改状态、不应用旧失败补丁。原生协议不能续接旧 JSON Session；这项拒绝不是回归。
- 所有驱动、日志、trace、测试工作区集中于一个新验收根；不删旧环境、缓存、数据和现场。临时注入只在验收驱动，不能写回产品包。
- 凭据只运行时加载，不输出值、不写脚本。记录模型、API endpoint（去除凭据）、Profile、上下文/输出额度、timeout和价格估算；API调用前由用户确认本轮成本。下述调用数是预算，不是货币硬上限。
- 只用标准库小仓库，无 GPU、torch/numpy、数据下载或文献服务。准备/复用 Python 环境必须走现有环境机制；若意外请求大依赖或新费用，停止并报告。

确定性基线（先激活服务器的 `ResAgent2` conda 环境）：

```bash
python -m pytest tests apps/cli/tests -q
git diff --check
```

重点文件：`tests/runtime/test_native_tool_calls.py`、`test_llm_recovery.py`、`test_resume.py`、`test_agent_loop.py`、`test_prompt_client.py`、`apps/cli/tests/test_shell_render.py`。mock E2E 可用临时目录运行，结果必须 completed，目录用毕清理；不要把它当真实原生协议验证。

## 2. Coding 小对照：格式与完成分开看

预先冻结一个小 Git 仓库：`add.py` 的 `add(a,b)` 错写为 `return a-b`；标准库 unittest 覆盖正数、零、负数加法，README只交代测试入口，不包含修法或验收分析。

系统目标只给一句：**“修复 add.py 的加法错误，运行现有 unittest 验证，说明修改和验证结果；不安装第三方依赖。”**

通过现有 `NativeCodingAgent.invoke` + `ModuleTaskRequest(code_modify)` 驱动，使用真实客户端、现有 Workspace/ResourceLayout/环境绑定与完成检查，不直接绕过 AgentLoop 调工具。可参考已有 Coding 测试的装配；驱动在验收根保存。准备好两个全新、初始字节相同的仓库副本，先跑旧基线一次、再跑新版本一次，均用 Flash、同一Profile，`max_steps=50 / max_llm_calls=50 / timeout_seconds=600`。环境准备耗时独立记录，不悄悄增加预算或覆盖失败。

两边都开 full trace，分别报告：

- 实际模型、逻辑调用/HTTP尝试、模型输出格式错误数、schema/权限/工具失败数。
- 首次编辑、首次验证、finish 的位置；总调用数、工具分布和连续重复片段。
- 真实 Git diff、unittest stdout/stderr/退出码、最终 ModuleResult 与产物；写对但没有验证/完成不能算闭环成功。
- 新版本有效动作必须来自 `raw_tool_calls`，`content` 可以为 null；旧版才按正文 JSON 解析统计。不可将原生空正文计作坏 JSON。

有限样本只能说明本次表现，不证明永久稳定或统计优势。若要补 Pro 或更多重复，先报告首轮结果并确认额外成本，不重跑到绿。

## 3. 共享路径冒烟

### Scientific：真实 CLI 跨进程问答

新 Run，短目标：“请先问我选择 Accuracy 还是 F1，收到回答后只记录选择，不执行实验。”使用 `--max-tasks 2 --max-attempts 1 --max-llm-calls 12 --timeout-seconds 300`；Profile和模型同§2新版本。

进程1 `run` 得 paused；`show` 读取真实 requested_fields；进程2 `answer --field '实际字段=第二个'`，必要时带相同 `--workspace`。检查原题配对、同Session、最终意见F1；续跑请求有之前assistant/tool消息及reasoning（若Provider返回），最后user消息包含新的答案。不能让测试AI把“第二个”翻译成F1。

### Experiment：标准库小脚本

单独新工作区，`run.py` 只用标准库写 `metrics.json={"value":42}`。通过现有 `NativeExperimentAgent` 和正式 `run_command` 工具，使用 `max_steps=20 / max_llm_calls=20 / timeout_seconds=300`。要求真实执行、冻结证据、metrics与文件一致。不得安装torch或跑GPU，不能只让模型自报42。

Compiler不改原生：对一个小 WorkRequest 跑一次已有 `LLMWorkflowCompiler` draft/review（只编译、不执行任务），确认仍是正文JSON/4096模块输入、能产出合法图；不把缺少 `tools` 当缺陷。

## 4. 定向失败：一条坏参数，不执行前缀

在一个新的 Coding 标准库探针中，只包装Provider返回：将一次 schema 可校验的原生 tool call 的 `function.arguments` 后追加文字一次，其余响应（含call ID和reasoning）不变；原响应私有备份0600，标明 injected及call_id。后续全部真实模型，不给修复答案。

验证：坏参数无工具执行；Session保留该call并配对错误receipt；下一请求包含恰一个当前runtime_feedback，使用新call_id、同Session/Attempt纠正；旧坏字符串不是可执行前缀。分别报告格式恢复成功与任务最终成功，不能混为一项。多调用整批拒绝、无回执中断及跨服务恢复拒绝已有确定性测试，不再为此浪费付费调用。

## 5. 必查原始消息、预算与持久化

1. 原生 full trace 的 `request_text` 用 `json.loads` 读取 `{messages, tools}`；schema与当时 Tool.input_model对应；`included_sections`含native_tools，有历史时含tool_history，无旧tool_contracts/recent_observations段。工具参数示例可能仍在领域prompt，不等于旧JSON协议仍在使用。
2. assistant中的每个call ID都有且只有一个匹配的tool receipt；多轮有序、无其他Session内容；旧完整业务prompt不累积，末尾只有当前一次业务上下文。
3. reasoning若返回，须在Session和同Session下次请求中逐字一致；不得作为业务证据。`tool_protocol_key`非空且不含密钥；换模型/API不续接。trace关闭不代表Session不保存协议历史。
4. 当前 `estimated_tokens == ceil(len(request_text)/4)`（按JSON文本本身长度，不按反解析后长度），且不超有效总额度。历史、schema、转义、当前材料在同一128K里；Compiler沿旧计量规则单列。估算不等于Provider usage。
5. 计量只取带model的逻辑调用主记录，按call_id去重后求`retry_number+1`；与ModuleResult/Run账本对应。schema补充行不是新调用。原生tool call ID也不是LLM call_id。
6. 同一调用的finish_reason/usage/attempts/原始tool calls完整可查；正文为空但有合法tool calls是正常。分开记解析失败、调用数量/身份拒绝、schema拒绝、HTTP retry、Task Attempt retry。
7. 所有新Session与trace目录0700、文件0600；扫描真实凭据值但不打印。旧源码、旧L3与旧结果不变。

## 6. 交付与停止条件

交付 `MANIFEST.md`：提交/配置/指针、每次结果、调用记账、关键call_id、真实diff和测试输出、失败原文与四层判断（协议→模型行为→工具执行→任务完成）。失败不可被重跑覆盖，不将“新版本一次成功”写成“根因永久消除”。

若Provider拒绝schema/消息、同一Session续传失败、输入超限、重复循环或完成门禁失败，保留现场并报告，不增预算、不调prompt、不换模型。小验收通过后再由用户决定是否启动**全新**L3；本验收本身不授权恢复旧L3或合并main。
