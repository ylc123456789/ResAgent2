# 设计原则与架构约束

本文回答：**修改功能或修复错误时，哪些职责、机制和流程必须保持。** 它汇总项目已经采用的设计，不新增框架，也不要求所有实现细节永远不变。

[ARCHITECTURE](ARCHITECTURE.md) 说明当前模块和流程；[CONTRACTS](CONTRACTS.md) 定义接口、字段与行为；[CONTEXT](CONTEXT.md) 说明模型实际接收的信息。本文只维护跨功能的原则和检查方式，不重复字段表、状态表或测试轮次。历史 ADR 解释取舍，已被取代的旧字段和实现不因此恢复有效。

## 1. 简洁、通用的具体含义

模块相对独立，是指调用方依靠公开契约，模块有自己的职责和状态，可以单独测试、替换具体实现；不是零依赖、每类一个包或每项功能都部署为服务。共享 contracts/runtime/components 是有意设计。

简单是同一事实、同一执行语义有明确归属，同一行为只有一条生产主线。通用是在真实相同的使用场景间复用已有组件；新共享抽象至少应有两个语义一致的消费者。只供一处使用的小函数可以留在原模块，不为扩展可能性预造基类、管理器、事件总线或插件发现系统。

开闭原则（OCP）和依赖倒置（DIP）在这里服务于上述目标。它们约束依赖与修改范围，不是让所有类都多一层接口，也不是禁止修正既有代码。

## 2. 修改时保持的十二条约束

| 原则 | 在本项目中的要求 | 需要警惕的改法 |
| --- | --- | --- |
| 1. 职责分离 | Scientific 判断与提出证据需求；Compiler 翻译当前工作请求；Scheduler 执行任务；Controller 负责 Run 闭环；CLI 只做入口与装配 | Scientific 直接生成执行图，CLI 自己改 Run 状态，Scheduler 根据文字决定科学结论 |
| 2. 公共边界与依赖方向 | Agent 不直接互调；上游通过公开请求、结果、SessionRef 和工件交接；具体实现由外层组合根注入 | Orchestrator import 具体 Agent，Agent import 兄弟 Agent，读取下游私有 Session/memory 驱动调度 |
| 3. 单一 Agent 协议 | 每个 Agent 只有 invoke(AgentRequest) → AgentResult 和一种业务模式；instruction/input_artifacts 表达任务，report/artifacts 表达结果 | 为分析、执行、批准恢复另开业务入口或 mode；权限允许写就强制写、允许执行就强制执行 |
| 4. 共享机制、领域规则就地归属 | 三个 Agent 共用 AgentLoop；差异通过工具、上下文、权限和完成检查注入。Runtime 不理解具体科研任务；领域规则留在 Agent | 为单个 Agent 复制 Loop，或在共享 Loop 中按 Coding/Experiment 名称添加专用流程 |
| 5. 模型提议，代码裁定 | 模型表达目标、动作和观点；代码管理身份、合法图、依赖、状态、授权、预算及完成判据 | 解析 report/summary 推断成功，模型自己修改 Attempt/Run 状态，绕过接收校验 |
| 6. 状态与事实单一权威 | Run/WorkRequest、Task/Attempt、Session 各按既有所有权管理；目录登记、实际可用性、授权、环境绑定分别有明确来源 | 在 prompt、metadata 或另一个缓存里再维护一份权威状态；从磁盘有文件推断登记或执行成功 |
| 7. 控制循环各司其职 | Controller 科学闭环、Scheduler 任务循环、AgentLoop 工具循环保持分工；Compiler 是无 Session 的有界编译器 | 合成超级循环；为纠错新增一套控制器；把 Compiler 变为长期对话 Agent |
| 8. 问答与重试分开 | 回答继续同一 Task/Attempt/Session；真正失败重试才创建新 Attempt。原题、作用域、恢复材料和已消费身份必须匹配 | 通过新建任务/Session 来恢复问答，重复消费旧答案，恢复时重置预算、基线或工作区授权 |
| 9. Run 是预算和权限上限 | 子调用继承或收紧同一调用余额、期限与授权；重试、纠错、压缩和 Compiler 都占用 Run 总账；人工等待按现有规则扣除 | 各模块各开钱包，新增隐形步数预算，批准扩大授权，缺数据集时擅自下载或静默替换数据集 |
| 10. 副作用与批准可审计 | 权限检查与实际文件/进程边界仍有效；批准绑定当前精确操作，执行前持久消费。操作确认和执行回执区分；未知结果不自动重放 | 把问到同意当作已执行；复用一次批准执行其他命令；以“恢复”为名重复可能已经发生的副作用 |
| 11. 证据与完成分层验证 | Registry 冻结工件并校验来源/hash；跨任务用显式 output_name 绑定；Agent 检查领域事实，接收端和 Run gate 检查各自边界 | 用模型自报数值代替测量，把可读当成已读，把 schema 合法或 exit 0 当成科学结论正确 |
| 12. 修复保留真实失败与历史 | 失败、警告、消费与中断记录不丢；图按既有规则追加，不改写已执行历史；旧 schema 不兼容恢复，不保留无人需要的兼容路径 | 重试到绿后覆盖失败证据；为“恢复成功”清空预算/历史；只改生产者而遗漏接收、持久化或恢复链 |

这些要求约束的是语义，不锁死文件名、私有 helper 或无消费者的字段。删除重复包装、移动职责正确的代码、合并相同纯判据，可以让实现更简单；必须验证上述行为仍成立。新需求若确实改变这些边界，应明确形成设计决定、说明取代关系，并同步实现、契约和验证，不能藏在普通修复中。

## 3. 依赖倒置怎样落实

业务调用方向和源码 import 方向是两件事。Scheduler 运行时调用 Agent，但它在源码中只认识 ModulePort；CLI/E2E 组合根选择具体 Agent 并注入。Python 的 Protocol 允许结构化实现，具体 Agent 无需继承 Orchestrator 的基类。

| 调用方依赖的约定 | 具体对象由谁提供 | 保持的边界 |
| --- | --- | --- |
| [ModulePort](../../packages/orchestrator/src/resagent2_orchestrator/ports.py) | CLI/E2E 注入 Scientific 和任务 Agent | 编排不 import 具体 Agent，不判断其实现类 |
| [AgentDefinition / CompletionCheck / PermissionPolicy](../../packages/runtime/src/resagent2_runtime/loop.py) 与 [Tool](../../packages/runtime/src/resagent2_runtime/tools.py) | 各 Agent 组合已有工具和领域策略 | 通用 Loop 不承担领域工作流 |
| [LLMClient](../../packages/runtime/src/resagent2_runtime/llm.py)、[SessionStore](../../packages/runtime/src/resagent2_runtime/store.py)、[RunStore](../../packages/orchestrator/src/resagent2_orchestrator/store.py) | 入口或调用方注入实现 | 模型调用和持久化通过既有接口替换，仍遵守行为契约 |
| [LiteratureSearchBackend](../../packages/components/src/resagent2_components/literature/backends.py) | 组合根装配文献来源 | Scientific 无需知道供应商响应格式 |
| [ArtifactRegistrationPort](../../packages/components/src/resagent2_components/artifacts.py) | 组合根注入 Orchestrator 的登记实现 | 文献 Tool 不反向 import Orchestrator；登记规则仍由 Registry 管理 |

完整依赖图及允许范围只在[模块边界](ARCHITECTURE.md#modules)维护。当前 Orchestrator 除 contracts 外，还明确使用 runtime.budget 与 components.workspace；这两个有限依赖已经属于现行设计，不能引用早期 ADR 的旧简写强行删除。

Controller 与 Scheduler 是同一 Orchestrator 包内的协作对象，使用同一 store/registry 和必要私有 helper 不等于跨 Agent 读取私有状态。普通稳定组件也不必为了形式上的依赖倒置再套 Protocol。

## 4. 开闭原则怎样落实

优先沿已存在的变化点扩展，避免每加一种能力就改所有 Agent 或核心 Loop。但当前系统有意保留有限的 Agent 类型、工具白名单和权限规则，不承诺任意新行为无需修改代码。

| 变化 | 正常修改范围 | 不应顺带改变 |
| --- | --- | --- |
| 替换某个 Agent 实现 | 实现同一 ModulePort，在组合根接线，通过同一返回边界与流程测试 | Controller/Scheduler 增加针对新实现类的分支 |
| 增加普通模型工具 | Tool/输入 schema、所属 Agent 的显式工具列表与动作 schema；按需要复用 Components | 通用 AgentLoop 的控制流程 |
| 增加有副作用的操作 | 在上述基础上显式审查授权、目标快照、执行边界、失败及恢复；必要时修改共享权限策略 | 假定只注册工具就自动获得安全规则，或为了零改动允许未知操作 |
| 替换模型、存储或文献源 | 现有接口的实现和组合根；遵守预算、协议身份、错误与持久化约定 | 把供应商分支散到所有 Agent，或在恢复时静默更换协议 |
| 新增顶层 Agent 类型或公共字段 | 明确的职责/契约变更，同步枚举、生产接收端、装配、版本和测试 | 声称这是现有接口的无成本插件，或保留两套业务协议 |

Capabilities、Components 不要求一一对应或强制逐层调用。领域 run_verification/run_command 留在各自 Agent，复用同一个 ProcessRunner；机制复用不等于领域规则也必须合并。

替换实现要满足相同的**行为**，仅有同名方法或 Protocol 类型检查还不够：身份、工件来源、暂停返回、预算计量和失败都必须守约。最小模型客户端与原生工具客户端、Compiler JSON 与 Agent 工具协议是底层传输差异，不是第二种 Agent 业务模式；Session 固定协议身份且不自动降级。

## 5. 每次变更怎样检查

评审至少回答以下五项，受影响的项用具体代码和测试说明，未涉及的项也核对没有被旁路：

1. **归属与依赖**：需求属于哪个模块？是否引入兄弟 Agent 调用、反向 import、私有状态读取或重复权威？新增依赖是否符合现有图？
2. **扩展方式**：现有 Port、Tool、完成检查或普通函数能否表达？为什么需要修改共享机制？有没有新增 mode、兼容分支或只为一个补丁存在的框架？
3. **完整流程**：沿生产者 → 校验 → 持久化 → 消费者 → 失败/问答/重启追踪；保持 Task/Attempt/Session、工件和单次消费语义。字段没有真实消费者就不要加入公共契约。
4. **预算、授权与事实**：子调用是否只收紧？副作用前是否检查？失败/中断是否保留？数据、命令回执和观点是否各有来源？不能只检查正常成功路径。
5. **验证与文档**：运行相应边界/行为/公开入口测试；影响模型输入或执行链时再安排真实验收。同步当前文档；重要取舍追加 ADR，阶段结果放 history/reviews。失败用例不能通过放宽断言消失。

自动测试按已有模块目录维护，不新增架构检查框架：包边界 AST 测试约束 import；contracts/接收边界测试约束协议；问答、预算、权限、工件、恢复和最终 gate 用行为测试；公开入口整链检查真实装配。具体命令见[开发与验证](../guides/DEVELOPMENT.md#local-checks)与[测试目录](../../tests/README.md)。

静态 import 检查不证明全部运行期语义，确定性脚本响应不证明模型永远正确，一次真实 Run 也不证明通用成功率。当前保证仍限于单 Run 单写入者、现有授权边界和持久化机制；没有 OS 沙箱、全局事务或副作用 exactly-once 的承诺。

## 6. 设计来源

- [ADR-0001](../history/decisions/0001-monorepo-and-module-boundaries.md)：逻辑模块边界与独立测试；旧专用输入输出已由统一协议取代。
- [ADR-0002](../history/decisions/0002-shared-agentic-loop.md)：共享 Loop 与注入差异。
- [ADR-0007](../history/decisions/0007-scientific-control-and-workflow-compilation.md)：科学判断、工作请求、图编译、确定性调度和最终 gate 分离。
- [ADR-0011](../history/decisions/0011-stabilization-schema-3.md) / [0012](../history/decisions/0012-state-recovery-boundaries.md)：单一控制面、状态权威与恢复边界；具体旧字段和计量实现以后续契约为准。
- [ADR-0015](../history/decisions/0015-tool-components-boundary.md)：Tool 与普通组件分离，不引入对应类层级。
- [ADR-0016](../history/decisions/0016-unified-agent-io-and-run-controls.md)：统一 Agent IO、Run 总账与权限上限、精确单次批准。

本页是这些现行原则的统一入口，不是新架构决策。实现与原则发生冲突时应记录具体差异，再修实现或提出有理由的设计调整；不能仅靠改文档把意外退化合法化。
