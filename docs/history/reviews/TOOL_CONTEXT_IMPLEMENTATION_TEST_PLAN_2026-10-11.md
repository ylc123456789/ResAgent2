# 工具上下文与完整工件目录：实施和服务器验证

日期：2026-10-11。实现基线为 fix/native-agent-receipts @ ecaede9e80802cd1b6a269bfe91a46b9ae0ab4d7，公共 schema 从 24.0 升到 25.0。本轮未合并。设计以 [已确认方案](TOOL_CONTEXT_REDESIGN_AGREED_2026-10-11.md) 和 [当前上下文](../../current/CONTEXT.md) 为准。

后续验收状态：冻结 `5d28f74` 已完成服务器 §1–§5 与 L3，结果及发现见[验收及问题记录](TOOL_CONTEXT_ACCEPTANCE_2026-10-11.md)。产品链路通过；研究按原规程评 9/10、有人协助完成，严格数值核对为 86/88，不能写成全部通过。下文保留本地开发结果与原测试步骤，不代表服务器目前仍待启动；本轮仍未合并。

## 1. 先确认版本和环境

测试前记录分支、完整 SHA、git status。确认所有 editable 导入落在待验收源码，SCHEMA_VERSION 和 AgentState.schema_version 均为 25.0。按项目声明安装依赖，不另做供应商或模型装配；CLI 和真实 E2E 共用 build_application。旧 24.0 Run/Session 必须拒绝恢复，旧证据原样保留。

不在旧 L3 Run 上续跑；创建新 Run，保留中性基线、固定数据集切分和独立证据目录。模型配置、真实预算和权限按已有验收规程冻结，测试方不得替模型决定算法、材料或结论。

## 2. 离线回归

在仓库根目录、项目环境中分别执行并留完整输出与真实退出码：

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

本轮工件数量会因删除控制/搜索/总体报告工件而变化，不沿用旧 mock “13 工件”硬编码。应检查 completed、真实 Task 报告与交付物、唯一 opinion/final_report 及无旧工件 kind。

重点定向入口：tests/runtime/test_unified_context.py、tests/components/test_artifact_index_context.py、tests/components/test_request_task_context.py、tests/components/test_operation_permissions.py、tests/orchestrator/test_work_interpreter.py，以及登记恢复、控制重放、材料获取、原生真实 Store 端到端测试。不把历史版本数字当本版预期。

### 本地开发验证结果（2026-10-11）

下列结果在 `/home/cyl/ResAgent2`、项目原有 Conda 环境中执行；9 个 editable 包均经独立检查指向该源码树。

| 验证 | 实际结果 |
| --- | --- |
| `python -m pytest tests apps/cli/tests -q` | 2115 passed、1 skipped，64.74 秒，退出码 0 |
| `python -m e2e.mock_e2e` | run_golden completed、8 个工件；Coding/Experiment 各一个 completed Attempt，退出码 0 |
| `python -m pip check` | 退出码 1：既有 pdfminer-six 20260107 缺少 cryptography；本轮未调整环境，不宣称依赖检查通过 |
| `git diff --check` | 无输出，退出码 0 |
| 源码、schema 与文档核对 | 9/9 源码导入；contracts/Session 均 25.0；四份 current 文档 UTF-8、代码围栏及 109 个本地链接目标通过 |

原工作集、问答/控制工件及旧索引快照的测试随对应实现删除或替换；新版新增原生消息四部分、完整目录、无工件搜索、直接控制、原调用批准恢复、登记/接收中断、终态重入和编译失败后新 WorkRequest 等断言。测试总数变化不能直接解释为覆盖率变化，服务器应执行当前提交的完整测试集。

本地没有运行付费模型、服务器场景或 L3；以下步骤仍须在新冻结提交上独立完成。

## 3. 真实材料消费

使用现有 CLI 新建短材料任务，要求读取 https://docs.python.org/3/library/json.html 并逐字给出带 sort_keys=True, indent=4 的多行例子。搜索后端可按现有规程关闭，以固定覆盖 fetch → 冻结 → read_artifact；工具选择和读取范围仍由模型决定。

核对原生请求四部分：真正 system 身份；完整任务问答；成对历史；只有目录的第四部分。正文只在 read 回执，不重复成工作集。核对源 HTML、冻结 Markdown、读取回执、意见和最终报告中的例子，保留原换行、空格及键顺序；不凭记忆构造官方基准。

文献任务另验证 search 不新增工件，候选携准确 fetch_source，显式 fetch 后登记 paper/PDF/fulltext 的实际成功部分和来源链。全文下载或解析失败不得伪造成功。精确 DOI/arXiv/OpenAlex 解析可由确定性测试证明，不要求真实模型自然触发所有坏响应。

## 4. 问答、工作交接与恢复

沿现有三 Agent 场景覆盖至少两次同作用域业务问答、一次真实执行批准和一次 request_work：

- 问题/答案无工件，完整原题/选项/值累计在第二部分；跨 Task/Attempt/Session 隔离。
- 批准不进入第二或第四部分，绑定原准确命令和环境，原调用最终一个回执；不靠模型重复命令执行。
- request_work 只有 accepted + work_request_id；完成后目录 WorkRequest → Task 展开各真实 Attempt 的原报告和交付物，不生成总体报告或旧控制/index 工件。
- 失败旧报告保留；编译无 Task 失败如实显示；报告正文与 AgentResult.report 按字节一致，不自动折行。
- 原生 call/receipt 一一配对；批次批准前已完成项不重放、后续项 not_executed。

登记/接收中断、供应商重复 call ID、合同指纹改变等危险边界由确定性故障注入测试覆盖。不要为了真实触发而破坏服务器证据或重放未知副作用。

## 5. 压缩

按现有规程在独立 Run 降低 Agent 输入上限，引导真实读取和多轮工具调用。记录实际 plan_compaction、history_start、summary 和原 tool_turns。只有第三部分缩短；完整业务问答和目录逐项不丢、无 URI/hash/正文泄漏，原始事件不删除。固定契约/任务/目录自身超限应明确失败，不静默选前 N 条。

## 6. L3 研究质量

结构及短材料验证通过后，按既有 L3 校准规程独立跑新冻结 SHA。比较采用相同任务、模型配置、预算、基线与切分；保留模型自主选择差异。独立复算逐样本概率、accuracy/NLL/ECE、温度参数、freeze 与唯一 test 访问时序，重放前先移除旧预测文件并检查实际退出码，避免复制文件产生假阳性。

记录完成类别、人工帮助范围、调用/失败/压缩次数、材料使用及限制；单 seed/单 Run 不能证明平均质量提升。执行批准次数和业务问答次数分别统计，避免批准污染“需求变更”统计。

## 7. 留证

沿既有证据目录、公共/私有 MANIFEST、sha256 和报告方式落盘，不新建另一套测试平台。Session/full trace 按现有私有边界保存。环境调整、初次失败、探针预期错误和无效尝试如实记入报告；测试完成确认无残留进程，产品工作区没有测试写入。服务器测试、GPU/付费模型和 L3 仍由测试方执行，本轮开发不声称已完成这些真实验证。
