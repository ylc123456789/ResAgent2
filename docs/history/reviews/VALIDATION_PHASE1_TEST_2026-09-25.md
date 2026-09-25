# Validation 阶段 1：服务器定向复测

状态：测试方已报告阶段 1 服务器验收通过（2026-09-25），实测产品提交 `8fc0e79`，schema 16.0。报告与本计划范围核对一致；主开发本轮尚未独立读取服务器原始证据。验收结果及来源见 §6。

## 1. 范围

本轮只检查原生 Agent 的完成候选反馈和登记一致性，不重做 validation 架构。三个 Agent 继续使用同一 invoke 和 AgentLoop。

新增行为：Coding/Experiment 提交不存在或有歧义的候选文件、三个 Agent 提交重复输出名时，返回已有 runtime_feedback，在同一 Session 继续。登记继续独立检查授权、来源和哈希。真实失败执行仍失败，保留执行记录和原错误。

不使用 LLM 判断语义正确性。conclusion_requirements 的扩展和新的运行前检查留在阶段 2、3。

## 2. 环境与证据

只使用服务器主仓库 `/root/autodl-tmp/projects/ResAgent2`。先确认受控文件无修改，再获取当前分支；测试期间不拉取或切分支，不新建产品副本。

```bash
cd /root/autodl-tmp/projects/ResAgent2
conda activate ResAgent2
git status --short
git fetch origin
git switch fix/code-health
git pull --ff-only
git rev-parse HEAD
```

将实际 SHA 保存为 TEST_COMMIT，并核对本轮产品提交为祖先。每次运行使用独立 Run ID、证据目录和 trace；不得覆盖失败现场或向旧 trace 追加。建议证据目录：

`/root/autodl-tmp/resagent2/runs/validation-phase1-<时间戳>/`

保存命令、原始脚本、stdout/stderr、退出码、前后状态、Session、trace、REPORT.md 和 results.json。确认 9 包导入均来自当前主仓库，执行 pip check；无需为本轮安装依赖。模型沿用现有配置。

## 3. 确定性回归

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
```

重点核对新增 `tests/e2e/test_completion_feedback.py` 和 `test_failed_experiment_keeps_execution_record_when_candidate_is_missing`：

- 两种任务 Agent 的 finish 错误会出现在下一次真实模型请求结构的 runtime_feedback 中。
- Task/Attempt/Session 身份保持，只有一次 invoke 和一个 Attempt，预算累计。
- output_dir 的候选与 Registry 冻结内容一致。
- 预算耗尽保留拒绝记录；越界文件不通过。
- 非零退出码即使伴随错误候选，Run 中也有 execution_record 和原 TOOL_FAILED。

这些是确定性模型响应测试，不宣称调用了服务器真实模型。

## 4. 真实模型小场景

使用已有公开 NativeCodingAgent / NativeExperimentAgent 与 Scheduler 装配，固定一个任务即可；必须标为定向 fixture，不当作 Scientific 自规划整链。任务无需命令、环境准备或 GPU。

每个场景独立 Run；初始 Run 调用上限建议 12，不得运行中增大。两种 Agent 各一次：

1. 准备只读 Git 工作区，已有 `metrics.json`，内容为 `{"baseline":0.45,"candidate":0.52}`。
2. 测试任务明确说明：这是完成检查探针，第一次用不存在的 `metrics_typo.json` 提交 finish；收到反馈后查明真实文件名，再用真实路径提交，不改动文件。此设定用于稳定触发拒绝，不评价模型是否会自然犯错。
3. 真实 Agent 调用保持原工具、权限、CompletionCheck 和 Registry。不可 mock 文件解析、完成检查或登记，不在两次 finish 中间手动改 Session/Attempt。
4. 核对原始请求和 Session：第一次确实提交错名，下一次请求含 `artifact_path_missing` 及错名，随后提交正确文件。模型若未按要求触发第一次拒绝，应如实写“未覆盖”，不能仅凭最终 completed 算通过。
5. 核对只出现一个 Task/Attempt/Session；所有调用累计，拒绝和成功均保留。正确候选已登记，kind、来源、内容 hash 与原文件一致；零命令、源文件未变。
6. trace 与 usage 按 `call_id:retry_index` 对账；trace 的尝试字段实际读取 `attempts[].retry_number`，账本读取 `usage.requests`，不查询不存在字段或只比较条数。

不要求真实模型重复演示所有异常；歧义、重名、越权、预算及失败执行材料以确定性回归为准。若新增真实失败，保留现场，先诊断，不重跑到绿后覆盖。

## 5. 收尾

报告分别写明全量回归、mock、两种真实 Agent 探针的结果与限制。原始脚本全部留存，验证器必须核对字段内容、ID、时序与 hash，不用恒真条件。

本轮不改模型上下文结构、命令执行或资源装配，不要求重跑 GPU 训练；既有 GPU 验收不能替代本轮候选反馈证据。暂不合并，不清理历史证据。


## 6. 服务器验收结果（2026-09-25）

**依据测试方提交的报告，阶段 1 验收通过；没有新增必补测试。**

本节的信息来自用户转交的服务器测试报告。主开发已对照本计划及当前确定性测试核对覆盖范围；本轮通过本机已有 SSH 配置访问服务器时认证失败，未读取原始 REPORT.md、验证脚本、Session 或 trace。因此，本节不宣称原始证据已独立复核，也不对验证器实现作未经读取的保证。

| 验收项 | 测试方报告结果 |
| --- | --- |
| 冻结与环境 | HEAD 全程 8fc0e79，schema 16.0，9/9 包从主仓库导入，pip check 通过 |
| 确定性回归 | 1291 passed / 1 skipped |
| mock 整链 | completed，13 工件，最终报告登记 |
| Coding 真实模型定向探针 | 20/20 PASS，3 次模型调用 |
| Experiment 真实模型定向探针 | 20/20 PASS，3 次模型调用 |
| 计量 | 两个 Run 各自 trace/usage 3:3 逐条对账，无 retry，账本全 succeeded |
| 工作树 | 受控文件干净；仅未跟踪 .ipynb_checkpoints/，未修改产品、合并或推送 |

两种 Agent 均先提交不存在的 metrics_typo.json，下一次请求收到 artifact_path_missing 与错名，随后查阅工作区并提交真实 metrics.json。报告确认 Task/Attempt/Session 保持同一身份，拒绝和成功历史保留，最终清空 runtime_feedback，Session 与 Task completed。原件按 workspace 来源登记，内容和 sha256 与只读源文件一致；没有执行命令或修改源文件。

这两条是固定单任务 fixture，使用真实 Native Agent、Scheduler、模型与登记链；没有 WorkRequest/Scientific 收尾，Run 留在 running 属于预期。它们证明完成候选反馈与纠正链，不代表 Scientific 自规划整链，也不估计模型自然犯错率。请求明确要求先提交错名，是为稳定触发本轮检查。

缺文件纠正属于完成检查拒绝，不是 HTTP retry 或 usage 失败。因此“账本全 succeeded”与“第一次 finish 被拒绝”并不矛盾。

歧义文件、重复输出名、越权、预算耗尽、真实失败执行记录和原 TOOL_FAILED 的保留，沿用本轮确定性回归覆盖。测试计划没有要求真实模型重复每一种异常，也没有要求本轮 GPU 测试，不追加测试任务。

证据根目录：

`/root/autodl-tmp/resagent2/runs/validation-phase1-20260925/`

测试方报告目录内含 REPORT.md、results.json、probes/scripts/ 的 5 份脚本，以及 cases/completion-coding/、cases/completion-experiment/ 的 state、session、trace、final-run.json。验证器名为 verify_completion_feedback.py。后续若独立读取原始证据，应另补复核记录，不能把本节来源说明改写成此前已经复核。

阶段 2 的 Run 级明确产物存在要求、阶段 3 的新增运行前检查仍未实现。阶段 1 的通过结论不包含这两部分。
