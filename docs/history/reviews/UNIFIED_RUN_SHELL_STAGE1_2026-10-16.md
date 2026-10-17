# 统一命令执行入口：第一阶段实施与本地验证（2026-10-16）

从已推送的 `main@2bcde651f7bb31add86d79650255c410f7d3591a` 新建 `feat/unified-run-shell`。本阶段只统一命令入口、删除环境生命周期管理；自动权限审查和 OS 沙箱另分阶段实施。设计依据见 [ADR-0030](../decisions/0030-unified-shell-model-managed-environments.md)，当前规范见 [CONTRACTS](../../current/CONTRACTS.md)。

## 实施范围

1. Capabilities 的命令执行工具只保留 `run_shell`，实际删除 `run_setup`、`run_verification`、`prepare_environment`、`audit_env` 的实现、导出和注册。Coding、Experiment 使用同一工具，创建环境、安装、检查、训练和评测均提交普通 Bash 脚本。
2. 删除环境创建、绑定、认证、generation、重建与专用验证完成门槛。移除 `EnvironmentSpec`、相关权限字段、环境资源根和旧 CLI 环境参数，不保留兼容入口。已有环境、缓存、文件和历史证据不自动删除。
3. 环境选择由模型根据任务要求和机器情况判断；需要共享环境时，上游通过原任务输入和报告传递实际路径、解释器等信息。提示词仅说明通用职责：检查并遵循上游要求、明确使用的解释器，每次调用的激活、变量和目录变化不会自动延续到下一次。没有新增环境管理协议。
4. 固定代码继续在 Components 执行 Git 等内部命令，承担进程、超时、中断、日志和执行事实。Coding 从真实变更生成 patch；两个工作 Agent 从原生回执生成 `execution_record`。不再用专用验证工具或零退出码代替模型对检查是否充分的判断。
5. 保留现有 `execute_commands` 授权和逐条脚本的准确批准。批准绑定原脚本、工作目录、固定 Bash 参数及过滤后的启动环境指纹；执行前若配置改变，返回未执行，不能沿用旧批准。指纹不保存变量值，也不代表对 Python 环境作了绑定或认证。
6. 继续使用既有 AgentLoop、公共输入输出、共享总预算、取消和工件交接。四部分上下文保持原职责，只有第三部分可压缩。没有新增总体报告、索引层或模型总结步骤。

这是可信宿主机上的通用命令执行，**尚无 OS 沙箱**。脚本可创建、修改或删除其进程权限可访问的文件；应用层文件工具的路径限制不能约束任意 Bash 脚本。因此继续要求完整读写的可信工作区和逐次批准，不以虚拟环境代替安全隔离。后台任务仍不在支持的受管前台执行范围内。

## 兼容与文档

公共 Run/Session schema 升为 **28.0**。旧 schema27 状态明确拒绝 show/resume/answer，不自动迁移或修改历史证据。Python 版本等任务要求使用已有 goal、constraint、instruction 表达，不再由专用环境配置字段表达。

同步四份 `docs/current`、根 README、CLI、Capabilities、Components、Coding/Experiment 文档和开发/L3 指南；ADR 说明被取代的环境生命周期与专用验证规则。历史报告原文继续保留当时语境。

## 验证结果

在 WSL `Ubuntu-D` 的权威源码 `/home/cyl/ResAgent2`，使用既有 `/home/cyl/miniconda3/envs/ResAgent2/bin/python`：

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest tests apps/cli/tests -q --disable-warnings` | **2126 passed / 1 skipped，40.13 秒，rc=0** |
| `python -m e2e.mock_e2e` | **completed，8 件工件，rc=0** |
| `pip check` | **No broken requirements found，rc=0** |
| `git diff --check` | **rc=0** |
| 九个生产包导入 | **9/9 位于本次源码树的 src** |
| 跟踪中的生产 Python 源码与 E2E 旧接口检查 | **无已删除工具与环境管理标识残留** |
| 本地真实环境操作冒烟 | **32/32 断言通过，另独立复核 12/12，rc=0** |

冒烟使用 **NativeExperimentAgent + 脚本模型 + 真实进程**，逐次批准后执行：真实创建 venv、从本地自制 wheel 以 `--no-index --no-deps` 离线安装、使用明确的 venv 解释器导入并计算结果 `37`。批准前分别确认环境、安装包和结果文件尚未出现；完成后核对三条命令的唯一回执、实际解释器路径、日志、公开 execution_record 和批准启动快照。环境与现场保留，不下载第三方包，不使用真实 LLM。

原始日志及冒烟材料位于 `E:/dshWorkSpace/implementation/unified-shell-stage1-20261016/checks/`：`pytest.txt`、`mock.txt`、对应退出码、`source-audit.json`、`venv-shell-smoke/`、独立审计及说明。冒烟文件清单保存逐文件哈希和大小。

旧环境与专用验证模块的测试随功能删除；仍有效的批准、命令事实、失败、中断、工件与恢复检查迁移到统一入口，并新增真实环境创建/复用和 Shell 边界验证。用例总数下降不是跳过失败；最终全量仅保留原有一个 skip。

## 复核中发现与修正

- 独立审阅重现：若命令完成后的 Git 诊断被打断，宽泛异常处理可能丢失“进程未确认停止”的事实。现保留原取消信号、停止确认和未确认进程，并在同一原生回执的 `completed_command` 中保存已经完成的脚本结果；execution_record 保存两项真实事实，不另造工具回执。
- 独立审阅重现：批准时的启动配置与真正执行时的环境可能变化。现执行前比较批准快照，并将同一份过滤后的环境传给实际进程和回执，避免检查后重新构造环境。相关定向测试包含配置漂移时无执行副作用及停止确认 true/false 两种诊断取消。
- 首次全量为 **2122 passed / 4 failed / 1 skipped**：启动测试时最后一处测试替身修订尚未被该进程加载，旧替身错误要求 prepared 字典整体相等，未接受 policy 的实际 cwd。当前替身按其声明字段校验并确认 cwd；该文件 10 项及 Agent 192 项复验通过，重新启动全量得到上表结果。没有为迁就替身放宽产品批准检查。
- 本地独立冒烟首次测试驱动在导入 E2E fixture 时失败，产品尚未运行；改用直接原生 ToolCall 构造后完成。该测试方错误及证据保存在冒烟说明，未归为产品执行失败。

## 后续验证边界

本轮没有执行真实 DeepSeek、远程服务器、Conda 在线安装、CUDA/GPU 或 L3 研究。离线脚本模型验证的是统一工具与真实进程、环境和批准链路，不证明模型已能稳定自主选择环境或科研质量提高。后续真实验收应新建 schema28 Run，检查模型通过统一工具选择/创建/复用环境、安装依赖、运行检查，以及真实中断后的执行记录和文件保留；长 GPU 研究另按现行 L3 指南验收。

第一阶段在开发分支提交；自动 reviewer 和沙箱不纳入本提交，本阶段不合并主线。
