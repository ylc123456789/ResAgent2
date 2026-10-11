# 完整工件索引：公开实现调研与下一步讨论

> **后续方案更新（2026-10-11）：**用户进一步明确“搜索仅返回第三部分内容、不创建工件，fetch 才登记材料”，工作产物按 WorkRequest → Task → 工件组织，保留各 Task 原始报告、取消总体报告。第 5、6、8、9 节中搜索快照工件及延迟入口的讨论保留为当时研究过程，不再作为实施约定。当前完整修改方案见 [工具、上下文与工件](TOOL_CONTEXT_REDESIGN_AGREED_2026-10-11.md)；本文的固定版本源码事实和适用限制仍可复核。

日期：2026-10-11。项目记录基线：`fix/native-agent-receipts@ecaede9e80802cd1b6a269bfe91a46b9ae0ab4d7`，schema 24.0。

本文是源码调研和讨论建议，不是当前实现说明或已批准的修改方案。已确定的上下文和工具规则另见 [共识记录](TOOL_CONTEXT_REDESIGN_AGREED_2026-10-11.md)。本轮没有改产品代码或 schema，没有运行付费模型、GPU、L3 或上游测试。

## 1. 要解决的问题

用户已认可统一四部分上下文、只有第三部分可以压缩、第四部分只有完整工件目录，以及 ask_user 不产生工件。尚未认可的是整体索引怎样组织。

此前提出的“工件条目表＋调用结果组表”确实有两层组织：先找到某次调用的结果组，再由组内 ID 找到实际工件。它不是只靠改名就能变成一层，也没有得到用户确认。本次调研重点是判断这层中间目录有没有必要，而不是重新讨论已经确定的上下文边界。

## 2. 调研对象、方法与结论边界

| 项目 | 固定版本或资料 | 实读范围 |
| --- | --- | --- |
| Google ADK Python | `bcac35a6efb21f176eef9d28d663db247f4e5afd` | 工件服务、文件存储、保存事件增量、目录注入与加载工具及相关测试源码 |
| DeepSeek Harness | `d743267388641bc76f17c45ce8b4c231aed1d32c` | spill、工具呈现、子 Agent 目录和结果投递、历史压缩与可选历史查询 |
| Codex | `c3d3b142d10f4316b46e35aad7e5317e7e506cb7` | Agent 启动/等待/邮箱、进程句柄、文件定位、附件关联表、上下文和压缩 |
| Pi | `e4c75a73222ae2c72abb5f5314fa35ee8effc508` | 长输出保存、读取、上下文、压缩路径清单、subagent 示例扩展 |
| OpenHands SDK | `97dbce5c37865dd7a3020d2b9662f4a76d11fa71` | 工具观察事件、长输出保存、浏览器多输出、delegate、压缩与 MEMORY.md |
| Claude Code | 2026-10-11 抓取的官方工具、子 Agent 和上下文文档 | 公开的任务 ID、后台输出路径与通知契约；不推测闭源实现 |

调研直接阅读源码及相关测试源码；没有执行这些测试。DSH 的 31 份、Codex 的 33 份缓存源码已与固定提交的 Git blob 核对；ADK 按固定提交 URL 下载；Pi、OpenHands 阅读固定 HEAD 的干净检出。结论中的“未见”仅限列出的链路，不是对整个项目或所有插件的不存在证明。

详细报告与源码缓存在本机 `E:\dshWorkSpace\reviews\artifact-index-research-20261011\`，包括 `ADK_CLAUDE.md`、`DSH.md`、`CODEX.md`、`PI_OPENHANDS.md`。本文件保留独立可用的结论及公开固定版本链接，不依赖这些缓存才能理解。

## 3. 最贴近完整目录需求的参考：Google ADK

ADK 的工件服务提供 save、load、list。工件按应用、用户、会话和文件名定位，保存后返回版本；完整目录直接是当前作用域内的文件名列表。

- [BaseArtifactService.list_artifact_keys](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/src/google/adk/artifacts/base_artifact_service.py#L163) 明确要求：当前作用域可读取的名称都应列出。
- [FileArtifactService 的目录生成](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/src/google/adk/artifacts/file_artifact_service.py#L676) 从文件及元信息得到名称集合，去重、排序后直接返回列表。底层可以有版本目录，模型导航不因此必须变成多层调用树。
- [LoadArtifactsTool 的请求构造](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/src/google/adk/tools/load_artifacts_tool.py#L448) 列出工件名称，把列表加入动态说明；模型需要正文再按名字 load。此处没有挑选前 N 个目录项的分支。
- [相关测试](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/tests/unittests/tools/test_load_artifacts_tool.py#L525) 核对目录进入动态说明，而不是提前注入全部正文。

一次操作新增了什么，则通过事件记录简单增量：

```text
artifact_delta = {文件名: 新版本}
```

[Context.save_artifact](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/src/google/adk/agents/context.py#L563) 在保存后写入当前事件的 [artifact_delta](https://github.com/google/adk-python/blob/bcac35a6efb21f176eef9d28d663db247f4e5afd/src/google/adk/events/event_actions.py#L118)。这是“本次新增/更新了哪些工件”的记录，不是要求模型再打开的一份全量目录。一个事件可以记录多个工件；同名多次保存只记录该事件最终版本，中间版本由底层服务负责，不能夸大成全部中间事件无损记录。

**可以借鉴：一份完整工件清单＋每次操作的产物增量，不必为每次调用额外持久维护一个结果组目录。**

不能照搬全部实现：ADK 将 load 的正文作为本次请求附件加入，不存为 Session 正文；ResAgent2 已确定读取正文应进入第三部分原生工具历史。ADK 的目录使用文件名和版本，也不等于本项目应替换冻结工件 ID。它没有为本项目的 pending WorkRequest 预留结果工件，也未证明与本项目相同的四部分压缩边界。

## 4. 其他项目怎样定位结果

| 实现 | 核对到的行为 | 对本项目的含义 |
| --- | --- | --- |
| DSH | 长输出回执直接给 locator，本地就是完整输出文件路径；子 Agent 先返回 ID，完成后另投递消息 | 已有内容地址和延迟活动身份可以直接表达，不必用另一个索引文件代替 |
| Codex | spawn 返回 task_name；进程返回 session ID；已有文件用路径或 MCP URI 定位 | 请求身份、文件身份各有用途，不等于需要两套材料目录 |
| Pi | 长输出保存到临时 log，回执直接给 Full output 路径；压缩时确定性累计部分文件路径 | 精确引用不应完全依赖 LLM 摘要；目录完整性需要明确机制 |
| OpenHands | 长观察可保存为文件并直接返回路径；同一浏览器回执可含文字和截图多个地址 | 一次调用有多个产物，也不必自动增加一个结果组索引 |
| Claude Code | 官方说明后台任务返回 task ID 和输出文件路径，完成后通知 | 任务句柄不是工件；闭源内部目录结构无法据此判断 |

主要源码证据：

- DSH：[SpillRef](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/spill/spill/src/types.ts#L13)、[本地直接返回 locator](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/spill/spill-local/src/index.ts#L149)、[子 Agent 接收回执](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/subagent/tool-subagent/src/index.ts#L223)、[完成通知](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/subagent/subagent/src/continuation-messages.ts#L138)。
- Codex：[spawn 返回 task_name](https://github.com/openai/codex/blob/c3d3b142d10f4316b46e35aad7e5317e7e506cb7/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs#L247)、[wait 不重复返回子任务正文的测试](https://github.com/openai/codex/blob/c3d3b142d10f4316b46e35aad7e5317e7e506cb7/codex-rs/core/src/tools/handlers/multi_agents_tests.rs#L3741)、[生成文件直接路径](https://github.com/openai/codex/blob/c3d3b142d10f4316b46e35aad7e5317e7e506cb7/codex-rs/ext/image-generation/src/artifact.rs#L8)。
- Pi：[完整长输出文件](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/tools/output-accumulator.ts#L91)、[bash 回执路径](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/tools/bash.ts#L306)、[确定性路径收集](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/compaction/utils.ts#L26)。
- OpenHands：[观察到原生 tool 消息](https://github.com/OpenHands/software-agent-sdk/blob/97dbce5c37865dd7a3020d2b9662f4a76d11fa71/openhands-sdk/openhands/sdk/event/llm_convertible/observation.py#L17)、[保存长输出并返回路径](https://github.com/OpenHands/software-agent-sdk/blob/97dbce5c37865dd7a3020d2b9662f4a76d11fa71/openhands-sdk/openhands/sdk/utils/truncate.py#L50)、[浏览器多个产物](https://github.com/OpenHands/software-agent-sdk/blob/97dbce5c37865dd7a3020d2b9662f4a76d11fa71/openhands-tools/openhands/tools/browser_use/definition.py#L60)。
- Claude Code：[官方工具说明](https://code.claude.com/docs/en/tools-reference#background-commands)、[后台子 Agent](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background)。

这些项目没有在所查默认链路中展示与 ResAgent2 完全相同的“逐轮完整授权科研工件目录”。DSH/Pi 的临时日志可回收；Codex 的附件表不等于模型全目录；OpenHands 的 MEMORY.md 会精选和裁剪，而且部分长输出在保存前已经裁剪。不能把这些机制说成本项目冻结证据、完整目录和不压缩规则的现成替代品。

## 5. 调研后更值得讨论的结构：单份工件条目集合

以下是建议，不是已确认 schema。

第四部分直接列出每份授权工件，每个工件只出现一次。条目携带可直接读取的工件 ID、类型、简短说明及必要来源。调用/工作归属作为条目属性，按来源排序或临时分组不再维护另一份 `group -> artifact_ids` 目录。

示意：

| 工件 ID | 内容 | 调用/工作关联 | 材料来源 |
| --- | --- | --- | --- |
| a1 | 网页搜索结果清单 | web_search / call_1 | 查询及 URL 等来源元信息 |
| a2 | 某网页正文 | web_fetch / call_2 | a1 |
| a3 | 文献搜索结果清单 | literature_search / call_3 | 查询及文献标识 |
| a4 | 某论文全文 | 全文获取 / call_4 | a3 |
| a5 | 工作简报 | request_work / work_1 | 必要输入工件 |
| a6 | 对照实验结果 | request_work / work_1 | 必要输入工件 |

模型从这张清单直接选 `a5` 调用 read_artifact，不必先读取 `work_1` 的索引工件再找到 `a5`。表中内容栏只是便于选择的说明，不能成为另一份简报或正文。这里的关联不代替真实生产来源：子 Agent 冻结的交付物仍保留其原始 producer，WorkRequest 是交付关联。字段的准确名称、必需项、以及关联怎样复用现有记录，实施方案还需明确。

一次工具调用返回的新工件 ID 集合，只是本次产物增量。系统用同一身份规则登记和合并，之后从权威记录与当前授权范围生成完整目录；不再给该增量创建一个必须维护的“工具名 index”对象。业务上确实有意义的搜索结果清单仍可以是一份工件，它保存候选网页/文献内容，不承担全系统目录管理职责。

同一工件再次被引用不应生成新的副本或改写原生产者；允许有多个交付/引用关联。从其他工作传来的工件只有在授权接入后进入目录。初始输入没有工具调用来源，按真实导入来源表示。多个来源/派生关系用 ID 关联，而不强迫每个工件只能属于树中的一个父节点。工具调用 ID 还须结合 Session 作用域识别，不能假定跨 Agent 唯一。

## 6. 延迟请求仍需讨论，不能用改名跳过

WorkRequest 在发出时尚未产生最终材料，因此接收回执需要稳定的工作身份。完成后，简报及交付工件行关联该工作身份；这借鉴 DSH/Codex 的任务句柄，而不是预建一个可覆盖的冻结工件。

这种方案有“工作”和“工件”两种身份，但只维护一份工件目录。它确实改变了此前“索引指向另一份索引”的提案，不能假称只是换个字段名就完全等价。

尚未决定的具体问题：

1. 工作 ID 是否足以满足用户要求的稳定未来结果入口，还是确实需要可直接读取的预留位置？
2. pending 尚无工件时，工作状态是否只保留在接收回执和运行状态中，第四部分保持只含已存在工件？
3. 完成、失败和部分完成怎样使模型获知结果，尤其原接收回执已压缩、恢复后目录没有新增材料的情况？具体通知协议应服从第三部分原生配对及第四部分只含目录的边界。
4. 同一工作产生多个 Attempt/多个交付物时，怎样保留准确归属、完整性和失败事实，同时避免再增加结果组表？

ask_user 已确定为不产生工件，不参与这份工件目录：业务回答进入第二部分，操作批准由权限链消费，问题 ID 用于持久配对与恢复。无需为与 WorkRequest 形式一致而把问答登记成工件。

## 7. 底层存储与整体目录的关系

磁盘怎样放文件、权威记录怎样保存身份/hash/授权、模型看到怎样的目录，是不同职责。模型一份清单可由权威记录生成，这个展示投影不需要再独立维护第二份事实。保留文件存储和元信息，也不等于让模型经历两层导航。

现有冻结文件可以继续按工件 ID 保存。没有源码证据表明应为目录好看而改成物理树、删除底层登记或覆盖已冻结内容。树或按来源分组若能改善阅读，可以先作为同一数据集合的展示方式评估，不能同时维护扁平表、分组表和树三份可变目录。

ResearchIndex 当前还是全局分组快照，与此次目标有偏差。是否删除它、保留仅某个业务用途或改名，需要整体索引定案后沿生产者到消费者一起确定；本轮不保留一个旧机制又叠加新目录。

## 8. 后续确认与验证重点

调研支持的推荐方向是：**一份完整工件清单，直接工件引用，每次操作只提交产物增量；任务句柄不冒充工件索引。** 完整索引结构和延迟结果的具体协议仍由用户讨论确认后实施。

后续验收应检查真实集合和接入链：授权输入与实际返回产物的去重全集是否完整、同一工件是否只出现一次、直接引用能否读取、派生来源与工作归属能否追溯、暂停/失败/重试/压缩后能否找回结果、ask_user 是否确实不新增工件。第二及第四部分不得被摘要或选择性裁剪，容量不足时应明确处理而非静默遗漏；具体容量策略在实施计划中确定。

本轮只做方案记录与源码核对，没有科研质量提升结论，也不把目录结构选择当成改架构的目的。

## 9. 用户提出的三个具体问题：补充调查与明确建议

上一轮回答只说明可以采用平表，没有回答去重、排序和层数的实际取舍。本节给出明确推荐，仍是待用户确认的方案，不改变前文已确定的上下文边界。

### 9.1 去重放在哪里

**搜索命中的资源去重由工具领域逻辑负责；完整工件目录只合并同一工件身份。跨调用复用在获取/登记入口完成，不放到每轮上下文目录渲染中猜测。**

| 情况 | 推荐位置与规则 |
| --- | --- |
| 同一次搜索重复返回同一网页/论文 | 工具内部清理。网页使用准确 URL 或有明确依据的保守规范化；论文使用 DOI、明确版本的 arXiv ID 等稳定标识，不能只按标题。 |
| 不同搜索再次发现同一份材料 | 工具调用已有登记/查询能力：同一资源、同类材料及相同快照可以复用既有工件；来源、内容或版本有实质差异则保留新快照。识别规则属于文献/网页领域，不让全局目录解析各种搜索 JSON。 |
| 同一工件从输入、工具结果和工作交付多次接入 | 完整目录按 artifact_id 只显示一条，保留真实生产来源及必要的多个交付/引用关联。 |
| 同一论文的元信息、PDF、提取全文，或同一 URL 的不同正文版本 | 不是应删除的重复工件。各自有独立类型、内容或版本和来源关系。 |

例如搜索一得到 `[A, B]`、搜索二得到 `[B, C]`：两份检索结果都是调用事实，应完整保留；B 的同一份材料可共享一个工件 ID，在完整目录只出现一次。不能改写第二份冻结清单把 B 删掉，也不能把不同正文的网页仅按 URL 合并。复用不等于跳过必须进行的新抓取。

**特别前提：**按用户前文“一次搜索返回单份列表工件”的目标，列表内候选不是天然的独立工件。因此，对工件 ID 去重不会让所有历史列表里的候选自动唯一。建议允许同一候选出现在不同真实搜索快照中，在实际获取/登记材料时复用；本轮不额外引入一份全局候选资源目录。若目标变为“模型始终得到所有搜索候选的唯一全集”，那需要明确新增资源目录职责，不能假称现有工件去重已经做到。

源码依据：

- [DSH mergeSearchResults](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/tool-web/src/search.ts#L258) 在搜索工具内部用 URL Set 合并单次多查询结果，保持轮流取结果的次序并执行上限。没有把搜索清洗交给全局工件目录。
- 新增阅读 Zotero `44fe83d98bcf9b982b74782dcc5f824c8ab9cb43`：[duplicates.js 的 ISBN/DOI 检测](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/chrome/content/zotero/xpcom/duplicates.js#L194) 使用文献领域标识，[标题候选检测](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/chrome/content/zotero/xpcom/duplicates.js#L278) 还检查 DOI/ISBN 冲突、年份及作者。这是资源库领域行为，不是目录排序时按标题静默删记录。不能把候选相似直接当成可安全合并的身份。
- ADK 的名称 set 和版本保存、OpenHands 的输出内容 hash、Codex 的附件 identity_key，分别对应不同对象的身份/存储处理，不能统称为已经解决了论文/网页的跨检索去重。

本项目当前已有这部分基础：`web_deepseek.py` 单次按精确 URL 去重；`literature_search.py` 单次按 paper.key 去重、并对相同 key 和完整元信息复用既有 Ref；`materials.py:merge_artifact_index` 只按工件 ID 合并；Scientific 登记身份包含内容和来源。未来统一应明确这些职责，不能把它们搬成全局索引里的一个万能去重函数。

### 9.2 完整目录按时间/调用，还是按内容组织

**推荐按内容类型组织，同类型内按首次进入当前目录的次序稳定排列。调用、工作和时间保留为条目属性，不构建调用树。**

第三部分和持久事件已经记录动作顺序；第四部分主要解决“去哪找某份材料”。将所有内容绑定到某次调用，会让同一论文在两个搜索分支下重复，或让同一交付物只能归属于一个工作。完整目录不应承担另一份调用流水的职责。

展示可采用一张带类型列的表，按现有 kind 对应的用途排列，例如检索清单、文献材料、网页材料、工作交付。每个工件 ID 全表只出现一次；不用 LLM 重新判断主题和重要性，不选择性隐藏条目，不另增可变分类体系。不同 Agent 得到各自完整授权目录，采用同一组织规则。

这不是“所有优秀项目都按内容分类”的结论：ADK 实际按文件名字典序列举；Codex 的应用附件表按创建时间和 ID 排序。新增核对的 Zotero 更清楚地区分这几件事：[统一 items](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/resource/schema/userdata.sql#L158) 保存身份，[collectionItems](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/resource/schema/userdata.sql#L313) 允许同一条目属于多个集合，[排序代码](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/chrome/content/zotero/itemTree.jsx#L1851) 根据当前选择列和次排序工作。借鉴点是身份、内容关系和展示顺序分开；本项目选择内容优先，是针对材料导航用途的取舍，尚无 A/B 质量提升结论。

### 9.3 一层、两层还是多层

**推荐一份完整、直接可读的工件目录；不再维护“调用索引 → 工件索引”。** 类型只作为列或全部展开的排版标题，不创建类型 ID、分类索引工件或另一份分类映射事实表。为避免名称争论，默认示例直接用平表：

| 类型 | 工件 ID | 说明 | 来源/关联 |
| --- | --- | --- | --- |
| 检索清单 | s1 | 第一次搜索结果 | call_1 |
| 检索清单 | s2 | 第二次搜索结果 | call_2 |
| 文献 | p1 | 已获取的题录摘要 | 稳定文献标识 |
| 文献 | t1 | 对应全文 | source=p1 |
| 网页 | w1 | 官方页面正文快照 | URL 与获取调用 |
| 工作交付 | r1 | 工作简报 | work_1 |
| 工作交付 | m1 | 对照结果 | work_1 |

模型从完整表直接得到 t1、r1 等可读 ID。正文关系仍存在，但两端都已列出，读取不必先打开另一份索引。Zotero 的 [论文与附件关系](https://github.com/zotero/zotero/blob/44fe83d98bcf9b982b74782dcc5f824c8ab9cb43/resource/schema/userdata.sql#L205) 证明题录与 PDF 各有身份且有真实内容关系；不意味着 ResAgent2 必须把父项设成唯一导航入口。

两层并非普遍错误：“资源 → 不同版本/附件”的结构适合专门的长期文献库，Zotero 就有这样的内容层次；“调用 → 产物”也可以用于操作记录查看。但前者会给本项目增加独立资源身份及统一版本归属机制，后者又重复动作流水。当前目标是统一工具接入与完整工件导航，已有工件 ID 和来源关系足以表达，尚无必要引入这些额外目录。

磁盘继续按 ID 保存冻结文件，权威登记继续维护身份、hash 和归属，模型目录从它们及授权事实生成。这是存储与展示分工，不是要求 LLM 经过两次索引解引用。延迟 WorkRequest 的工作句柄和终态获知仍按第 6 节单独明确，不能借本节的一层目录建议掩盖未定协议。
