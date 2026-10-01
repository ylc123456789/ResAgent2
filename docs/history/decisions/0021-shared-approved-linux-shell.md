# ADR-0021：共享 Linux Shell 与逐次批准

状态：accepted。日期：2026-10-01。

## 背景

旧 Experiment run_command 把通用进程执行包装在单个 Agent 内，并按可执行文件名区分实验和准备操作。需要支持命令组合及通用诊断时，再增加一个平行 Shell 入口会重复工具与记录链。

## 决定

共享 Capabilities.run_shell 取代旧 run_command，Coding/Experiment 显式装配，Scientific 不获得执行能力。Components 是向全系统开放的普通部件层，负责实际 Linux Bash 执行；Capabilities 是面向 LLM 的参数、说明与回执层。两者继续复用 Runtime，不增加 AgentLoop、审批服务或执行管理器。系统固定 Git、环境操作继续直接调用 Components。

Bash 使用固定路径、非登录且无 profile/rc、pipefail，不隐式启用 errexit。脚本原文保持不变；不管理持久终端或后台任务。安装和验证仍使用 run_setup/run_verification，共享进程、日志尾部和超时实现，保留绑定安装目标及验证版本的专业语义。

Shell 要求 execute_commands 和完整可信工作区，每次都匹配具体脚本、目录和环境的单次批准，不受 confirm_commands=False 绕过。prepare_environment 继续管受控环境工具；使用已有环境执行不额外要求它。脚本不经过浅层命令分类，不声称批准或这些开关构成 OS 沙箱。环境修改与角色分工由原工具指引和用户审核约束；未来实际访问隔离可接成熟后端，本次不预建框架。

脚本执行前保守失效原环境认证及相关验证，失败也不能保留旧成功；只读脚本亦可能增加复核成本。Coding 记录实际文件变化及读取过期提示，Experiment 从真实回执生成执行记录。Shell 输出不自动获得注册工件已读资格。

公共 schema 升为 20.0，旧 Run 保留但不恢复或迁移。不保留旧 run_command 工具别名。本决定取代 ADR-0004/0005 中执行工具统一采用 shell-free、Experiment 独占普通命令入口的部分；路径授权、可信工作区、角色职责和单次批准边界继续有效。

## 验证范围

确定性检查覆盖 Linux 实际脚本/管道、绑定环境与预算、单次批准及恢复、失败记录、代码变化和验证失效。决定形成时真实模型行为尚待验收，已有 L3 结果不代表本次新工具已通过真实模型测试。

2026-10-01 补记：`8b01bd0` 首轮与 `e902698` 补测已完成真实 CLI 定向验收；旧验证失效、独立失败回执、指定方式传递与拒绝创建路径均已核对。原始缺口、勘误和覆盖边界见 [验收收尾](../reviews/PROMPT_SHELL_ACCEPTANCE_2026-10-01.md)，本次未重跑完整科研 L3。
