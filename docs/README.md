# 文档从这里开始

文档分成三类。区别是**你现在要解决什么问题**，不是给项目增加三层架构。

| 你想做什么 | 去哪里 | 维护方式 |
|---|---|---|
| 查现在有哪些模块、怎样调用、字段是什么意思 | [当前实现与规范](current/ARCHITECTURE.md) | 随代码一起更新，只写当前行为 |
| 查为什么这样设计、过去改过什么、怎么验收 | [决策与历史](history/README.md) | 保留当时记录；新决定、新结果另记 |
| 第一次使用或参与开发 | [入门与实践](guides/README.md) | 用例子解释和操作，示例随当前实现维护 |

## 第一次接触这个项目

1. 先读 [理解一次研究任务](guides/UNDERSTANDING.md)，不要求先认识所有类名。
2. 想操作系统，看 [CLI 使用说明](../apps/cli/README.md)；想动代码，看 [开发与验证](guides/DEVELOPMENT.md)。
3. 遇到具体模块或字段，再查参考，不必从头读完所有文档。

## 当前参考的三个入口

- [架构](current/ARCHITECTURE.md)：模块职责、调用方向、状态所有权和能力边界。
- [模块接口与契约](current/CONTRACTS.md)：按调用边界把方法、输入输出、字段、失败和恢复约定放在一起。原 CONTRACTS 与 INTERFACES 已合并，不再维护独立接口卡。
- [模型上下文](current/CONTEXT.md)：Scientific、Coding、Experiment 和 Compiler 实际看到什么；信息来源、用途、刷新、裁剪和预算。字段定义仍链接契约，不另造一套字段规范。

[上下文审查与方案演变](history/reviews/CONTEXT_REVIEW_2026-09-13.md) 保留最初问题与随后获批的实现；[最终验收与边界](history/reviews/CONTEXT_128K_ACCEPTANCE.md#verified-closeout) 记录真实回归、文献回放补验和报告勘误。历史候选不代替当前行为。

CLI 命令、环境变量和部署配置以 [CLI README](../apps/cli/README.md) 为准，不在架构文档再复制参数表。各包 README 只作包内入口和简短说明。

查具体操作的实现可看 [Components](../packages/components/README.md)；查模型可调用入口看 [Capabilities](../packages/capabilities/README.md)。二者没有一一对应关系，调用约定统一在 [普通组件接口](current/CONTRACTS.md#components)。

想看系统能否完成更真实的研究任务，使用 [L3 风格测试规程](guides/L3_RESEARCH_TEST.md)；外部基准与自进化方法的取舍见 [调研记录](history/reviews/L3_BENCHMARKS_AND_SELF_IMPROVEMENT_2026-09-15.md)。原生调用、串行续接和统一上下文分配的小型服务器验收见[阶段结果与勘误](history/reviews/CONTEXT_ALLOCATION_REVIEW.md#verified-closeout)。随后 `e6688f3` 的新 L3 已完整完成真实对照实验，详见 [复核、勘误与待办](history/reviews/COMPILER_CONTEXT_L3_ACCEPTANCE.md)。旧 Coding 暂停和 Compiler 超限的现场仍保留，不迁移、不倒写为通过；后续新 Run 仍需单独确认成本。

L3 预检遇到 arXiv 限流后，先按 [文献平级来源补验](history/reviews/LITERATURE_FALLBACK_ACCEPTANCE.md) 核验请求节奏、双向切换与真实检索，再确认新版 200 次调用 / 4 小时执行预算；旧预检与实验准备保留。

<a id="maintenance"></a>

## 修改代码时，文档怎么跟着改

| 代码改了什么 | 同步什么 |
|---|---|
| 模块职责、依赖方向、状态归属 | 架构；对应接口边界也有变化时再改契约 |
| 方法签名、字段、返回分支、接收或恢复规则 | 模块接口与契约 + 对应边界测试 |
| 模型可见内容、上下文段、刷新/裁剪/预算策略 | 模型上下文 + 对应构造测试；公开字段同时改变时再同步契约 |
| 用户命令、配置、安装方式 | CLI README；受影响的入门示例 |
| 重要设计取舍 | history/decisions 追加 ADR，并更新当前文档；普通实现细节不用写 ADR |
| 一轮审查、开发计划、服务器验收 | history/reviews 留记录；当前文档只吸收已落地的规则 |

不要在每份文档反复写“本轮待验收”或测试总数。轮次、提交和证据放在历史记录里；当前行为由代码与测试核实。教程是解释，不另造规则；历史中的“当前”“下一步”只对当时有效。

职责和字段描述必须能对应当前代码。发现不一致应修正文档或实现，不能靠“文档是权威”忽略真实差异。
