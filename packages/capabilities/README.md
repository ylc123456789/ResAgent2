# Capabilities

Agent 能做什么（可装配的具体能力）：文件、Git、进程、Artifact、仓库、环境、数据集、硬件等。

runtime 只定义「Agent 怎么运行」（Agentic Loop 和 Tool 接口）；capabilities 提供「Agent 能做什么」的具体实现。三个 Agent 各自通过 Tool Profile 装配自己需要的部分——依赖本包不等于自动获得所有能力。

代码依赖分成两支：`contracts ← runtime ← capabilities ← agents`，以及 `contracts ← orchestrator`。composition root（CLI/E2E）同时依赖 orchestrator 与具体 Agents 并注入 ModulePort；orchestrator 不 import 具体 Agent。capabilities 依赖 runtime（Tool 协议、AgentState/ToolObservation）和 contracts，不依赖任何具体 Agent。

后续 Phase 7 会加入 `literature.py`（论文检索能力）。
