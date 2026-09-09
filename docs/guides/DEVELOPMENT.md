# 开发与验证

用一次小改动熟悉项目，不需要先读完历史。整体认识看 [理解一次研究任务](UNDERSTANDING.md)，规范查 [架构](../current/ARCHITECTURE.md) 和 [接口与契约](../current/CONTRACTS.md)。

## 1. 本地环境

从仓库根执行，environment.yml 会 editable 安装项目包和 CLI：

```bash
conda env create -f environment.yml
conda activate ResAgent2
resagent2 --help
```

已有环境按仓库 environment.yml 同步依赖。模型、数据集、资源路径的配置集中在 [CLI README](../../apps/cli/README.md)，本地确定性测试不需要真实模型。

安装后的 `*.egg-info` 是 Python 包元数据，不是业务代码。`build/`、`__pycache__/`、`.pytest_cache/` 也不是需要阅读的实现；源码在各包 `src/resagent2_*` 下。不要改构建副本，不能用删除元数据代替修正安装指针。

<a id="local-checks"></a>

## 2. 先跑不依赖真实 LLM 的检查

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
```

mock 不调用真实模型，默认把 Run 数据放入独立系统临时目录。若要自己掌握记录位置，可从新目录调用它的程序化入口（Linux/WSL）：

```bash
repo_dir="$PWD"                  # 此时位于仓库根目录
check_dir="$(mktemp -d)"
cd "$check_dir"
PYTHONPATH="$repo_dir" python -c 'from pathlib import Path; from e2e.mock_e2e import run_mock_e2e; run = run_mock_e2e(workdir=Path.cwd()); assert run.status.value == "completed"; print(run.run_id, run.status.value)'
# state/ 和 artifacts/ 位于 check_dir；检查后自行决定如何保留。
cd "$repo_dir"
```

测试布局见 [tests README](../../tests/README.md)。通过说明已覆盖边界成立，不意味着真实 LLM 永远选择正确行动。

## 3. 改一个行为，去哪个位置

| 想改的内容 | 优先修改 | 同时检查 |
|---|---|---|
| 命令和显示 | apps/cli | 仍只调用 Controller，不加第二套调度 |
| 科学判断或证据呈现 | agents/scientific | prompt / interpreter / completion 的不同职责 |
| 代码修改策略 | agents/coding | 真实变更、当前版本验证和失败记录 |
| 实验执行或指标 | agents/experiment | 命令记录、完整证据集和 warnings |
| Run、图、暂停恢复 | orchestrator | 身份、状态、重复交付、消费保留 |
| 文件、环境或进程 | capabilities | 所有同语义消费者，别复制进另一 Agent |
| 上下文、反馈、工具派发 | runtime | 三个 Agent；Compiler 是否经相同适配受影响 |
| 公共字段和组合规则 | contracts | 生产/接收端、持久化、恢复与版本 |

先找现有组件，能补一个函数或判据就不新增框架。内部算法未变更外部约定时，调用方不应被迫一起改。

## 4. 怎样验证接口修改

先在 [调用边界](../current/CONTRACTS.md#boundaries) 找输入、分支、状态归属和失败规则，再补测试：

- 正常返回能接收；错误身份、错 payload 和矛盾状态会拒绝。
- 暂停回答继续同一次尝试；真正 retry 才新建 Attempt。
- 失败保留实际消费、原始错误、Session 和已有工件，不伪装成功。
- 重复交付不误消费下一题/工作结果；跨 Run 不串证据。

Protocol 描述方法形状，不保证实现守约。替换 Port 必须通过行为测试，不仅让类型检查通过。

## 5. 什么时候需要真实服务器验收

纯文档导航通常不需要。影响模型看到的 prompt、工具参数、上下文、编译或执行链时，需要在确定性测试后安排真实验收。

保留准确 commit、干净 checkout、editable import 指针、模型和显式预算。CLI/E2E 是独立组合根，改装配不能只测其一。fresh workdir 保留成功/失败现场，不覆盖失败、不重跑到绿后只报最后一次。

验收不只是 rc=0：读原始请求/响应、Session 观测、stdout/stderr、工件和最终状态，核对目标行为是否发生。action_valid 不证明参数、执行或科学结论正确。

full trace 可能含源码和用户输入，仅在授权下查阅；不打印 API key。保留记录与清理环境是不同操作，清理须另行核对具体目标。

## 6. 文档与提交

1. 明确本次行为范围，不顺手做相邻新需求。
2. 改代码与测试；按 [维护表](../README.md#maintenance) 同步当前参考和示例。
3. 重要取舍追加 ADR；阶段计划/验收放 history/reviews。普通修复不强制写长报告。
4. 检查 diff 不含构建副本、产物和无关用户修改，再做 scoped commit。合并、推送和部署按当次授权执行。

历史用于解释取舍，不要求新开发者按旧 Phase 重新经历项目。测试总数和服务器证据查 [历史索引](../history/README.md)，不在每份教程重复数字。
