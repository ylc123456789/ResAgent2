# Unified Agent IO V2：服务器复测独立验收

日期：2026-09-21。开发分支：`refactor/unified-agent-entry`。

测试提交：`22347c511f9daaea5197f8d7646856a7bbd2d914`（schema 12.0）。
证据目录：`/root/autodl-tmp/unified-io-v2-retest-8PHUoh`。
验收依据：[复测交接](UNIFIED_AGENT_IO_V2_RETEST_2026-09-21.md)。

## 结论

产品修复和主要真实流程已验证。repair 的自动谓词仍为 FAIL，但按交接允许的复杂包装命令人工复核规则，实际失败、修复和重跑链路通过。保留原始 FAIL，不改运行记录或放宽谓词。

本轮开发验收通过：既有真实两命令确认流程与新增确定性入口拒绝补测共同覆盖最后一项验证。原真实测试的旧答案拒绝步骤确实未保存原始材料，该历史事实保留；新测试明确标为新测试，不冒充原轮次补录。repair 继续区分“自动谓词 FAIL、人工复核通过”，不宣称所有自动检查均为绿色。

本次仅补充验收文档，未改产品、验收器、服务器证据或镜像配置；未调用模型、重跑 GPU、合并或推送。

## 独立核对结果

| 项目 | 独立结论 | 原始证据 |
| --- | --- | --- |
| 版本与安装 | 通过 | product HEAD 等于测试 SHA，工作树干净；9 包导入路径属于本轮 product，pip check 干净 |
| 确定性回归 | 通过 | logs/pytest.txt：1071 passed、1 skipped；跳过项为显式启用的真实网络文献 smoke |
| repair | 人工复核通过，自动谓词仍失败 | 首个 Experiment Task/Attempt 为 failed，训练 exit 1；Coding 仅修正 totla→total；新 Experiment 同命令 exit 0，冻结 accuracy=0.8 |
| 完整 CUDA 流程 | 通过 | python train.py --epochs 1 --seed 0，exit 0；冻结 baseline_accuracy=0.4545、candidate_accuracy=0.521、epochs=1、seed=0、device=cuda |
| Probe 1/2/3 | 通过 | Coding 两组文件树哈希不变、edit_revision=0、无输出工件；Experiment 正确分析 0.70→0.72，command_count=0 |
| Probe 5 两个命令分别确认 | 通过 | 同一 Task/Attempt/Session；first 硬门事件16→执行22，second 硬门24→执行28；两份独立批准及两条成功执行记录匹配，marker 内容正确 |
| Probe 5 拒绝旧答案 | 通过（新增确定性入口验证） | 真实 answer_question 拒绝陈旧 question_id，持久 Run 全文件字节不变；Session 未触达由入口控制流确认，详见补测范围 |

repair 的原始命令为诊断包装：`python -c "…sys.exit(subprocess.call([sys.executable,'train.py']))"`。stderr 包含 train.py 第7行 totla 的真实 NameError，重跑相同包装成功。`_training_commands` 只识别直接 Python 运行 train.py，故自动谓词漏识别。人工验收依据是完整执行记录、单行补丁及冻结指标，不能推广为接受任意包装命令。0.8 为夹具算术 8/10，不是 CIFAR-10 实测准确率。

CUDA 场景的 train.py diff 仅改变 SE 实现及注释；既有 CIFAR-10 数据读取、两组完整训练及测试集评估未改变。冻结源码与工作区源码 SHA256 一致：`86df656df6a8c619ed3d3e0058cad1d6ce6508ef3ef11491732ee17e69bf2c26`。这是约定的单 epoch、单 seed 验证，不代表充分训练或普遍科学结论。

repair 的21份及 CUDA 场景的18份登记工件均存在且 SHA256 匹配；关键补丁、执行记录和指标被 Scientific 实际观察并引用。

## 原真实测试的命令确认材料偏差

- 交接要求 max_tasks=1、max_attempts=1，实际脚本为2/2；实际仍只用1个 Task、1个 Attempt，无重试，不影响已观察到的同一会话恢复结论，但应如实披露配置偏差。
- `probe5_answer.json` 被各次答题覆盖，只剩最终状态，未按交接保留每次暂停摘要。不可变 question/answer 工件及 Session 事件足以复核两个命令的独立硬门。
- 未找到报告声称的独立 reject 脚本、拒绝输出或 `probe5_reject.json`。现存 probe5_confirm.py 的 reject 模式选取最初软问 confirm_run，并非第一个硬门批准题；只比较 Run.updated_at 和答案数量，不能支持完整 Run/Session/pending 不变的断言。
- 现有确定性回归包含 `test_successive_questions_in_one_attempt_reject_the_previous_answer` 和 `test_confirmation_only_authorizes_the_paired_command`。这些测试通过可支持相应产品行为，但不能代替声称已执行的真实探针原始记录。

## 当时的最小补证交接（已由下述补测关闭）

优先补交当时实际执行的独立 reject 脚本、完整原始输出及拒绝前后状态材料，确认重交对象为第一个硬门批准题。不能事后重建文件并称为当时记录。

若原始材料不存在，报告应明确该子项未留证。可另做隔离的确定性补测：让同一 Task/Attempt/Session 进入第二条命令的硬门，重交第一条硬门批准答案；断言 OrchestrationError，并比较持久 Run、Session 的完整内容或 SHA256（包括 pending、answers、预算及命令记录）前后一致，第二 marker 仍不存在。随后批准第二题并验证命令成功。使用1 Task/1 Attempt预算，逐阶段单独保存日志和快照，标明“新确定性补测”，不得称为原真实模型轮次的补录。

无需为该材料缺口重新安装依赖、调用真实模型或重复 CUDA 训练。原始证据目录和 FAIL 记录保持不变。

## 新增确定性补测与关闭结论

2026-09-21 补交，针对同一产品提交 `22347c511f9daaea5197f8d7646856a7bbd2d914`。独立核对服务器 product HEAD 与干净工作树、脚本、traceback、前后摘要及结果；未重跑补测或调用模型。证据位于上述证据根目录：

- 脚本：`probes/probe5_reject_deterministic.py`。
- 结果：`probes/p5_reject_deterministic/result.json`，`is_new_test=true`、PASS、model_calls=0、gpu_used=false、reinstall=false。
- 拒绝输出：同子目录的 `reject_output.txt`；前后摘要：`before_snapshot.json`、`after_snapshot.json`；最终持久 Run：`state/run_reject_det.json`。
- result.json SHA256：`eb3d55a1e49b50f1242632219810da525d0c4c1e2bdc7d5941d0db3b7be8a2b1`；两个摘要 SHA256 均为 `eaa5cb3335cc1cd954a0a4687e22101ced72bbd4009e6f2de4830d2e6a79b066`。

脚本构造 Q3（second 硬门）pending、Q1/Q2 已答的 paused Run，经真实公开 `ResearchController.answer_question` 重交 Q2。原始 traceback 定位到 controller.py:74 → scheduler.py:29，消息为 `answer does not match pending question`。脚本比较拒绝前后完整 Run 文件字节，结果相同；摘要中的 updated_at、2条答案、pending Q3、paused 状态也相同。未 monkeypatch 产品或改写校验。

范围必须准确：

1. `session_store_byte_identical` 比较的是手工创建且未绑定产品 Session 的 `sessions/session_probe_seed.json`，不能称为真实 Session 持久文件的实测快照。Session 未受影响的依据是产品入口的控制流：JsonRunStore.load 只读，随后立刻校验；异常先于答案工件登记、Run 修改、task 恢复和 agent 调用。
2. 本补测未建立真实 workflow/Task/Attempt；正向对照仅直接调用 `_validate_answer`，没有通过公开入口恢复当前答案。脚本预算仍为2/2，但此次不调度任何任务，不构成1/1任务预算测试。真实 Task/Attempt/Session 的连续批准与命令执行由先前 Probe5 原始事件和工件证明。
3. 不把上述两项证据合称为一次完整的新端到端测试。组合结论为“既有真实两命令确认 E2E + 新增确定性入口拒绝验证”，足以关闭剩余验证事项，无需第三轮补测。

原 REPORT.md / results.json 及旧失败记录保持原样；本文件作为开发验收补充索引，原84次模型调用计量不增加。验收关闭不等于合并，当前分支继续保留，未合并或推送。

## 计量

84行 trace 均带 model、call_id 唯一、retry=0，HTTP尝试数=84，无 schema 补充行。

| 组 | 逻辑调用/HTTP | 持久化对账 |
| --- | --- | --- |
| repair | 31/31 | Run.llm_calls_used=31 |
| code-experiment | 27/27 | Run.llm_calls_used=27 |
| probes | 26/26 | Probe5 Run=19（Experiment14+Scientific5）；Probe1/2/3直接调用合计7 |

本轮无中断轮次。上一轮计数、GPU smoke、命令确认范围及阿里云下载情况的补正保留在测试方 REPORT.md 第7节，不覆盖旧报告。
