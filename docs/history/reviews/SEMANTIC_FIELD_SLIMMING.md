# 三组自然语言字段精简：实施与小型验收

日期：2026-09-16。对照基线：`f32f2e6`（schema 8.0）。
产品提交：`c03115b`，分支 `refactor/semantic-field-slimming`，schema **9.0**。

状态：本地实现与确定性验证完成；真实模型小型对照尚未执行，未合并、未 push。不能把“没有独立的代码消费者”直接等同于“删除字段不会影响生成质量”。

## 1. 这次究竟删什么

| 范围 | 删除的填写项 | 信息放在哪里 |
|---|---|---|
| Scientific 完成 | ScientificFinish.summary | 模型只写 opinion；完成检查通过后，Runtime summary 直接取 opinion.statement |
| 三个 Agent 提问 | AskUserToolInput / AskUserInput / QuestionDraft.reason | 问题 text 包含回答所需背景；requested_fields、原题配对和恢复作用域不变 |
| Compiler 图级说明 | CompilationDraft.summary/rationale、WorkflowProposal.summary/compilation_rationale、WorkflowPatch.reason | WorkRequest 表达本轮目的；任务保留 goal、constraints、typed inputs 和 depends_on |

不是删除所有自然语言，也不是让用户失去可读说明。Coding/Experiment 的结果 summary、Artifact 摘要、工具 receipt、诊断 reason、CompilationReview.issues 与纠错反馈全部保留。ResearchRequest.goal/hypothesis/context/constraints、证据要求、指标身份和环境安装策略不在本轮范围内。

实现只用现有类和调用链：

- Scientific AskUserInput 继承 Runtime 的 AskUserToolInput，仅增加 assessment；text 的字段描述经既有原生工具 schema 送给三个 Agent。
- Experiment 的代码生成确认问题也把“已启用运行前确认”放进用户可见的正文，不丢掉原 reason 的背景。
- Compiler 仍是 JSON-only 草案 → 确定性物化/校验 → 语义评审 → 有界纠错。没有删除评审，没有新推理提示、新模型调用或替代说明对象。
- 不改状态机、Session、预算、上下文分配和完成门禁。CLI 与 E2E 仍是独立组合根，只同步 mock 输入。

公开模型删除字段，所以升级 schema 9.0。旧 8.0 Run 不续跑、不迁移；旧文件原样保留。没有剥离旧字段或 extra-ignore 兼容层，误传旧字段由现有校验/反馈处理。单独 Session 的解析不等于支持旧 Run 恢复，见 [版本边界](../../current/CONTRACTS.md#schema)。

当前规范：[接口与契约](../../current/CONTRACTS.md)、[上下文](../../current/CONTEXT.md)。本记录补充此前 [语义交接阶段](SEMANTIC_HANDOFFS_ACCEPTANCE.md)，不改写旧验收结论。

## 2. 本地已验证

- 全量：**1027 passed, 1 skipped**；相对基线 1015/1 增加 12 个测试实例。
- `python -m e2e.mock_e2e`：completed，最终报告生成；`git diff --check` 干净。
- Scientific 不填 summary 也可完成，运行时摘要与 opinion.statement 一致；多填旧 summary 被拒。
- 两种原生 ask_user schema 共用正文背景说明；无 reason 的问题可暂停并保留 assessment，requested_fields 仍必填非空。现有答案配对、作用域与恢复测试继续通过。
- 仅 tasks 的草案仍经过 draft/review，Proposal/Patch 的任务目标、约束、依赖和 instructions 保留；旧图级字段被拒，空图/非法依赖等原门禁继续通过。
- schema 8.0 Run 加载失败后原文件字节不变。

相关测试：
[字段边界](../../../tests/contracts/test_models.py)、
[Scientific 工具](../../../tests/scientific/test_scientific_agent.py)、
[完成检查](../../../tests/scientific/test_completion_evidence.py)、
[Compiler](../../../tests/orchestrator/test_compiler.py)、
[旧记录只读](../../../tests/orchestrator/test_persistence_and_artifacts.py)。

## 3. 交给测试 AI：小范围真实验收

### 3.1 纪律、身份与基线

只同步、测试、分析、报告。不改产品/prompt/目标/额度，不合并、不 push，不恢复旧 L3；不下载数据集或安装 torch，不执行生成的研究任务图。

新版本同步此分支实际 HEAD，记录产品提交与文档提交；旧对照固定 f32f2e6。两版独立干净 worktree、独立工作目录/trace。运行前核验 8 包 import 路径与模型/Profile；记录安装指针，不静默修改旧环境。每版用自己的模型类从相同业务输入新建对象，不能把旧 schema JSON 强行改版本号加载。

在新版本安装对应 editable 包后，隔离 cwd 运行（把路径换成实际 checkout）：

```bash
cd /tmp
PYTHONPATH=/path/to/checkout python -m pytest /path/to/checkout/tests /path/to/checkout/apps/cli/tests -q
PYTHONPATH=/path/to/checkout python -m e2e.mock_e2e
git -C /path/to/checkout diff --check
```

预期 1027 passed / 1 skipped。新 Run 与新 Session 从零开始，旧报告/trace 保留。真实调用需按已有纪律先确认费用；下面是小探针，不是完整 L3。

### 3.2 Compiler 新旧对照（重点）

冻结下列两份 WorkRequest 的业务内容、registry、workspace descriptor、模型/Profile 和预算；每版均通过既有 PromptLLMClient → LLMWorkflowCompiler 正常入口编译，不能手写替代评审或只校验预制 JSON：

1. **新图**：已有项目缺少 add(a,b) 实现。实现加法并进行单元验证，再由实验任务运行已有脚本采集结果文件；不得改数据划分，失败时保留日志。应先 code_modify 后 experiment_run，带成功依赖；“失败时留日志”不应变成预先安排的修复任务。
2. **追加修复**：上一轮实验已因变量 totla 拼写错误失败。修正并验证，再重新运行采集证据。current 包含上一轮失败任务，只生成新的修复/重跑任务，不引用旧 Task 作为新任务依赖、不覆盖旧历史。

Flash：两例 × 新旧版本，各一次。Pro：新图 × 新旧版本，各一次。共 6 个只编译探针，正常每个 draft/review 两次调用；单探针 remaining_calls=4，保留已有有界纠错和真实尝试计量，不循环重跑到绿。输入上限两版均 128000；使用相同明确记录的 ModelProfile/输出额度，勿顺带调整 thinking。

逐份保存请求、草案、评审、物化结果与调用账本。不能用删除字段后字节不相等来判失败，也不能只看“合法 JSON”判通过：

- 目标/证据条件没有遗漏，任务分工和依赖正确，约束落在对应任务上，没有凭空扩张范围。
- 新版草案仅含 tasks，工具/提示/schema 不要求旧字段；Proposal/Patch 也没有旧图级说明。
- review 确实看到了 WorkRequest、任务 goal/constraints/inputs/依赖；issues/拒绝纠错仍有效。
- 计量与 trace 对齐，分别报告格式合法、语义质量、纠错次数；不承诺两版生成相同图或相同调用数。

若新版出现目标遗漏或评审退化，保留失败并回报，先决定是否保留 rationale；不要加 prompt 补丁、暗扩预算或反复重跑掩盖结果。有限样本通过只支持这次精简的采用，不证明永久等效。

### 3.3 提问与完成（只测新版本）

用既有 Agent/Controller 入口，小型标准库夹具，三种问答各一次；回答读实际 requested_fields，不猜字段名。单个小任务 max_llm_calls=20、timeout_seconds=1200，不改变全局配置：

- Scientific：未给定偏好，在 Accuracy/F1 中询问，跨进程回答“第二个”。CLI run → show → answer 一并冒烟，最终意见对应 F1。
- Coding：未给定待解释文件，在 helper_a.py/helper_b.py 中询问；回答“第二个”，读取并解释 helper_b。无需代码改动/GPU。
- Experiment：未给定计算模式，在 add/mul 中询问；回答“第二个”，真实运行标准库脚本得到乘法结果 6，不是加法结果 5。

核对原始 messages/tools：ask_user 参数没有 reason、问题 text 给用户足够背景、requested_fields 非空；Scientific 仍带 assessment。问题正文原样进入 PendingQuestion/RecordedAnswer，恢复沿用原 Session；回答确实影响行动，不能以“恢复接口返回成功”代替验证。

另跑一次 Scientific direct（不要求额外证据）：finish 只提交 opinion，没有 summary；CLI/最终报告仍能读到同一结论。Runtime 摘要派生已由上述确定性测试核对；它是 Loop 返回的 ModuleResult.summary，不是 ScientificCompletedResult 或 Session 的独立顶层字段，不要求从持久化文件找一个不存在的 summary。建议同为 20 调用/1200s 上限，不强制模型用满。

无需重新进行 GPU code-experiment/完整 L3。若这组小探针出现真实产品缺陷，再按证据决定扩大范围。

### 3.4 报告

单独验收根保存 MANIFEST、logs、full traces、workdirs、ops。原始消息可读但不公开泄露；沿用 trace 0700/0600 与凭据扫描，不把 key 写进命令/脚本/报告。

报告分开写：确定性契约是否正确、模型是否按新字段行动、工具是否真实执行、任务是否完成。保留第一次失败与所有纠错，区分外部故障与产品问题；不以一次成功证明“模型漂移永久消除”。本轮没有文献/网络检索或训练依赖，出现额外安装应先检查测试装配。
