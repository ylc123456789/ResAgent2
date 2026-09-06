# Capabilities

Agent 能做什么（可装配的具体能力）：文件、Git、进程、Artifact、仓库、环境、数据集、硬件等。

`workspace_context.py::workspace_context` 是 Coding/Experiment 共用的轻量上下文投影，不是新 Agent 或缓存服务：

- 从工具实际使用的 `EnvironmentBinding` 读取当前环境与 `certified`，不以 Session 里的历史 environment/env_audit 副本判定现状；
- 用 runtime 的通用片段函数把文件与工件放进**同一份 6000 字符**工作集，并保留有界已读来源索引和目录清单；来源索引只说明读过，不证明当前内容未变化；
- `read_file` / `read_artifact` 都支持可选行范围。后者先核对 Run 授权及整份工件 SHA256，再切片；`text.py` 只负责两者共用的行切片；
- `search_text` 支持工作区内单文件或目录，沿用相同路径/软链授权检查。工具说明提示范围与参数上限。

内容超过预算仍会截断或淘汰，不宣称所有已读内容永久可见；模型可按来源和行范围取回需要的部分。

runtime 只定义「Agent 怎么运行」（Agentic Loop 和 Tool 接口）；capabilities 提供「Agent 能做什么」的具体实现。三个 Agent 各自通过 Tool Profile 装配自己需要的部分——依赖本包不等于自动获得所有能力。

代码依赖分成两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`。composition root（CLI/E2E）同时依赖 orchestrator 与具体 Agents 并注入 ModulePort；orchestrator 不 import 具体 Agent。capabilities 依赖 runtime（Tool 协议、AgentState/ToolObservation）和 contracts，不依赖任何具体 Agent。

Phase 7.3 已加入 `literature.py`：`LiteratureSearchBackend` Protocol + `ArxivLiteratureBackend`（stdlib urllib + defusedxml，https，timeout/重试/退避，规范化/去重/截断）+ `LiteratureSearchTool` + `ArtifactRegistrationPort` Protocol。backend 由 composition root 注入；Tool 从 `AgentState` 取 run_id/session_id 做 provenance，不自行分配 ArtifactId/hash。
