# schema 27 服务器验收执行计划

日期：2026-10-14。范围：用户确认的纯任务路由、上下级问答、有序业务上下文、运行中普通输入和显式打断。本文是待执行计划，不是服务器通过报告。

## 0. 冻结与准备

本轮实现、全部新增模块/测试和文档由开发方在 `fix/native-agent-receipts` 一并冻结；验收使用该新提交的实际完整 SHA。旧基线 `f5fb215c48a0a7a5090814cd497344b9fe8f891d` 不包含本轮实现，不得作为 schema 27 冻结版本。服务器确认分支/SHA、产品源码无改动、schema 27.0。只能创建新 Run，不能恢复 schema 26 或更早 Run。

建议证据根 `/root/autodl-tmp/resagent2/runs/correction-routing-27-20261014/`；若已存在，使用新目录，不覆盖已有证据。每个短场景独立 Run ID 和 data root。所有证据保留所用冻结 SHA、命令、实际退出码和时间。

同步通过现有 Git；GitHub 不通时使用 Git bundle，不换源码。依赖按 environment.yml 和各包声明安装，9 个 editable 包的导入路径必须落在冻结源码树。使用独立测试环境与工作区，不清理或修补共享缓存。若共享 conda 缓存再次报 ESTALE，给本轮环境准备设置独立 CONDA_PKGS_DIRS，并把基础设施修复单独记账。

中性 LOCAL 工作区本身必须是 Git repository root，准备时验证 `git rev-parse --show-toplevel`，不要用缺 `.git` 的 archive 工作区直接启动。短场景不需要 GPU、官方 test 或下载数据集。

所有真实模型入口统一加载同一配置：模型/endpoint、输入窗口/输出预留、资源路径、权限、data root 和 full trace。真实 CLI 使用 build_application，不另造测试装配。配置中的凭据沿用既有私有载入方式，不写进目标/记录。公共证据与私有 Session/full trace 分开，后者保持既有私有边界。

## 1. 服务器离线复验

从冻结源码、声明安装的环境执行：

```bash
python -m pytest tests apps/cli/tests -q --tb=short
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

本地最终基线是 2237 passed / 1 skipped，mock run_golden completed、8 工件。服务器按实际结果报告；pip check 结果独立记录，不能把本地 pdfminer-six/cryptography 缺依赖改写为 clean，也不能拿旧轮服务器 clean 代替本轮核验。

直接复用现有测试覆盖微小时序，至少核对：late reply、完整批次停止、进程树/父先退出的继承输出管道、无法确认停止时 failed、PTY Esc、owner lock、多任务澄清和完成提交收件竞态。不能把某次真实模型没有自然触发边界写成该边界已经实测。

确认旧 Run 的 show/resume/answer 在源码绑定正确时拒绝，旧证据 hash 不变。此阶段失败先定位，不进入长实验。

## 2. 真实模型短场景

先做受控功能验证，明确它是为触发机制而设置的场景；随后至少一轮自然业务任务，不在目标中教模型调用 ask_user/reply_to_task 或分配 Agent。两类结果分开报告。每个场景预设小预算，例如 40 调用、1800 秒、4 Tasks、2 Attempts；超预算是结果，不能重置账本或偷偷续跑。超时时长按现有扣除人工等待的语义核对。

| 场景 | 操作 | 必查事实 |
| --- | --- | --- |
| 原文与纯路由 | 一个小任务同时要求代码实现及基于它的测量，给共同限制和精确输出名 | Scientific 给明确 Tasks；Compiler 只有 route_tasks；每个 key 恰匹配一次；原 instruction/objective/constraints 逐字符到执行输入；依赖/输出绑定正确 |
| 子问父答 | 在工作区设置需执行 Agent 检查后才能发现的业务选择；一轮父方有足够已有信息，一轮明确需要真实用户决定 | 子 ask_user 指向直接父；父先自行解答或向用户升级；父收到的下级问题/自身回复只在第三部分；子方问题和上级答复在第二部分；回复后同 Task/Attempt/Session，不能重建子会话 |
| 来源和权限 | 业务回答使用选项外自由文本；一次拒绝操作附说明，再一次准确批准 | 原字句/原时间/来源保留；父解释不冒充用户要求；拒绝未执行且原回执包含说明；批准不进第二/第四部分；父不能代替用户扩权 |
| 普通补充 | 分别在 Scientific 模型请求、子模型请求和短工具运行期间提交普通 input；再做一条补充影响两个 Tasks 的情况 | 原请求回复和已采纳批次保留；下一次子模型判断前交 Scientific，不能等完整 WorkRequest；受影响任务先收到澄清再恢复；同 Session/Attempt；当前用户问题不能被普通 input 自动回答 |
| 模型期间打断 | 模型请求在途时按 Esc 或 interrupt | 未采纳响应不能执行工具；停止确认后 paused；请求预算占用保留；恢复原 Scientific，完整事实保留 |
| 待答期间打断 | 分别在根业务问题、子问题升级和操作批准等待时 interrupt | 普通 input 不代答；有效且无关的根问题保留；旧委派关联问题/未消费批准失效；旧问题 ID 不能串到新任务或执行原操作 |
| 工具/工作期间打断 | 受控 CPU 命令写部分文件并启动受管子进程，在运行中打断；WorkRequest 中安排一个先完成 Task 和一个正在执行 Task | 当前受管进程停止、未开始动作不派发；原生唯一回执/accepted 后关联通知正确；已完成 Task/工件和真实文件 hash 不变；未完成 Task/Attempt cancelled；旧子 Session 不恢复 |
| 打断后继续 | 停止确认后补充“基于已保留结果调整后续工作”，再 resume | Scientific 原 Session 继续；按需新 request_work；不能重复已完成的副作用或把部分文件当成功交付；如重新委派，显式授权复用原材料/工作区 |

普通补充的“下一次判断前处理”意味着当前长工具需要正常完成；若要立刻停止它，使用明确 interrupt。测试人工等待时间、真实执行时间与预算不能混算。

模型随机性会影响任务分工、何时提问和是否理解澄清。自然任务至少用固定目标/初始工作区/模型配置重复 2–3 次，每轮新 Run，并保留失败尝试；这只能观察稳定性，不能给出总体成功率。受控机制触发通过不等于自然纠正能力已改善。

## 3. 实际交互命令

服务器既有安全配置入口应导出：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR="$ACCEPTANCE_ROOT/private/traces"
export RESAGENT2_DATA_ROOT="$ACCEPTANCE_ROOT/data"
resagent2 shell --data-root "$RESAGENT2_DATA_ROOT"
```

其中 ACCEPTANCE_ROOT 是测试方事先设置的本轮绝对证据目录；不在配置中输出密钥。以下是交互形状，实际 goal 文件、工作区、Run ID 由冻结测试材料指定：

```text
/run --run-id run_correction_case_a --workspace /absolute/prepared/git-workspace --goal-file /absolute/goal.txt --max-llm-calls 40 --timeout-seconds 1800 --max-tasks 4 --max-attempts 2
补充要求：……
/interrupt
/answer
/resume run_correction_case_a
```

这些动作按场景选用，不能不看当前状态机械全执行。普通文本只保存业务补充；/answer 回答当前问题；/interrupt 纯暂停后还需 /resume；/interrupt 后附补充的交互 shell 会确认停止后处理并继续。Esc 需要真实 POSIX PTY，不能用普通管道声称测过 Esc。

第二个终端可使用相同配置和 data root：

```bash
resagent2 input run_correction_case_a --text '补充要求：……' --data-root "$RESAGENT2_DATA_ROOT"
resagent2 interrupt run_correction_case_a --data-root "$RESAGENT2_DATA_ROOT"
resagent2 show run_correction_case_a --data-root "$RESAGENT2_DATA_ROOT"
```

一次性 interrupt 只确认请求已保存，必须从执行者的状态和实际进程核对停止。不要在活动 owner 存在时另开 resume/answer 抢执行；owner 锁应明确拒绝。

权限确认可人工逐条做，或用已明确授权、逐条记审计、越界即停的既有闸门。闸门误判单独记录。测试方不代写业务任务、研究代码、选择算法、调参或改模型生成的报告；只有预先设计的受控功能场景可以使用固定测试输入，不能事后追加提示来伪装自然通过。

## 4. 真实 L3 回归

前述机制验收通过后，跑一个新 L3，沿用既有温度缩放校准规程、相同中性基线/数据集/切分/初始目标和模型配置。产品版本和旧轮 schema 不同，但输入条件尽量保持一致，任何差异在报告列出。预算按已确认研究规程冻结，不把短场景 budget 当 L3 budget。训练/拟合/正式评测必须真实执行；方法、参数、口径和 checkpoint 先冻结，再唯一一次官方 test 评估。

完整自然 L3 与人为打断/纠正测试分开：完整 L3 看是否遵循目标与收敛，受控打断场景看机制；不能往同一个正式科研 Run 人为插入测试变化后再直接比较旧轮评分。

完成后由测试方独立复算逐样本 accuracy/NLL/ECE，并按已有规程核对 Brier/MCE、独立重拟合 T、checkpoint/切分 hash、冻结和唯一 test 访问时序、逐样本 argmax。独立前向重放必须先删除预测、导出真实数据集根、记录真实退出码，并证明本轮实际生成新文件；不得以复制原文件的 hash 相等当作重放通过。

重点审阅 full trace：是否再发违反原任务边界的冒烟命令，拒绝说明是否真正出现在匹配工具回执，模型随后是否调整，以及是否存在过度研究/不必要重委派。无违规自然请求也不等于拒绝反馈机制已经实测，机制场景证据另列。

研究评分按原规程给；调用次数、重试和工件数量仅是过程统计。一个新 Run 不能单独证明比旧版本更好，结果应同时说明输入差异、模型随机性和人工介入。

## 5. 证据与通过标准

保留冻结 SHA、源码/schema/9 包导入核对、全部命令与退出码、模型配置（不含密钥）、原目标/补充/问答/批准账、Run 状态与请求账、私有原始 Session/full trace、公开工件与 SHA256 MANIFEST、进程停止和文件保留证据，以及 L3 独立复算/重放。报告区分产品缺陷、基础设施问题、测试方错误和模型执行质量问题。

机制验收要求：原文传输/来源/上下文位置/唯一回执/生命周期/实际停机/owner/预算全部符合约定。不得仅凭 Run completed 或模型自述评分。无法触发的场景标明未覆盖；已失败证据不可删掉后只留成功重跑。

可以分别得出“机制通过，科研质量待复核”或“机制通过，L3 独立验收通过”；不要把后者提前写进前者的报告。

参考：[总体计划](CORRECTION_ROUTING_TEST_PLAN_2026-10-14.md)、[本地实施结果](CORRECTION_ROUTING_IMPLEMENTATION_2026-10-14.md)、[CLI 使用与配置](../../../apps/cli/README.md)。
