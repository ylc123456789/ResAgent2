# 上下文时序与读取语义：验收单

分支：`fix/contract-foundations`；本轮基于 `b2b7056`。正式测试前记录待测提交的完整 SHA；不得把本轮父提交的服务器结果当新代码验收。

## 本次改了什么

1. 片段仍优先选择近期读取，展示顺序统一为每组从旧到新；`observed_at` 对应 Session 中原始事件编号，不是重新编号，也不是文件版本。
2. 文件片段之后出现同路径成功 `create_file` / `replace_text` 时，附 `modified_after_read_at` 指向最近这次成功写入。旧正文保留；失败修改、修改其他文件不触发；重新读取后的片段不受此前修改影响。冻结 Artifact 不套用文件修改标记。
3. `truncated` 描述实际呈现的片段，`context_truncated=true` 表示工作集又做了一次截断。短 `recent_observations` 明确是 `Value preview`，同样使用原始事件编号，其省略不改变原始工具结果。
4. `search_text` 明确为大小写不敏感的字面子串搜索；`a|b` 不表示两个候选词，要分别搜索。

不变：文件/Artifact 正文各 6000 字符，Coding/Experiment 默认总输入上限 8192 tokens；已有来源/范围去重和预算淘汰保留；不增加读取/修改工具，不自动重读文件，不改状态机/Compiler/Scientific 决策，不增加新缓存或 LLM 调用。没有修改 Session/trace 原记录。标记只来自已记录的内置工具成功结果，不声称能发现外部修改或所有软链接别名。

## 本地确定性检查

在隔离 cwd 中运行，避免把测试产物写进仓库；使用已有 ResAgent2 环境，`PYTHONPATH` 指向待测 repo。

```bash
repo=/home/cyl/ResAgent2
test_dir=$(mktemp -d /tmp/resagent2-context-order-check.XXXXXX)
cd "$test_dir"
export PYTHONPATH="$repo"
/home/cyl/miniconda3/envs/ResAgent2/bin/python -m pytest \
  "$repo/tests" "$repo/apps/cli/tests" -q
/home/cyl/miniconda3/envs/ResAgent2/bin/python -m e2e.mock_e2e
git -C "$repo" diff --check
```

重点测试：`tests/runtime/test_context.py`、`test_observation_previews.py`；`tests/capabilities/test_workspace_context.py`、`test_text_windows.py`；`tests/e2e/test_native_context_capacity.py`、`test_workspace_read_history.py`。后者使用真实 NativeCodingAgent/AgentLoop 和文件工具，覆盖读取→成功或失败修改→另一个范围读取→历史轮转→暂停。需要验证的是实际发给客户端的 prompt，不只是中间函数的 JSON。

本地实测（2026-09-06）：隔离 cwd 全量 **694 passed、1 skipped**（较 b2b7056 新增 17 例），mock E2E completed，`git diff --check` 干净。独立只读复核未发现阻断问题。服务器：本轮尚未运行，不能以本地测试或父提交的成功结果代替。

## 服务器任务单（可直接交给测试 AI）

只同步、测试、分析、报告：不要修改产品代码、prompt、场景目标或预算；不要合并 main，不删除旧环境/缓存/数据集/失败现场。

1. 用 Git pull 或 bundle 同步正式提交到干净 checkout/worktree，记录 `git rev-parse HEAD`、`git status --short`。核验 editable 包实际 `__file__` 指向待测 checkout；若改变安装指针，报告前后位置。
2. 用已有 Python/Conda、数据集 catalog；每次运行独立 fresh workdir。可以沿用原有环境复用策略，但明确记录 warm/cold，避免把清目录当作清环境。本轮所有日志、trace、状态与分析统一放在一个新的验收根目录，不覆盖 `e2e-context-b2b7056-20260906`。
3. 原目标/预算运行：Flash `code-experiment` **3 次**；V4-Pro 同场景 **1 次**；Flash `repair` **1 次**；Flash `direct` 与 `ask-start` → 新进程 `ask-resume` 各 **1 组**。固定次数、保留每次结果；失败不以“再跑一次通过”覆盖。
4. 每个场景启用 `RESAGENT2_LLM_TRACE_LEVEL=full` 和独立 `RESAGENT2_LLM_TRACE_DIR`。使用既有模块模型配置覆盖方式并核查实际 trace.model；不可只改报告中的模型名。保留 request_text/raw_response_text/raw_reasoning_text（供应商返回时）及 Session/Run/artifacts，不打印凭据。
5. 另跑一次 `literature` 作为共享 Runtime 提示回归。若检索服务继续 timeout/429，保留原响应及后续动作，单独报告“外部检索失败 + 是否仍错误转发”；本轮没有修复该路径，不放宽 required evidence，不把未检索到误写成无相关文献。

### 必看原始消息与状态

- `workspace_reads.file_snippets` / `artifact_snippets` 各自的 `observed_at` 单调递增，能映射到本 Session 的成功读取事件，而不是当前 step 或局部编号。
- 有旧片段在成功编辑后继续出现时，旧正文仍在，并有正确 `modified_after_read_at`；之后读取的内容来自修改后的真实文件。若模型没有触发新旧共存，记“本轮未触发”，不要为了制造样例修改产品 prompt；本地负例已负责覆盖。
- 若两种读取同时发生，两组都可见且各自正文总长不超过 6000；原始 Session 中被预览裁剪的完整工具结果仍可查。工具 `truncated=false`、工作集 `context_truncated=true` 可以同时成立，它们描述不同层，不是互相矛盾。
- 短历史使用原始 `Event N` 与 `Value preview`。查看 reasoning 是否仍把短预览的省略误当作“根本没读到文件”，从而扩大范围、挤掉已有目标片段。
- 对每次 `replace_text`：关联之前的读取、行范围、内容以及执行结果，检查 `old_text` 有无可见的精确依据。单独统计编辑前/编辑后读取数、有范围/无范围次数、重复相同范围、成功/失败编辑；不设“调用数低就一定正确”的假门槛。
- 若模型使用 `search_text` 查询 `a|b` 形式，核对收到的工具 guidance 及后续恢复；零匹配只能说明未匹配此字面字符串。模型未触发该形式时，不能宣称真实调用已证明恢复行为。
- Experiment 的 environment.prepared/certified 与真实工具一致；实际 run_command 跑了入口；repair 原始 stderr 含埋入的 typo，代码确实修正，再跑得到原始指标。指标核对源 JSON，报告 warnings/delivery_issues，不只看 completed/action_valid。
- trace 目录/文件仍为 0700/0600；凭据检查只报告命中数与是否泄漏，不在报告中贴出可能的密钥。

### 交付

给出提交 SHA、安装指针、逐次结果（含失败）、调用/读取统计、关键 call_id 与事件编号、实际新旧片段及截断证据、原始指标/日志路径、trace 权限及已知缺口。不要仅凭 Pro 成功判定 Flash 失败与代码无关，也不要把本地测试通过等价成真实模型不再循环。
