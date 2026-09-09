# Capabilities

Agent 能做什么（可装配的具体能力）：文件、Git、进程、Artifact、仓库、环境、数据集、硬件等。

`workspace_context.py::workspace_context` 是三个 Agent 共用的轻量上下文投影，不是新 Agent 或缓存服务。Coding/Experiment 使用文件、工件与可选环境绑定；Scientific 只用工件读取，不传环境绑定：

- 从工具实际使用的 `EnvironmentBinding` 读取当前环境与 `certified`，不以 Session 里的历史 environment/env_audit 副本判定现状；
- 用 runtime 的同一个片段函数分别保留文件/工件正文，**各 6000 字符**，一类读取不会淘汰另一类；同类的多个来源共享额度，不是每个来源各 6000。保留有界已读来源索引和目录清单；来源索引只说明读过，不证明当前内容未变化；
- 近期片段优先装入后，按原始事件编号 `observed_at` 从旧到新展示。对后续有同路径成功内置写入的文件读取，附 `modified_after_read_at`，保留修改前正文而非清空历史；失败修改不标记，冻结工件不标记。无标记也不保证外部进程未改变文件；
- 工作集 `truncated` 表示展示正文不完整，`context_truncated=true` 表示工作集预算追加截断；短操作历史只是 preview。原始工具观测仍留在 Session，不因渲染改变；
- `read_file` / `read_artifact` 都支持可选行范围。后者先核对 Run 授权及整份工件 SHA256，再切片；`text.py` 只负责两者共用的行切片；
- `search_text` 支持工作区内单文件或目录，沿用相同路径/软链授权检查；是大小写不敏感的字面子串搜索，不支持正则，`a|b` 按原文匹配。工具说明提示分别搜索、行范围与参数上限。

内容超过各自预算仍会截断或淘汰，不宣称所有已读内容永久可见；模型可按来源和行范围取回需要的部分。正文工作集是 required，仍一起计入 Agent 总输入预算；Scientific/Coding/Experiment 默认上限为 8192 tokens，用户配置和模型容量仍可限制它。Scientific 的多个工件共用工件组的 6000 字符，不另加一份额度。没有动态分配器，也不维护 read_artifact_summaries 等第二份正文缓存。

`replace_text` 要求的是**每次调用的 old_text 唯一匹配**，不是整个任务只能替换一次。需要多处修改时可分别调用，每次以实际文件内容和执行结果为准。

runtime 只定义「Agent 怎么运行」（Agentic Loop 和 Tool 接口）；capabilities 提供「Agent 能做什么」的具体实现。三个 Agent 各自通过 Tool Profile 装配自己需要的部分——依赖本包不等于自动获得所有能力。

代码依赖分成两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`。composition root（CLI/E2E）同时依赖 orchestrator 与具体 Agents 并注入 ModulePort；orchestrator 不 import 具体 Agent。capabilities 依赖 runtime（Tool 协议、AgentState/ToolObservation）和 contracts，不依赖任何具体 Agent。

`literature.py` 提供：`LiteratureSearchBackend` Protocol + `ArxivLiteratureBackend`（stdlib urllib + defusedxml，https，timeout/重试/退避，规范化/去重/截断）+ `LiteratureSearchTool` + `ArtifactRegistrationPort` Protocol。backend 由 composition root 注入；Tool 从 `AgentState` 取 run_id/session_id 做 provenance，不自行分配 ArtifactId/hash。
