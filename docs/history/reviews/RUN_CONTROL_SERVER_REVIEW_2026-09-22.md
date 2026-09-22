# Run Control schema 13 服务器验收复核

日期：2026-09-22。原测试提交：`29d3ab80e0363a7cac79ca9d8238ded4921f68ed`，分支 `refactor/run-control`，schema `13.0`。

最新结论：修复产品 `602ffee` 已在服务器实测 `8b071e6a` 下通过定向功能复核，两处产品缺陷关闭，未发现新产品缺陷。原轮次失败保留；修复轮的证据缺口、测试方法及计量边界见[第 5 节](#fixed-round)。分支保持未合并。

原始证据：`/root/autodl-tmp/resagent2/runs/run-control-20260922/`。本记录依据 REPORT.md、results.json、探针脚本、逐项结果及 Session 观察复核；不修改服务器原报告或失败现场。修复后的操作步骤见[测试交接](RUN_CONTROL_TEST_HANDOFF_2026-09-22.md)。

## 1. 结论与报告勘误

本轮不能按“只有删除工具漏项，其余是模型漂移”收尾。确认两个产品缺陷：Coding 删除工具未被动作模型接受；Coding/Experiment 命令批准后重建环境绑定，认证丢失导致重复审计和确认。此外，两项报告中的 PASS 缺少对应通过证据。

原报告表格有安装、回归及 item1–7，共 **9 项**，不能写成 5 PASS / 3 FAIL。按原始结果及交接要求复核为 **4 PASS / 5 FAIL / 0 BLOCKED**；FAIL 中包含探针未覆盖和证据缺失，不表示发现五个产品缺陷。

| 项目 | 复核结果 | 证据及限制 |
| --- | --- | --- |
| 安装与导入路径 | PASS | 9 包指向固定主仓库，httpx 0.28.1，pip check 干净。 |
| 确定性回归 | PASS | 1136 passed / 1 skipped，对应原测试提交。 |
| item1 修复与普通删除 | FAIL | 修复和文本删除成功；delete_path 在 Coding 动作校验层被拒。 |
| item2 目录删除确认 | FAIL | 同一动作模型缺陷，未到达结构化删除确认链；ask_user 的 action:null 不能替代它。 |
| item3 两个命令分别批准 | FAIL | 反复确认及审计，两个 marker 未生成；环境认证恢复缺陷有直接 Session 证据。 |
| item4 陈旧答案拒绝 | FAIL | 探针原始结果为 FAIL，四个检查全 false；未到第二命令等待态，也未保存声称的拒绝输出和前后快照。 |
| item5 小预算终止 | PASS | budget_exhausted，llm_calls_used=2，无额外总结调用。 |
| item6 Experiment 只读分析 | FAIL | 实际由 Coding 读文件、Scientific 总结；session_command_counts=[]，command_count_zero=false，原始 verdict=FAIL。只读 hash 和 +0.07 结论有效，但不能作为 Experiment 验收。 |
| item7 GPU 进程封装 | PASS | 短进程检查 device=cuda、exit 0、执行记录完整；范围是 ProcessRunner 集成，不是完整 Agent GPU E2E。 |

### F1：Coding 删除动作漏项

Coding 已注册 DeletePathTool，提示词也介绍该工具，但 CodingAction.tool 的 Literal 漏了 delete_path，导致原生工具调用在动作解析时失败。原测试只覆盖底层删除策略，未覆盖模型动作入口。

修复只补 **CodingAction**。Experiment 没有注册源码编辑/删除工具，不向 ExperimentAction 加入它不支持的动作。两种 Agent 仍各自只有一条 invoke 入口和一种业务模式；能力集合可以不同。

### F2：环境认证在命令批准后丢失

每次 Native Agent.invoke 都重建 EnvironmentBinding，默认 certified=False。此前明确要求模型先 audit_env，再执行命令；confirm_commands 又会让命令暂停，批准后的新 invoke 再次失去认证，形成循环。

Session 证据位于 `cases/item3_two_commands/sessions/`：

- Coding 的 `coding/session_run_rc_two_commands_task_env_audit_1_9ed1d7c3ef82359c27cf2c7b4de6d362.json`：事件 16 审计通过，21 请求命令批准，23 又返回“Environment not audited; call audit_env before verification”。
- Experiment 的 `experiment/session_run_rc_two_commands_task_run_first_1_4c5a651347dc199cf32ad1ea9563ce1f.json`：事件 12 因未审计阻止命令，16 审计通过，18 又请求第一条命令批准；最终仍等待 `python measure.py --tag first`。

原交接漏写 prepare_environment 权限确实需要补正，但保存的实际探针已授予该权限，因此不能用规格遗漏解释全部失败。模型反复尝试有上述产品状态循环诱因，不能全部归为模型漂移。

修复将执行前环境核验放在已获授权命令的固定执行步骤中：新绑定尚未认证时真正检查环境，检查通过才执行命令，失败则阻止命令。核验沿用同一 Run 截止时间，不要求模型为同一前置检查再走问答循环。显式 audit_env 保留为诊断工具；任意命令的逐次批准、环境 generation 失效及旧验证不可复活规则不变。

## 2. 本地修复及验证

- `5221835`：补 Coding delete_path 动作；新增 NativeCodingAgent 原生工具协议测试，覆盖普通文件/空目录删除、递归删除跨实例批准恢复及实际工具清单一致性。相关回归 91 passed。
- `602ffee`：Coding/Experiment 获准命令执行前核验新绑定。新增 14 项确定性回归，使用真实 Native Agent、重建的 SessionStore 和真实 marker 进程；模型、环境发现和审计边界使用测试桩，不能当作真实模型/Conda 复测。覆盖逐次批准、重复命令也需新批准、审计失败、审计耗尽期限、无执行权限和陈旧答案拒绝。
- 最终全量 **1153 passed / 1 skipped**，pip check 无依赖冲突，git diff --check 通过。执行步骤见[测试交接第 1–2 节](RUN_CONTROL_TEST_HANDOFF_2026-09-22.md)。本地通过不代替修复后真实模型复测。

修复不恢复旧认证标记、不跳过环境检查，不新增模式或兼容层，也不调整镜像和缓存架构。

## 3. 计量结论的范围

原轮次 item2/3/6 重用了 run_id，trace 累积而持久化状态只保留末次运行；不能将累积 trace 与最终 usage 强行一一对账。报告记录 135 条模型 trace、1 次真实 retry 和 1 条 unknown 预留。最终持久化用量与历史 trace 的差额须作为旧轮次证据限制保留，不能删除 trace 或把多次运行当成同一次恢复。

每次重试必须使用新的 run_id、状态/Session/trace 目录，并保留该次最终或中断状态。只有同一 Run 的正常问答恢复才继续原标识与原账本。

## 4. 下一轮范围

复用主仓库、现有 Conda 和依赖缓存，检查导入来源即可；无需为本修复再次安装 PyTorch/CUDA 或重跑完整训练。更新后跑全量确定性回归及 item1/2/3/4/6；item5 与 item7 保留为旧提交的既有证据，不冒充新提交重测。

命令与只读探针固定目标 Agent/Task，避免把自动路由到其他角色当作目标角色已验收。陈旧答案拒绝在第二条命令 pending 时通过公开 Controller API 测试，必须保存原始脚本、异常、前后 Run 和全部 Session 字节快照及用量。若真实模型未到该状态，可另做明确标记的确定性补测；它不使真实两个命令探针自动变成 PASS。

每项原始 result.json、汇总 results.json 和 REPORT.md 的判定必须一致；人工复核和程序谓词分别列出。失败、补测和中断记录均保留。

<a id="fixed-round"></a>

## 5. 修复轮服务器复核与收尾

实测提交：`8b071e6a67f6a964ed61b556876c21963f472bdb`，产品基线 `602ffeef597ba86a4bfa4de3c958caba9dddb9de`。原始证据根：`/root/autodl-tmp/resagent2/runs/run-control-fix-20260922/`。本次只读核对服务器报告、结果、脚本、Run/Session、工件和 trace，不修改原证据，不运行新模型或 GPU 任务。

产品仓库 HEAD 与 TEST_COMMIT 相同、修复基线是 HEAD 祖先；受版本控制的文件干净，仍有未跟踪 `.ipynb_checkpoints/`。9 个包的导入路径均指向固定 `projects/ResAgent2`，未重装；pip check 干净。服务器全量 **1153 passed / 1 skipped**，退出码 0。

### 功能结果

本轮是 **4 个真实模型探针场景，覆盖 5 个验收项**（item3/4 共享一个场景）。安装、回归另计；item5/7 只保留原提交的已有证据，不属于新提交重测。不能把“复测 4 项”与列出的 5 项混用。

| 项目 | 独立复核结果 |
| --- | --- |
| item1 修复与普通删除 | Coding 的 replace_text 两次成功，delete_path 的实际观察记录 `deleted_paths=[old_util.py]`、无剩余目标；Task completed，未要求普通删除确认，未运行命令。F1 通过。 |
| item2 目录删除确认 | 同一 Attempt/Session 先暂停再执行；目标为 junk 及三个文件，文件有 sha256/version、目录有 version。实际删除观察与四个目标相符，keep.txt 保留。F1 通过。 |
| item3 两个命令分别批准 | 两个不同问题与答案的 action 完整匹配；真实 Experiment 执行 first/second 均 exit 0、各有 `env_audit.success=true`，两个 marker 依次生成。冻结执行记录的 SHA 与注册值匹配；command_count=2，9 次调用与账本逐一一致。F2 通过。 |
| item4 陈旧答案拒绝 | 第二题 pending 时通过公开 controller.answer_question 重交第一题，原始异常为 OrchestrationError。落盘 Run 前后快照字节相同、pending 仍为第二题、usage 均为 5；Session 不变有探针断言，但原始前后 Session 快照未保存，见下文。拒绝行为通过，证据完整性不能写成全部满足。 |
| item6 Experiment 只读分析 | 实际 Experiment Session 读取 0.45/0.52，报告正确给出差值 0.07；command_count=0、无执行记录、源文件 hash 相同。纯分析行为通过；报告和计量限制见下文。 |

两处产品缺陷可以关闭。本次收尾不新增产品修改，不需要为记录勘误重复运行真实模型或 GPU。

### 测试方法与证据边界

1. **固定任务探针不等于完整科研 Run E2E。** 原生 Agent 使用真实模型，Scheduler 使用固定任务图，执行工具与环境核验未 mock。但正向批准由 `rc_fixed.answer_and_resume` 测试辅助代码记录答案后调用 Scheduler，它镜像了 Controller 的部分逻辑，并未经公开 Controller 正向问答入口；例如未累计 user_wait_seconds。最终 Task completed，而 Run 保持 running。可验收 Agent/Task 的批准恢复和工具执行，不能称本轮覆盖 Scientific 拆图、Controller 完整生命周期或最终报告收尾。陈旧答案拒绝则确实使用公开 Controller API。

2. **F2 根因仍是产品认证状态丢失。** 固定任务图排除了路由变化，但不能把上一轮失败重写成仅由 Scientific/模型漂移导致。42→9 次调用来自不同测试方法和运行轨迹，只作背景记录，不作为受控的性能提升比例。

3. **item4 没有保存全部 Session 原始快照。** `stale_before_state.json` / `stale_after_state.json` 实际各只有一份 Run 文件的 hex 字节映射，解码后均为 10561 字节，SHA256 为 `66372f2e8b8cbcc727879e0e8311cdeaf2a4151e7a4130464ed64765b9d2de37`。脚本比较了内存中的 Session 字节字典并记录 true，但没有写盘。不能从执行完第二条命令后的最终 Session 反推原拒绝时快照。拒绝栈与 Run 原文支持拒绝行为及无新增占用；本提交全量回归中的 `tests/e2e/test_command_approval_resume.py::test_stale_first_answer_cannot_change_second_command_or_session` 提供额外确定性依据。接受功能结论，同时保留未满足原交接 Session 原始快照条款的事实；不补造历史证据。

4. **item6 的测试断言确实改过。** 现存脚本仍有“必须产出工件”的注释，但检查已改为非空报告；报告承认因断言修正重跑，因此不能不加说明地写成“未改断言”。产品 ExperimentCompletionCheck 本就允许纯分析仅返回报告，任务也只要求读取并比较，不要求额外文件；无命令时不应强制生成执行记录。本次接受 report-only 为合法输出，纠正交接中过度要求输出工件的文字，不改产品契约。原来的失败脚本/结果版本未保留，不能独立复核完整修改前后差异。

### 调用计量

24 条模型 trace 均 retry_number=0，调用键唯一。三场景可完全对账：repair 5/5、delete 5/5、commands 9/9。readonly 的前次 3 条 trace 仍在，但同一 run_id/session_id 重跑覆盖了前次 Run/Session；最终保留的 2 条 usage 可精确匹配后两条 trace。不能把 5 条 trace 与最终 2 次占用当作同一 Run 的完整对账，也不能说所有失败状态均已保留。

因此准确表述为：观测到 24 次模型调用；现存四份最终账本共 21 次成功占用且无 unknown；另 3 次历史调用缺少对应当时的持久状态。本轮继续复用 run_id 违反了交接要求，已作为测试记录缺陷保留，未发现由产品导致的计数丢失。后续重跑须使用新标识和独立目录，不能 reset 状态后追加到旧 trace。

## 6. 合并前文档同步检查

2026-09-22 更新远端引用后，`origin/main` 为 `f771a70e`，是当前开发分支祖先；`refactor/unified-agent-entry` 的 `c0add70` 也已包含。只读 merge-tree 检查无冲突，可直接将 `refactor/run-control` 合入主线，不需要先分别合并其父分支。本次检查未执行合并。

按现有代码复核当前 22 份入口、规范、指南和模块文档：补齐 CONTRACTS 的预算/权限/恢复边界，CONTEXT 的实际模型可见字段及自动核验回执，架构和模块 README 的单次批准、环境核验及失败判据；修正旧答案上下文、Components 缺项、过时验收导航与服务器使用规则。按文档规则新增 [ADR-0016](../decisions/0016-unified-agent-io-and-run-controls.md)，明确旧决策的部分取代关系；历史正文与原始失败记录不改写。

检查全部 78 份项目 Markdown 的相对文件链接（含新增 ADR），当前文档的章节锚点、19 个 bash 示例及其中 10 条 CLI 命令解析，均通过；6 个因源码迁移失效的历史定位链接已修复。git diff --check 通过。本次只改文档，产品仍是已验收的 `602ffee`，沿用本地/服务器 1153 passed / 1 skipped，不将文档检查称为再次执行完整产品测试。
