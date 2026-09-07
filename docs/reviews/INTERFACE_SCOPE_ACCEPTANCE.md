# 接口优化追加收口：任务职责与预算验收

## 1. 待测版本与边界

代码/测试基线：**`0d611a7aaaf069e00c42baf534057f5decde126f`**，分支 `fix/interface-contracts`。本地及服务器全量 **749 passed, 1 skipped**，mock E2E completed，`git diff --check` 干净。验收已完成：E2E 9/9，CLI 问答通过；CLI 编译+实验主运行失败、独立诊断重跑通过，两次并列保留。原始 trace 复核未支持“确定是外部 provider 空响应”的归因，诊断缺口与追加收尾见 [优化计划](INTERFACE_OPTIMIZATION_PLAN.md)。以下保留这轮验收方法，不把历史结果冒充后续版本验收。

仍按原 [接口优化验收单](INTERFACE_OPTIMIZATION_ACCEPTANCE.md) 的环境、资源、安全与纪律执行：仅同步/安装核验/测试/分析/报告，不改产品代码、prompt、测试目标、预算或验收断言，不合并、不 push、不清理旧 worktree/环境/缓存/数据集/失败现场。用 git bundle/fetch 和新干净 worktree 保留 Git 身份，不 scp 零散源码。

本轮仅涉及 Scientific 规划提示、Compiler 审查信息和职责规则、Controller 新编译前任务名额预检。未改 Coding 工具/上下文、Experiment、CLI/E2E 装配、模型配置或 schema 5.0。模型语义判断仍可能失败，不把 prompt 测试当成真实行为保证。

## 2. 环境和本地确定性测试

核验 HEAD、工作树、Python 3.12 和八个包的实际 `__file__`；若调整 editable，记录前后指针。沿用已确认的 Conda 路径、dataset catalog 和模型配置；不打印凭据。使用单一新产物根，保留 logs/traces/workdirs/ops 和清晰的 MANIFEST。

在独立 cwd，显式把仓库加入 PYTHONPATH：

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo"
python -m pytest "$repo/tests" "$repo/apps/cli/tests" -q -p no:cacheprovider
python -m e2e.mock_e2e
git -C "$repo" diff --check
```

应为 749 passed, 1 skipped；mock completed。重点测试（已包含在全量，不必重复执行）：

- `tests/orchestrator/test_compiler.py`：review 接收完整任务语义、与物化器相同的 inputs 投影、同一 registry 说明；不泄漏执行身份；语义拒绝使用已有一次纠错。纠正后需要两任务而只剩一个名额时拒绝，不放宽预算。
- `tests/scientific/test_prompt_responsibilities.py`：区分已知前置问题和未来条件失败，删除“一律先跑实验”的歧义。
- `tests/orchestrator/test_controller.py`：既有任务成功/失败都占任务名额；零名额新请求及 COMPILING 重启不调用替代 Compiler、调用账本不增加、历史图不变、WorkRequest 与 Run 都记 budget_exhausted；已接受图在名额占满时仍可从 COMPILING/EXECUTING 恢复。
- `tests/e2e/test_composition_adapters.py`：真实 Compiler + 生产适配器下，完整 review 正文仍进入 Composer；超过 4096 时在 provider 前拒绝，仅此前 draft 计一次调用。不调用真实 LLM。

## 3. 真实 E2E：原场景和原预算，9 次入口

| 场景 | 模型 | 次数 | 必看结果 |
|---|---|---:|---|
| code-experiment | deepseek-v4-flash | 3 | 已知 SE 未实现时先修改+代码验证，再由独立 Experiment 执行正式训练，交付真实指标 |
| code-experiment | deepseek-v4-pro | 1 | 同职责边界，分段读取和训练回归 |
| repair | deepseek-v4-flash | 1 | 未预知的 totla 故障：先真实执行失败，再新一轮修复+重跑，不因新提示抢跑修复 |
| direct | deepseek-v4-flash | 1 | completed/inconclusive，不创建任务图 |
| literature | deepseek-v4-flash | 1 | 实际检索/阅读/引用，不派执行任务代替自有工具 |
| ask-start → ask-resume accuracy | deepseek-v4-flash | 2 个进程 | 非空问题字段，同 Session 恢复，accuracy 实际落盘并采用 |

每个独立 Run 用 fresh workdir，ask 两进程共用同一目录。全程开启 `RESAGENT2_LLM_TRACE_LEVEL=full`，各场景独立 trace 目录并核验 trace.model。执行方式与原单一致，例如：

```bash
export RESAGENT2_MODEL=deepseek-v4-flash
export RESAGENT2_LLM_TRACE_LEVEL=full
export REAL_E2E_WORKDIR="$accept_root/workdirs/ce-flash-1"
export RESAGENT2_LLM_TRACE_DIR="$accept_root/traces/ce-flash-1"
python -m e2e.real_e2e code-experiment
```

如失败保留第一次现场，不重跑到绿后隐藏失败，不改 max_tasks=2 来绕过分工问题。后续诊断性重跑须另列，不能替换矩阵中的原结果。

## 4. 必须审计 full trace，不只看 rc

1. **初次规划**：code-experiment 的第一轮已包含目标声明的前置实现；不能再明确禁止修改、先执行已知会失败的代码。Scientific 仍只表达语义，不填 capability/task id。
2. **任务职责**：编译的 code_modify 负责修改与代码验证，experiment_run 负责正式训练/指标交付。不得因名额紧张把“正式训练并交付 metrics”压进 Coding；结合 Task.goal/inputs/constraints 和实际工具调用判断，不看 task 名称猜测。
3. **审查可见性**：draft/review 都有同一份 Available capabilities 和职责规则。review 的 Draft tasks 同时带 goal、depends_on、constraints、inputs；条件写在 inputs 中应可见，不要求必须复写到标题。若 review 仍误判，摘原始输入/回复，不直接归因模型。
4. **调用和上下文预算**：所有 Compiler 调用 included_sections 含 system/compiler_request，estimated_tokens 在 (0,4096]。只回算已渲染 Context，不把末尾 Action schema 指令算进此模块额度。Run 消费与 trace 去重 call_id 后的 retry_number+1 尝试数一致。若出现上下文超限，记录真实输入长度和错误，不调大预算凑绿。
5. **Coding 循环诊断**：记录 read_file/search/edit/verify 调用数、首次成功编辑与验证的事件编号。若循环，检查当时原始 prompt 是否已有完整的相关代码、control_state 是什么、工具是否曾拒绝命令、Task 是否越界；不能仅凭另一模型或另一次成功证明是随机抖动。
6. **真实结果**：核对 SE 代码修改、验证记录、Experiment 的真实 run_command、stderr/metrics.json 与冻结工件。repair 保留原始 NameError: totla。不得用自然语言 summary 代替执行证据。
7. **零名额出口**：确定性测试已覆盖，不必为触发它改真实场景预算或故意再花 LLM 费用。若真实运行自然触发，应是 budget_exhausted、当前 WorkRequest failed、新轮 Compiler 调用为零；此前调用/失败记录仍保留。它是诚实失败，不代表业务场景验收通过。

## 5. CLI 与报告

复验独立生产组合根的两条路径（参考原验收单 §5）：问答 run→show→answer；编译+实验 run（显式 workspace/data-root）。问题字段名读取实际值，不猜字段名；产物均落指定 data-root。环境可按原配置正常复用，记录其与 fresh Run/workdir 的区别；若新 Run 创建新环境导致安装耗时，单独报告，不擅自改安装策略或判成 Agent 卡死。

报告包含：确切 SHA、安装指针前后、全量结果、逐场景模型/rc/状态/指标/警告/调用数、关键 call_id 和原始消息摘录、保留的失败原因与待核项。trace 目录 0700/文件 0600，凭据扫描只输出是否命中，不打印秘密。**本轮预期是矩阵与 CLI 通过；任何失败均先核原始因果链，再决定后续，不自动宣布“既有波动、不阻断”。**

验收后仅交报告，等待用户决定是否合并 main。
