# Coding Agent

程序员。

输入：代码任务、WorkspaceGrant、constraints、input Artifact。
输出：完整代码变化、验证结果、风险和 ArtifactCandidate。

复用 runtime 的 filesystem、process、Git、permission、context 和 AgentLoop。代码策略、编辑工具、验证策略和 patch finalizer 属于本模块。

本模块是第一个完成重写的专业 Agent。Phase 5 的原生实现提供两个 profile：

- `code_understand`：只读 list/read/search/Artifact/Git 工具，输出有证据路径的解释；
- `code_modify`：在干净 Git workspace 内进行精确替换或创建文件，运行预声明验证，并由 finalizer 生成真实变化和 ArtifactCandidate。

模块不接受 LLM 自报的 changed files、verification status 或 Artifact 路径作为最终事实。
