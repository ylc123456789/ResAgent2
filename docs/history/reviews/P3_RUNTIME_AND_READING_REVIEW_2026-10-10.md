# P3：运行时修复与网页、读取方案核对

## 基线与本轮范围

分支 `fix/native-agent-receipts`，本轮基线为 `5220ee12f9839b56ae4e777005ed20977dd47a47`；main 仍为 `fbf1a00`，未合并。schema 保持 24.0。

用户本轮只确认下面三项实现。网页提取、搜索覆盖提示及空读取回执仍在讨论；本文中的建议不代表已实现。

| 审查编号 | 原问题 | 本轮处理 |
|---|---|---|
| P3-5 | 反馈 JSON 头尾摘录在偶数上限时多出一个字符，且存在第二套裁剪算法 | `_trim_json` 仅负责序列化，复用现有 `_head_tail`；省略标记纳入上限 |
| P3-6 | 恢复时持久 Session 读入失败逃逸为进程异常 | JsonSessionStore 在存储边界标识预期加载失败，Loop 返回现有 failed / contract_error；原文件保留，模型调用为零 |
| P3-7 | 实时 trace 先推进位置，丢掉写入中的半行 | 以字节读取，仅消费换行结束的完整记录；半行和 UTF-8 半字符留待下一次，完整行的 UTF-8/JSON 损坏时跳过 |

不增加 schema 迁移、Agent 工作模式、上下文层或文件观察框架。Session 保存失败仍不能伪装成成功；自定义 Store 的编程错误不作为记录损坏吞掉。

## 验证与服务器交接

在仓库根目录、既有 editable 环境执行，分别记录退出码：

```bash
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

专项已经包含在全回归中；需要独立证据时可单独运行：

```bash
python -m pytest -q \
  tests/runtime/test_context.py \
  tests/runtime/test_resume.py \
  apps/cli/tests/test_shell_trace.py
```

| 判断点 | 应证明什么 |
|---|---|
| 摘录边界 | 0、小上限、标记长度、奇偶上限和 Unicode 都不越界；短完整值和 None 行为保留 |
| Session 加载 | 三 Agent 的坏 JSON、坏 UTF-8、非法状态、缺失/旧/未来 schema、非对象记录均结构化失败；原字节不变、不调用模型、不新建恢复文件、不泄露正文；读取错误可识别，编程异常继续暴露 |
| Trace 尾读 | 完整 JSON 尚未换行时等待；中文/emoji 字节分开写入不丢失；补齐后只展示一次；UTF-8/JSON 损坏的完整行不阻塞好记录；既有 Run 过滤、reset 与文件缩短处理保留 |

本轮不需要付费模型、GPU 或单独 L3。上述边界可确定性验证；用户计划待后续已确认问题一并处理完，再做最终 L3。此前 2ea65d3 的服务器验收保持独立，不作为本轮修改的验收。

## 本地结果（2026-10-10）

- 全回归：**2169 passed / 1 skipped**，59.97 秒，退出码 0；相对已验收基线 2106，新增 63 个用例（摘录 29、Session 加载 25、Trace 尾读 9）。
- mock：`run_golden completed`，13 工件，Coding / Experiment 各一次完成，退出码 0。
- `git diff --check`：clean，退出码 0。
- 本地 `pip check` 退出码 1：既有环境的 `pdfminer-six 20260107 requires cryptography, which is not installed` 仍在，本轮未改依赖或环境；此前服务器独立验收 clean，不能据此宣称本轮服务器也 clean，应由服务器重新核对。

上述是本地离线证据。未联网付费运行、未上服务器复测、未执行 L3、未合并分支。

## 网页提取：DSH 的实际做法与取舍（尚未改代码）

核对官方 DSH 固定版本 `5badb15009ae1756c3afe0ae0cef1faafc290ccc`。

ResAgent2 当前 `_HTMLTextParser` 是自行实现的 HTML 事件解析器。未闭合隐藏标签、链接包代码块、SVG/MathML title、嵌套 pre 四项都属于这一转换环节。

DSH 自行做 HTTP 请求，但 HTML 转 Markdown 使用 Turndown + GFM 插件，由 DOM 解析承担结构处理，另加少量移除及表格规则。它没有执行 JavaScript，也没有用 LLM 解读网页。其测试明确接受未闭合 script 转为空文本；转换失败或过深嵌套则输出省略提示。成熟依赖不保证恢复所有损坏 HTML，DSH 也没有等价的独立页面标题字段及全部相对链接解析契约。

建议下一阶段评估 Python 的通用 HTML→Markdown 依赖（例如 markdownify），只替换转换器；保留现有 HTTP、公网地址检查、冻结工件、WebFetchError 和 read_artifact 接入。采用前须检验四个原复现、官方多行代码示例、相对链接和普通技术文档。不要为此引入 Node 运行时、另一套工件或浏览器，也不要默认用面向新闻的正文抽取来删掉技术资料。不能确认可靠正文时沿既有失败路径报告，不能把空输出冒充完整正文。

公开源码：

- [DSH 转换依赖和规则](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/web/tool-web/src/fetch.ts#L9)
- [DSH 转换失败与未闭合 script 测试](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/web/tool-web/tests/tool-web.spec.ts#L309)
- [DSH HTTP 抓取](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/web/web-fetch-http/src/provider.ts#L146)

## 搜索覆盖与空读取：问题含义（尚未改代码）

P3-3 的 search_text 达到 max_results 就停止。比如上限 20，找到第 20 条时没有继续检查后面的文本，所以知道已返回 20 条，但不知道总计恰好 20 条还是更多。当前 truncated/incomplete 是保守的覆盖提示，与现行契约不冲突。建议明确提示“达到结果上限停止，余下范围未检查”，保留有界扫描；不为计算总数继续全扫。跳过文件也应继续说明原因，零匹配不能据此证明不存在。

P3-4 的空读取有歧义：空文件和 5 行文件请求第 8–10 行都会返回成功空串。建议在共享读取回执补 total_lines，分别显示 0 和 5，保留合法 EOF 空结果；不增加另一套读取器。

## 现有统一分段读取及其他实现

read_file 与 read_artifact 共用 components/text.py 的 slice_text_lines，先选 1-based、两端包含的行范围，再从所选行文本选 0-based、末端不含的字符范围，最后应用每次 128000 字符上限。字符偏移数 Unicode 字符，不数 UTF-8 字节或 tokens。原换行保留；它不是流式读取。

普通多行文件可只指定行范围。超长单行可不指定行范围，使用 start_char/end_char。某一段行内容太大，可保持行范围不变，以 next_start_char 继续选取其中的字符；此时字符 0 指的是该行段的起点，不是原文件起点。

next_start_char 是实际返回后的下一字符在同一行选择内的位置。请求 end_char 只是请求值；truncated=False 只代表请求窗口没有被工具上限裁剪；next_start_char=null 只代表选定行范围耗尽。上下文工作集还可再次裁剪并明确 context_truncated，不能把工作集头尾片段视为连续覆盖。

| 实现与固定版本 | 分段方式 | 超长单行 | 末尾与范围信息 |
|---|---|---|---|
| ResAgent2 本轮基线 | 行选择 + 字符窗口，共用切片 | 可按字符连续读取，不需要 shell | next_start_char；尚无 total_lines，越界允许成功空串 |
| DSH 5badb150 | offset/limit 按行；2000 行、50 KiB 输出 | 每行保留前 2000 字符并提示裁剪，没有字符续读 | 准确总行数、EOF、下一行；越界报错；大文件可流式读取 |
| Pi e4c75a73222ae2c72abb5f5314fa35ee8effc508 | offset/limit 按行；2000 行、50 KiB 输出 | 第一行超字节上限时提示用 bash 另取 | 提供总行数和继续 offset；越界报错 |

可借鉴明确的总量/末尾提示；保留已有字符窗口以适配超长 JSON、日志和正文。权限与内容载入路径可不同：工作区先校验工作区授权和默认 10 MiB 处理上限，工件先校验登记授权和整份冻结 hash。切片语义应持续共用。

公开源码：

- [DSH 读取入口](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/fs/tool-fs/src/read.ts#L14)
- [DSH 行截断与末尾提示](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/fs/tool-fs/src/read-render.ts#L69)
- [Pi 读取入口](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/tools/read.ts)
- [Pi 输出裁剪](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/tools/truncate.ts)
