# 运行期资源与人工等待：服务器验收单

状态：`577b8489` 基线已执行并复核；本轮小收尾 §8 待执行。分支 `fix/runtime-resources`，schema 6.0。基线结果及保留问题见 [实施记录](RUNTIME_RESOURCES_PLAN.md)。
测试 AI 只同步、测试、分析、报告，不改产品代码/prompt/既有场景目标/预算，不合并 main，不删旧环境、数据集、缓存和失败现场。

## 1. 同步与记录

- 同步分支最终完整 Git 提交；记录 HEAD、工作树状态。网络不通可用 bundle，不 scp 零散源码。
- 用干净独立 worktree；确认 contracts/runtime/capabilities/orchestrator/scientific/coding/experiment/cli 的 import 指向该提交。
- 若重指向已有 editable 安装，记录原指针和新指针，不删除旧 checkout。
- 新建单一产物根，集中保存 MANIFEST、logs、traces、workdirs、ops。新 Run 使用新目录；不要尝试恢复旧 5.0 Run。
- key 仅运行期加载，不打印、不写脚本。full trace 只存该受控根，目录 0700、文件 0600。

服务器 Conda 如不在非交互 PATH，可 source 实际安装的 conda.sh；按当前机器核对路径，不猜新环境。

## 2. 确定性基线

在隔离 cwd 运行，PYTHONPATH 指向待测仓库（避免测试产物落仓库）：

```bash
repo_dir="$PWD"  # 在待测 worktree 内
check_dir="$(mktemp -d)"
cd "$check_dir"
PYTHONPATH="$repo_dir" python -m pytest "$repo_dir/tests" "$repo_dir/apps/cli/tests" -q
PYTHONPATH="$repo_dir" python -m e2e.mock_e2e
git -C "$repo_dir" diff --check
```

本地最终数量以 RUNTIME_RESOURCES_PLAN.md 的记录为准。重点：
- tests/e2e/test_runtime_resources.py：三个 Agent 四种模式，真正独立进程，未登记/登记但缺失/目录补齐三轮；Session 不换、Attempt 不新增。
- tests/orchestrator/test_controller.py：实际目录刷新、重建 Controller、答案不改变目录事实；跨日等待、多个问题、重复答案、主机时钟、任务级预算与资源传递。
- tests/capabilities/test_resources.py：上下文与脚本映射一致；不相关目录缺失、非法格式、软链逃逸。
- tests/orchestrator/test_schema_version.py：旧记录拒绝恢复且文件不改写。
- CLI / E2E 两组合根：三个 Agent 同一 ResourceLayout，Controller 从部署 catalog 取引用。

脚本驱动动作只证明输入/状态/恢复，不代替真实模型的选择质量。

## 3. 真实已有场景回归

保留现有目标和预算。数据集根必须有实际有效的 catalog.json；E2E 不再在 ResearchRequest 硬编码 cifar10 引用。

| 场景 | 模型/次数 |
|---|---|
| code-experiment | flash ×3、pro ×1 |
| repair | flash ×1 |
| direct | flash ×1 |
| literature | flash ×1 |
| ask-start → ask-resume | flash，两个独立进程，共用该场景 workdir |

每个场景分别设置 REAL_E2E_WORKDIR、RESAGENT2_MODEL、RESAGENT2_LLM_TRACE_DIR，并启用 RESAGENT2_LLM_TRACE_LEVEL=full。
入口仍为 `python -m e2e.real_e2e <场景>`；ask-resume 传入实际测试答案（如 accuracy）。

检查真实产物和原始请求/响应：
- Scientific/Coding/Experiment 的可用目录信息正确，没有把暂缺目录作为可用路径。
- 编码/实验职责、真实 run_command、原始 stderr、冻结 metrics 与最终结论链路保持。
- Run.request 无 dataset_refs；Run.dataset_refs 为系统记录，不冒充实际使用清单。
- llm_calls_used 与 trace 尝试数核对；原始 reasoning 仅调试用途，不回填上下文。
- 不仅看 rc=0；warnings、缺证据、空答案、循环和失败全部报告，不重跑到绿掩盖原结果。

## 4. 新资源闭环：先做无训练的 CLI 场景

用独立小型 dataset_root，不改生产 catalog：

1. 初始没有登记 `resource_probe`。CLI goal：
   “只确认 resource_probe 数据集是否已准备好并记录结果；若系统目录中不可用，请询问用户补充，回答后重新确认。不做代码修改或实验，不下载或替换数据。”
2. 预期 paused（CLI exit 3），requested_fields 非空。保存原始 trace 和问题。
3. 仅登记 `{"resource_probe":"resource-probe"}`，不创建目录；在新进程按真实字段回答“已准备好”。
4. 检查新 prompt 仍有 unavailable_dataset_ids 中的 resource_probe，available_dataset_ids 不包含它。模型应继续询问，不虚构已就绪。若模型偏离，报告行为失败；不要更改 prompt 或手动改状态。
5. 创建目录并放一个小数据文件，再按新问题的实际字段回答。预期 completed，意见准确区分“目录可定位”与“数据内容已验证”。
6. 同 Run、同 Scientific Session；原始 request 不被改成资源表。show 只读，resume 不代替 answer。
7. 在 catalog 再留一个不存在的无关条目；它不应使这个已就绪确认或 direct 场景提前报错。

不需要等几小时：长等待的计时语义已有受控时钟测试。记录真实问答过程中的 user_wait_seconds 增加、已用 LLM 计数不归零即可。

## 5. 子 Agent 真实模型抽查

用测试驱动装配实际 NativeCodingAgent / NativeExperimentAgent（不改产品源码），每类一个小场景。
驱动自动从测试 DatasetCatalog 取得内部 ModuleTaskRequest.dataset_refs；调用方的 ResearchRequest 不填资源字段。

- Coding：检查一个数据读取辅助函数，任务说明必须先确认 resource_probe 已就绪；缺失时 ask_user。补齐后新进程同 Session/Attempt 续跑，完成只读检查并引用实际读取的代码。
- Experiment：小脚本只读 resource_probe/sample.json，生成一个 metrics.json。缺失先 ask_user；补齐后新进程同 Session/Attempt 续跑，真正执行并产生指标。
- 两者都测试“先确认但没建目录”的中间态，检查原始 prompt，不能只看用户口头答案。
- 原始失败若源于模型未按提示询问，必须保留；不可据此宣称本实现有强制资源访问闸口。

环境尽量复用该 Run/Workspace 的已有绑定；不要装 torch/CUDA 只为这个小用例。

## 6. 依赖与安全

依赖实现没有改写，但需要回归共享环境装配：
- code-experiment 核对 prepare/run_setup/audit 的实际序列，以及 Coding/Experiment 同 Run+Workspace 的 env_id。
- 若现有场景没触发安装，单独小用例用现有镜像安装一个体积小、事先确认缺失的普通依赖，再审计和执行；不卸载已有依赖来造失败。
- 记录是否命中包缓存，不把“安装成功”宣称为“缓存命中”。安装耗时单独报告，不增环境池或清缓存。
- 模型不可自动下载数据集；所有 resource-probe 文件由验收驱动人工准备。
- 检查 trace 权限与凭据泄漏，扫描结果只报命中数/位置，不输出秘密。

## 7. 最终报告必须区分

- 正常通过 / 带 warning / 明确失败 / 本轮未触发。
- 确定性资源检查与模型是否正确使用 ask_user 是两层证据。
- 本次所有 commit、模型实测值、调用计数、资源/环境绑定、产物路径、editable 指针变化。
- 未合并 main；交开发方复核后决定是否合并。不得把有限样本称为永久稳定。

## 8. 本轮小收尾补验

仅共享资源提示和 CLI 展示变化，不要求重跑整套 GPU 训练矩阵。先按 §1/§2 同步新的完整提交并跑本地确定性基线，保留 `577b8489` 的所有原始现场。

- Coding（code_understand）目标表达“先确认 resource_probe 就绪，再解释 data_reader.py”；Experiment 目标表达“用 resource_probe/sample.json 运行小脚本并产出 metrics.json”。不要在测试目标中自行把“缺少”改写为“只在 unavailable 列表时询问”，也不要改产品提示、预算或手工改状态。
- 两类都从未登记开始：预期 ask_user；仅登记且目录仍缺，再回答“ready”：预期再次 ask_user；实际补齐目录/文件后回答：预期同 Session/Attempt 完成，Coding 引用读到的代码，Experiment 真执行并交付指标。
- 加一个不需要数据集的只读代码任务，catalog 含无关缺失条目：应能完成，不因该条目强制询问。
- 保存 full trace 的共享目录片段和真实动作；询问不可误称 `RESAGENT2_DATASETS_JSON` 为 catalog 文件路径，不可把 unavailable 解释为目录已存在。提示到达和模型遵循分别报告，不拿脚本动作当真实模型结果。
- CLI 展示由本地公共 renderer 测试验证：最终意见不和旧过程判断并排显示，旧判断仍留在 Run；无最终意见时仍显示标注 interim 的过程判断。无需为展示单独付费跑 LLM。
- JSON/Schema 错误和任何模型偏离仍逐条记录，不通过重跑覆盖失败。[JSON 专项](LLM_JSON_OUTPUT_FOLLOWUP.md) 本轮只记录、不修复。

## 9. 用户回答上下文补验

本轮修复 Coding/Experiment 的用户回答未进入模型上下文；仍用原有 Composer，不改状态机、预算、JSON 处理或 Scientific 答案逻辑。`d03abee` 的 §8 现场保留，不能用旧结果替代新验收。无需重跑 GPU 训练矩阵、重装 torch 或新增环境池。

### 同步与确定性基线

按 §1/§2 同步本轮完整提交到新干净 worktree，记录 HEAD、8 包 editable 指针与产物根；不要只复制零散源码。确定性基线为 **805 passed, 1 skipped**；执行 mock_e2e 与 diff-check。重点测试为 `tests/runtime/test_context.py`、`tests/e2e/test_runtime_resources.py` 和 `tests/orchestrator/test_workflows.py`（Task 回答作用域）。

### 真实资源恢复

复用 §8 的小型原生 Coding/Experiment 驱动与目标，但在独立目录建立新 Run/Session，不能覆盖旧现场。各跑一套真实 Flash 三阶段：

1. 未登记：应 ask_user。允许调查代码，不应执行依赖尚缺数据的命令。
2. 仅登记但目录仍缺，新进程按实际 requested_fields 回答“ready”：应再次 ask_user；不能因为先前已问过而执行依赖该数据的工作。环境审计或只读诊断与真正的数据依赖命令分开记录。
3. 建目录并放置 sample.json，再按实际问题回答：同 Session/Attempt 恢复。Coding 读实际代码后完成；Experiment 真执行脚本、冻结并交付 metrics.json。不得把口头回答当作目录/内容已检查的证明。

对每次恢复的完整请求核对：`included_sections` 含且只含一个 `answers`；正文包含驱动实际传入的 UserAnswer 值，而非仅有历史 ask_user 的观测。发生非终止工具调用时，检查后续请求仍包含这些答案；与本次刷新后的 available/unavailable 视图同时可见。记录旧工具观察及其时间顺序，不要求删除历史。Scientific 的既有答案路径做一次小型 CLI ask/answer 冒烟即可。

### 回答必须真的被消费

另加一个不依赖数据目录变化的用户选择用例，分别测 Coding（code_understand）和 Experiment：任务先询问两个候选中的选择；驱动在暂停后才指定答案，并经现有 UserAnswer/新进程 resume 传入，不把答案预写进初始 goal。

- Coding：让用户选择解释哪个辅助文件，完成时读取/引用并解释用户选中的文件。
- Experiment：一个标准库小脚本接受用户选择参数，不同参数产出不同的已知数字指标；完成时实际执行、冻结指标与该答案一致。

这验证“收到回答并据此行动”，而非只凭新建目录恢复。可复用现有环境，无须大数据或训练。保留一个无数据依赖且目录中有无关缺失条目的只读任务，确认仍可完成。

### 报告口径

分别报告：代码确定性投影、原始 prompt 中实际可见、模型是否遵循、真实结果是否符合答案。缺数据仍先运行就是行为失败，即便最终完成也不能记全绿；不要求本次提示具有确定性强制保证。JSON 解析错误、schema 校验补充行、重试和 Task Attempt 分开统计，逻辑调用按 call_id 去重；保留所有失败，不重跑覆盖、不修改产品 prompt 或预算。[JSON 专项](LLM_JSON_OUTPUT_FOLLOWUP.md) 仍待独立调查。
