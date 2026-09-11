# 运行期资源管理（schema 6.0）

状态（2026-09-11）：阶段 1–4 及两轮小修复已完成；最终产品提交 `f3179e5` 的 §9 补验已独立复核，未发现产品代码合并阻断。用户已授权合并收尾，本次仅同步文档；JSON 专项仍开放。基于 `95f965f`，开发分支 `fix/runtime-resources`，各阶段与证据范围见下文。

目标：调用方只提交研究意图；系统提供数据集目录，Agent 在运行中发现需求。
复用现有 DatasetCatalog、环境安装/审计和 ask_user，不引入统一 Resource 框架。

## 分阶段提交

1. 数据集引用从 ResearchRequest 移到 Run 内部状态，同步内部输入及两个组合根，schema 升到 6.0。
2. 共享数据集检查区分登记和可用；不相关目录缺失不阻塞；回答后重新检查，复用原 Session/Attempt。
3. 所有 ask_user 等待统一不计入 Run 超时；不重置已用时间或 LLM 预算。
4. 全量本地验证、当前文档与服务器验收要求。

## 初次本地验证（577b8489）

- 795 passed, 1 skipped；mock_e2e completed；git diff --check 干净。
- 最终全量使用 WSL --cd 显式隔离在 `/tmp/resagent2-resource-final.QBUI1J`，避免跨 shell 变量展开干扰测试 cwd。
- 127 个文档本地文件链接目标检查通过；未宣称渲染器/所有标题锚点均做 UI 验证。
- 第一阶段 `bafe18b`：schema 6.0 / Run 引用归属。
- 第二阶段 `ccf4b90`：三个 Agent 的共享可用性与恢复检查。
- 第三阶段 `f61488c`：统一人工等待预算。
- 第四阶段：当前架构/契约、CLI、教程、ADR 和验收单同步。

本地测试未调用真实模型或安装外部依赖；未上服务器、未清理旧数据。
服务器仍须按 [验收单](RUNTIME_RESOURCES_ACCEPTANCE.md) 检查真实模型的资源选择与 ask_user 行为。

## 2026-09-10 服务器复核与小收尾

以上 795/1 是初次本地记录。服务器 `577b8489` 也为 795 passed、1 skipped，八个回归 Run 最终 completed；资源目录更新、口头确认不改变目录事实、同 Session 恢复、人工等待及调用计量均已核对。证据根为 `/root/autodl-tmp/acceptance-runtime-resources/`，不是后续收尾提交已验收的证明。

复核不接受“全部无缺口”的表述：Coding 未登记探针的任务明确要求先确认数据集，但询问条件被写成只看 unavailable 列表。模型据此绕过询问并完成；这是行为验收缺口，不是“任务没有提数据集”。原始 trace 保留。

本轮仅两处产品调整：

- capabilities.dataset_context 明确 available/不可用/未登记三种语义；需要的数据不在 available 中就先询问。仍是共享行为提示，不新增资源闸口或字段；同时说明脚本 JSON 映射不是 catalog 路径。
- CLI 公共 renderer 有 final_opinion 时隐藏旧过程判断，无最终意见时标注 interim；不改写 Run 历史，不复制或同步两个结果字段。

回归覆盖共享提示到四种 Agent 模式的实际上下文、三种资源情况，以及 CLI 最终/运行/暂停/失败展示与渲染无副作用。真实模型补验见 [验收单 §8](RUNTIME_RESOURCES_ACCEPTANCE.md#8-本轮小收尾补验)。

本轮本地验证：定向 72 passed；全量 **802 passed, 1 skipped**；mock_e2e completed；git diff --check 干净。使用 `/tmp/resagent2-resource-closeout.Xu7SSK` 隔离 cwd。新增 7 个用例，不调用真实 LLM、不安装依赖、不修改服务器状态；这些结果不替代 §8 的真实模型补验。

JSON 协议失败另见 [专项记录](LLM_JSON_OUTPUT_FOLLOWUP.md)：八场景 192 次尝试中 31 次 JSON 解析失败，两次耗尽客户端重试后发生任务 Attempt 重试；不修改 JSON 处理，不归为资源闭环已修问题。

## 2026-09-10 用户回答上下文补齐

`d03abee` 的 §8 现场在 `/root/autodl-tmp/acceptance-closeout/`。共享目录提示已到达、CLI 展示测试通过、最终指标为 `{"value":42.0}`，但不能认定全部行为通过：Experiment 在目录仍缺时先执行了 `python run.py`（KeyError），随后一次环境映射诊断成功、一次 `ls` 被拒绝，再次 ask_user；不是三次实验命令均失败。

原始请求与驱动显示 UserAnswer 已传入 ModuleTaskRequest，却未进入 Coding/Experiment 的 context builder。模型只能看到历史 ask_user 成功观测，无法看到实际回答；Scientific 已有答案段，不存在这一遗漏。回答不可见是确定性代码缺口，但不据此宣称它是所有模型偏离的唯一原因。

修复沿用现有 ContextSection → ContextComposer：

- runtime.context 增加纯函数 `user_answers_section`，仅投影调用方传入的回答，不新增状态或缓存；按传入顺序完整呈现为 required 段，共享现有预算。
- Coding 两种模式与 Experiment 接入同一个函数，每一步都呈现；Scientific 原有答案段不动。Scheduler 的 Task 答案作用域、同 Session/Attempt 恢复规则不变。
- 共享资源提示明确：回答后所需目录仍缺就再次询问；历史 ask_user 成功不代表资源已就绪，旧命令结果也不是刷新后的资源视图。
- 不增加强制资源闸口、不改公共 schema、调度状态机、JSON 解析或重试。

定向 **73 passed**；全量 **805 passed, 1 skipped**；mock_e2e completed；git diff --check 干净，使用隔离 cwd `/tmp/resagent2-answer-context.0bWfVV`。新增三项 helper 测试，覆盖顺序、无缓存/无修改与必需段预算；扩展四种模式的三进程测试，验证恢复首步及实际读取文件后的下一步仍含准确答案且只注入一次。脚本驱动不证明真实模型一定遵循资源提示；新补验要求见 [§9](RUNTIME_RESOURCES_ACCEPTANCE.md#9-用户回答上下文补验)。

§8 三个子 Agent trace 文件共 **24 个逻辑调用、25 行记录**，多出一行是 schema 校验补充记录；JSON 解析错误 0、schema 错误 1。JSON 专项保持未解决，不因本轮未复现而关闭。

## 2026-09-11 最终复核与收尾

最终产品提交：`f3179e5e6e32cb4cd6176da967829674c716ebf9`。服务器干净 worktree 为 `/root/autodl-tmp/projects/ResAgent2-runtime-resources-f3179e5`；editable 由 d03abee 指向该提交。证据根 `/root/autodl-tmp/acceptance-answers/` 内含原始 `ACCEPTANCE_REPORT_9.md`、MANIFEST、trace、Session、命令日志与实际文件。原始报告保留，以下复核说明修正其口径，不改写失败现场。

- 本地/服务器基线均为 **805 passed, 1 skipped**，mock_e2e completed；服务器报告后本地合并前再次全量回归，使用隔离 cwd `/tmp/resagent2-resource-merge.jFE9qy`。
- Coding/Experiment 各三阶段：未登记询问 → 仅登记并回答后再次询问 → 目录补齐后同 Session/Attempt 完成。Experiment 未在缺目录时运行依赖数据的命令，最后真实执行得到 `{"value":42.0}`。
- 每个恢复请求只含一个 answers 段，后续工具步骤仍可见实际回答；选择用例中 Coding 读取 helper_b.py、未读取 helper_a.py，Experiment 执行 `python calc.py mul` 得 `{"value":6}`。
- 选择用例同时携带 unrelated_missing，不因无关缺失目录阻塞。Scientific CLI 仍记录 accuracy 并完成，错误字段被拒绝。
- 五份 trace 均为 Flash，权限 0700/0600。共 **32 个逻辑调用、32 次 HTTP 尝试、34 行记录**；2 行是 schema 校验补充记录。JSON 解析错误、客户端重试、Task Attempt 重试均为 0；有限样本不构成永久稳定保证。

三处证据边界：

1. 两次 extra_forbidden 是合法 JSON 的字段层级错误。`choice-coding` 的 result、`sci-smoke` 的 opinion/summary 被放在动作顶层，均由 **AgentLoop 的 runtime_feedback → 新逻辑调用** 纠正，不是客户端 HTTP 重试。原始失败 call_id 分别为 `1d20c9ad926346898fc91531644a108b`、`6fb3e144f4ba42f5ba3f8a04bb490fe7`。
2. 驱动每阶段只传当前回答，故 answers 段不混入上一阶段值；这证明投影只使用调用方输入，不表示系统删除历史。生产 Scheduler 按 Task 过滤已保存回答，可以同时传该 Task 的较早回答。
3. 这轮探针直接调用 Native Agent，验证了实际指标文件和成功命令，没有经过 Scheduler 的 Registry 注册冻结。原验收单和报告的“冻结指标”表述过宽；本轮不据此宣称新增了完整注册冻结的真实验收。该路径未改，原有完整流程证据仍归属于 `577b8489` 那轮。

结论：用户回答上下文修复验收通过，无需追加产品修改或 GPU 重跑。收尾同步当前架构/契约、教程及历史状态；合并身份以 Git 记录为准。JSON 问题继续按 [专项](LLM_JSON_OUTPUT_FOLLOWUP.md) 独立调查，保留 `577b8489`、`d03abee`、`f3179e5` 各轮服务器产物与环境，不顺带清理或重指服务器部署。

## 分阶段验证说明

阶段 3 调度回归：208 passed。ResearchRun 用一个累计 user_wait_seconds 和
remaining_timeout_seconds(now) 统一剩余超时；Controller 结算回答时用系统时钟，
与答案、Task 恢复一次保存。当前开放暂停从 PendingQuestion.created_at 推导。
覆盖跨日等待、重建控制器、多次暂停、伪造 answered_at、重复答案、普通宕机计时。

阶段 2 本地全量：789 passed, 1 skipped。新增检查使用真实目录和 JsonSessionStore，
Scientific / Coding understand / Coding modify / Experiment 各用三个独立进程核对
“未登记 → 登记但缺失且用户确认 → 实际补齐”的上下文及 Session 复用。
模型动作是脚本驱动；不能据此宣称任意真实模型都会选择 ask_user。

DatasetAvailability 是一次目录检查的结果（可用路径、不可用 ID），不是新的管理器或状态机；
三个 Agent 的上下文和脚本环境映射消费同一检查结果，恢复调用重新计算。

## 边界

- 目录路径属于部署配置，不是 ResearchRequest；Run 引用快照不等于实际使用清单。
- 数据集由用户放置并登记；系统不下载、不猜路径、不替换数据集。
- 路径越界、非法目录配置仍报错；目录存在不保证数据内容完整。
- 依赖继续使用已有环境准备、run_setup、audit_env 及 pip/conda 缓存。
- 旧记录原样保留，不迁移、不添加旧 schema 兼容层。
- CLI 与 E2E 保持各自组合根；不自动合并 main 或推送。
