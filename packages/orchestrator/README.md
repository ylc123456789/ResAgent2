# orchestrator

ResAgent2 的顶层控制模块。

负责：

- ResearchRun；
- Workflow validation/revision；
- Task/Attempt 状态；
- capability 路由；
- Module Port/Adapter；
- retry、Ask User 和 finish gate；
- Artifact index。

它不直接实现科学分析、代码修改和实验执行。普通调度必须由确定性代码完成；LLM 只用于计划提出/修订及需要智能判断的有界任务。
