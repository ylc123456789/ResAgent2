# P3：网页转换与分段读取服务器测试交接

## 范围和状态

在 `fix/native-agent-receipts` 上，从 `74a59fc7813f779e1af269083769b5855af5dfb6` 继续修改；开始验收时冻结该分支最终完整 SHA。schema 保持 **24.0**，不合并 main。本轮只替换网页转换器、补读取范围事实并澄清工作区搜索覆盖，不调整搜索 provider、Agent 分工、四部分上下文或工件登记方式。

实现与取舍见[阶段记录](P3_RUNTIME_AND_READING_REVIEW_2026-10-10.md#后续实施网页转换搜索提示与读取范围)，当前契约见 [CONTRACTS](../../current/CONTRACTS.md#text-io) 和 [CONTEXT](../../current/CONTEXT.md#literature)。前阶段 2169 测试通过与原生协议的服务器验收是独立证据，不能替代本轮验收。

2026-10-10 本地最终回归：**2207 passed / 1 skipped**（59.32秒，退出码0）；网页专项97 passed。mock 为 `run_golden completed`、13 工件，Coding/Experiment 各一次完成，退出码0；`git diff --check` clean。本地 `pip check` 退出码1，仍有既有 `pdfminer-six 20260107 requires cryptography, which is not installed`，不写成本地或服务器依赖 clean。

本地另用生产 `WebPageFetcher` 实时获取 Python 官方 json.html，并以同一次响应的原始 HTML 中对应 `pre.get_text()` 为基准，确认含 `sort_keys=True, indent=4` 的五行示例在返回 Markdown 中逐字保留，包括四空格缩进。观察到 parser 为 `markdownify/html.parser`、正文45523字符；这是组件级公开网页验证，没有调用 LLM、GPU，也没有验证服务器完整 Agent/工件消费链。**服务器复测和 L3 尚未执行。**

## 1. 同步、依赖和身份

产品仓库使用 `/root/autodl-tmp/projects/ResAgent2`；证据另存 `/root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/`，不要混用。先检查服务器仓库、分支和未提交文件，不覆盖其他工作。同步 `origin/fix/native-agent-receipts`，fast-forward 后记录完整 SHA、Git 状态与环境；测试期间不拉取或编辑产品代码。

激活既有验收环境，在仓库根目录安装本轮声明依赖，不必重建 Conda 环境：

```bash
cd /root/autodl-tmp/projects/ResAgent2
python -m pip install -e packages/components
git branch --show-current
git rev-parse HEAD
git status --short
```

这从 `packages/components/pyproject.toml` 解析 `beautifulsoup4>=4.15,<5` 和 `markdownify>=1.2.3,<1.3`，不手工绕过依赖。保存安装输出；若依赖安装失败，先记录环境阻断，不通过改产品或删除测试绕过。

核对9个 editable import 的实际来源、schema 和依赖版本：

```bash
python - <<'PY'
import importlib
from importlib.metadata import version
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
for dependency in ('beautifulsoup4', 'markdownify'):
    print(dependency, version(dependency))
print('SOURCE_SCHEMA_AND_DEPENDENCIES_OK')
PY
```

## 2. 离线回归和边界

分别保存以下命令完整输出和退出码，不能只看最后一个命令：

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

应全部测试通过且只有既有 skip；mock 为 `run_golden completed`、13 工件；服务器依赖和 diff 检查须独立 clean。服务器结果与冻结提交的本地最终数字核对，差异要解释。

定向证据需要时可运行以下专项；它们已包含在完整回归中，不必重复凑通过次数：

```bash
python -m pytest -q \
  tests/components/test_web.py \
  tests/capabilities/test_web_capability.py \
  tests/components/test_text_io.py \
  tests/components/test_workspace_context.py \
  tests/capabilities/test_text_windows.py \
  tests/capabilities/test_workspace_text_limits.py \
  tests/e2e/test_tool_surface.py
```

| 判断点 | 验收含义 |
|---|---|
| HTML→Markdown | 标题、普通段落、合法链接、代码块均转换；HTML parser 为 markdownify/html.parser；text/plain 原文与 plain parser 不变 |
| 四类原网页问题 | 未闭合 script/style/noscript/template/title → parse_failed；链接包代码块不压平正文；SVG/MathML title 不污染页面标题；嵌套 pre 之间有分隔 |
| 代码与链接保真 | 代码缩进、换行、空行、嵌套 code/span、pre 内 br；相对链接按 final URL 解析，非法 href 不进入输出 URL，合法链接仍可发现 |
| 原抓取边界 | 公网地址、重定向、字节/时间/编码限制不变；解析失败无网页工件；Run 期限不冒充网页超时；不执行 JS、自动取链接或 PDF |
| 读取范围事实 | 空文件 total_lines=0、selected_chars=0；非空文件的越界行窗口 total_lines 非零且 selected_chars=0；字符越界不改变所选行字符总数 |
| 实际续读 | 行范围不变，按 next_start_char 接续可无重漏重建 Unicode/CRLF 和超长单行；不得把请求 end_char 当实际返回末端 |
| 上下文投影 | total_lines/selected_chars/next_start_char 经共享工作集保留；内容再裁剪时只改变展示与 context_truncated，不改源范围事实 |
| 搜索覆盖提示 | 恰好达到和超过 max_results 都提前停止，余下范围未检查、总匹配数未知；保留 incomplete、跳过文件及原因，不假定一定还有更多匹配 |
| 工具说明 | 三个受影响工具的 schema/说明指纹同步；实际工具指导与回执语义一致 |

前阶段 `_trim_json`、Session 加载和 TraceTail 修复继续由完整回归覆盖。如需单独复核，沿用[原阶段方案](P3_RUNTIME_AND_READING_REVIEW_2026-10-10.md#验证与服务器交接)。本轮不保留旧 HTML 转换器作为备用路径。

## 3. 一条真实网页材料消费任务

网页展示格式改变，需要一个短任务确认真实模型仍能从冻结正文按需读取，并准确引用。关闭搜索可收窄为网页材料链；这不是搜索质量测试，也不规定工具调用顺序。不需要 GPU 或 L3。

使用全新 Run，在独立证据目录保存以下任务和 CLI 配置；API key 沿部署现有安全配置载入，不写进 goal 或公共产物：

```bash
mkdir -p /root/autodl-tmp/resagent2/runs/p3-web-reading-20261010
cat > /root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/goal.txt <<'GOAL'
请从 https://docs.python.org/3/library/json.html 的官方文档核对并逐字抄录包含 sort_keys=True, indent=4 的多行交互示例（包括输入行和输出）。用读取到的正文支持报告，注明来源；保留原换行、缩进和键顺序，不凭记忆改写。无需运行代码或实验。
GOAL

export RESAGENT2_WEB_SEARCH_PROVIDER=off
export RESAGENT2_LLM_TRACE_LEVEL=full
export RESAGENT2_LLM_TRACE_DIR=/root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/traces
resagent2 run \
  --run-id run_p3_web_reading_20261010 \
  --data-root /root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/data \
  --goal-file /root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/goal.txt \
  --no-execute-commands --no-prepare-environment \
  --max-llm-calls 16 --timeout-seconds 300
```

已有配置若必须通过 `apps/cli/run-configured.sh` 载入，可将上述 export 保存进本 Run 的 cli-config.sh，并经该入口执行同样的 run 参数。记录实际入口、模型、配置有无和所有人工回答，不将测试方复算脚本暴露给 Agent。不删除失败现场后重跑凑 PASS。

核对冻结工件、原生回执、Session、final_opinion 和报告：

1. 网页成功登记为 web_page，source/final URL 与 parser 正确，冻结 SHA256 与登记 ref 一致。正文是 Markdown；不要把格式变化误报成整页损坏，也不要声称整个页面与旧纯文本逐字相同。
2. 模型实际 read_artifact 的回执包含目标示例；total_lines/selected_chars 来自冻结文本，next_start_char 与实际范围一致。索引展示与原生 assistant/tool 配对正常，搜索关闭时请求中没有 web_search schema。
3. 冻结 Markdown 中的目标代码正文、读取回执和最终意见/报告保持相同五行与缩进。独立保存本次官方 HTML，定位对应 pre，移除语法高亮标签后保留文本，用真实原文逐字符比较；排除 Markdown 围栏本身和围栏的分隔换行，但不要 strip 正文空格、重排键或用模型答案当基准。 原始 HTML 若由另一次独立请求取得，记录响应时间与页面变化，不能把更新后的页面差异直接判为提取错误。
4. 原生调用数与唯一 Run 用量账本对齐；请求或读取失败如实报告。未自然触发坏 HTML、搜索上限或越界读取时标注未触发，由确定性测试证明相关边界。

此前官方示例仅供定位，以本次实际页面为准；官方内容变化时记录变化：

```python
>>> print(json.dumps({'6': 7, '4': 5}, sort_keys=True, indent=4))
{
    "4": 5,
    "6": 7
}
```

## 4. 证据与后续 L3

保存完整 SHA、Git 状态、安装记录、源码/schema/版本核对、四项回归输出与退出码、Run/Session、冻结工件、读取回执和独立复算脚本/输出。公共与私有 MANIFEST 分开；full trace 和 Session 按现有私有边界留存，不外发认证信息，不清理旧环境或无关文件。

报告分别说明功能边界、真实任务是否完成和材料消费质量。一次示例抄录通过不证明科研任务普遍变好，也不证明商业搜索质量提高。上述验收通过后，用户计划的最终 L3 按[现有规程](../../guides/L3_RESEARCH_TEST.md)在同一冻结提交独立运行，保留旧版对照输入、预算、全部尝试和负结果；不需要为本轮另建测试框架。
