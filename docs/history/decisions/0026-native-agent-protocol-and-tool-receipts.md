# ADR-0026：统一原生 Agent 动作与工具事实回执

状态：已实施，2026-10-09。基线：main@fbf1a00；工作分支：fix/native-agent-receipts。

## 问题与目标

AgentLoop 的生产 Agent 已使用原生 function call，但旧测试和注入客户端仍支持正文 JSON 动作。第二份静态工具名清单与实际注册工具漂移；相关短历史呈现也形成另一条上下文路径。HTTP 解码异常、Experiment 安装命令汇总和字符续读回执分别存在局部缺口。

目标是沿现有架构消除重复协议，并使模型取得清楚、可追溯的工具事实；不改变 Scientific 的科学判断、Compiler/Interpreter 分工或四部分上下文。

## 决定

1. 三个 Agent 及脚本测试统一 next_tool_call。删除 AgentLoop 的正文 JSON 路径、额外 tool_contracts/recent_observations、AgentDefinition.action_type 与各 Agent 工具名 Literal。实际 Tool 是 schema、说明和派发的唯一来源。无兼容入口。
2. Compiler 保留无状态结构化 JSON 输出；历史摘要保留纯文本输出。原生 arguments 仍校验 JSON，错误与未知工具进入现有有界反馈，调用与未执行回执完整配对。
3. HTTP 压缩正文损坏在请求所属边界转为已有领域错误，搜索仍冻结失败回执、网页抓取不登记假正文；ModelRequestClient 仍只请求一次。预算与 Run 截止透传，既有主模型解码错误只补安全 trace 诊断，不新增重试。
4. Experiment 的 execution_record 从同一 Session 事件汇总 run_shell、run_setup 的实际结果，保留顺序和失败；没有 exit_code 的 blocked 观察不属于已执行命令。
5. 共用文本窗口增加 next_start_char，坐标是完整所选行范围内的实际返回末端，无余文为 null。保留请求范围与 truncated 定义；上下文再次裁剪时，原续读边界不证明展示片段连续覆盖。
6. 保留问答作用域：answer 续原 Attempt/Session；失败重试创建独立 Attempt/Session，必要时重新问。旧问答仍保存，不自动跨尝试携带或复用批准。审查复现证明隔离现象，不证明失败即未投递。

## 影响与验证

Run/Session schema 仍为24.0，原生生产 Session 的协议身份不变；旧正文 JSON Session 不恢复。只实现 next_action 的自定义 Agent 客户端必须改原生接口，没有转接层。测试 fixture 工厂仅构造原生提供方回合，不参与生产派发。

确定性测试覆盖未知工具恢复及连续失败边界、完整原生输入容量、坏 gzip 的失败工件与计量、实际 setup 汇总、无遗漏字符续读、读取工作集再次裁剪和重试问答隔离。mock E2E 检查公共组合链，真实服务器任务检查模型是否能消费网页材料；不以接口回归宣称科研质量普遍提升。

墙钟回拨项按用户决定暂缓。测试步骤和结果见[本轮测试交接](../reviews/NATIVE_AGENT_RECEIPTS_TEST_PLAN_2026-10-09.md)。
