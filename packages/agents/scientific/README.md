# Scientific Agent

科学顾问。

输入：研究目标、明确授权的 Artifact、科学问题。
输出：WorkflowProposal、WorkflowPatch、ScientificConclusion 或文献分析。

允许：只读 Artifact、文献检索、科学推理。
禁止：选择物理环境、修改 TaskStatus、调用其他子 Agent、把建议描述成已执行事实。

本模块将最后重写，早期通过 legacy adapter 调用旧 ExpAgent。
