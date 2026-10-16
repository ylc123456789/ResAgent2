# 操作批准与业务问答分离：实现、验证及工件接入复核

日期：2026-10-11。分支：`fix/native-agent-receipts`；修改基线：`692e492dc36def161fd904a90ef09bbfafaacd68`。schema 保持 24.0。

## 1. 本次修改解决什么

此前 `request_task_context` 将当前作用域的所有回答都投影为累计 `user_answers`。这把“同意运行一条命令”也当成了研究/执行任务的长期补充。用户授权先修正这一点；工件体系及权限策略的进一步调整仍需讨论。

现在先按原有规则校验答案的作用域、冻结原件和结构，再根据 `RecordedAnswer.action` 分类：

- `action=None`：业务问答，继续进入上下文第二部分的累计任务问答。
- `action` 为准确操作快照：操作批准或拒绝，不进入第二部分；仍完整持久保存，由待执行动作和权限策略处理。

不通过 `approve` 字段名、回答文字或工具名猜测类别。普通业务问题即使使用 `approve=yes` 也不会被误当成执行许可；真正操作批准无论同意或拒绝，都不会变成任务需求。

共用函数供 Scientific、Coding、Experiment 使用，没有新增模式、schema 字段、兼容层或另一套回答链。CLI 的实时展示、最终汇总和 `show` 按同一依据显示 `User question` / `Operation approval`，待答状态带 `Pending`。两类问题继续使用同一 `answer` 入口。

批准仍可在授权目录中导航、按原 ID 读取；原生历史仍保留“操作尚未执行”的确认回执。用户回答本身不执行命令：恢复后模型重交准确动作，运行时匹配作用域、工具参数及实际目录/环境/目标，再按既有规则消费一次批准。此次没有减少确认次数、改变授权范围或修改权限匹配策略，也不承诺跨崩溃的 exactly-once 执行。

当前四份规范和 CLI、Runtime、Agents、Scientific README 已同步。

## 2. 当前上下文实际怎样构造

三个 Agent 共用四部分的逻辑框架，但不是四条独立 API 消息：

| 逻辑部分 | 当前内容 | 更新方式 |
|---|---|---|
| 固定契约 | 职责、系统规则、实际工具说明/schema | 每次提供；不由工具正文改写 |
| 任务需求 | 本次 instruction 与同一作用域的累计业务问答 | 每轮从授权回答重建；本次排除操作批准 |
| 原生协议历史 | 配对的 assistant/tool 调用及回执 | Session 保留原事件；近期完整回合续传，必要时压缩较早完整回合 |
| 当前任务上下文与完整目录 | 当前要求、状态、反馈、已读取片段及 artifact_index | 每轮从当前事实重建；正文有界选择，完整目录为必需内容 |

实际请求是固定协议 `system`、近期配对历史、最后一条重建 `user`，工具 schema 单独放在 `tools` 中。Agent 职责提示位于重建文本内名为 `system` 的领域段；任务、当前状态、目录和已有历史摘要也在这条 `user` 中。旧轮次的完整领域 prompt 不反复累加。原生历史压缩不删除原事件，不裁掉工件目录；完整必需内容装不下时明确超限失败。

`read_file` / `read_artifact` 的内容既作为原生回执保存，又可能作为第四部分的当前正文工作集呈现。因此仍可能重复占用模型容量。两份都计入预算，本次没有优化这处重复。

## 3. 工件、ResearchIndex 与 artifact_index 的不同职责

| 对象 | 当前职责 | 不代表什么 |
|---|---|---|
| 冻结文件与 `Run.artifacts` | 按工件 ID 保存原件、Ref、来源、URI、hash 及归属；登记表是身份权威 | 不等于模型应逐轮读取全部正文 |
| `ResearchIndex` | Controller 构造并冻结的研究材料分组快照：初始输入、Scientific 材料及每次 WorkRequest | 目前不是某一次 WorkRequest 的独立结果索引 |
| 上下文 `artifact_index` | 当前 Agent 的完整授权输入目录并上 Session 已记录工具产物条目；条目详情出现一次，分组只引用 ID | 不暴露整个 Run，也不授予新读取权限 |

文件保存位置仍为 `<artifact_root>/<run_id>/<artifact_id>/<filename>`。`ArtifactCandidate` 是登记前的提交对象，不是“模型已看过且认定有价值”的状态。搜索/fetch 按现有契约冻结并登记；read 不创建新的正文工件。

目录合并已经有共用 `merge_artifact_index`：按真实工件 ID 去重，授权输入先列出，再追加 Session 中尚未出现的条目。输入条目在重建目录中优先；Scientific 分组由当前 `ResearchIndex` 给出，仅列 `artifact_ids`，实际尝试状态补到对应目录条目。直接来源 ID 表示材料关系，没有用嵌套树代替底层登记表。

Scientific 连续直接调用工具时，新材料先进入 Session 的平铺目录。Controller 在控制权返回后刷新 ResearchIndex；它的分组快照不是每次直接工具调用后即时更新。因此“完整条目即时可见”和“分组快照已更新”是两件事。

## 4. 工具结果接入：已经做到与尚有偏差

直接文献/网页工具的路径：

```text
调用工具 → 冻结登记 → ToolObservation.value → 对应原生 tool 回执
                   → memory_updates.artifact_index → Session 目录
下一轮 → 授权输入 ∪ Session 目录 → 完整 artifact_index
```

这些工具已经共用目录合并函数，但仍由各工具自行准备 `memory_updates`。没有统一的“直接内容 + 本次索引增量”回执契约，也没有按单次调用统一维护所有工具的结果分组。内部 memory/control 字段不会直接作为模型回执发送。

`request_work` 的路径仍不同：

```text
request_work → 原生回执：已请求，尚未执行
Controller → Compiler / 执行任务 → WorkRecord + ResearchIndex + WorkFeedback
Scientific 恢复 → 第四部分呈现本轮工作事实框/报告，完整目录纳入交付物
```

最终 WorkFeedback 没有作为最终工作结果统一进入原生工具历史；`WorkFeedback.index_artifact_id` 指向包含全部研究材料的 ResearchIndex 快照，而非仅该次工作的结果目录。

这说明上一轮统一上下文改动落实了四部分、唯一完整目录和共用 merge，但没有完整落实用户此前提出的“所有工具直接返回内容进入第三部分，返回的索引统一合入第四部分”。这是实施与原意的偏差，不是用户本轮才新增的要求。此前测试证明的是现有实现按其断言可运行，不能代替对设计意图的核对。

下一轮需先确认统一工具结果如何表达、WorkRequest 最终结果如何接回、全局目录和单次工作/工具目录如何命名及组织。本次未重命名 ResearchIndex、修改 WorkRequest 协议、增加目录树或删除底层登记表。

## 5. 本地验证

环境：Ubuntu-D，`/home/cyl/ResAgent2`，既有 ResAgent2 conda 环境。未安装依赖，未执行付费模型、联网材料获取、GPU 或 L3。

| 检查 | 结果 |
|---|---|
| 任务需求与权限策略定向 | 34 passed，0.66s |
| CLI 分类与现有入口定向 | 63 passed |
| 两个新增 Coding/Experiment 完整暂停恢复场景 | 2 passed，0.76s |
| 9 个恢复、CLI、任务需求相关文件 | 149 passed，12.40s |
| 调整后的递归删除恢复文件 | 3 passed，2.21s |
| `python -m pytest tests apps/cli/tests -q` | 2214 passed / 1 skipped，59.74s |
| `python -m e2e.mock_e2e` | `run_golden completed`，13 工件，Coding/Experiment 各一次 |
| `python -m pip check` | rc=1：既有 `pdfminer-six 20260107 requires cryptography, which is not installed` |
| `git diff --check` | 通过 |

第一次完整回归为 2213 passed / 1 skipped / 1 failed，59.33s：递归删除恢复测试仍断言批准值自动出现在重建任务文本，符合旧行为却与本次目标冲突。调整为断言任务 user_answers 为空、目录含批准入口、授权读取原件可精确还原完整批准；实际删除、保留其他文件、同 Session 恢复和单次执行断言保留。该失败及调整不隐去；最终完整复跑结果见上表。

新增 19 个用例（9 个 Components、8 个 CLI、2 个 E2E），相对 2195 passed 基线预期增加至 2214。Components 覆盖三个 Agent × 同意/拒绝、普通 approve 字段及批准工件损坏/作用域错误；新 E2E 走真实 Controller、Scheduler、AgentLoop 和磁盘 Session，模型响应为脚本替身。普通 `approve=yes` 不授权命令；真实批准后执行本地 Python marker 一次，并核对持久两类答案、实际模型输入、配对历史与共用调用账本。环境发现/审计用测试替身，无科研质量结论。

## 6. 服务器怎样独立核对

冻结本次最终分支提交，用声明安装的环境核对 editable 指针、schema 24.0 和工作区，分别保留输出与退出码：

```bash
python -m pytest tests/components/test_request_task_context.py tests/components/test_operation_permissions.py tests/coding/test_delete_invoke.py tests/e2e/test_approval_task_context.py apps/cli/tests/test_cli.py apps/cli/tests/test_shell_render.py -q
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

六个定向文件预期 102 passed；全量预期 2214 passed / 1 skipped；mock completed / 13 工件。pip check 以服务器实际结果为准，不能把既有服务器 clean 代替本次本地缺项。

这次改动的判断点是：业务问答累计、批准/拒绝不进第二部分、两类原件及导航仍在、普通 yes 不授予操作权、准确批准可以恢复并消费、原生调用与回执完整配对、CLI 类别正确。确定性生产链已覆盖这些点。若需要观察真实模型行为，可追加一个全新证据目录的短问答/命令批准任务，并如实记录模型是否自然走到目标边界；无需为这次局部投影修改重跑科研 L3。
