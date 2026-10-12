# 权限反馈、CLI 问答与通用提示词：实施和服务器测试

日期：2026-10-12。分支：`fix/native-agent-receipts`；实施基线：`a60f6b3`；公共 schema 从 25.0 升为 26.0。本轮按[已确认方案](PERMISSION_FEEDBACK_AND_TASK_INSTRUCTIONS_PLAN_2026-10-12.md)实施，尚未合并。当前契约以[CONTRACTS](../../current/CONTRACTS.md)、[CONTEXT](../../current/CONTEXT.md)及实际冻结源码为准。

**验证状态：本地验证已执行完毕；包含崩溃恢复补全的最终全量回归、受影响复测、mock 与 diff 检查通过。** pip check 保留既有环境缺项，服务器真实模型与 L3 尚未执行。下文分开记录开发结果与待执行步骤，不是服务器验收通过声明。旧版本的测试总数和科研评分不能作为本版结果。

## 1. 先确认本轮改变了什么

| 范围 | 本轮行为 | 应取得的证据 |
| --- | --- | --- |
| 回答与持久化 | `UserAnswer.comment` 可选，`RecordedAnswer` 保存原文；业务问题不得提交独立 comment | 原题、提交答案、落盘回答、磁盘恢复后原文一致 |
| 操作反馈 | 匹配答案经权限决定进入 `ToolObservation.user_comment`，原工具回执顶层含 `user_comment` | 一个原生调用只有一个回执；拒绝未执行，批准说明随实际结果保留 |
| 上下文归属 | 业务自由回答留在第二部分；操作决定及说明只由第三部分反馈，不产生工件 | 实际模型 messages 分区与授权目录，不能只查外围审计日志 |
| CLI | `answer --comment`；待答时 shell `/answer` 提供选项和自由输入入口 | 交互与非交互都沿同一 Controller 回答链路 |
| 通用提示词 | Scientific 明确表达，Compiler 忠实细化；执行遵守任务边界，交接区分事实与建议 | 实际生成的 WorkRequest、Task、子 Agent 输入、工具行动及交付物 |

决定和说明分开；说明不扩权，不替代被批准操作，不产生继续/停止控制字段。保留单次准确批准、作用域、环境、工作区和恢复校验。WorkRequest 仍是 `objective + expected_evidence + constraints + input_artifact_ids`，不增加显式 Task 列表或代码切分阶段；不将整个请求拼给每个子 Agent。

## 2. 冻结代码与预检

记录分支、完整 SHA、工作区状态、模型配置、预算、操作权限、数据根和部署入口。产品使用既有服务器检出，测试期间不拉取、切分支或改源码；证据放在 `/root/autodl-tmp/resagent2/runs/<本轮目录>/`，不写入产品仓库。GitHub 不可达时沿既有 Git bundle 流程同步，再核对完整 SHA，不靠口头版本号。

按项目声明同步依赖，独立核对 9 个 editable 包的 import 都落在该冻结源码；`SCHEMA_VERSION` 与 `AgentState.schema_version` 均为 26.0。CLI 与真实 E2E 仍共用 `build_application`，不另装一套模型、工具或权限流程。

旧 schema 25.0 Run 的 show、answer、resume 和旧 Session 恢复必须明确拒绝；在旧证据副本或隔离目录核对，保留测试前后 hash，不修改旧原件。新提示词改变固定契约指纹，不能静默注入旧 L3 Session；本轮全部新建 Run。

测试前查当前环境创建、缓存、授权工作区 Git 根与数据可读性。基础设施错误单独记录，未通过预检先修准备，不以正式模型预算反复试错。不得擅自清理共享缓存、数据集或旧验收记录。

## 3. 离线回归与确定性专项

在仓库根目录、项目环境分别执行，保存每条完整输出与真实退出码：

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

mock 应 completed，并核对唯一最终意见、报告、实际任务交付物及无旧控制工件；不要以历史工件数量替代这些检查。服务器 `pip check` 独立执行，本地缺依赖和服务器 clean 是不同结论，均按实际记录。

重点读本版新增及受影响用例，包括 `tests/contracts/test_answer_fields.py`、`tests/components/test_operation_permissions.py`、`tests/components/test_request_task_context.py`、`tests/runtime/test_native_tool_calls.py`、`tests/orchestrator/test_controller.py`、`tests/orchestrator/test_compiler.py`、`tests/e2e/test_command_approval_resume.py`、`tests/e2e/test_approval_task_context.py`，以及 `apps/cli/tests` 的命令与 shell 测试。以下性质必须由实际断言证明，不靠提示词关键词存在：

1. 说明的 Unicode、换行和首尾空白按原文保存；缺省说明仍可正常批准或拒绝。业务问题附 comment 被拒，自由字符串仍可作为原字段答案。
2. 拒绝操作没有副作用，原调用回执包含未执行事实与说明；批准操作实际成功或失败时，原业务 value 保持原形并带说明。标量/列表 value 不能因说明而丢失或被改成另一种业务返回。批准后派发期间期限中断也保留说明和唯一回执，以 `execution_status=unknown` 保守记录可能部分执行，不能据它宣称原操作没有副作用。
3. 回答从真实 Store 落盘、恢复后仍交给相同 Run/Task/Attempt/Session 和原 call；不增加说明专用 tool 回执。批准后的说明与 executing_call_id 一起持久保存，执行中崩溃恢复也附回原调用的未知回执，保存回执后清除临时说明；不重放操作或重新授权。错误作用域、变化快照、已消费批准及新命令不能复用许可或旧说明。
4. 操作说明不进入第二部分或第四部分，不生成问答/说明工件；业务原题、选项和自由答案完整保留在第二部分。
5. CLI 可提交多字段自由答案和准确批准/拒绝说明；交互入口展示实际原题、字段、选项及自行输入方式，中断输入不伪造答案。
6. schema 26.0 与实际装配一致，旧版本明确拒绝；无额外任务接口、分段猜测逻辑、专用业务禁令或权限绕过。

### 本地开发结果（2026-10-12）

| 检查 | 实际结果 |
| --- | --- |
| 最终全量 pytest | 2168 passed / 1 skipped，61.11 秒，退出码 0；包含执行中崩溃的说明恢复补全 |
| 初轮受影响专项 | 545 passed，退出码 0；检查契约、权限、恢复、原生回执、CLI 和既有提示词相关测试；早于崩溃恢复补全 |
| 补全后受影响复测 | 145 passed，5.92 秒，退出码 0；覆盖崩溃、权限、原生回执及恢复路径 |
| 最终 mock E2E | `run_golden completed`，8 工件，Coding / Experiment 各一次，退出码 0 |
| pip check | 退出码 1：既有 pdfminer-six 缺少 cryptography；未宣称本地依赖检查 clean |
| diff check | 无输出，退出码 0 |
| 最终文档检查 | 9 份 UTF-8 文档及 236 个本地链接目标通过 |
| 源码与 schema | 9 个包的实际 import 全部指向 `/home/cyl/ResAgent2` 源码；SCHEMA_VERSION / AgentState 均为 26.0 |

首次全量为 1 failed / 2167 passed / 1 skipped，失败来自工具面指纹基准仍保存 schema 25。独立核对 22 个工具 schema：将本次版本标记 26.0 归一化为原 25.0 后，与原基准完全相同；实际只改变四个受版本影响的 fingerprint。确认没有无意改变工具参数后更新相应 fixture，完整重跑为 2168 passed / 1 skipped（62.36 秒）。随后补齐下述崩溃恢复边界，再执行受影响复测、完整回归及 mock，得到上表最终结果。首次失败与各次完整重跑均保留，不写成首轮全绿，也不移除指纹校验。

本地没有运行付费真实模型、服务器场景或 L3；开发通过证明本轮已覆盖的数据与控制边界，不提前证明提示词改善模型行为。

收尾只读审查发现，批准后若操作中崩溃，局部权限决定中的说明可能无法进入恢复回执。修复沿已有 executing_call_id 保存点同步保存 `executing_user_comment`，原回执保存后清除；未知恢复仅携带原文，不重放或扩权。扩展已有崩溃用例，不新建控制流程或增加测试总数；补齐后受影响复测、完整回归及 mock 均已通过，未用修复前通过结果代替最终验证。

新增或扩展的关键用例定位如下。它们证明数据、执行及恢复边界，不证明模型必然遵循提示词：

| 性质 | 用例位置 |
| --- | --- |
| 业务问题不接受独立 comment | `tests/contracts/test_answer_fields.py::test_business_feedback_must_use_answer_values_instead_of_operation_comment` |
| 无效业务 comment 不改已落盘状态、不恢复模型 | `tests/orchestrator/test_controller.py::test_business_operation_comment_is_rejected_before_persisting_or_resuming` |
| 任意 JSON value 原形进入实际原生消息 | `tests/runtime/test_native_tool_calls.py::test_user_comment_preserves_arbitrary_tool_value_in_native_model_input` |
| yes/no、无说明/空说明/原文说明，唯一回执及第二/第四部分隔离 | `tests/e2e/test_command_approval_resume.py::test_operation_comment_reaches_model_once_without_becoming_task_requirements` |
| 批准后环境核验失败，说明仍交到原回执 | `tests/e2e/test_command_approval_resume.py::test_failed_audit_after_approval_blocks_actual_command` |
| 批准后期限中断，说明及 unknown 状态唯一保存 | `tests/e2e/test_command_approval_resume.py::test_deadline_expiring_during_audit_prevents_command` |
| 环境变化重新批准，旧说明不进入新动作结果 | `tests/components/test_operation_permissions.py::test_shell_approval_does_not_follow_a_changed_environment_prefix` |
| 批准消费后执行中崩溃，原说明随唯一未知回执恢复、不重放 | `tests/components/test_operation_permissions.py::test_consumed_approval_is_durable_before_effect_and_not_replayed_after_crash` |
| CLI 磁盘原题及独立说明提交 | `apps/cli/tests/test_cli.py::test_operation_answer_loads_disk_snapshot_and_passes_separate_comment` |
| 自由字段答案保留空白及换行 | `apps/cli/tests/test_shell_parser.py::test_business_field_preserves_whitespace_and_line_breaks` |
| shell 多字段自由回答和操作批准交互 | `apps/cli/tests/test_shell_reuse.py::test_shell_prompt_accepts_free_text_for_each_business_field`、`test_shell_operation_prompt_keeps_decision_separate` |

## 4. 真实权限说明与业务自由回答

通过现有 CLI 创建独立短 Run，使用一份授权的临时工作区和有限预算，启用 full trace。题目应需要一个确定的实现或操作，并含清楚、通用的范围/顺序要求；不向模型预置本计划、评分表或旧 L3 答案。不新增测试框架或模拟用户 Agent。

若发生业务提问，从 show 读取实际字段和选项，在原字段提交一个与选项不同但与题目相符的自由答案。不得改机器键，不要求自然运行必须恰好提问；未触发的路径如实标“确定性测试覆盖，真实未触发”。操作批准以 action 快照判定，不能仅凭字段名 approve 认定。

操作说明提交示例（Run ID、字段及内容按当前原题替换）：

```bash
resagent2 answer ACTUAL_RUN_ID --data-root ACTUAL_DATA_ROOT \
  --field 'approve=no' \
  --comment '这条操作不符合原任务的范围要求；请依据既有目标和边界重新选择下一步。'
```

说明必须对应实际拒绝理由；若候选符合范围，可以主动拒绝该次操作以检验反馈，并在报告注明人为场景，不把它说成自然发现的缺陷。拒绝说明不替模型给出算法、代码补丁或扩大授权。另覆盖批准附说明：仅批准明确授权范围内的准确操作，核对正常结果保留该说明，不把说明当自动批准后续操作。

逐项核对：用户原文在 RecordedAnswer、原工具回执顶层 `user_comment` 及下一次实际模型输入中一致；被拒操作没有执行；每个 native call 恰好一个配对回执；第二部分和目录没有操作决定或说明；预算未重置，Session/Attempt 未替换。若模型改用另一条命令，它仍需按既有权限检查。CLI 交互 `/answer` 入口另做人工或现有 CLI 测试验证，业务选项和自由文本两种方式均可用。

权限链路可判定通过，而模型仍可能误解说明或提出不合适的新动作。两种结论分别报告，保留重复请求和失败，不能仅因收到 comment 就宣称行为问题解决。

## 5. 提示词与整条指令链观察

本轮先沿现有入口观察代表性任务：仅分析已有材料；实现某入口但不要求现在执行完整业务；工作带有明确前提、先后关系或阶段限制；上游交接包含未执行建议。场景可以结合，不为每句提示词创建一套测试基础设施。

保存并对照用户需求 → Scientific WorkRequest → Compiler 实际输入与输出 → 持久 Task.instruction → 子 Agent 的实际模型输入 → 工具行动及报告。关注：

- Scientific 是否准确区分用户要求与自己的科学选择，是否写清动作、条件和范围，objective 与 constraints 是否一致。
- Compiler 是否保留上游明确要求、逻辑输出名、数值、顺序与限制，是否只补必要实施说明；编号步骤不等于必须逐条生成 Task。
- 子 Agent 是否根据真实入口行为选择检查，是否把“实现能力”误当成“立即执行”，是否无必要增加预检或完整运行。
- 拒绝后的行动是否使用实际反馈；交接是否区分已观察结果、被拒操作、未执行建议和未完成项；下游建议是否替代了当前任务要求。

按实际产物和行动判断，不以指令更长、更多禁止句或关键词覆盖作为质量指标。真实场景未出现某行为时如实说明，不为覆盖率强行干预正式 Run。提示词链路忠实与模型实际遵循分开核对，固定代码不能保证语义逐字保留。

## 6. 最后独立运行 L3

使用相同冻结提交，在结构和短真实场景检查后，沿[现行 L3 规程](../../guides/L3_RESEARCH_TEST.md)新建 Run。题目、中性基线、固定切分、资源、预算和评分规则不因本轮改动改变；不得复制旧 Run 的候选代码、checkpoint 或报告。测试方的权限回答仅依用户已授权范围，额外费用、资源或范围不明时保持 paused 请求实际决定。

每条批准/拒绝记录完整操作、工作目录、绑定环境、原题、实际答案及说明；如使用外部闸门，遵守既有审查范围并记录其启动、退出及每次决定，不能在产品外静默自动批准，也不能把外围 reason 日志当作产品 comment。

完成后冻结证据，再独立从逐样本概率复算指标、重拟合参数，核对 checkpoint、切分、freeze 与最终评估时序，并进行独立前向重放。重放先移除分析目录中的旧预测，导出真实数据集根，记录实际执行退出码，再对比新输出，防止比较复制原件的假阳性。依原规程分别报告运行/交付链、研究质量、完成类别和人工介入；不将 completed 等同科研全正确。

与 `5d28f74` 单次旧 L3 的比较只作描述：模型、输入、环境及预算差异要列出，模型随机性不能靠一次成功排除。需要主张提示词带来质量提升时，应另做条件对齐的对照或重复运行；本轮不新建评测平台、不重跑至 PASS，也不承诺满分。

## 7. 证据和收尾

沿现有布局保存 frozen SHA、配置、日志、protocol 报告、独立复算脚本及结果；MANIFEST 对公共证据逐一 hash。full trace、Session 和可能含原文的用户说明按既有私有边界保留，不复制到产品文档或外发。首次失败、环境修复、测试脚本错误及作废复算也留存。

报告明确哪些由确定性测试证明、哪些由真实模型自然触发、哪些仍未覆盖。每条命令独立记录退出码；测试方不改产品、不扩大预算、不编辑冻结交付物，不合并或推送。结尾核对产品 SHA、git status、无残留进程和磁盘余量，再将实际摘要交回开发者。
