# 目标驱动的文献检索：实施与测试交接（2026-10-05）

分支 `refactor/literature-foundation`，起点 `e03a151`，公共数据 schema 保持23.0。本轮完善既有检索工具和 Scientific 的行动指引，不新增 Agent、检索流水线、Provider、缓存服务或依赖。推送，不合并。

## 实施范围

Scientific 始终从用户目标和明确约束判断材料是否足够；自己的工具和委托工作都用于补足交付所需材料。文献搜索不是必经第一步，也不要求固定轮数或论文配额。看过小批结果，再判断阅读、改词、换源、下一页或停止；相关性和科学含义由 LLM 判断。

- Components：search 返回小型 LiteratureSearchResult，提供完整来源摘要与真实来源、执行查询、分页、来源尝试；原有 HTTP 节奏、有限重试、冷却和 Run 截止保持，错误带类型和可用冷却时间。
- Capabilities：沿用一个 literature_search，新增 source / scope / page，默认数量10→5、范围1–20。topic 只查标题/摘要，title 查标题，不保证唯一精确匹配。auto 首页仅在不可用时切源；空结果算成功，显式来源和后续页不回退。next_request 固定实际来源和原查询条件。
- 元信息/摘要仍按论文冻结，500字符只是工具预览并标明裁剪；完整摘要可从论文工件读取。相同规范化元信息快照仍复用且标注 reused，搜索回执仍独立保存。
- 查询只接受普通关键词及双引号短语，不新增自动查询扩写、相关性评分或凑数策略。arXiv 以 ti/abs 字段和 AND 查询；OpenAlex 用官方 search.title / search.title_and_abstract，词干及停用词处理与 arXiv 不保证等价。
- 缺少总数时使用原始页条数提供下一页探测，不因去重缩页而误挡续页。OpenAlex 页号最多覆盖前10000条；没有下一页参数不必然等于结果耗尽。

Runtime、Controller、Compiler、Interpreter、执行 Agent、权限和冻结来源链不变；Components 仍为普通调用接口，Capabilities 仍为模型工具入口，Scientific 仍负责目标与证据判断。current、包说明和 CLI 参考已同步；guides 没有需要迁移的旧文献参数。工具指纹只改变 shared/literature_search，其他工具说明与参数保持原基线。

## 本地验证

WSL Ubuntu-D 的9个 editable 包均解析到本仓库 src；全量 **1751 passed / 1 skipped（54.91秒）**，mock E2E **run_golden completed / 13工件**，git diff --check通过。确定性覆盖包括来源选择/故障切换/合法空结果、显式来源失败禁回退、预请求参数校验、两来源查询范围/页号、未知计数和分页边界、完整摘要保留、冷却与截止、按篇复用与冻结、真实 Scientific 调用链的两页搜索→范围读取→引用完成。固定响应测试只证明接线和事实边界，不能代替真实模型检索质量验收。

本地 pip check 仍有既存环境差异：pdfminer-six 20260107 缺少 cryptography。本轮没有安装新依赖；服务器须独立检查。不调用真实论文搜索 API，不运行付费模型或训练。

## 服务器测试建议

先冻结当前分支 HEAD、工作区、9个 editable 源码指针和模型/上下文/预算/OPENALEX_API_KEY 是否配置（仅记录有无，不输出密钥）。运行：

```bash
python -m pip check
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
git diff --check
```

沿既有 CLI run / answer 流程，每场景新建 Run 和独立证据目录，开启 full trace；保留中间失败，不重写旧记录。无需重跑 GPU L3。

1. **已知论文定位**：请 Scientific 查找 Guo 等的《On Calibration of Modern Neural Networks》，核对标题、作者和来源，再解释从摘要能确认什么、哪些细节需要正文。看是否选择 title/短语查询，反馈及单篇完整摘要是否一致。若出现服务错误，如实保留来源尝试，不把离线导入算作在线检索成功。
2. **小批主题检索与适应**：给一个收窄的目标，例如“温度缩放在哪些情形有局限？找有关方法或反例，给依据并说明不足”。只限定预算，不指定查询轮数/论文数量。检查模型是否浏览本批后再决定改词、阅读、换源、翻页或停止，是否避免自动大量下载。服务可用时另做一次明确请求续页的小探针，核对 next_request 的来源、条件和页号，返回条目与冻结哈希。
3. **故障与空结果**：运行现有确定性来源/HTTP测试证明空结果不换源、显式来源失败不回退、429冷却和有限重试；真实 Run 若自然遇到错误，再核对模型看到了 error_type/source_attempts、没有重复轰击失败源，关键材料受阻时正确询问或依授权说明限制。不要为了凑429测试主动压测服务。
4. **已有材料足够**：导入一篇论文元信息，要求“只根据这些材料总结摘要，不联网”。应直接读取/分析，不启动固定文献搜索步骤或无关委托。实际阅读范围必须与最终报告一致。

冻结报告、Run状态、论文/回执/PDF/全文、trace和独立 hash 复算。报告区分元信息、摘要预览、完整摘要与实际已读正文，不因下载/访问日志就声称读完整篇。

上述是功能和行动选择验收，不证明达到商业搜索平台的检索质量。若要比较改动前后效果，应固定目标集合、模型、预算及服务条件，对照相关性、关键论文覆盖、查询数量、故障恢复和实际交付；一次 Run 的调用减少不能单独归因于提示词改进。外部来源可用性和索引覆盖仍是限制。

## 服务器验收与独立复核（2026-10-05）

产品实测提交1faab0f97e2885e9203cf51420b6198265dbc9e9。测试方报告服务器1751 passed / 1 skipped、mock completed / 13工件、pip check干净，已知论文定位、目标驱动主题检索、错误/空结果及材料足够四场景通过。只读独立复核原始state、Session、trace和清单，确认：

- A/B/D三个Run均completed/supports，LLM调用5/11/3共19次，逐call_id:retry与trace对账一致；本轮不是GPU训练或科研L3。
- 三Run登记工件20/43/8，共71项冻结SHA256全部一致。public清单实际120项、private4项，全部hash匹配；服务器报告仍写119public，属于统计陈旧。另有check_editable.py未列入清单，不影响产品工件。
- A三次检索实际0+5+5，共10篇论文；141/759是来源报告的总命中数。读两份同源论文的完整摘要并区分书目一致和独立科学证据，未读取全文。
- B四次搜索生成27篇论文，7次全文获取中4次成功、3次下载超时；成功正文以实际读取片段为限，不能写成27篇全读或4篇正文全部读完。Ovadia等1906.02530v2的PDF下载超时两次（含一次重试），2210.16315v3下载超时一次；随后使用其他可用论文，失败记录保留。
- D没有literature_search或request_work，只读取导入论文和目录，在摘要层面交付。
- OpenAlex独立分页探针原回执为第1/2页各5篇、来源/条件固定、下一页2/3，10篇冻结hash一致且页间不重叠。另一探针在auto切到OpenAlex后人为指定arxiv页2，因冷却失败；它没有执行返回的OpenAlex next_request，不能据此否定分页链。
- 原始Session还显示A的首次opinion含不支持字段，B/D首次verdict分别为supported / supported_at_abstract_level，均收到completion_check后在原Session纠正。这些恢复及B的3次下载超时未写入初版服务器报告，应补录；最终completed不等于全程没有失败。

证据根：/root/autodl-tmp/resagent2/runs/lit-search-20261005/；服务器报告为protocol/AGENTIC_LITERATURE_SEARCH_acceptance_report.md。以上复核不修改服务器记录或重跑真实模型。

### 标题查询的召回问题

A首次裸标题query=On Calibration of Modern Neural Networks、scope=title被翻译为每个词分别ti字段并AND，包括On/of，得到total_results=0。模型改用topic后恢复，证明行动反馈链有效，但“title不保证唯一精确匹配”不能解释成完整标题零召回没有问题。

独立在同一服务器对同一标题做3次有间隔、无重试的公开arXiv API请求：
- 原逐词title AND：0条。
- 整个标题的双引号短语：2条，包含1706.04599v2目标论文。
- 去掉On/of的标题词AND：4条，包含目标论文。

本次对照证明查询表述影响召回；未单独隔离每个停用词或确认提供方内部分析器实现。短语查询也返回相似标题，不能保证唯一或严格等值。

后续建议在title定位中让完整标题作为一个短语，topic仍保留关键词检索；不要新增停用词词表、自动查询扩写或多源批量扫描。需要对这个行为取舍做小范围修改和几篇已知标题的定向复测，不需要重跑训练L3。本次只同步验收事实和发现，不修改产品查询逻辑；现有机制通过与此召回质量问题分别记录。
