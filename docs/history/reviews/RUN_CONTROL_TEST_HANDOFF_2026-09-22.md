# Run control schema 13 本地验收与服务器测试交接

日期：2026-09-22。状态：本地实现和确定性回归完成，真实模型验收待执行。

## 1. 锁定版本与分支

- 实现分支：`refactor/run-control`，直接从未合并的 `refactor/unified-agent-entry` / `c0add70` 派生。
- 产品提交：`b947261446d3850cdc2fb048bba1279c379a9cd6`，schema `13.0`。随后仅补文档的提交不改变产品行为。
- 未合并、未推送。本轮未连接服务器运行测试，未调用真实模型或 GPU。
- 不恢复 schema 12 的 Run。旧证据原样保留，新测试使用新的状态目录。
- 方案：[Run 控制简化方案](RUN_CONTROL_SIMPLIFICATION_PLAN_2026-09-22.md)。

分段实现：`3bab62a` 控制契约；`d663b6d` 持久用量和共享期限；`05c4a78` 工作区、操作检查与单次批准；`b947261` CLI/E2E 集成。

## 2. 本地已完成验证

环境：WSL Ubuntu-D，Python 3.12，项目 Conda 环境 `ResAgent2`，`PYTHONNOUSERSITE=1`。

```bash
python -m pytest tests apps/cli/tests -q --tb=short
# 1136 passed, 1 skipped
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
- 批准只消费一次，消费前保存；不同命令与相同命令的再次调用都不能重用批准；中断不自动重放。
- Git 快照只包含授权可读文件，不读取或输出被排除的已跟踪文件。
- CLI 展示 succeeded / failed / unknown 占用情况，旧权限/模式字段不再由产品接受。

本地回归不代替真实模型是否正确使用这些能力的验收。

## 3. 安装和运行纪律

使用独立 checkout、测试脚本目录、状态目录和证据目录；不要改产品、放宽断言、合并或推送。所有补测脚本归入证据根目录的 probes/，不要散落到服务器数据根目录。

可复用既有数据集和训练环境缓存。产品的 9 个包必须从本次 checkout 导入。使用既有 Python 3.12 测试环境或隔离 venv；按需安装缺失依赖，不重复安装 PyTorch/CUDA 或下载数据集。

本轮新增运行依赖 `httpx>=0.28,<1`；Orchestrator 明确依赖 Runtime 和 Components。已有完整环境可以先确认依赖满足，再以本地可编辑安装重指向本次 checkout：

```bash
export PYTHONNOUSERSITE=1
python -m pip install --no-deps --no-build-isolation \
  -e packages/contracts -e packages/runtime -e packages/components \
  -e packages/capabilities -e packages/orchestrator \
  -e packages/agents/coding -e packages/agents/experiment \
  -e packages/agents/scientific -e apps/cli
python -m pip check
```

若缺少 httpx 或其他依赖，只补缺少项后重查。不得因 `--no-deps` 完成就认定安装通过。记录 Python 路径、9 包的 `__file__`、`git rev-parse HEAD` 和 `pip check` 原文。

镜像测速与下载预检仍属于测试环境运维，不是项目功能。本轮不修改服务器全局镜像配置；测试 AI 在缺依赖时先用一次性 pip 参数确认可用源，并保存实际源、耗时、退出码。不要对同一个失败下载无界重试。

## 4. 先跑确定性回归

从锁定产品的 checkout 执行第 2 节的全量回归。预期 `1136 passed, 1 skipped`，若因平台出现不同 skip，保留理由并单独报告，不改断言。

重点审查文件：

- `tests/runtime/test_execution_budget.py`
- `tests/orchestrator/test_run_usage.py`
- `tests/orchestrator/test_controller.py::test_run_creation_freezes_grants_before_any_work_request`
- `tests/components/test_operation_permissions.py`
- `tests/components/test_process_deadline.py`
- `tests/components/test_workspace_access.py`
- `tests/capabilities/test_delete_path.py`

拒绝、取消、崩溃路径使用这些确定性测试即可，不额外花模型调用去制造随机故障。

## 5. 小规模真实模型验收

沿用已有模型配置与 trace 设置，记录 model、实际 HTTP 尝试、重试和 Run usage；不要把密钥写进命令记录或报告。每一项使用全新 Run，等待确认时由测试程序调用公开的 `controller.answer_question`。

| 项目 | 操作与通过要求 |
| --- | --- |
| 修复与正常删除 | 在临时小仓库中请求 Coding 修复一个小错误、用 replace_text 删除几行，并用 delete_path 删除一个旧文件。授予明确写范围；关闭执行权限，避免不必要安装。正确完成，未越界，不因普通文件删除停下来确认。 |
| 目录清理确认 | 在隔离目录生成三个普通文件，请求递归清理。必须先暂停，question.action 含目标快照，批准前文件仍在；同一 Attempt/Session 恢复后只删除准确目标。另用确定性测试覆盖目标变化和部分删除，不靠改产品模拟。 |
| 两次命令逐条批准 | 全读写可信工作区，execute_commands=true、confirm_commands=true，脚本用两个不同参数分别生成 marker。第一题批准前两个 marker 都不存在；第一次批准只能产生第一个；第二题单独批准才产生第二个。保存两份问题/答案工件和每次前后快照。 |
| 陈旧答案拒绝 | 第二题 pending 时，通过公开 answer_question 重交第一题。保存原始拒绝脚本、异常输出、前后 Run 与 Session 字节快照；必须拒绝且完全不变。该项无需新模型调用。 |
| Run 小预算终止 | 用真实 Controller 创建 max_llm_calls=2 的 Run，目标需要多步工作。无论模型停在哪一阶段，usage 不得超过 2，耗尽后不得额外调用模型写总结；真实重试也占用额度。记录实际轨迹，不强求特定拆任务方式。 |
| 只读分析 | 给已有 metrics 文件和只读源目录，关闭执行/环境准备权限，请 Experiment 比较结果。应生成输出工件，command_count=0，源文件 hash 不变。 |

新脚本使用 schema 13：`RunBudget` 只有模型次数与时间；任务数/尝试数放在 `ExecutionLimits`；`permissions` 必填；工作区用 `WorkspaceAccess`；命令逐次确认用 `confirm_commands`。不要复制旧模式字段或旧全局确认开关。

由于本轮更改了进程封装，需要额外做一次已有训练环境的短进程集成检查：复用已装好的环境和数据，执行既有小训练入口，保存退出码、metrics 与执行记录；有 GPU 时验证 device=cuda。不要为了权限测试重新跑大规模训练。如果只能 CPU 检查，明确 GPU 集成为未验证。

既有完整流程可按需运行，不作为所有探针的前置：

```bash
REAL_E2E_WORKDIR=/absolute/evidence/direct python -m e2e.real_e2e direct
REAL_E2E_WORKDIR=/absolute/evidence/repair python -m e2e.real_e2e repair
```

repair 的自动谓词仍按直接训练命令识别。复杂包装命令可能导致谓词不通过，应保留 FAIL 与原始命令，另报人工语义复核结论；不要自动把人工通过改成脚本 PASS。新规则可能对内联代码要求确认，这是预期控制行为，应通过公开问答继续；不能把暂停误报成执行失败。

## 6. 证据交付和判定

证据目录至少保存：

- REPORT.md、results.json、PRODUCT_COMMIT、安装与 pytest 原始日志；
- 每一探针脚本、Run JSON、Session JSON、所有 question/answer、完整 trace、命令日志和实际工件；
- 拒绝和目录确认前后快照，不只保留最终态摘要；
- 所有失败、重试、中断和补测。新补测单列，不能覆盖原失败记录。

逐项报告 PASS / FAIL / BLOCKED，并分开“程序谓词”和“人工语义复核”。计量对账以 Run usage 的请求占用为准：每个 call_id/retry_index 唯一；正常完成时与 trace 对账；崩溃或保存后未发送时可能出现 unknown 或 trace 差额，逐条解释，不删记录。占用数不冒充供应商账单。

已知实现边界：同一 Run 串行、单执行者；批准消费与外部副作用不构成事务；宿主可信脚本执行不是 OS 沙箱。HTTP 取消不等待系统 DNS 线程，尚在 libc 中的解析可能后台结束，但其结果不会恢复已取消请求；本地取消也不保证供应商停止计费。

