# 原生 Agent 协议与工具回执：服务器测试交接

## 范围与版本

基线是 `main@fbf1a00b46892f5c226855824f0bdf2fd4765124`，分支是 `fix/native-agent-receipts`。实现提交为 `db7a7cf5edd995bbbd87e0f68b8c4c12ca5c881c`。验收分支最新 HEAD（包括本文），开始前记录完整 SHA。schema 仍为 **24.0**；请创建全新 Run。现有原生生产 Session 的协议身份不变，旧正文 JSON Session 不支持恢复，没有兼容层。

本轮检查五件事：Agent 只走原生 function call；不可用工具能收到纠错回执；损坏的 HTTP 压缩正文进入既有失败链；Experiment 保留实际 `run_setup` 命令事实；按实际返回边界分段读取。问答保留原作用域：同 Attempt 续答，新 Attempt 不自动继承旧回答，必要时重新询问。墙钟回拨暂不处理。

这些改动不改变四部分上下文、Compiler/Interpreter 分工或搜索策略。Compiler 仍用结构化 JSON，历史摘要仍用纯文本；原生工具的 arguments 仍须合法 JSON。

设计取舍见 [ADR-0026](../decisions/0026-native-agent-protocol-and-tool-receipts.md)，当前规则见 [CONTRACTS](../../current/CONTRACTS.md) 和 [CONTEXT](../../current/CONTEXT.md)。

## 同步与源码核对

此前服务器 Git 仓库是 `/root/autodl-tmp/projects/ResAgent2`，证据根是 `/root/autodl-tmp/resagent2/runs/`，二者不要混用。先检查 Git 根目录、当前分支与未提交文件；不要覆盖其他实验。fetch 本分支后，切换到对应本地分支并 fast-forward 到 `origin/fix/native-agent-receipts`。若 GitHub 不可达，可用包含最新 HEAD 的 bundle，同样核对完整 SHA。

使用既有验收环境，不必为本轮重建 Conda 环境；进入项目根目录，确认 `python` 来自该环境。以下命令在服务器 Bash 中执行：

```bash
cd /root/autodl-tmp/projects/ResAgent2
git branch --show-current
git rev-parse HEAD
git status --short
python - <<'PY'
import importlib
from pathlib import Path
from resagent2_contracts import SCHEMA_VERSION
from resagent2_runtime import AgentState

repo = Path.cwd().resolve()
for name in (
    'resagent2_contracts', 'resagent2_runtime', 'resagent2_components',
    'resagent2_capabilities', 'resagent2_orchestrator', 'resagent2_coding',
    'resagent2_experiment', 'resagent2_scientific', 'resagent2_cli',
):
    source = Path(importlib.import_module(name).__file__).resolve()
    assert source.is_relative_to(repo), (name, source)
    print(name, source)
assert SCHEMA_VERSION == '24.0'
assert AgentState.model_fields['schema_version'].default == '24.0'
print('SOURCE_AND_SCHEMA_OK')
PY
```

## 离线回归

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

预期：全部测试通过、唯一既有 skip；mock 为 `run_golden completed`、13 工件，Coding/Experiment 各一次完成；服务器依赖与 diff 检查 clean。完整保存各命令输出及退出码，不能用最后一条命令的退出码代替全部结果。

需要定位或单独出证据时，可运行下面的专项；完整回归已包含它们，无需重复凑通过次数：

```bash
python -m pytest -q \
  tests/orchestrator/test_answer_retry_scope.py \
  tests/runtime/test_native_tool_calls.py \
  tests/runtime/test_agent_loop.py \
  tests/runtime/test_llm_recovery.py \
  tests/runtime/test_model_request.py \
  tests/components/test_web.py \
  tests/capabilities/test_web_capability.py \
  tests/experiment/test_completion.py \
  tests/components/test_text_io.py \
  tests/components/test_workspace_context.py \
  tests/capabilities/test_text_windows.py \
  tests/capabilities/test_workspace_text_limits.py \
  tests/e2e/test_native_context_capacity.py \
  tests/e2e/test_scientific_context_capacity.py \
  tests/e2e/test_workspace_read_history.py
```

专项的判断点：

| 路径 | 应证明什么 |
|---|---|
| 用户问答与重试 | Controller 将回答交给原 Attempt/Session；失败后的新 Attempt 无旧答案或 resume refs，可完成或再次提问；旧答案和尝试记录仍保存 |
| 原生工具 | 实际注册工具生成 schemas 并派发；未注册单调用可纠正；含未知工具的批次在副作用前整体拒绝；所有调用都有配对回执，连续失败和调用预算仍有界 |
| 原生容量和恢复 | 计量包含 messages + tools；配对历史、读取时序、工作集及协议身份规则保留；只有 next_action 的客户端不能进入 AgentLoop |
| 坏 gzip | 使用真实 httpx MockTransport 的坏压缩体，无联网或付费；DeepSeek/Tavily 搜索产生 `invalid_response` 失败工件；DeepSeek 请求占用一次且不新增重试；web_fetch 无假正文工件；Run 截止仍透传 |
| 执行事实 | 实际 run_setup/run_shell 成功和失败按事件顺序进入 execution_record；没有 exit_code 的等待审批或阻断不算已执行 |
| 分段读取 | 请求 end_char 不冒充实际末端；超限、恰好等于上限、显式短窗口、Unicode/CRLF、空结果、所选行末尾均准确；按 next_start_char 连续拼接无重漏；工作集再次裁剪明确缺口 |

## 一条真实材料消费任务

只需一个短任务，不要求 GPU 或 L3。它核对本轮单一原生路径下，真实模型能否从冻结材料读取并准确交付；不能代替离线错误边界测试，也不证明科研质量普遍提升。

在独立证据目录保存脚本，以显式绝对路径运行；避免多层 SSH 嵌套插值。使用部署中已有的 DeepSeek 配置，不把密钥写入本文或公共产物。可关闭搜索，以收窄为网页材料链；此配置不是测试工具纠错的替代品。

```bash
mkdir -p /root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009
cat > /root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009/goal.txt <<'GOAL'
请从 https://docs.python.org/3/library/json.html 的官方文档核对并逐字抄录包含 sort_keys=True, indent=4 的多行交互示例（包括输入行和输出）。用读取到的正文支持报告，注明来源；保留原换行、缩进和键顺序，不凭记忆改写。无需运行代码或实验。
GOAL

export RESAGENT2_WEB_SEARCH_PROVIDER=off
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR=/root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009/traces
resagent2 run \
  --run-id run_native_agent_receipts_20261009 \
  --data-root /root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009/data \
  --goal-file /root/autodl-tmp/resagent2/runs/native-agent-receipts-20261009/goal.txt \
  --no-execute-commands --no-prepare-environment \
  --max-llm-calls 16 --timeout-seconds 300
```

这只规定任务与授权，不指定工具调用顺序。若模型提问，按实际字段回答；记录人工介入，不代替它选范围。保留第一次 Run 和所有结果，不能丢掉失败后重跑凑 PASS。

核对实际 trace、Session 和工件：

1. 请求有原生 tools；搜索关闭时不存在 web_search schema，但 web_fetch 仍可用。assistant/tool call ID 配对；没有正文 JSON 动作通路。
2. 模型获取并读取网页正文。冻结工件、read_artifact 回执、最终意见/报告中的同一示例逐字一致；所有原生调用按唯一 Run 账本计量。
3. 网页正文保留换行和四空格缩进，冻结 SHA256 与登记 ref 一致；读取窗口的 next_start_char 符合实际源范围。
4. 比对时以本次实际抓取的官方 HTML 为准，正确移除语法高亮标签后保留文本。此前官方输入为 `{'6': 7, '4': 5}`，输出排序为 4、6；不要拿记忆中的 `{'4': 5, '6': 7}` 当输入基准。若官方内容变化，应记录变化。

此前官方示例供定位：

```python
>>> print(json.dumps({'6': 7, '4': 5}, sort_keys=True, indent=4))
{
    "4": 5,
    "6": 7
}
```

若未完成，记录回执和失败原因再判断产品、网络或模型行为；不得改产品代码或降低断言取得 PASS。真实模型未自然触发未知工具、坏 gzip、retry 或 setup 时如实标注，相关能力由确定性测试证明。

## 证据与本地状态

保存 FROZEN_SHA、Git 状态、源码核对、完整回归日志、Run/Session、冻结工件、实际读取回执和独立复算脚本/输出。公共 MANIFEST 覆盖可公开材料，full trace 和 Session 按现有私有边界留存并另记 hash；不推送私有原始输入或 trace。

报告分别说明协议/事实正确性、真实任务完成情况和材料使用质量。无需清理旧 Conda 环境或服务器根目录杂散文件。

2026-10-09 本地最终验证：`pytest tests apps/cli/tests -q` 为 **2106 passed, 1 skipped**（59.63s）；mock 为 `run_golden completed`、13 工件；`git diff --check` 通过。`pip check` 有既有环境缺项：`pdfminer-six 20260107 requires cryptography, which is not installed`，服务器须独立核验，不能据此写 clean。服务器验收尚未执行。
