# 科研目录与 Interpreter：服务器证据复核

日期：2026-09-24。分支 `fix/code-health`，产品 `b31648d1`，回归基线 `42efaa16`，服务器测试 HEAD `36360f85adac71496823122149da91699aaa902f`，schema 15.0。依据[验收计划](RESEARCH_HANDOFF_TEST_2026-09-23.md)。

## 1. 结论与范围

**核心功能和真实 CUDA 训练链已有通过证据；不能把原计划所有项目写成无保留通过。** 本次只读复核服务器文件和本地代码，另做无模型的完成检查机制复现；没有修改产品、服务器报告、Run/Session 或原始现场，没有重跑模型/GPU，没有合并。

原始证据根：`/root/autodl-tmp/resagent2/runs/research-handoff-20260923`。测试方 REPORT.md/results.json 保留原来的 PASS 与“无新产品缺陷”结论；本文补充独立复核、口径勘误和未覆盖项，不倒改旧材料。

| 范围 | 已核实的事实 | 边界 |
| --- | --- | --- |
| 确定性回归、mock | 日志为 1269 passed / 1 skipped、exit 0；mock completed、13 工件 | 不是语义成功率证明 |
| 两轮公开入口 | run_research_handoff_01 completed、supports，2 轮，27 次请求；删除确认后执行，最终报告登记，无 terminal_error | 指标问题由 Scientific 提出，没有覆盖 Experiment 任务内指标问答 |
| GPU 公开入口 | run_gpu_handoff_01 completed、inconclusive，2 轮，132 次请求；原命令真实 exit 0、24.6766 秒，metrics=0.4515/0.4604、cuda、epochs=1、seed=0 | GPU 独立进程/利用率采样未找到；后续一次任务交付失败、一项依赖任务 blocked |
| 科研目录与简报 | 每条 Run 各 2 次 Interpreter；旧条目保留、增量不重复旧条目、引用在目录中，最终报告在 scientific 组 | 引用存在不证明全部语义正确 |
| 原证据完整性与计量 | public 35、GPU 100 份已登记工件逐份 hash 匹配；最终引用均属于 Scientific 已观察集合；trace 的 call_id:retry_index 与账本逐键一致，全部 retry=0/succeeded | 只证明已登记集合，不能推出每个磁盘文件都已登记 |
| 混合失败探针 | fixture=true，正式 Interpreter trace 为 1 次请求；5 条引用陈述区分失败、0.8 与小样本限制，原输入文件存在 | 没有在交付目录找到原始探针脚本；不冒充真实训练失败恢复 |

第一条 supports 只支持已存数字的算术比较（0.52−0.45=0.07），其 final opinion 明确不支持因果或普遍有效性。第二条 inconclusive 与有限且不一致的实验结果相符。Run completed 表示已经交付结论，不表示全部任务都成功。

## 2. 失败任务的准确原因

任务 `task_literal_repeat_runs` 的两个重复实验实际上执行过，结果文件保存在：

```text
cases/gpu-chain/data/runs/run_gpu_handoff_01/attempts/task_literal_repeat_runs/attempt_1/
  repeat_1/metrics.json           # 0.4402 / 0.4869
  repeat_1/execution_record.json
  repeat_2/metrics.json           # 0.4469 / 0.5224
  repeat_2/execution_record.json
  repeat_summary.json
```

模型第一次 finish 把脚本写的记录声明为保留给系统的 execution_record 类型，被正常反馈拒绝。第二次改为 run_record 类型时，还把 path 改为 `repeat_1/run_record.json`、`repeat_2/run_record.json`，但并没有把真实磁盘文件改名。这两个候选路径都不存在。

Experiment 完成检查本来就先查 `output_dir / candidate.path`，找不到再查 workspace；不是“只在 repo 找文件”。对不存在的 run_record.json，第二个目录查询恰好在缺失的 repo/repeat_1 父目录报错，因此最终错误文字容易误导。

已用当前代码的临时夹具直接调用完成检查复现，零模型、零 GPU：

| 候选 | 结果 |
| --- | --- |
| workspace 中的脚本 + output_dir 中的 repeat_1/metrics.json | complete=True |
| 增加 kind=run_record，path=repeat_1/execution_record.json | complete=True |
| 仅把上项 path 改为不存在的 repeat_1/run_record.json | FileNotFoundError，错误指向 workspace/repeat_1 |

类型与文件名不要求相同。完成检查已有双根支持，见 [ExperimentCompletionCheck](../../../packages/agents/experiment/src/resagent2_experiment/completion.py)。因此不能把本例归因于 Interpreter 改动或输出目录不受支持。

同时不能忽略产品的已有处理缺口：[AgentLoop](../../../packages/runtime/src/resagent2_runtime/loop.py) 遇到完成检查异常会立即终止 Attempt。这个可纠正的候选路径错误没有返回模型重提 finish 的机会；本 Attempt 的 artifact_ids 最终只有 6 个 question 工件，没有重复训练的 metrics/log/execution_record。因此后续依赖汇总任务 blocked，Interpreter 得到的是失败事实，没有这两次重复测量的登记引用。

准确表述应是：**原始 Session、命令日志与测量文件仍在磁盘；失败状态已保留，但测量结果没有完成登记，也没有进入科研目录和简报。** Interpreter 没有捏造这些未交付的结果；它忠实说明本轮没有可用的 literal-repeat 测量。

这属于既有 validation 的失败分类、纠正反馈与失败证据保存问题。本次只记录，不借验收顺便改变 validation 政策、增加新调用模式或放宽完成条件。

## 3. 训练结果的统计口径

初始训练源码与输入清单 hash 一致，最终 train.py diff 只修改 SELayer.forward；完整 CIFAR-10 loader、训练循环、backward/optimizer.step 和测试集评估未改。原始直接命令 `python train.py --epochs 1 --seed 0` 的系统执行记录、日志和冻结 metrics 一致，baseline=0.4515、candidate=0.4604，即 +0.0089（0.89 个百分点）。

第二轮 seed sweep 是系统额外开展的探索：新增 harness 调整了初始化随机数控制等行为，并采用 seeds 0/1/2。原 seed=0 配方与结果仍保留，但不能把所有后续测量都说成执行了原封不动的相同协议。两组协议应分别汇报。

尤其，0.3935 与 0.4236 的差值来自不同 harness 下的 baseline seed=0，不能仅凭这两个数字就断言“完全相同条件下漂移约 3pp”，或直接归因于 CUDA 非确定性。Interpreter 第二轮简报实际上明确说明它们不是同一协议的测量。

真正的 literal-repeat 原始文件给出 0.4402/0.4869 与 0.4469/0.5224，但因上述交付失败未成为登记证据。这些文件可供测试人员诊断，不能追认 Scientific 当时已读、已引用或已用于结论。

## 4. 仍需补齐或明确标为未覆盖

1. **Experiment 任务内指标问答恢复**：本轮该问题由 Scientific 在派工前提出。Scientific 跨进程问答和 Coding 硬确认已覆盖，但不能代替计划 §5 的 Experiment 指标问答。GPU 场景有 Experiment 命令确认，证明另一条恢复路径；不能混同任务内业务提问。如需关闭完整 §5，只补一个小型真实 Experiment 只读指标提问/新进程回答探针，不需要 GPU，保留独立 Run 与原始快照。
2. **GPU 监看**：本次证据根未找到与训练时间/PID 对应的 GPU 利用率、显存和进程采样；报告与 verify-gpu-chain.json 也没有该验收项。现有源码、环境、进程执行和 metrics 强烈支持真实 CUDA 训练，但计划 §6.4 的独立监看证据仍缺。先定位是否另存；若未保存，应如实标记不可补溯，不用当前 nvidia-smi 截图代替过去的运行证据。
3. **原始脚本**：指定目录只有 verify-public-chain.json、verify-gpu-chain.json，以及 probe-result.json、探针输入与 trace，未找到相应原始执行/验收脚本。38/38、34/34 是测试方自定义检查计数，不能等同于计划逐项覆盖。优先补交原脚本与命令记录；若只能从历史重建，必须标明重建时间与来源，不能称原始留存。
4. **报告勘误**：纠正输出路径根因、区分磁盘保留与已登记产物、把 seed sweep 与原协议分开，解释统计比较不能单独定位非确定性来源。不要更改旧 Run/Session、补登记旧产物或把失败 Attempt 改成成功。

不要求现在重跑整套 GPU 或重新安装环境。先补已有材料和报告，再决定是否需要小范围复测。分支保持未合并；本记录不宣布全部验证和失败处理问题已经关闭。
