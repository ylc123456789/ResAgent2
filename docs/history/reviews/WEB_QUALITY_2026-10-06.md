# 网页工具的信息保留与质量复测（2026-10-06）

## 范围与已确认问题

基线为 `feat/web-search-hardening@e286977`；既有 `f69df60` 功能验收及只读复核保留在 [原记录](WEB_TOOLS_2026-10-06.md)。本轮继续在原分支修改、不合并 main，公共 Run schema 仍为23.0。

上轮通用网页任务的一个 DeepSeek 请求进行了三次内部搜索，原生结果按精确 URL 去重后有24条，但旧组件只返回前5条，搜索工件也只有5条。后续批次里的 CPython 官方文档源码因此无法通过工具工件读取。任务最终完成主要依靠 Scientific 自主选择的官方 URL，不能据此证明搜索结果很好。

对照 DSH 官方 `master@5badb15009ae1756c3afe0ae0cef1faafc290ccc`，其独立搜索请求、4096输出tokens/最多5次内部搜索和普通查询提示与旧实现相同。DSH 会提示结果截断，但也在返回顺序上截取前几条；它的网页 Markdown 转换保留链接。借鉴呈现与信息保留，不把官方项目默认值当成质量证明。

## 修改

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
