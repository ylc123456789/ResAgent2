# 完整科研目录与问答阅读：服务器验收计划

日期：2026-09-24。分支：`fix/code-health`，schema **16.0**。状态：待验证；本文不表示测试通过。产品基线：`4fbac1fdb047416ee6c805a31f108cb36ff04932`；测试 HEAD 应包含该提交，后续仅文档提交可直接使用。实际测试提交、结果和证据在执行后另行填写，不沿用 schema 15 的通过总数。

本地预检已完成：`python -m pytest tests apps/cli/tests -q --tb=short` 为 **1280 passed / 1 skipped**；`python -m e2e.mock_e2e` 为 `run_golden completed`、13 个工件；`git diff --check` 通过。公开 CLI 两轮确定性用例实际读取旧轮批准问答和本轮指标问答，Interpreter 保持每轮一次。工具指纹更新前已验证：将 schema 常量 16.0 还原为 15.0 后全部指纹与原基线一致，只有四项内嵌版本常量变动，工具参数与说明没有变化。这些是本地确定性结果，不代替下述真实服务器验收。

## 1. 本轮范围

验证 WorkRequest 交接提供“本轮带引用简报 + 更新后的完整科研目录正文”，以及子任务完整问答的索引、引用和原件读取。登记表仍为唯一来源，Scientific 不额外收到平铺登记表，其他上下文和调用流程保持原样。

不重做 validation，不修改提问路由、操作批准和恢复规则。本轮没有新增依赖、环境或执行能力变化；复用现有模型配置和环境，无需 GPU 或重新安装 PyTorch。此前 GPU 通过记录属于原提交，不作为本轮新版已测证据。

## 2. 固定产品与证据位置

只使用服务器主仓库 `/root/autodl-tmp/projects/ResAgent2`。检查受控文件干净后获取分支；测试开始后不拉取、不切分支，不新建产品副本。测试方不修改产品、不放宽断言、不合并或推送。

```bash
cd /root/autodl-tmp/projects/ResAgent2
git status --short
git fetch origin
git switch fix/code-health
git pull --ff-only
conda activate ResAgent2
export PYTHONNOUSERSITE=1
export TEST_EVIDENCE_DIR=$(mktemp -d /root/autodl-tmp/resagent2/runs/complete-index-qa-20260924-XXXXXX)
mkdir -p "$TEST_EVIDENCE_DIR/logs" "$TEST_EVIDENCE_DIR/cases" "$TEST_EVIDENCE_DIR/probes/scripts"
git merge-base --is-ancestor 4fbac1fdb047416ee6c805a31f108cb36ff04932 HEAD
git rev-parse HEAD > "$TEST_EVIDENCE_DIR/TEST_COMMIT"
git status --short > "$TEST_EVIDENCE_DIR/git-status-before.txt"
```

受控文件有改动时先记录和核对，不自动 reset/clean。核对 contracts 的 SCHEMA_VERSION 为 16.0，九个包导入路径指向主仓库，`python -m pip check` 通过。导入已正确就不重装；有缺项先记录实际缺项。旧 Run 不迁移或续跑，新测试使用独立 Run ID、状态和 trace。模型、endpoint 与显式预算落盘，不输出密钥。

## 3. 确定性回归与 mock

```bash
python -m pytest tests apps/cli/tests -q --tb=short
python -m e2e.mock_e2e
git diff --check
```

保存原始输出和退出码；要求回归、mock 完成且无受控产品修改。重点核对专项覆盖：第二轮完整目录含第一轮条目、成对问答的归属和可读性、简报引用闭环、损坏/越界材料拒绝、旧 index_changes 字段不再接受、问答恢复及批准作用域保持。不能只核对测试总数；此阶段失败先停止付费测试。

## 4. 真实模型：两轮工作交接

通过真实公开入口创建小型只读分析任务：第一轮分析已有 baseline/candidate 指标，第二轮分析另一份稳定性材料，最后结合两轮原件判断。材料使用已知简单数值，例如第一轮 0.45、0.52，第二轮提供多个种子的结果和明确元数据。不伪造实验执行，不需要命令或 GPU。给出明确的两轮研究目标，让 Scientific 自己 request_work，Compiler 和 Interpreter 均使用真实模型。

沿用现有配置，建议每个公开 Run 明确上限 80 次模型请求、1800 秒、4 个任务、每任务 2 次尝试。启用 full trace，每次重跑使用新 Run 和证据目录；若实际需求超出预算，保留该轮并先说明原因，不临时扩额掩盖失败。

逐项保存并验证：

- 两轮 WorkRequest、work_record、work_feedback、research_index 快照一一对应。
- 第二轮 Scientific 实际模型请求中的 research_materials 是完整索引正文，含第一轮与第二轮条目；不含 index_changes，也不另加完整平铺 input_artifacts。
- 第二轮简报只解释第二轮工作，引用可以指向必要的已有输入，但不能把第一轮工作重写成第二轮新成果。每条非空说明有引用。
- 展开的索引与该次引用的冻结目录一致。所有索引 ID 都能在 Run.artifacts 找到相同登记原件，属于本 Run，sha256 正确，并在该次 Scientific 的读取范围中。简报每个引用都在索引中且可读。
- Scientific 在第二轮交接后实际通过 read_artifact 读取至少一份第一轮原件和一份本轮原件。目录展示及 Interpreter 阅读不能冒充这两次原证据阅读。
- 最终 Run 正确完成，opinion 与 final_report 保留原证据 ID；不得重注册输入数据冒充新产物。

如果自然发生提问，使用保存的问题和 requested_fields 回答，保存每次暂停及回答前后快照。Scientific 自己提问不等同于下面的子任务问答覆盖。

## 5. 子任务问答与公开 CLI 恢复

用独立 Run 做固定单个 Experiment 任务探针，明确标记 fixture。通过正式 WorkRequest/任务图/验收绑定建立场景，使用真实 Experiment 和 Interpreter；固定任务只消除规划随机性，不替换模型、工具或回答流程。场景缺少必要指标解释，指令要求在解释结果前询问指标名称和方向；回答不能预写进任务。

暂停后结束当前进程，在新进程使用真实 `resagent2 answer` CLI 回答。不能手写 answer artifact、Run.answers 或恢复状态。CLI 继续走完整 Controller，直至工作交接和 Scientific 判断；若 Scientific 另有问题，照真实字段回答并保留记录。

要求保存并核对：

- 问题确由 Experiment Task 发出；原问题、用户答案、配对 RecordedAnswer 和登记引用均保留。
- 回答后同一 Task/Attempt/Session 延续，预算累计；只读分析零命令，输入文件 hash 不变。
- answer 在完整目录的原工作分组中，保留原 ID 和来源，不要求它冒充 attempt.artifact_ids 中的 Agent 输出。
- Interpreter 的本轮输入真实含此问答窗口；简报能引用其中事实。要求 Scientific 实际 read_artifact 读取该 answer 原件，核对返回同时含原问题和答案。
- 该阅读不改变 delivered_answer_ids、pending_action 或原答案作用域。旧答案拒绝和批准单次消费由本轮确定性回归验证，不用重复付费制造审批场景。
- 公开 CLI 继续走完整 Controller，登记最终意见和报告。不能把 scheduler 直连成功写成公开入口成功。

## 6. 交付与判定

所有原始驱动/验证脚本在执行前保存到 `probes/scripts/`，记录命令和用途。保留完整模型 trace、状态及 Session、各轮目录/反馈/原件、暂停与回答前后快照、输入 hash、工具原始读回结果、退出码。验证脚本逐项计算断言，不使用恒真分支或仅复述模型文本。

在证据根写 REPORT.md、results.json 和脚本清单。报告明确真实公开链与 fixture 的覆盖边界，列出实际 TEST_COMMIT、PASS/FAIL/BLOCKED、每轮请求及 retry/unknown，并与 Run 用量对账。失败和中断不能删除或用成功重跑覆盖。结束时记录 HEAD 与 git 状态，受控产品文件应保持干净。
