# Unified Agent IO V2：本地实现与验收

后续服务器复测及独立验收见 [2026-09-21 复核记录](UNIFIED_AGENT_IO_V2_RETEST_REVIEW_2026-09-21.md)。本文保留初次本地验收时的结果与范围。

日期：2026-09-20。分支：`refactor/unified-agent-entry`。对应[实施方案](UNIFIED_AGENT_IO_V2_PLAN_2026-09-20.md)。

## 实现结果

Scientific、Coding、Experiment 均使用 `invoke(AgentRequest) -> AgentResult`。每个模块只有一套 prompt、action、finish 和业务流程；任务输入为 `instruction + input_artifacts`，业务输出为 `report + artifacts`。提问、请求工作、恢复、失败和完成保留为生命周期状态。

- 删除旧 capability 业务模式、专有请求/结果外壳和 payload；Workflow 只按 coding/experiment 路由。
- 系统把答案、数据集目录、工作反馈、Task 验收要求和 Run 结论要求冻结为工件；Agent 经统一输入材料读取。
- Task/Attempt 绑定同一验收要求 Ref；显式 output_names 合入同一快照。未来产物绑定必须声明直接依赖并解析唯一逻辑输出名。
- Coding 支持只读分析、可写但不修改；Experiment 支持分析已有结果。需要实际执行时由显式验收要求约束。
- patch、验证、执行和观察记录由确定性代码生成；三个 Agent 共用系统产物保留规则，模型 finish 不得提交这些事实类工件。
- 命令确认绑定待批准命令；连续问答只将新回答标记为恢复事件，同一问答恢复保留 Attempt 与 Session。
- 共享环境工具在执行入口检查操作授权；Registry 支持授权工作区或受控输出目录，核对真实路径、hash、来源与归属。
- Compiler 请求一版结构化草图，JSON/结构错误最多纠正一次；预算或传输失败不伪装成结构纠错。已删除单独语义复审调用。

公共契约为 schema 12.0，不提供旧版本运行路径。旧 Run/Session 加载被拒绝，历史文件不迁移、不改写。

## 本地验证

在 WSL Ubuntu-D 的 ResAgent2 Conda 环境运行：

```bash
PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  /home/cyl/miniconda3/envs/ResAgent2/bin/python -m pytest \
  tests apps/cli/tests -q -p no:cacheprovider --tb=line
```

结果：**1036 passed，1 skipped**。跳过项为需显式启用 `RESAGENT2_LITERATURE_SMOKE` 的真实网络文献检索测试。

覆盖包括公共契约与非法实例重验、结果计量、工具权限、真实执行记录、来源/hash、验收快照、输出绑定、问答及跨进程恢复、失败诊断与部分产物、跨 Run 隔离、上下文容量、文献分页、CLI 和本地确定性 E2E。

额外验证：

- `python -m e2e.mock_e2e`：Run completed，两个任务完成，登记 9 个工件，生成 `artifact_final_report`。
- `python -m resagent2_cli --help`：入口正常。
- 生产源码不再引用旧 Agent 请求/结果类型与 code_understand/code_modify/experiment_run 路由。
- `git diff --check` 通过。

工具 schema 基线仅更新本次必要的 5 个控制 schema，其余 13 个工具指纹保持原值，见 [变更依据](../../../tests/e2e/TOOL_SURFACE_V2.md)。

## 提交与验证范围

本地代码分阶段提交：

1. `5885d02`：明确单模式协议与工件交接方案。
2. `dcd6d64`：统一公共契约与 Runtime 完成协议。
3. `368a43e`：三个 Agent 的统一业务流程、材料投影与操作权限。
4. `535856d`：编排、不可变验收、恢复与产物边界。
5. `54e5ebf`：CLI、E2E 入口与回归迁移。

本轮没有合并或推送。服务器尚未启动；按用户安排，真实模型、真实网络和 GPU 验收待服务器地址与端口提供后执行。本地测试结果不代替该验证。

ModulePort 是组合根注入的可信 Python 实现，确定性 finalizer 负责从内部事件生成事实；Controller/Scheduler 检查公共结果和工件，不读取下游私有 Session。该边界约束模型输出，不隔离恶意自定义 Python Port，也不保证科学观点必然正确。
