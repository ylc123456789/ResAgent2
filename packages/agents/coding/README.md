# Coding Agent

程序员。

输入：代码任务、WorkspaceGrant、constraints、input Artifact。
输出：完整代码变化、验证结果、风险和 ArtifactCandidate。

复用 runtime 的 filesystem、process、Git、permission、context 和 AgentLoop。代码策略、编辑工具、验证策略和 patch finalizer 属于本模块。

本模块是第一个重写的专业 Agent。
