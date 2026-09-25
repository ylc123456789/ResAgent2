# Validation 阶段 1：服务器定向复测

状态：本地实现完成，服务器尚未执行。分支 `fix/code-health`，schema 16.0。本地基线为 1291 passed / 1 skipped，mock completed、13 工件。

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
