# 科研目录与 Interpreter：服务器证据复核

日期：2026-09-24。分支 `fix/code-health`，产品 `b31648d1`，回归基线 `42efaa16`，服务器测试 HEAD `36360f85adac71496823122149da91699aaa902f`，schema 15.0。依据[验收计划](RESEARCH_HANDOFF_TEST_2026-09-23.md)。最终收尾见[第 6 节](#verified-closeout)；前五节保留原轮发现与分阶段补测记录。

## 1. 原轮结论与范围

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

## 4. 原轮缺项与补测要求

1. **Experiment 任务内指标问答恢复**：本轮该问题由 Scientific 在派工前提出。Scientific 跨进程问答和 Coding 硬确认已覆盖，但不能代替计划 §5 的 Experiment 指标问答。GPU 场景有 Experiment 命令确认，证明另一条恢复路径；不能混同任务内业务提问。如需关闭完整 §5，只补一个小型真实 Experiment 只读指标提问/新进程回答探针，不需要 GPU，保留独立 Run 与原始快照。
2. **GPU 监看**：本次证据根未找到与训练时间/PID 对应的 GPU 利用率、显存和进程采样；报告与 verify-gpu-chain.json 也没有该验收项。现有源码、环境、进程执行和 metrics 强烈支持真实 CUDA 训练，但计划 §6.4 的独立监看证据仍缺。先定位是否另存；若未保存，应如实标记不可补溯，不用当前 nvidia-smi 截图代替过去的运行证据。
3. **原始脚本**：指定目录只有 verify-public-chain.json、verify-gpu-chain.json，以及 probe-result.json、探针输入与 trace，未找到相应原始执行/验收脚本。38/38、34/34 是测试方自定义检查计数，不能等同于计划逐项覆盖。优先补交原脚本与命令记录；若只能从历史重建，必须标明重建时间与来源，不能称原始留存。
4. **报告勘误**：纠正输出路径根因、区分磁盘保留与已登记产物、把 seed sweep 与原协议分开，解释统计比较不能单独定位非确定性来源。不要更改旧 Run/Session、补登记旧产物或把失败 Attempt 改成成功。

不要求现在重跑整套 GPU 或重新安装环境。先补已有材料和报告，再决定是否需要小范围复测。分支保持未合并；本记录不宣布全部验证和失败处理问题已经关闭。

<a id="supplement-review"></a>

## 5. 补测独立复核（2026-09-24）

**脚本留存与 GPU 独立采样已补齐；Experiment 任务问答的底层恢复已验证，但补测绕过了公开回答入口。** 未发现本轮 Interpreter 产品改动的新缺陷，不能把自定义的 16/16、14/14 直接解释为所有原计划要求均已闭环。本节只读复核，没有重跑模型、训练或安装，没有修改服务器原报告、Run、Session 和登记工件。

服务器仍为 `36360f85adac71496823122149da91699aaa902f`，受控文件干净，存在未跟踪的 `.ipynb_checkpoints/`。以下路径均相对于同一证据根。

### 5.1 原始脚本：留存缺口关闭，来源表述收窄

`probes/scripts/` 实际有 **33 个文件：31 个脚本、1 份目标文本和 SCRIPT_INDEX.md**。31 个脚本包括原轮 23 个、补测 8 个。不是“33 个脚本另加清单”。关键 `verify_public_chain.py`、`verify_gpu_chain.py`、`probe_interpreter.py` 均已补交。

归档的 32 个非清单文件与服务器 `/tmp/` 同名副本逐份 hash 一致；本地也有 32 个同名文件（包含清单，不含 import_check.py），与归档一致。这能证明现存副本一致；“从未修改、一次成稿即用”的历史来源仍是测试方说明，不能靠当前副本独立证明。

两个原链路 verifier 读取证据、输出检查 JSON，并用非零退出码表示检查失败。Interpreter fixture 脚本只打印检查布尔值，没有失败退出，因此其退出 0 本身不构成通过依据；原简报、输入、引用和 trace 已经独立复核，不必为此再调用模型。初始化和快照脚本会覆盖同名现场，归档脚本不应直接在旧目录重跑。

### 5.2 Experiment 问答：机制通过，公开入口仍未覆盖

`cases/experiment-qa/` 原始快照、Session、trace 和冻结工件支持以下事实，已重新直接检查：

- run-phase.json 中 Run paused，Task/Attempt 为 needs_user_input；task_exp_qa 类型及 Session.module 均为 experiment。真实 ask_user 的问题和 primary_metric/better_direction 与 pending_question 一致。
- 回答后同 Task、Attempt 1、Session 延续，Task completed，原账本 3 个键保留并增加 1 个；4 条 trace 与最终账本逐键一致。
- 工具仅 list_files/read_file/ask_user/finish；初始 Git 版本、当前 metrics.json、冻结 evidence 字节相同，登记 hash 正确。报告和工件正确表达 accuracy、higher_is_better、0.45/0.52/+0.07。

但 `probe_experiment_qa_verify.py:24` 的 `att['status'] == 'needs_user_input' or True` 是恒真条件。Session 检查也只查 ID 字串，预算检查只比最终数量。不能原样采用“16 个有效检查全部通过”；现有快照足够做有实际判断作用的前后比较，无需重跑模型。

更重要的是，`probe_experiment_qa_answer.py:55–84` 手写答案校验、RecordedAnswer、工件登记、清 pending、resume_task_in_place 和 scheduler._save，再调用 Scheduler 循环；没有调用 `controller.answer_question` 或 CLI。这基本复制了产品回答逻辑，能够验证 Scheduler/Runtime 恢复，但无法替代公开入口的装配与 Controller 续跑验证。固定任务可避免 Scientific 规划随机性，不应靠复制回答处理代码来隔离流程。

独立 run/answer 脚本、清单和 trace sequence 重启支持跨进程执行说明；没有保存两次启动的进程 ID/命令日志。这个定向 Run 保持 running、任务 completed 符合测试边界，不算公开整链 completed。

若要求关闭原计划中公开回答入口的这项覆盖，只需一个新建的轻量问答用例：允许固定 Experiment 任务，回答必须在新进程经生产 Controller.answer_question 或 CLI 进入，不手写恢复状态，不替换回答后的 Controller 循环；保存进程日志和前后快照。不需要 GPU，也不必再跑整套公开链。当前材料已足以确认底层恢复机制通过。

### 5.3 GPU 独立采样：训练成立，计量和封存需补记

`cases/gpu-monitor/` 的最终 Experiment Task completed。原命令为 `python train.py --epochs 1 --seed 0`，exit 0，duration=23.2042 秒；stdout、源 metrics 和冻结 metrics 一致：baseline=0.4321、candidate=0.4582、device=cuda。train.py 与原 GPU 链最终 SE 代码 hash 一致；三个登记工件 hash 均正确。

Session 中 run_command 动作/回执时间为 **2026-09-23 22:03:31.135–22:03:56.136 UTC**。CSV 的 24 个非零利用率样本在 **22:03:39.881–22:03:55.077 UTC**，全部落在命令时间窗内；进程样本为 PID 10146/python，峰值利用率 95%、总显存 1535 MiB。结合执行日志和源码，独立 GPU 活动证据足够，不需要再跑训练。采样未保存该 PID 的完整命令行或父进程关系，不应把时间关联描述为额外取得了进程树证据。

采样脚本每轮 sleep 0.5 秒，加上 nvidia-smi 开销，实测间隔中位数约 **0.652 秒**。非零首末时间差为 **15.196 秒**；验证器按 `(24-1)*0.5` 算出的 11.5 秒不是实测时间跨度。GPU 忙碌时段短于整个命令执行时间正常，应按真实时间戳比较，不要求二者等长。

补测内部实际存在重跑：同一 run_gpu_monitor 的 trace 有 **20 条独立调用，前 14 条旧轮、后 6 条最终轮**，全部 retry=0。现存 Run 的 6 个账本键与后 6 条完全匹配；前 14 条 trace 与 resume.log 仍在，但先前 Run/Session 未单独保留。最终脚本以固定 Run ID 重新初始化，所以不能写成整个 GPU 补测仅 6 次调用或全部 trace 与最终账本一致。它与原 132 次 GPU 公开链相互独立。

复核时采样器仍在后台运行、CSV 继续追加空闲样本，verify-gpu-monitor.json 中 275 行只是早先读取的数量。需要停止本轮遗留采样器后封存 CSV，再生成只读核验与 hash；保持原采样和旧验证结果，不重写旧结果冒充原始验收。

### 5.4 收尾范围

后续只需整理已有证据、修正恒真检查/计量/采样时间口径；若坚持原计划全部入口覆盖，再补 5.2 的公开回答入口小用例。无需重装、GPU 重训或产品改动。新测试使用独立 Run ID 和目录，保留原轮失败。

测试方 REPORT_SUPPLEMENT.md 仍写“结果在 attempt、校验只在 repo 找”，应勘误为第 2 节已复现的候选文件名错误；完成检查支持两种目录，问题是可纠正的路径错误被终止处理，以及失败测量未登记。本项继续留给后续 validation 设计讨论，不算被本次补测修复。分支继续不合并。

<a id="verified-closeout"></a>

## 6. 公开入口补测与最终收尾（2026-09-24）

**三项验收缺口已关闭，科研目录与 Interpreter 本轮功能验收可以收尾。测试 AI 无需继续补跑。** 结论综合原公开链、真实 CUDA 训练、失败材料探针和定向补测，不表示某一条 Run 独自覆盖所有场景，也不表示历史失败处理问题已修复。服务器产品实测仍为 `36360f85`、schema 15.0；本次只读核对原始证据，未修改产品或服务器现场，未调用模型/GPU，分支未合并。

### 6.1 公开回答入口已覆盖

新证据在 `cases/experiment-qa-cli/`，Run 为 `run_experiment_qa_cli`。初始脚本创建固定 Experiment 任务，并补齐真实 WorkRequest、conclusion_requirements 与 Scientific Session 的运行绑定。这是有明确夹具的定向集成测试，未覆盖 Scientific 初始规划；初始规划由原公开整链覆盖。

回答脚本实际调用独立 `resagent2 answer` CLI，进入产品 `controller.answer_question`，没有手写回答登记、恢复状态或替换 Controller 循环。对原始状态、trace、Session 和工件的独立检查确认：

- 暂停问题属于 Experiment 的 task_exp_qa / Attempt 1，字段为 metric_name、better_direction；任务 Session.module=experiment。
- 回答后同 Task、Attempt、Session 延续。原 3 个账本键及状态全部保留，最终为 14 个；全部 trace 的 call_id:retry 与账本逐键一致、唯一、retry=0、succeeded。14 次分别为 Experiment 5 次、Interpreter 2 次、Scientific 7 次，不能都算作 Experiment 调用。
- Experiment 未执行命令或修改源文件，metrics.json 以原内容交付，0.45/0.52/+0.07 分析正确。全部 17 份登记工件逐份 hash 一致。
- work_1 consumed，work_record、research_index、work_feedback 已冻结，Scientific 读取证据。第二次通过 CLI 回答 Scientific 澄清后，Run completed、verdict supports、artifact_final_report 登记，无 terminal_error。

Scientific finish 曾因非法 opinion 字段、错拼引用得到反馈并纠正，最终合法完成；通过不表示中间从未报错。这里的 supports 只支持按用户确认指标对既存数字作比较，不支持统计显著性、因果或可重复改进；最终报告明确保留这些限制。

两个回答使用同一个重定向日志路径，最后一次 answer-cli.log 覆盖了第一次暂停输出；前后状态、两份真实答案工件和完整 trace 仍保留。独立脚本/CLI 调用路径与原始记录支持跨进程恢复结论，但没有完整进程 ID 清单，不把它写成进程身份审计。无需为这个留证瑕疵再跑模型。

### 6.2 验证器、GPU 计量和封存已核对

- 旧 scheduler 直连用例新增 verify-experiment-qa-v2.json，旧脚本保留；恒真条件已去除，暂停状态和 Session 改用前后快照比较。独立复核进一步确认 3→4 的原账本键与状态确实保留，不仅比较计数。
- GPU 补测完整 trace 为 20 次，前轮 14 次与最终轮 6 次分别记载；最终 6 键与账本完全一致。旧轮 Run/Session 未独立留存的限制仍在，不追认全程账本完整。
- GPU 采样器已不存在；封存 CSV 为 100698 字节、2633 行（含表头），SHA256 为 `05948c347ea88ba09b9ac1ee6607ae9eb6fa535443b123a36df9f6b96d72c6a3`，与 gpu-monitor-final.json 一致。实际间隔均值约 0.651 秒，非零首末跨度 15.196 秒、24 个非零样本、峰值 95% / 1535 MiB，训练时间关联沿用第 5.3 节的独立核查。
- 归档新增 5 个脚本后，现有 36 个脚本、目标文本、清单，共 38 个文件；新增 5 脚本均与服务器 /tmp 同名副本一致。

服务器结果已补上 CLI 用例、10/10 的 v2 结果、20 次 GPU 计量与候选文件名错误。报告部分段落仍保留旧“16/16”、旧脚本数量和按 0.5 秒描述的文字；results.json 也仍有“同 seed 约 3pp 非确定性”的旧结论。阅读时以第 3 节的协议区分、第 5–6 节及新增 v2/final 文件为准；这些文字差异不要求重跑测试。

### 6.3 留给后续设计的问题

1. **validation 的错误分类和失败材料交付**：不存在的候选文件名使 Attempt 直接失败；可纠正反馈和失败测量登记仍未解决。原失败现场保留，详见第 2 节，按既有约定后续讨论，不在此次验收中改政策。
2. **任务答案的跨层可发现性**：本次 Scientific 再次询问同一指标，且声称旧答案未登记。这个说法不正确：任务答案 artifact_system_answer_7d7927886fed_746c194d 实际已登记且 hash 正确，只是未出现在 Scientific 当时的目录和请求中。科研目录按既定规则过滤问答等系统工件，Controller 的授权材料只向 Scientific 提供其自身 Session 的 answer，任务摘要只转述了任务答案；Scientific 又把 question.action=null 错读成“没有答案”。action 表示待批准操作，并非答案字段，答案保存在独立 answer.values 中。以后讨论如何让 Scientific 找到相关用户事实时，应同时考虑工件可发现性和模型理解，不能把它归为数据丢失。这不影响公开回答入口和本轮反向交接验收通过，也不要求测试 AI 重演。

本轮完成的是已约定功能和补测范围的验收；保留上述已知边界，开发分支继续未合并。
