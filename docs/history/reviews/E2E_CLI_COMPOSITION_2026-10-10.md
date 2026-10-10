# 真实 E2E 共用 CLI 装配：实现与验证

日期：2026-10-10。分支：`fix/native-agent-receipts`；修改基线：`e7610f02435c93604ad3da1d809245d20c68ccb6`。schema 保持 24.0。

## 改动与运行方式

此前真实 E2E 自行创建模型、Agent、Controller 和存储，没有继承 CLI 的模型 Profile、超时、各组件上下文覆盖、网页工具和 PDF 解析配置。这让同一业务代码在测试入口和生产入口的配置不同。

现在 `e2e.real_e2e` 的所有阶段都通过现有 CLI `build_application` 创建应用，删除重复装配和默认值，不增加工厂、配置模型或兼容层。完整研究场景调用应用的 Controller；`code` / `experiment` 定向阶段从 Scheduler 的 binding 取得已装配 Agent，调用其 `invoke`；数据集工件也使用该应用的 ArtifactRegistry。模型配置、工具和资源注入共用 CLI 实现，场景任务、预算、权限和验收断言继续由 E2E 定义。

创建应用本身不发模型请求或扣减预算。定向阶段只调用选定的 Agent，不因此启动完整研究 Run；它们通过也不代表完整研究场景通过。`e2e.mock_e2e` 保留确定性测试替身。

存储统一到 `REAL_E2E_WORKDIR/data/state`、`data/sessions/{coding,experiment,scientific}` 和 `data/artifacts`；定向交付物仍在 `out`。`ask-start` / `ask-resume` 使用同一工作目录和一致部署配置。旧 E2E 布局不迁移、不自动恢复，新验收使用新目录，旧现场保留。此次没有修改持久化 schema。

当前架构、接口、上下文、设计原则及 CLI / 开发 / 测试说明已同步；同时纠正文档中已失效的 Agent 工具“原生与正文 JSON 双路径”说明。Compiler 的结构化 JSON 输出仍属原职责。

## 本地验证

环境：Ubuntu-D，`/home/cyl/ResAgent2`，既有 ResAgent2 conda 环境。本轮只跑确定性检查，未执行付费真实模型、网络材料获取、GPU 或 L3。

| 检查 | 结果 |
|---|---|
| 5 个迁移相关测试文件 | 68 passed，4.27s |
| 新增 `tests/e2e/test_cli_composition.py` | 4 passed，2.75s |
| `python -m pytest tests apps/cli/tests -q` | 2195 passed / 1 skipped，67.31s |
| `python -m e2e.mock_e2e` | `run_golden completed`，13 工件，Coding / Experiment 各一次 |
| `git diff --check` | 通过 |
| 旧独立装配 helper 检索 | `e2e` / `tests` / `apps` 无残留 |
| `python -m pip check` | rc=1：既有 `pdfminer-six 20260107 requires cryptography, which is not installed` |

新增用例核对实际组件的非默认模型、模型容量与输出预留、四个模块不同的上下文上限、超时、trace、搜索、抓取、PDF 解析和目录配置。问答用例走真实 Controller / AgentLoop / 磁盘存储，重新创建应用后恢复，检查原生调用配对、回答入上下文及累计两次模型用量；响应由 ScriptedLLM 提供，不构成真实模型质量证据。两个定向阶段用 invoke spy 证明调用已装配 binding、使用同一工件根及正确授权，HTTP 禁止。

本地依赖缺项属于既有环境差异，此轮没有安装依赖，不能用先前服务器 clean 代替本次本地结果。

## 服务器复核方法

冻结最终分支提交，核对 editable 指针、schema、工作区和部署配置，使用声明安装的项目环境，在仓库根运行以下命令并分别保存输出及退出码：

```bash
python -m pytest tests/e2e/test_cli_composition.py -q
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

预期新文件 4 passed、全量 2195 passed / 1 skipped、mock completed / 13 工件；pip check 以服务器实际环境为准。

如追加真实短场景，使用全新证据目录及 [CLI 部署配置](../../../apps/cli/README.md#6-模型与上下文预算)。下面两条会调用真实模型；保持相同 `REAL_E2E_WORKDIR` 和配置，先得到持久化问题，再提供测试用回答：

```bash
export REAL_E2E_WORKDIR=/absolute/path/to/new-evidence-directory
python -m e2e.real_e2e ask-start
python -m e2e.real_e2e ask-resume 'top-1 accuracy'
```

应核对 paused → completed、同一个 Run / Session、问答入上下文、工具调用配对以及累计账本，不只看命令退出码。真实短场景和科研 L3 尚未在本次修改上运行；既有 L3 走 CLI 的验收范围不因此次入口统一而改变。
