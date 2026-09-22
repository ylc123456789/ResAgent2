# Run Control schema 13 服务器验收复核

日期：2026-09-22。原测试提交：`29d3ab80e0363a7cac79ca9d8238ded4921f68ed`，分支 `refactor/run-control`，schema `13.0`。

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
