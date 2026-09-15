# Capabilities

数据集能力集中在 `dataset.py`：DatasetCatalog 读取部署登记；resolve_dataset_refs 返回 DatasetAvailability（可用路径 / 不可用 ID）；dataset_context 与 dataset_env_overrides 消费同一结果。目录缺失不阻塞无关工作；危险路径和坏配置仍拒绝。三个 Agent 复用它，调用/恢复时重新检查；目录存在不保证内容完整。此检查结果不持久化成第二份资源状态，也不是新的管理器。

依赖安装仍由 EnvironmentManager、run_setup、audit_env 配合；包缓存归 pip/conda，不归 DatasetCatalog。

Agent 能做什么（可装配的具体能力）：文件、Git、进程、Artifact、仓库、环境、数据集、硬件等。

`workspace_context.py::workspace_context` 是三个 Agent 共用的轻量上下文投影，不是新 Agent 或缓存服务。Coding/Experiment 使用文件、工件与可选环境绑定；Scientific 只用工件读取，不传环境绑定：

- 从工具实际使用的 `EnvironmentBinding` 读取当前环境与 `certified`，不以 Session 里的历史 environment/env_audit 副本判定现状；
- 用 runtime 的同一个片段函数分别保留文件/工件正文：Coding/Experiment 各占有效输入的 25%，Scientific 只有工件、占 50%，按 4 字符/token 换算。一类读取不会淘汰另一类；同类多个来源共享额度。保留有界已读来源索引和目录清单；来源索引只说明读过，不证明当前内容未变化；
- 近期片段优先装入后，按原始事件编号 `observed_at` 从旧到新展示。对后续有同路径成功内置写入的文件读取，附 `modified_after_read_at`，保留修改前正文而非清空历史；失败修改不标记，冻结工件不标记。无标记也不保证外部进程未改变文件；
- 工作集 `truncated` 表示展示正文不完整，`context_truncated=true` 表示工作集预算追加截断；短操作历史只是 preview。原始工具观测仍留在 Session，不因渲染改变；
- `read_file` / `read_artifact` 都支持可选行范围。后者先核对 Run 授权及整份工件 SHA256，再切片；`text.py` 只负责两者共用的行切片；
- `search_text` 支持工作区内单文件或目录，沿用相同路径/软链授权检查；是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按原文匹配。工具说明提示分别搜索、行范围与参数上限。

内容超过各自预算仍会截断或淘汰，不宣称所有已读内容永久可见；模型可按来源和行范围取回需要的部分。正文工作集是 required，仍一起计入 Agent 总输入预算；Scientific/Coding/Experiment 默认上限为 128000 tokens，用户配置和模型容量仍可限制它。Scientific 的多个工件共用工件组额度，不另加一份额度。没有动态分配器，也不维护 read_artifact_summaries 等第二份正文缓存。详见 [上下文预算](../../docs/current/CONTEXT.md#budgets)。

`replace_text` 要求的是**每次调用的 old_text 唯一匹配**，不是整个任务只能替换一次。需要多处修改时可分别调用，每次以实际文件内容和执行结果为准。

runtime 只定义「Agent 怎么运行」（Agentic Loop 和 Tool 接口）；capabilities 提供「Agent 能做什么」的具体实现。三个 Agent 各自通过 Tool Profile 装配自己需要的部分——依赖本包不等于自动获得所有能力。

代码依赖分成两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`。composition root（CLI/E2E）同时依赖 orchestrator 与具体 Agents 并注入 ModulePort；orchestrator 不 import 具体 Agent。capabilities 依赖 runtime（Tool 协议、AgentState/ToolObservation）和 contracts，不依赖任何具体 Agent。

`literature.py` 提供 `LiteratureSearchBackend` Protocol、`ArxivLiteratureBackend`、`MultiSourceLiteratureBackend`、`LiteratureSearchTool` 与 `ArtifactRegistrationPort`；`openalex.py` 提供 `OpenAlexLiteratureBackend`。CLI/E2E 将 arXiv、OpenAlex 作为平级来源装入同一列表，互为备份；Agent 仍只依赖原 Protocol。Tool 从 `AgentState` 取 run_id/session_id 做 provenance，不自行分配 ArtifactId/hash。

来源选择只保存一个实例内索引：首次按组合根配置顺序尝试，成功后继续用该源；它不可用则依次试其他源，每次检索最多遍历一轮。当前初始顺序是 arXiv、OpenAlex，不赋予固定主备身份；切到 OpenAlex 后，它不可用也能切回已恢复的 arXiv。没有轮询探活、健康表、额外冷却状态或持久选择记录，新建实例从初始顺序开始。不强制每次轮换，也不同时查询两个源。

两个 HTTP 后端复用私有 `_literature_http.py`：应用 User-Agent、进程内按来源串行，arXiv 请求结束后至少间隔 3 秒、OpenAlex 1 秒。429 不立即重试，冷却至少 60 秒；Retry-After 支持秒数/HTTP 日期，更长则遵守更长等待。5xx/408 带 Retry-After 时同样进入冷却；其余超时、网络和 5xx/408 最多 3 次 HTTP 尝试，退避 3/6 秒；耗尽后冷却。冷却期调用直接返回不可用，不在 Agent 内长睡眠。`max_retries` 沿用已有参数名，含义是总尝试数。

来源切换仅捕获 `LiteratureUnavailableError`；正常空结果是成功响应，HTTP 4xx（除 408/429）、无效 XML/JSON 不触发切换，也不吞掉编程异常。不可用原因写应用日志；全部不可用时汇总各源原因报错。结果保留真实来源：arXiv ID/链接，或 `openalex:W...` / OpenAlex Work URL。只返回一个成功来源的结果，不合并两源、不把同标题当成同论文。全部不可用不登记空工件伪装成功。

OpenAlex 的可选 `OPENALEX_API_KEY` 仅由组合根读取，经 Authorization header 发送，不进入 URL、论文记录或模型上下文；未配置时匿名请求，额度/授权以服务端政策为准。摘要缺失就留空，两源都最多保留每篇 2000 字符，不获取 PDF 或新增 LLM 摘要。工件继续为原有 Markdown 条目。

节奏和冷却只协调**同进程**内的实例，重启不保留；并行进程/同出口其他程序由部署方协调，不能据此宣称跨进程限流。实现没有轮换 IP、持久缓存、任务队列或多源融合框架。

依据：[arXiv API 使用约定](https://info.arxiv.org/help/api/tou.html)、[OpenAlex 鉴权](https://help.openalex.org/api/authentication/)、[OpenAlex Work 字段](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md)。
