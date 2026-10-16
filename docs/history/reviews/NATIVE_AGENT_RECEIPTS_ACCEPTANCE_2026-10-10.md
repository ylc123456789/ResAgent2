# 原生 Agent 协议与工具回执：验收收尾

状态：服务器验收通过，2026-10-10。分支 `fix/native-agent-receipts` 保留，尚未合并。

## 验收对象与结论

冻结提交为 `2ea65d3ca6e0eac5887c57eb0b44957831ceb0bb`，实现提交为 `db7a7cf5edd995bbbd87e0f68b8c4c12ca5c881c`；基线是 main@fbf1a00，schema 仍为 **24.0**。后续本次收尾仅更新文档，不把文档提交称作重新运行过的产品版本。

本轮沿原架构统一 Agent 原生工具调用、删除正文 JSON 动作分支，补齐 HTTP 解码失败、Experiment setup 命令事实和分段读取续读位置。问答作用域按已确认方案保留：同 Attempt/Session 恢复时保留回答，新 Attempt 不自动继承，必要时重新询问；旧记录不删除。没有增加兼容层，也没有重做上下文或科研流程。

验收满足[测试交接](NATIVE_AGENT_RECEIPTS_TEST_PLAN_2026-10-09.md)与 [ADR-0026](../decisions/0026-native-agent-protocol-and-tool-receipts.md)，本轮没有必须补测项。墙钟回拨仍按用户决定暂缓。

## 服务器结果

服务器入口为 `ssh -p 14225 root@connect.cqa1.seetacloud.com`，Git 仓库是 `/root/autodl-tmp/projects/ResAgent2`。证据根保留原目录名：

```text
/root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009/
```

正式报告为其中的 `protocol/NATIVE_AGENT_RECEIPTS_ACCEPTANCE_20261009.md`，报告正文日期为 2026-10-10。环境 Python 是 `/root/autodl-tmp/conda-envs-dev/ResAgent2/bin/python`；9 个包来自源码树，`SOURCE_AND_SCHEMA_OK`。

| 验证 | 服务器结果 |
|---|---|
| 完整回归 | **2106 passed, 1 skipped**，83.67s |
| mock E2E | `run_golden completed`，13 工件，Coding/Experiment 各一次完成 |
| pip check | `No broken requirements found`，服务器独立检查 |
| git diff --check | clean |
| 交接列出的专项 | 367 passed |
| 五个判断点聚合用例 | 206 passed / 0 failed |
| 真实材料任务独立核对 | 17/17 PASS |
| 分段读取独立探针 | 14/14 PASS |

四条离线命令分别保存退出码和输出。专项与完整回归有重叠，不将它们相加为新增覆盖数量。

真实任务 `run_native_agent_receipts_20261009` 为 completed，CLI exit 0，7/16 次模型请求，账本的 7 项均为 succeeded；无人工介入或任务重跑。登记 8 个工件。冻结网页为 40042 字节、1404 行，模型通过 3 次 `read_artifact` 获取片段，最终意见及报告逐字保留 json.dumps 多行示例。

测试方将本次抓取的官方 HTML 去除语法高亮标签后与冻结正文比较，确认原文一致、换行及四空格缩进保留。输入键顺序是 6、4，输出顺序是 4、6，未凭记忆改写。7 次原生调用均有配对回执。搜索关闭时，7 份请求均有 web_fetch 工具说明、没有 web_search 工具说明；这是 schema 可用性检查，不表示执行了 7 次网页抓取。

字符探针使用受控的 1000 字符返回上限：请求 end_char=50000 时仍回显请求值，实际续读点为 next_start_char=1000。只按实际续读点拼接可完整重建 10000 字符原文；Unicode/CRLF 样本也逐字符一致。显式短窗口、恰满上限及所选行耗尽分别按既定契约处理。

## 本次只读复核

2026-10-10 从服务器读取现有报告、公共日志、清单、Run 状态与窗口探针源码，没有重新运行测试或模型请求，没有修改服务器环境或产物。

- 公共 MANIFEST 的 **37/37** 文件重新计算 SHA256，与清单一致；私有清单列 2 项，未下载原始 trace/Session。
- Run 登记的 **8/8** 工件重新计算 SHA256，与 ref 一致；Run 为 completed，账本为 7 次 succeeded。
- 冻结网页、scientific_opinion.statement 与 final_report 中的示例逐字一致。
- 完整回归、专项和四条独立退出码与测试方摘要一致；服务器 HEAD 仍为验收 SHA，产品工作区干净。

17/17 中的实时页面比较、原生历史配对和三次读取细节依据测试方保留的复算输出，本次未再抓页面或重跑复算脚本。公共 MANIFEST 未列正式报告本身；本次单独记录该报告 SHA256 为 `83ce3e482f0e56938cd7a3a9048011a772119aee688e5d1dfb486cb77f34d783`，不将它算进 37 个已核对条目。

## 环境偏差与覆盖边界

首次完整回归在收集阶段因旧环境缺少 pymupdf/pymupdf4llm 而中断。原日志保留在 `logs/regression-attempt1-pdf-missing/`。测试方经用户授权安装项目已经声明的 `pymupdf4llm>=1.28.2,<1.29` 后，环境由 61 增至 70 个 distributions，依赖检查及完整回归通过。产品依赖声明和代码没有因测试而改动；不能将首次环境失败抹掉或说成产品回归。

服务器从原 `fix/code-health@c0a483d` 检出到验收分支，原状态保存在 FROZEN_SHA。测试结束无残留进程、工作区或 stash 变化，未推送服务器数据。原始 full trace 和 Session 按既有私有边界留存。

真实模型未自然触发未知工具、坏 gzip、retry、run_setup 或提问/回答，这些由确定性测试覆盖。真实读取使用行窗口，字符截断与连续重建由独立探针和回归证明。没有运行 GPU/L3、没有验证搜索后端，也没有新旧科研质量 A/B；本次结论是修复行为正确且真实材料消费链可用，不能推广为科研质量普遍提高。

本地此前 pip check 的 cryptography 缺项仍是本地环境事实；服务器 clean 是独立结论，不相互替代。本轮验收不需要再追加产品修改或 L3；分支是否合并另由用户决定。
