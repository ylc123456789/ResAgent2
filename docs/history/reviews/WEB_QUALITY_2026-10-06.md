# 网页工具的信息保留与质量复测（2026-10-06）

## 范围与已确认问题

基线为 `feat/web-search-hardening@e286977`；既有 `f69df60` 功能验收及只读复核保留在 [原记录](WEB_TOOLS_2026-10-06.md)。本轮继续在原分支修改、不合并 main，公共 Run schema 仍为23.0。

上轮通用网页任务的一个 DeepSeek 请求进行了三次内部搜索，原生结果按精确 URL 去重后有24条，但旧组件只返回前5条，搜索工件也只有5条。后续批次里的 CPython 官方文档源码因此无法通过工具工件读取。任务最终完成主要依靠 Scientific 自主选择的官方 URL，不能据此证明搜索结果很好。

对照 DSH 官方 `master@5badb15009ae1756c3afe0ae0cef1faafc290ccc`，其独立搜索请求、4096输出tokens/最多5次内部搜索和普通查询提示与旧实现相同。DSH 会提示结果截断，但也在返回顺序上截取前几条；它的网页 Markdown 转换保留链接。借鉴呈现与信息保留，不把官方项目默认值当成质量证明。

## 第一阶段修改（5b71d1b）

- Components 返回有界 HTTP 响应内收到的全部规范化来源；DeepSeek 沿返回顺序合并原生批次、按精确 URL 去重，snippet仍只来自 URL 匹配的 cited_text，缺少时留空。生成回答和 encrypted_content 仍不充当正文或摘要。
- Capabilities 把全部规范化结果保存到既有 web_search 工件。max_results 1–10只限制模型预览；观察的 result_count、omitted_count、truncated 直接由已保存结果及预览推导。truncated只表示预览省略，余下来源由 read_artifact 读取。Tavily仍将 max_results 作为提供方数量提示，收到超额结果时也保留。
- 独立 DeepSeek 请求明确只发现来源，不回答研究问题或写研究总结。4096输出tokens/最多5次内部搜索保持原值；max_results不承诺减少内部搜索次数、tokens或费用。
- 网页链接保留单独提交：沿用HTMLParser，把可见链接文字与合法http(s)目标保存为 `label (URL)`，相对地址按重定向后的final URL解析。非法/空/非http(s)/带凭据目标只保留可见文字；链接内的内联文字和块分隔保留，不自动抓取链接，不解释HTML `<base>`或浏览器布局。

Scientific仍按用户目标判断相关性、资料种类、阅读和停止；不新增 Agent、Session、查询循环、排序规则或网页到文献的固定流程。模型工具说明/参数描述指纹有意更新，公共 Run 字段和恢复协议保持原约定。

## 已有真实响应复放

从服务器证据 `/root/autodl-tmp/resagent2/runs/web-f69df60/general/traces/llm_traces.jsonl` 只读取得上轮 `run_a_web_general_20261006` 的托管响应，分别用基线源码与新组件在 Ubuntu-D 内复放。没有调用新付费服务，也没有改写服务器现场。

| 项目 | 旧实现 | 新实现 |
|---|---|---|
| 搜索工件可保存来源 | 5 | 24 |
| 工具预览 | 5 | 5 |
| 预览外可读取来源 | 0 | 19 |
| 后续批次 CPython 官方文档源码地址 | 丢失 | 保留2条 |

这证明已修复本地信息丢失，不证明提供方排序、提示改动或 Scientific选择来源的总体质量提高。

## 第一阶段实施与本地验证

- 搜索信息保留与副请求职责：`c6d95b6`。
- 网页链接保留：`fb5b4ec`；后续只同步本记录，不改产品源码。
- Ubuntu-D `/home/cyl/ResAgent2`、Conda `ResAgent2`：完整回归 **1950 passed / 1 skipped**（52.11秒）、mock `run_golden completed / 13 artifacts`、`git diff --check`通过。
- 确定性覆盖：多批原生响应完整登记、前5条预览/19条省略、冻结hash及授权读取、Scientific继续读取并抓取预览外来源、Tavily实际超额响应、空/失败回执、隔离搜索输入及共享模型预算；网页覆盖绝对/相对/重定向地址、嵌套内联/块标签、无效目标、未闭合/嵌套锚点与冻结后读取。
- 模型工具指纹只变更 `scientific/web_search` 和 `scientific/web_fetch`；独立只读复核未发现新的正确性或分层问题。没有新增依赖、模型调用层、Run字段或状态。
- 本地 `pip check`仍报告既有 `pdfminer-six 20260107 requires cryptography, which is not installed`；未安装或修改环境，不能写成本地依赖全绿。上轮服务器环境clean是另一个事实，本轮服务器须重新核对。
- 第一阶段本地收尾时，真实搜索质量对照尚未执行；当时只读复放使用旧响应、不发送新付费请求。随后服务器对照已完成，独立复核与修复见文末。

## 服务器复测

先冻结待测提交、工作区和9个editable源码指针；回归运行 `python -m pytest tests apps/cli/tests -q`、`python -m e2e.mock_e2e`、`python -m pip check`、`git diff --check`。原始 trace 和 Session仅私有留存，报告记录配置存在与否和必要事实。

1. **交接补测**：用已有真实原生响应，不发送付费请求。核对新工件包含全部24条、预览5条、omitted_count=19、truncated=True；通过授权 read_artifact读到后续来源并独立核对冻结hash。空结果/429/未完成响应等边界继续用确定性测试，不主动制造线上限流。
2. **网页链接补测**：用公开论文或文档页面核对保存正文中的可见链接文字与实际href，包括相对地址按final URL解析；从被冻结web_page经read_artifact读到地址并重算hash。确认保留下载链接未触发自动PDF下载，失败路径仍不生成正文工件。
3. **搜索质量成对测试**：基线e286977和待测版本使用同样DeepSeek账号、deepseek-flash模型、max_results=5、60秒/4MiB响应上限、4096输出tokens/最多5次内部搜索。固定下面六条查询，各版本各做两次，交替执行并保留所有响应/失败，不重跑到命中后只报最好一轮。
4. **Scientific真实任务**：沿CLI运行两个短任务，每次新建Run/证据目录。目标分别为“按Python官方原文解释asyncio.timeout与wait_for的差异”和“定位On Calibration of Modern Neural Networks原始来源并核对身份与有依据的结论”；不指定网页/文献工具顺序。各48次模型调用/900秒、full trace。若资料已足够而未调用某工具，按实际覆盖记录。

固定查询：

| 查询 | 人工核对重点 |
|---|---|
| Python official documentation asyncio.timeout vs asyncio.wait_for difference | 官方asyncio任务文档或对应官方源码是否命中、可抓取 |
| Python 3.12 asyncio eager_task_factory official documentation | 版本与原始文档内容是否对应 |
| PyTorch torch.inference_mode vs torch.no_grad official documentation | 官方入口与两个概念的区别是否对应 |
| On Calibration of Modern Neural Networks Guo Pleiss Sun Weinberger ICML 2017 PMLR | 论文原始入口、身份正确性与引用噪声 |
| MobileNets Efficient Convolutional Neural Networks for Mobile Vision Applications Howard 2017 arXiv | 原始论文题名/年份/作者与后续同名材料是否区分 |
| Attention Is All You Need Vaswani 2017 original paper | 原论文入口与二手解释是否区分 |

每条查询分别统计原生响应、已保存来源和前5条预览的目标来源命中与相关结果比例，记录无法抓取/空snippet/引用噪声。另记客户端请求、服务端内部搜索次数、input/cache/output tokens、延迟和失败。没有引擎完整结果总数，不估计召回率；小样本不宣称商业搜索水平或普遍成功率。

真实任务核对资料是否实际读到、最终引用是否由对应原件支持、是否诚实区分线索/摘要/正文及资料缺口；任务completed本身不作为质量分。只有新旧同条件证据才能评价本轮提示修改的收益或代价。

## 5b71d1b 服务器验收与独立复核

证据位于 `/root/autodl-tmp/resagent2/runs/web-quality-5b71d1b-20261006/`。服务器回归1950 passed / 1 skipped、mock13工件、pip check及diff检查通过；独立核对24/24冻结工件hash，以及97 public / 77 private清单。结果保留与网页链接修改有效，保留这两项，不以任务completed推断搜索质量优秀。

基线e286977与5b71d1b各12次请求（6查询 × 2轮），另有Scientific两任务的3次搜索，共27次。原报告把3次失败写成“畸形条目、3/26”，复核原始响应后更正：三次均为合法的 `web_search_tool_result_error` / `max_uses_exceeded`，出现在列表形状的结果块中；每次此前已有50条有效原生来源。旧解析器只识别对象形状的工具错误，列表形状误报invalid_response并丢弃有效来源。服务端usage记录6次尝试，工具配置仍是max_uses=5。

| 成对请求合计 | 基线（12次） | 强化提示（12次） |
|---|---:|---:|
| 服务端内部搜索计量 | 12 | 42 |
| input tokens（含cache） | 116357 | 346174 |
| 非缓存input tokens | 103429 | 202174 |
| output tokens | 9655 | 11080 |
| 延迟中位数（ms） | 5097 | 6325.5 |

强化提示在部分查询中取得更多来源，但没有足够证据支持排序或效率收益；不能把总input约3倍直接写成费用3倍，计费应区分缓存与非缓存。自动相关性评分漏掉raw.githubusercontent.com、docs.pytorch.org及ACM的 `/doi/abs/`，其top-5汇总不作为质量依据。真实Scientific两任务未调用read_artifact展开搜索工件尾部；它们实际依靠官方网页和论文材料完成。尾部可读与模型继续使用的能力由确定性测试覆盖，不能倒写为真实任务已覆盖。

## 第二阶段修复：恢复短提示与保留已知限制下的来源

- 恢复DSH的原始短提示 `Perform a web search for the query: {query}`，4096输出tokens和max_uses=5不变。生成回答仍不作为证据，查询和后续行动仍由Scientific根据用户目标选择。
- Components统一处理对象/列表形状的原生工具错误。只对已知max_uses_exceeded且存在有效来源保留结果，沿原返回顺序按精确URL去重；返回incomplete_reason。没有有效来源仍报search_limit_exceeded，其他工具错误、畸形条目或非end_turn响应仍整体失败。
- Capabilities沿原登记/冻结/读取链交付status=partial、incomplete_reason和有效来源；ok=True表示已取得可用材料，不表示搜索完整。预览省略仍由truncated/omitted_count表达，与提供方限制分开。只增加一个组件结果字段和回执信息，不修改公共Run字段、schema23.0、共享预算、Session或循环，不补发请求。

将三份原始失败响应经生产Backend、WebSearchTool、ArtifactRegistry和授权RegisteredArtifactReader只读复放（不新增付费请求）：

| 原响应 | 原生有效来源 | 保存的精确URL去重来源 | 预览 | 省略 | 回执 |
|---|---:|---:|---:|---:|---|
| quality/test/q1/run2 | 50 | 45 | 5 | 40 | partial |
| taskA第一次 | 50 | 43 | 5 | 38 | partial |
| taskA第二次 | 50 | 48 | 5 | 43 | partial |

三份均标记incomplete_reason=max_uses_exceeded；来源列表逐项等于原生有效来源的有序去重，冻结hash与授权读取均吻合，只执行一次模拟客户端请求。复放只证明旧失败的有效来源现在能保留，恢复短提示后的实时费用和搜索质量仍须实测。

Ubuntu-D /home/cyl/ResAgent2、Conda ResAgent2：86项受影响测试通过；完整回归 **1982 passed / 1 skipped**（65.34秒），mock `run_golden completed / 13 artifacts`，`git diff --check`通过。比第一阶段增加32项边界测试，覆盖限制错误形状/顺序、URL去重、无来源失败、其他错误/未完成响应、partial冻结回执和Scientific继续读取/抓取。工具指纹仅更新scientific/web_search。未新增依赖或付费调用；本地pip check仍报告既有pdfminer-six缺cryptography，未修改环境。服务器修复提交的实时补测尚未执行。

## 第二阶段服务器补测

1. 冻结修复提交，核对9个editable包仍指向源码；运行完整pytest、mock_e2e、pip check与diff检查，保留原失败现场。
2. 复放上述三份原始响应，使用生产DeepSeekWebSearchBackend.search与WebSearchTool，不沿用直接调用旧私有_search_items的脚本；将记录响应注入客户端，不发送付费请求。核对partial/reason、45/43/48来源、预览/省略、独立冻结hash和授权read_artifact。另核对无来源限制、其他错误和未完成响应仍失败，不能一律跳过未知条目。
3. 用本页固定六查询各做一次真实短提示请求，配置与旧对照相同，保留所有结果和失败；对照已有基线的内部搜索、缓存/非缓存input、output、延迟和目标来源。只评价此次样本，不将来源条数等同相关性或宣称成本必然降低。
4. 做一个短Scientific资料任务，不指定工具顺序，核对实际消费及引用。线上未出现partial时注明未触发；其读取/继续处理由原响应复放和确定性Scientific测试补充，不为了触发限制增加付费调用。无需重跑完整科研L3。

完成以上补测再评估合并；当前继续推送开发分支，不合并main。
