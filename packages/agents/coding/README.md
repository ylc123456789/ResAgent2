# Coding Agent

程序员。

输入：代码任务、WorkspaceGrant、constraints、input Artifact。
输出：完整代码变化、验证结果、风险和 ArtifactCandidate。

复用 runtime 的 permission、context 和 AgentLoop，装配 capabilities 的文件、Git 和工件 Tool；准备/完成检查直接使用 components 的 workspace、process、Git 与 Artifact 操作。代码策略、验证策略和 patch finalizer 属于本模块。

原生实现提供两个 profile：

- `code_understand`：只读 list/read/search/Artifact/Git 工具，输出有证据路径的解释；
- `code_modify`：准备/复用仓库后在 WorkspaceGrant 内进行精确替换或创建文件，根据项目实际自主选择 shell-free 验证命令（经 `VerificationCommandPolicy` 约束），并由 finalizer 生成真实变化和 ArtifactCandidate。

模块不接受 LLM 自报的 changed files、verification status 或 Artifact 路径作为最终事实。

验证工具、命令规则与编辑 revision 配对集中在 [verification.py](src/resagent2_coding/verification.py)；进程执行本身仍复用 Components。
