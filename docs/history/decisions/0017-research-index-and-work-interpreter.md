# ADR-0017：科研目录与反向工作解释

状态：accepted。日期：2026-09-23。

## 要解决的问题

Scientific 原来同时收到平铺的授权产物清单、完整 work_feedback 和内部 interpreter 生成的 work_brief。内容重复，执行细节直接进入科研层；先失败后成功的任务只在轮次摘要中关联最后一次尝试。原登记表已保存历史产物，不能为展示再建立一份独立产物权威。

## 决定

保留 Run.artifacts 作为唯一登记表。新增 research_index 作为派生科研目录：沿用原 Artifact ID，按已有研究目标和来源组织，保存必要状态，不复制路径、hash 或权限。目录可从登记表和历次执行事实重建，Controller 保存当前版本引用。旧版本不覆盖。

Interpreter 移到 Orchestrator，与 Compiler 并列。固定代码生成目录及变化，LLM 根据本轮实际材料生成每条带引用的简报。Controller 保存 work_record 和 work_feedback，负责预算、版本引用和交付。Scientific 默认只看到目录入口、当前变化和简报，仍可读取原产物作科研判断。原执行事实保留，最终验收仍读取机器事实，不以模型简报替代。

WorkInterpreter 是普通注入接口，不新增 Agent、Session、工具循环或调度状态。使用共享 PromptLLMClient、Run 预算和调用计量；结构错误最多纠正一次，引用必须来自提供的文本内容或完整执行记录。文本窗口有明确上限和截断标记；不把二进制文件当成已经解释的内容。

feedback_refs 的现有绑定就是交付准备检查点。已保存反馈可重复读取，不重新解释；保存前中断允许重做，但已花预算不退还。只有 Scientific 的有效返回才能消费 WorkRequest。目录或简报被读取，不自动授予原产物的观察记录。

## 取代范围

取代 ADR-0014 中“Interpreter 只位于 Scientific 内，纯投影且不调用 LLM”的实现选择，以及同时展示完整执行反馈的做法。保留其解释不能冒充测量、原题配对、证据来源、模块独立等约束。schema 升到 15.0；不迁移旧 Run，不提供旧反馈格式的兼容路径。

## 代价与边界

每轮正常增加一次简报模型调用；一次结构纠正及 HTTP 重试也计入 Run。没有额外模型钱包。目录和有效引用保证可追溯，不证明解释或科学结论正确。语义 validation、实验失败判据和任务重试机制不在本决定中重做。

当前接口与完整行为以 [CONTRACTS](../../current/CONTRACTS.md#interpreter)、[CONTEXT](../../current/CONTEXT.md#scientific) 为准。
