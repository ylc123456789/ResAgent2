# 工具结果、工件与模型上下文调研

> 状态：架构评审材料。本文记录调研结论和建议，不表示建议中的改动已经实现。
>
> 调研范围：Codex 的公开工具调用契约、Claude Code SDK 类型、DeepSeek Harness（DSH）公开包和文档，以及 Pi 类开源 coding agent 的公开会话/压缩模式。重点是工具结果怎样进入下一次模型请求，而不是搜索供应商本身。

> 当前实现核对（2026-10-07）：本文第 3--9 节保留调研时的候选方案和验收建议；其中关于“新增多重分组/状态字段”的风险分析不应被理解为当前模型输入契约。当前分支已经收敛为四部分输入：固定契约、任务需求、原生 assistant/tool 历史、当前任务上下文加唯一完整 `artifact_index`。`Run.artifacts`/Registry 仍是工件唯一权威来源，`ResearchIndex` 只做 Scientific 导航和代码侧校验；`artifact_index` 只提供 compact 元信息，不复制正文、URI、hash 或权限。以下“建议”只有在明确标注为待验证时才代表后续工作。

## 1. 要解决的问题

ResAgent2 同时有三种容易混在一起的东西：

1. **工具回执**：这一次调用发生了什么，供下一轮模型继续决策。
2. **正式工件**：经过登记、校验、冻结和 hash 的 Run 材料，供交接、恢复和引用。
3. **模型上下文**：本次请求实际发送给 LLM 的有限内容。

它们的关系不是“一个对象的三个名字”：

```text
工具调用 -> 工具回执 -> (必要时) 登记正式工件 -> 上下文投影
```

回执可以只存在于 Session 历史；工件可以被后续请求通过 ID 读取；上下文每轮重新选择，不能当成永久记忆。一个文件已经登记，也不代表全文每轮都发送给模型；一个回执进入历史，也不代表它是科研证据。

这一区分直接服务于 LLM-first 原则：代码保留事实、身份、权限、预算和来源；Scientific 决定材料对当前问题是否有用，以及哪些材料支持最终意见。

## 2. 调研得到的共同模式

### 2.1 DSH：事件日志是历史，消息是派生视图

DSH 的 `Session` 是追加式、带序号的 typed event log。`user/message`、`assistant/message`、`tool/call`、`tool/result` 等事件保存完整交互；下一次请求的消息历史从日志派生，而不是另存一份可变的“当前 prompt”。请求头、模型路由和压缩边界也是日志事件，但不都直接成为模型消息。

这带来三个清晰边界：

- 工具调用和回执是模型可见的历史消息；工具的内部元数据可以只给 UI 或重放逻辑使用。
- `request/header`、路由等控制信息用于重建请求，不伪装成用户材料。
- 压缩通过显式的 compaction 事件记录摘要和边界；原始事件仍可恢复，模型只在当前容量内看到摘要加近期完整历史。

DSH 对超长结果采用 spill：保存**完整文本**，返回不透明 locator、字节数和检索提示；模型上下文只保留有界的 head/tail 预览和“用 read/grep 继续读取”的提示。spill 是工具结果的溢出存储，不是研究 Artifact Registry，也没有把每个 URL 自动升级为正式材料。

DSH 的 web 工具也体现同一原则：

- `web_search` 是 discovery，返回可选 answer、URL、snippet；
- `web_fetch` 是对指定 URL 的显式获取，返回正文和截断信息；
- provider、工具 schema、提示词和 UI presentation 分开；
- 搜索命中不会自动创建一个持久资源对象。

可借鉴的是“事件历史 + 有界观察 + 显式继续读取”，而不是照搬 DSH 的 TypeScript 包层级。

### 2.2 Claude Code：工具输出有稳定的分页和来源标记

Claude Code SDK 的公开类型显示：

- `WebSearch` 只接收查询，结果是标题和 URL 的命中集合；
- `WebFetch` 接收明确 URL，返回获取后的结果、状态和耗时；
- `Read` 返回文件路径、起始行、总行数和当前内容；超过 token 上限时有 `truncatedByTokenCap`；
- 读取已保存材料时可以附带 `artifactRead`，标记材料及版本已经被读取。

这里的关键不是字段名字，而是模型操作顺序：先拿一个小的、可定位的观察，再按 offset/range 继续读取；读取标记帮助系统和 UI 追踪“读了哪个版本”，但不会把所有全文永久塞进上下文。公开 SDK 类型不能证明 Claude 内部所有压缩策略，因此只采纳其可观察契约，不猜测未公开的实现。

### 2.3 Codex / Responses 类接口：对话项和文件引用分开

Codex 使用的公开模型接口把 assistant 的 tool call 和 tool result 作为有序输入项继续发送。模型在下一轮看到的是这些项及当前系统/用户消息，而不是工具进程的全部内部状态。文件、容器或其他大对象通过 ID、路径或工具提供的引用读取；需要正文时再发起读取，读取结果进入后续消息。

这类接口的稳定启示是：

- `call_id` 用于把调用和结果精确配对；
- 工具结果必须是模型能理解的短观察，不能把内部日志、认证信息和执行器对象直接暴露；
- 大内容用引用和后续读取控制 token，不能假设模型请求可以无限增长。

Codex 的产品实现和服务端索引并不完全公开，所以不能把其网页索引、内部 rerank 或压缩机制当成 ResAgent2 可复制的代码。能复制的是消息契约和引用边界。

### 2.4 Pi/开源 coding agent：JSONL 会话 + 有界压缩

Pi 类开源 coding agent 通常把每轮 user/assistant/tool call/tool result 写入 JSONL 会话，读取文件和命令输出保留工具名、参数、结果和截断标记。上下文接近容量时，对较早回合做摘要或丢弃可重建的工具细节，保留最近动作和当前目标。长文件通过行号、offset 或 grep 继续读取。

这类项目普遍没有把“搜索命中”“已读取正文”“最终引用”做成一个通用资源状态图；它们把工具历史当作运行记录，把需要跨轮复用的文件放在工作区或引用存储里。这个取舍符合 ResAgent2 的“不为可能需求提前造框架”原则。

## 3. 对 ResAgent2 当前架构的判断

当前设计已经有几个正确基础：

- `Run.artifacts` 是唯一登记表；
- `research_index` 是从 Run 和工作记录派生的导航，不是第二个状态库；
- `ArtifactCandidate -> Registry -> ArtifactRef` 区分候选文件和冻结工件；
- `Session/tool_turns` 保存模型交互和工具回执；
- `ContextComposer` 按 required/priority/weight 重新选择材料，正文可用 `read_artifact` 分段读取；
- Scientific 通过 `evidence_artifact_ids` 表达最终引用，不能把“已读”直接当成“证据成立”。

调研版本曾考虑把 `artifact_role`、`review_status`、`evidence_level` 和 `searches/candidates/materials` 分组直接暴露给模型；这会带来三个风险：

1. `ArtifactCandidate` 原本是“等待 Registry 冻结的文件对象”，如果再用 candidates 表示搜索命中，模型和代码都会遇到术语冲突。
2. `selected/rejected` 目前没有完整的状态迁移和实际消费者，容易产生一个并不存在的筛选状态机。
3. kind、role、review、evidence、分组一起发送时，模型要重复学习多个标签；目前结构测试通过，但还没有真实 A/B 证据证明这些字段改善了 Scientific 的选择或结论。

这些问题不说明“文献搜索”和“联网搜索”必须合并。它们的后端输入、解析和来源不同；需要统一的是进入上下文的语义路径。当前实现已采用这一收敛方向，搜索和全文工具仍保持各自后端。

## 4. 建议的统一语义路径

把搜索和文献都按下面四步理解：

```text
发现 discovery
  -> 搜索回执 search receipt（记录这次动作及候选来源）
  -> 获取 material（明确 fetch/import 后登记的正文或文件）
  -> 读取 read observation（当前请求实际读到的片段）
  -> 引用 evidence reference（Scientific 意见中的 artifact ID）
```

### 4.1 搜索回执

一个搜索动作对应一个有界的 `search_receipt` 记录，至少包括：查询、来源/provider、时间、状态、候选数量、完整候选列表（在既有上限内）、预览数量和省略数量。候选行保存 title、URL/文献标识、snippet/abstract preview 和来源说明。

搜索回执回答“刚刚查了什么、得到哪些线索”。它不是正文证据，也不要求每个低价值命中成为 Artifact。失败和 partial 也要保留，使模型知道“没有结果”和“结果被限流/截断”不同。

### 4.2 正式材料

只有明确获取或导入的内容才进入正式材料：网页正文、PDF 原件、解析全文、用户导入的论文等。每个材料仍由现有 Registry 负责来源、授权、冻结和 hash；解析全文可以通过 `source_artifact_id` 指回 PDF。文献和网页可以有不同的 `kind`，但都遵循同一“来源 -> 可读取内容”的引用方式。

### 4.3 读取观察和引用

`read_artifact(id, range)` 返回本次真正读到的片段、范围、是否截断和来源 ID。片段进入当前 Session 的工具历史及 `artifact_reads` 投影；它不改变 Artifact 内容，也不自动把材料加入最终证据。

Scientific 在 `scientific_opinion.evidence_artifact_ids` 中选择支持结论的正式材料。需要表达“某段正文来自哪一页/哪一段”时，意见中的 claim/evidence 仍引用 Artifact ID 和读取范围；不新增一个独立的 evidence Artifact 类型。

## 5. research_index 和 LLM 上下文应该怎样分工

### 5.1 research_index 是导航目录

Research index 由 Run.artifacts、search receipt、work records 和读取记录派生，每轮可重新生成。建议只保留三组与研究相关的内容：

```text
searches   查过什么、候选线索、结果状态、可继续读取的来源
materials  已获取/导入/解析的正式材料、来源链、可读入口
evidence   已提交意见中引用了哪些 Artifact（来自 scientific_opinion）
```

已有的 inputs、work_requests 等运行交接信息继续按当前职责保留；不要再把 `selected/rejected` 当成新的权威状态。候选搜索命中在 index 里叫“候选来源”或“search result”，不要复用 `ArtifactCandidate`。

### 5.2 Scientific 每轮实际收到什么

默认顺序应是：

1. 当前研究目标、约束和未解决问题；
2. 紧凑的 research index 导航；
3. 最近搜索回执的少量候选预览及 omitted/incomplete 提示；
4. 已获取材料的 ID、类型、来源链、摘要/短预览和 `read_artifact` 入口；
5. 本 Session 最近读取的正文片段；
6. 当前工具回执、失败/限流/权限反馈和完成检查反馈。

全文不默认展开。模型需要细节时显式调用 `read_artifact` 分页；网页和文献获取也必须是显式工具动作，不能在 `read_artifact` 里隐式联网。这样每次网络成本、权限、失败和来源都留在工具历史中。

需要强调的标签只有三种：

- `search result / preview`：搜索线索或摘要，不能当全文依据；
- `material / readable`：已经获取并冻结，可以继续读取；
- `evidence reference`：Scientific 意见明确引用的材料。

不再同时给模型一套 `role + review_status + evidence_level + 多重分组` 的重复分类，除非某个字段有真实消费者和明确动作。

## 6. 一次研究检索的完整例子

用户要求 Scientific 查找某方法的局限：

1. Scientific 调用 `literature_search` 或 `web_search`。工具返回一个回执；回执进入 Session，并由 Controller/Interpreter 生成 index 的 `searches` 行。
2. Scientific 判断某个命中值得查看，调用 `fetch` 或文献全文获取。获取成功后，代码登记 PDF/网页正文为正式 Artifact，并把来源链写入材料。
3. Scientific 调用 `read_artifact(id, start/end)`，模型只看到这一段正文和范围；需要下一页时再次读取。读取记录进入 `artifact_reads`，但不复制一份正文到 Run。
4. Scientific 比较多个已读材料，形成 opinion；代码校验引用的 Artifact ID 属于当前 Run 且 hash 未变。
5. 下一轮上下文只重建 index、当前摘要和最近片段；旧的完整工具回执仍可从 Session 恢复，超长回执可以按 DSH 风格保存引用并继续读取。

如果搜索结果没有价值，流程在第 1 步结束；它仍作为一次搜索动作被记录，但不会污染正式材料清单。一次搜索产生多个阶段和多个来源时，index 通过 receipt -> material 的 ID 关系表达，不需要资源图或通用状态机。

## 7. 分阶段的最小改动计划

本文先不要求立刻改代码。若进入实现，建议按以下顺序，每一步都能单独测试和回滚：

### 阶段 A：先验证上下文是否真的更好

- 为修改前和当前分支保存同一批 Scientific 真实任务的最终请求快照（去除密钥和私有正文）。
- 比较模型实际看到的 index、候选预览、材料入口、最近读取片段、总 token 和重复信息。
- 记录目标命中、正文读取次数、摘要冒充全文次数、重复搜索次数、最终 evidence IDs 和结论质量。

### 阶段 B：只做术语和呈现减法

- 搜索命中统一称为“候选来源/搜索结果”，保留 `ArtifactCandidate` 的原有文件候选含义。
- research index 只保留真实需要的搜索、材料、引用信息；删除没有消费者的 review/evidence 重复字段，或暂时不向模型呈现。
- 文献和网页都使用同一 receipt -> material -> read -> evidence 呈现约定，后端工具不合并。

### 阶段 C：加强长内容引用

- 统一 read 工具的范围字段、截断提示、来源 ID 和读取记录；复用现有字符/行窗口规则。
- 对超长工具回执采用有界预览 + 可读取 locator 的模式；完整内容仍在受控持久存储，失败时保留原短回执。
- 保持 `read_artifact` 显式，不在读取工具里偷偷发起网络请求。

### 阶段 D：再决定是否改变 Artifact 登记策略

- 不先把每个搜索命中都登记为 Artifact。
- 只有真实模型 A/B 显示“候选需要跨轮复用但回执不够”时，才为某类候选增加最小的可持久记录；优先扩展 search receipt，而不是新建资源图。

## 8. 验收指标

结构测试不足以证明上下文质量。至少需要同任务成对验收：

- **召回/相关性**：目标一手来源命中率、无关来源比例、模型选择正确来源的比例；
- **证据诚实**：摘要级材料是否被误称为全文，结论是否都有已读 Artifact 引用；
- **效率**：平均搜索次数、fetch/read 次数、上下文 token、重复工具调用和网络失败恢复；
- **可恢复性**：暂停/重启后能否从 receipt、material ID 和读取范围继续；
- **可追溯性**：每条结论能否回到来源 URL/PDF、冻结 hash 和读取范围；
- **上下文清晰度**：模型是否能区分“查过”“已获取”“已读”“已引用”。

通过这些指标后，才可以说某个字段或分组改善了 Agent 性能；测试通过 schema 或 hash 只能证明结构正确。

## 9. 明确不做的事情

- 不为搜索和文献建立两套完全独立的上下文状态库。
- 不把所有搜索命中自动变成正式 Artifact，也不把所有摘要自动当作证据。
- 不新增一个跨搜索、网页、文献、文件的通用资源图或复杂晋级状态机。
- 不让 `read_artifact` 隐式联网；网络获取必须显式、可授权、可记录。
- 不把 Session 历史、research index、Run.artifacts 合成一个大 JSON 发给模型。
- 不因参考 DSH/Claude/Codex 而复制其未公开的供应商索引、rerank 或内部压缩实现。

## 10. 资料范围和限制

本次可直接核对的资料包括 DSH 的 `dsh-session`、`dsh-tool-web`、`dsh-spill` 包文档与类型，以及 Claude Code SDK 的工具输入/输出类型。Codex 依据公开的 tool call/tool result 和文件引用契约；Pi 依据公开的 JSONL 会话与有界上下文模式。Codex 和 Claude Code 的部分产品实现、供应商检索索引及完整压缩算法并不开源，因此本文只把可观察的消息、引用、分页和截断边界作为依据，不把推测写成事实。

