# Unified Context 服务器测试交接

> 状态：本地确定性回归与 mock 已通过；本轮服务器及真实模型尚未测试。结构检查不代表模型质量已经提高。

## 版本与同步

- 开发仓库：WSL Ubuntu-D 的 `/home/cyl/ResAgent2`。
- 分支：`feat/unified-context`。
- 基线：`8f9d7256137f5059cb3b642ac63dc395deece493`，即联网搜索完成版。
- 当前实现是该基线上的未提交工作树，未推送、未合并。只同步基线 bundle 会漏掉本轮修改。
- 冻结基线加完整补丁，并包含 `git ls-files --others --exclude-standard` 列出的新增文件；记录版本、补丁 SHA256、文件清单及同步后差异。不要覆盖服务器已有实验或未提交修改。
- 服务器仓库路径以实际 Git 根目录为准；此前使用 `/root/autodl-tmp/projects/ResAgent2`，证据目录与 Git 仓库分开。
- schema **24.0**：使用全新 Run/Session。23.0、缺版本或其他版本的旧记录应明确拒绝恢复，不迁移、不兼容；旧证据只读保留。
- 保持原模型、工具、预算和环境配置；启用 `RESAGENT2_LLM_TRACE_LEVEL=full` 核对实际 messages/tools。不打印认证信息。

正式说明见 [CONTEXT](../../current/CONTEXT.md)、[CONTRACTS](../../current/CONTRACTS.md)。

## 本地已验证

2026-10-07，Python 为 `/home/cyl/miniconda3/envs/ResAgent2/bin/python`：

- `python -m pytest tests apps/cli/tests -q`：**2014 passed, 1 skipped**。
- `python -m e2e.mock_e2e`：`run_golden completed`，13 个工件，Coding/Experiment 各一次完成。
- `git diff --check`：通过。
- 生产代码与测试没有 `research_materials`、`literature_output_artifact_ids` 或 `web_output_artifact_ids` 引用。
- 本地 `pip check` 有既有环境缺项：`pdfminer-six 20260107 requires cryptography, which is not installed`。服务器应独立检查，不能沿用此前 clean 结论。

## 服务器预检

先核对 schema、源码路径、9 个 editable 包及依赖，不改产品代码来取得 PASS。在项目根目录使用测试环境的 Python：

```bash
python -m pip check
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
```

专项诊断入口：

```bash
python -m pytest -q \
  tests/components/test_request_task_context.py \
  tests/components/test_artifact_index_context.py \
  tests/runtime/test_resume.py \
  tests/e2e/test_public_native_lifecycle.py \
  tests/e2e/test_runtime_resources.py \
  tests/e2e/test_semantic_handoffs.py \
  tests/e2e/test_scientific_context_capacity.py \
  tests/e2e/test_tool_surface.py
```

完整回归已包含这些测试；只有失败或需要定位时才重复专项。

## 场景 A：三 Agent 的问答与恢复

真实链路覆盖 Scientific、Coding、Experiment 的 `ask_user` 暂停及恢复，至少一个作用域包含两次问答。目标可设计为需要用户选择研究范围或执行参数，不强制模型按指定工具顺序工作。

核对每个恢复后的实际请求：

1. 原 instruction 保留；任务区有原问题、结构化回答、时间和工件 ID，前一次回答不会被第二次覆盖。
2. Scientific 只读本 Session 的回答；Coding/Experiment 只读本 Task/Attempt 的回答。异作用域回答不得混入任务需求。
3. answer 正文不再重复进入 `material_<answer_id>`。近期原生历史和目录中仍出现答复回执或 answer ID 是正常的，不能误判成正文重复投影。
4. `request_work` 仍是普通工具调用，完成交接进入原生回执及当前反馈，不混入任务问答，也不新建任务需求记录。
5. 待确认动作、环境/执行反馈、权限及单次审批机制继续有效。

拒绝错误作用域、错误冻结 hash，以及旧 schema 恢复的检查由确定性测试覆盖，不必修改真实 Run 伪造这些条件。

## 场景 B：材料工具与统一目录

给 Scientific 一个同时可能需要论文和官方网页的真实问题，可附一篇论文与本地 PDF；自然运行，不规定 search/fetch/read/request_work 的固定顺序。实际没有触发的能力标注“未触发”，用已有确定性测试说明，不能冒充自然覆盖。

在首次搜索、全文获取、读取、工作交接及进程恢复之后抽取请求快照，核对：

1. 文献/网页工具仍按原契约登记和冻结产物。Registry 是权威；`memory_updates["artifact_index"]` 只维护模型目录，不是另一个登记系统。
2. 唯一 `artifact_index` 的条目集合等于该调用授权的 `input_artifacts` 与 Session 中已登记工具输出目录的并集，按 ID 去重；保留旧条目与新增条目。它不等于整个 Run 的无条件公开列表。
3. Scientific 仍校验冻结 ResearchIndex，groups 只列 `artifact_ids`；不再发送另一份含同样条目的完整目录。目录含 compact 类型、摘要、归属、执行状态及直接来源关系，不携带 URI、hash、权限或全文。
4. `fetch` 新增独立正文/PDF工件并维护原来源链；`read_artifact` 只返回读取片段和范围，不创建正文副本。搜索预览、摘要、全文和实际读取范围保持可区分。
5. 近期读取内容进入配对工具历史及当前读取投影；上下文缩减明确标注范围/省略。没有读取的部分不能成为“已读全文”的依据。
6. 非工件的工作区文件仍由已有文件工具和工作区投影处理，不因读取而自动登记。
7. 独立重算冻结工件 SHA256；最终引用和交付仍指向本 Run 的真实登记工件。

## 场景 C：压力与原生协议

确定性测试已覆盖完整目录预算不足时的明确失败。另做一条受控的真实原生 Session，降低该次实验的输入容量以触发既有压缩机制；保持产品代码和模型不变，并记录这一实验配置。

核对：

- 每个 assistant tool-call 均有正确 call ID 的配对 tool receipt；发送边界不切开一轮。
- 压缩只覆盖较早完整回合，保留后续回合及未完成调用；原 Session 原始事件不删除。
- checkpoint 是历史摘要，不是科研证据。较早读取正文可能不再原样发送，需要时可重读。
- 压缩和进程恢复后，任务问答及完整目录仍重新构建；目录不被摘要替代、不丢条目。
- 完整工具 schema、固定契约、任务需求和目录装不下时明确报容量错误，不静默截断目录。
- 旧轮完整领域 prompt 不累计到原生历史；以实际 `messages + tools` 为准，而不只看 included_sections。

## 证据与结论

保存回归日志、实际 messages/tools 的私有 full trace、Run/Session 状态、冻结工件、读取范围、checkpoint 边界及独立复算输出；记录 MANIFEST 和每项是否自然触发。公共报告只放必要脱敏信息。

分开报告：结构/协议正确性、真实运行是否完成、模型使用材料的质量。重点观察任务理解、材料选择、证据边界、重复调用与目录可发现性。若比较新旧质量，应在隔离环境对 `8f9d725` 与本轮快照使用相同目标/模型/预算，保留全部结果；不能只凭目录更统一或一个成功 Run 宣称质量提高。
