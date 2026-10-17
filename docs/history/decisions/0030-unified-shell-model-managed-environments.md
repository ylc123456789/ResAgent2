# ADR-0030：统一 Shell 与模型自主管理环境

日期：2026-10-16。状态：accepted，用户确认后实施。schema：28.0。范围：统一执行入口的第一阶段；自动审阅与沙箱不在本次实现内。

本决策取代 [ADR-0009](0009-shared-environment-capability.md) 的固定环境创建、绑定、认证、命名及版本切换管理，并取代 [ADR-0021](0021-shared-approved-linux-shell.md) 中保留专用安装/验证工具和失效环境认证的部分。旧记录及其验收结论按当时事实保留。共享执行组件、准确操作批准、任务职责、预算、中断和工件交接原则继续有效。

## 背景

旧实现同时向模型提供通用 Shell、环境准备、安装、审计和 Coding 验证工具。即使只是查看目录，run_shell 也要求先存在绑定环境，并可能执行自动审计；专用工具还带有各自的命令分类和完成状态。环境绑定、认证和 generation 被权限、完成检查、上下文和恢复共同消费。

模型已经能运行通用命令，环境的发现、选择、创建、依赖安装和诊断可以由模型按任务判断。继续保留专用生命周期，或换一种方式重做自动发现/绑定，会使同一操作存在多套模型入口和状态，并把模型本可承担的策略固定在代码中。

Conda/venv 主要隔离解释器及依赖，不提供宿主文件、网络或进程访问隔离。删除环境生命周期不能被解释为新增 OS 隔离；准确批准与受信执行的边界需要继续明确。

## 决定

1. **模型命令入口物理上只保留 run_shell。** 删除 run_setup、prepare_environment、audit_env 和 Coding run_verification 的实现、输入 schema、公开导出及注册，不保留兼容工具或替代管理工具。安装、验证、训练、评测和环境命令都走同一入口。

2. **环境由执行 Agent 管理。** 删除 EnvironmentManager/EnvironmentBinding、自动准备与审计、认证/generation、默认环境身份和自动重建/清理逻辑。版本与协作要求沿目标、约束、原任务指令和上级澄清传递；多任务需要共用时由 Agent 在任务或公开交接中说明路径，不固定共享路径。发现可访问环境不表示获准修改它。已有环境不会被系统自动接管、删除或重建；清理需要明确命令与正常批准。

3. **删除对应的公共环境控制字段。** EnvironmentSpec、WorkspaceSpec.environment、AgentRequest.environment_spec、prepare_environment 权限以及专用 CLI 版本/环境准备参数一并移除。Python 版本要求通过 goal/constraints 等原始业务输入表达，固定代码保真传递，不声称任意脚本及所有子进程都被强制绑定某个解释器。部署资源布局保留资源/数据集职责，删除受管环境根和专用 Conda 配置。

4. **保留现有进程与权限责任。** 固定 Git/仓库操作直接调用 Components；模型只通过 run_shell 提出命令。Components 负责实际 cwd、启动配置、过滤凭据、日志、超时、中断和进程停止事实；Runtime 的原生唯一回执、同一 Run 总预算和恢复规则继续沿用。run_shell 仍要求命令授权、完整可信工作区和每次准确人工批准，批准在副作用前单次消费；本次不改变默认授权策略，不加入自动 reviewer。

5. **明确 Shell 状态。** 保留 Linux `/bin/bash --noprofile --norc -o pipefail -c`，不隐式开启 errexit。每次独立启动，activate、export、cd 不延续到下次调用，不新增持久终端或后台作业管理。模型在同一脚本内激活并执行，或明确选择解释器/conda run；系统不隐式改写成绑定环境命令。

6. **统一记录实际事实。** 正常、失败和中断命令从原始回执生成 execution_record；保留脚本、退出/停止结果、耗时和日志，以及 cwd/shell/shell_args/pipefail/environment_fingerprint。指纹标识过滤后的启动环境，不保存完整继承变量值，不证明脚本内所有解释器、包版本或 GPU 可用性。Coding 可关联执行前后 Git tree 身份和实际变化；后置诊断失败保留原因，不能抹去已执行命令。删除专用系统验证工件生产链及认证完成门槛，模型解释检查充分性，代码核对真实交付和来源。

7. **以 schema 28.0 统一切换。** 公共模型与 Session 只接受新版本；旧 27.0 及更早 Run 明确拒绝恢复，不迁移、不重写，证据和已有文件保留。当前四部分上下文、业务问答来源、准确批准说明、UUID 身份及完整目录继续有效。

## 未采用与代价

- 不为保留旧接口建立空包装或新的自动环境绑定服务；生产者、消费者、字段与测试沿原路径一起清理。
- 不在本次引入持久 Shell。模型必须明确跨调用的环境选择，工具说明和通用任务提示负责告知这一事实。
- 不按工具名或浅层命令分类推断安装、验证是否足够。执行退出零不等于正确验证；环境变动后需要重做什么由模型判断，并按需采集可复现事实。
- 不承诺 Run/Workspace 自动获得独立 Python 环境。复用、隔离和共享要求由任务说明及授权命令表达，环境现状由真实探测取得。
- 当前仍是受信宿主账户执行，没有 OS 沙箱；审批不能阻止所有越界副作用。受限工作区不得借命令执行绕过现有拒绝规则。auto reviewer 与沙箱分别后续验证和实施，不借本决策声称已经接入。

## 外部实践依据及范围

读取 DSH 固定提交 `d743267388641bc76f17c45ce8b4c231aed1d32c` 的[普通 Bash 工具](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/shell/tool-bash/src/index.ts)、[工具 README](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/shell/tool-bash/README.md)及[本地执行器](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/shell/bash-local/src/index.ts)：已检查路径通过通用 Shell 请求执行，普通调用使用 fresh shell，没有强制 Python 创建/绑定/审计前置。它支持本决策的职责拆分，不证明 DSH 所有插件或部署都没有环境管理。

Claude Code 官方 [Bash tool behavior](https://code.claude.com/docs/en/tools-reference#bash-tool-behavior) 说明命令运行于独立进程，临时 export 不延续，并建议启动前激活 virtualenv/Conda。其 cwd 延续和启动配置规则与 ResAgent2 不完全相同；本项目保留现有 fresh shell 契约，不照搬整个闭源实现。上述判断来自已保存并读取的官方文档，不推断未公开内部机制。

## 验证边界

本 ADR 记录已经确认的架构取舍，不代表测试已通过。实施验收分别核对唯一工具表面、无环境前置的命令链路、任务原要求传递、准确批准与配置变化失效、真实执行记录、总预算、取消与唯一回执，以及旧 Run 明确拒绝恢复且证据未变。确定性回归和本地真实进程测试不代替真实模型、GPU 或 L3 科研质量验收。

当前事实以 [架构](../../current/ARCHITECTURE.md)、[契约](../../current/CONTRACTS.md#resources)和[上下文](../../current/CONTEXT.md)为准；阶段测试结论另存 reviews，不改写此前验收记录。
