# 主线代码健康审查（2026-09-22）

审查基线：`main@5fe2c7f1ad5731bca9b8449fec7dccf650ada2a6`，schema 13.0。审查开始时 main 与 origin/main 一致，工作区干净。本轮不修改产品代码；本记录和历史索引为唯一文件变更，未提交或推送。

## 结论和范围

统一 Agent IO 与 Run Control 已建立清晰的主要边界：三个 Agent 共用 invoke/请求/结果协议；业务内容走 instruction/artifacts；预算共用 Run 账本；权限从 Run 向任务收窄。没有发现仍在运行的旧 Agent 业务模式，也没有发现旧 schema 自动迁移兼容层。

但不能称为“无遗留、无断链”。本次确认 5 个正确性问题、1 处无效旧入口，以及数处已无业务消费者的状态和辅助代码。下一轮适合做有限的稳定性收尾，先修真实链路，再删确定的残留，不需要重新设计整套 Agent 框架。

方法：只读检索生产调用关系；检查 Contracts/Runtime/Components/Capabilities/Agents/Orchestrator/CLI；使用隔离临时仓库、脚本客户端、内存 Scheduler 和纯函数做确定性复现。没有真实模型/GPU调用，没有安装依赖、改变服务器或重跑全套验收。CLI 现有测试本轮运行 76 passed，仍遗漏有效 /answer 路径。此前 1153 passed / 1 skipped 是已有验收基线，不是本轮重新取得的结果。

静态“无消费者”指当前仓库的生产代码，不证明未知外部调用不存在。以下明确区分确定问题、清理候选和必要的边界检查。

## 需要先修的链路问题

### F1：交互 shell 的 /answer 缺少新权限参数（高优先级）

位置：[shell.py](../../../apps/cli/src/resagent2_cli/shell.py) 的 `_cmd_answer`（374 行）、[main.py](../../../apps/cli/src/resagent2_cli/main.py) 的 `_workspace_specs`（76 行）。

shell 手造的 SimpleNamespace 只有 workspace/git/python_version；共享转换函数已经无条件读取 read_path/read_only/write_path/deny_path。设置当前 Run 与待回答问题后，合法 `/answer yes` 在调用 Controller 之前报错：

```text
AttributeError: 'types.SimpleNamespace' object has no attribute 'read_path'
```

独立命令行 answer 的 argparse 字段完整，不受这个错误影响。这是两处手写参数转换失同步，不是问答控制器本身失效。shell 的 _WS_FLAGS 也只保留旧三项。现有测试覆盖无当前 Run 的报错，没有覆盖有效回答从 shell 到 Controller。

建议：共享一个参数解析/转换入口，补最小有效问答入口测试，避免只给另一个 Namespace 手填一批默认字段。

### F2：依赖安装可以偏离绑定环境（高优先级）

位置：[environment.py](../../../packages/components/src/resagent2_components/environment.py) 的 SetupCommandPolicy（557 行、566 行）、[run_setup.py](../../../packages/capabilities/src/resagent2_capabilities/environment/run_setup.py) 的 execute（76 行）。

策略拒绝 --target，却放行等价短参数 -t，以及 --user、--root；按可执行文件 basename 接受任意绝对路径 python/pip。pip/python 命令原样跟在 conda run 后面，只有 conda env update 会重建绑定的执行路径。

纯函数复核结果：

```text
False python -m pip install --target /tmp/a package
True  python -m pip install -t /tmp/a package
True  python -m pip install --user package
True  python -m pip install --root /tmp/a package
True  /usr/bin/python3 -m pip install package
```

另以本机已有 conda 环境运行只打印解释器信息的探针：conda run 后显式使用 /usr/bin/python3，sys.prefix 为 /usr，证明 conda run 不会把绝对解释器自动改成绑定环境。未运行安装命令；并不声称已经发生宿主污染。

影响：run_setup 可能装入宿主、用户目录或另一路径，之后审计绑定环境也不能撤销这些副作用。这属于确定的安装范围约束缺口；项目已明确任意进程不具备 OS 沙箱，本发现不等于首次发现可执行进程拥有宿主权限。

建议：pip 安装固定走绑定环境的解释器；在单一安装命令构造处拒绝/规范化改变目标位置的参数及其短形式。保留缓存设计，不另起资源管理系统。

### F3：首轮与追加轮的任务输出绑定校验不一致

位置：[contracts/models.py](../../../packages/contracts/src/resagent2_contracts/models.py) 的 _validate_task_graph（672 行）、WorkflowProposal（726 行）、WorkflowPatch（762 行）；[scheduler.py](../../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py) 的 _resolve_future_artifact_bindings（143 行）。

同一 producer/consumer 草案：producer 声明输出 actual，consumer 请求 typo。首轮 Proposal 拒绝；后续 Patch 只校验局部 ID，丢失对 output_names 的检查，转换成 WorkflowTask 后已不能补回。

零模型、真实 Scheduler 的复现：

```text
initial REJECTED: future artifact binding selects an undeclared output name
patch ACCEPTED WorkflowPatch
producer completed
OrchestrationError future artifact selector is missing or ambiguous
consumer pending attempts 0
```

后续轮会先执行并消耗上游成本，再在下游入口失败；绑定解析位于 execute_task 的异常收尾边界之外。首轮的 Compiler 结构纠正机会在此缺失。

建议：Proposal/Patch 共用完整的候选图校验，并把任务绑定失败纳入明确的失败持久化边界；不要分别维护两套近似规则。

### F4：依赖失败传播受任务列表顺序影响

位置：[scheduler.py](../../../packages/orchestrator/src/resagent2_orchestrator/scheduler.py) 的 _evaluate_run（369 行）和 _build_work_outcome；[completion.py](../../../packages/orchestrator/src/resagent2_orchestrator/completion.py) 的终态检查（61 行）。

合法依赖链 a → b → c → d，任务数组倒序存放为 [d,c,b,a]，a 不可重试失败。当前代码按数组只传播一遍；WorkRequest 没有 ready/running 即宣布 stable，并把残余 pending 在反馈中投影成 blocked。

零模型 Scheduler 复现：

```text
work stable
tasks   [(task_d,pending), (task_c,blocked), (task_b,blocked), (task_a,failed)]
outcome [(task_d,blocked), (task_c,blocked), (task_b,blocked), (task_a,failed)]
```

结果是持久任务状态与交给 Scientific 的结果矛盾；最后验收仍会因为 pending 拒绝结束。合法 DAG 没有要求数组必须拓扑排序，不能把问题归因于模型未排序。

建议：按拓扑顺序传播一次，或传播至不再变化；确认相关任务全部终态后才宣布 stable。补乱序、多层依赖失败测试即可，不需要新调度框架。

### F5：Coding 超时后，诊断收集覆盖原失败结果

位置：[coding/agent.py](../../../packages/agents/coding/src/resagent2_coding/agent.py) 的 invoke（69 行）和失败补丁处理（155 行）；[components/process.py](../../../packages/components/src/resagent2_components/process.py) 的 run_process。

AgentLoop 已把超时结果与 Session 保存；外层仍调用 changed_paths_since/diff_since。Git 命令受同一个已过期 deadline 控制，再次抛 DeadlineExceededError，被 invoke 换成新建的最小失败结果。

隔离临时 Git 仓库：第一次脚本响应让 replace_text 修改文件，第二次响应把注入时钟推进到 31 秒，额度为 30 秒。走公开 NativeCodingAgent.invoke，未替换文件工具、Git 或 AgentLoop；没有网络模型调用。断言后的实际输出：

```json
{
  "result_status": "failed",
  "result_error": "timeout",
  "result_session": null,
  "result_calls": 0,
  "result_artifacts": 0,
  "shared_used": 2,
  "persisted_status": "failed",
  "persisted_calls": 2,
  "file_content": "NEW=2\n"
}
```

文件与持久 Session 未丢，Run 的共享计量也没有因此归零；丢失的是 AgentResult 中的 Session 引用、调用投影及诊断交付。不能将这一点夸大为全部账本丢失。

建议：诊断补充失败时保留已有 AgentResult；预算已过期时不再依赖额外进程才能返回正确的失败记录。诊断 patch 无法取得时如实标记，不能因此改写已有事实。

## 遗留入口和可删除项

### 无 workspace 的 Run 在恢复时接受补给参数，但不会应用

[main.py](../../../apps/cli/src/resagent2_cli/main.py) 的 _specs_for_existing_run（115 行）仍返回新增 supplied workspace，293 行提示回答/恢复时补 --workspace 或 --git。新行为已在 create_run 时物化并持久化工作区；后续恢复不调用 _resolve_workspaces，因此这些新增参数没有效果。正常已持久化工作区的恢复不受影响。

建议删无效补给分支与提示；恢复只用既有授权，必要时对重复传入的配置做一致性校验。若未来真要支持中途加工作区，应另行明确授权收窄/扩展语义，不能让旧接口假装支持。

### 确认没有业务消费者的状态/函数

| 项目 | 当前证据 | 建议 |
|---|---|---|
| runtime/context.py:44 user_answers_section | 只有定义、导出和 3 个专属自测；业务已走 answer artifacts | 删除函数、导出及专属旧测试 |
| orchestrator/models.py:93 answer_task_ids | controller.py:100 写入，无生产读取 | 删除旧索引与写入；保留 RecordedAnswer 自身作用域 |
| experiment/agent.py:129 的 experiment_success_count | tools.py 只自增；没有业务判断或上下文消费者 | 删除计数和对应旧断言 |
| experiment/agent.py:131 的 workspace_snapshot | 新调用仍生成/持久化，Experiment 后续无读取 | 删除无效快照与完成检查中仅为访问 boundary 引入的 Observer 包装 |
| components/snapshot.py 的 WorkspaceObserver.changed_paths/is_git | 当前只有测试消费；旧 Experiment 自动归属检查已取消 | 删除失去用途的 Observer/非 Git 回退；保留 Coding 恢复真实使用的 Git baseline 能力 |
| scheduler.py:36 _question_id 的两个参数 | 生产均无参调用，参数不参与实现 | 移除无效参数和旧测试用法 |

Experiment 快照不是单纯“几行没用的字段”：非 Git 工作区会读文件并计算 hash（最多 4096 个文件，单文件读取未设体积上限）；Git 工作区也会扫描/生成树。纯分析请求因此承担了无消费者的 I/O。snapshot.py 顶部仍声称服务 Experiment evidence ownership，CONTRACTS 的普通组件表也保留旧用途，应随清理同步，不能把历史 ADR 原文改成新事实。

以下是更低优先级候选，不与确定的业务故障混算：

- runtime 的 ReadValueTool/WriteValueTool 仅测试使用，可移至测试夹具。
- components 的 HardwareAudit 只有导出和测试消费者；旧 E2E 仍 monkeypatch HardwareAudit.text，但当前 Agent 不再调用，测试准备已失效。
- components 的 mirror_env_overrides/_MIRROR_PROFILES 只有定义与导出，没有安装链消费者，不代表当前 Agent 实际使用这些镜像配置。可以删除未接入配置，实际 pip 镜像继续由运行环境管理。
- plan_environment_cleanup/apply_environment_cleanup 仅内部测试消费，但可能作为维护 API；删除前明确是否仍支持，不应仅凭内部引用数判断。
- scheduler.retry_task 只有测试消费；当前生产走错误自动重试或 Scientific 新任务。删除前明确是否仍作为受支持的独立 API。
- CompletionViolationCode.UNKNOWN_EVIDENCE 无实际发出点，当前归入 INVALID_OPINION，可清理或统一分类。
- Coding/Experiment 的装配重复可用少量辅助函数削减，但不建议为此增加庞大 AgentBase/插件框架。

## 哪些相似逻辑应保留

- Agent 使用 native tool calls，Compiler 使用结构化 JSON：职责不同，不是两个 Agent 业务模式。
- Agent 自身检查结果、Orchestrator 接收后再验：是不同信任边界。
- Controller、Scheduler、调用扣费前的预算检查：共用 Run.usage，不是多个独立预算账本。
- 工作区路径约束与危险命令规则：约束对象不同，可以共用决策入口，不必硬合成一项权限。

AgentLoop 的 JSON next_action 与 native next_tool_call 两条执行路径仍在维护，前者主要服务脚本/注入客户端。这是实际维护成本，但不是已经废弃的死分支。大量确定性测试走脚本路径，与真实模型的 native 路径有差别。可以后续让测试 adapter 也发 ToolCallTurn，缩小差异；不要顺便删掉 Compiler 的 JSON 协议。

## 建议下一步

1. 小轮修复 F1–F5 和无效恢复入口，分段提交；共享参数/校验/状态推进的最小实现，不新增兼容层。
2. 删掉确认无消费者的字段、快照、函数、旧测试；同步当前规范和注释，历史记录保留。
3. 补高价值确定性边界测试：真实 CLI/shell 回答入口、初始/追加图、乱序 DAG 失败、文件已改后的超时、安装目标约束。不要以新增纯自测数量作为完成标准。
4. 再跑少量完整公开入口 E2E：Scientific 提出工作 → Compiler → 执行 Agent → 提问/批准 → CLI/Controller 恢复 → Scientific 最终结束。此前固定 Task 验收有价值，但不能替代这条整链。
5. 链路稳定后再做功能增量。较小的增量可先把剩余 Run 调用数/时间和允许能力投影给模型；共享账本仍由代码执行，不新增模型自主管账或动态预算分配器。

本轮未宣称穷尽所有缺陷；上述问题足以确定下一轮优先方向。修复完成前，宜继续用于受控开发和实验，不应把现有绿测解释为长任务和所有恢复入口均已可靠。

