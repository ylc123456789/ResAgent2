# 统一执行入口与自动审核：分支收尾检查

日期：2026-10-17。分支 `feat/unified-run-shell`，检查起点 `be44121956b8d48cd5012e8dbb760d5d0d6a1da1`，schema **29.0**。主分支及本次只读查询的远端 main 均为 `2bcde651f7bb31add86d79650255c410f7d3591a`。

## 结论与范围

当前范围具备合并条件，未发现新增阻塞性实现问题。此记录仅检查并收尾，不执行合并或推送。

本分支完成 Capabilities 命令执行入口统一、删除固定环境生命周期管理，以及可选的无状态准确操作审核。后续真实测试发现的角色权限投影、单输出协议和审核反馈问题已有修复与验收。L3 后续仅补通用目录授权语义、修正最终报告栏目统计口径；它们此前已修改，本次检查没有再改产品运行逻辑。

合并不意味着自动审核误判已经消除。默认仍是 manual；auto_review 是可选模型判断，没有 OS 沙箱，不保证任意命令安全。L3 原有误放行及脚本当前内容证据不足继续作为明确局限保留。

## 本次实现核对

- Capabilities 只保留 run_shell 执行命令，模型自行检查、创建和使用 Python/Conda 环境。安装、训练、评测及检查不重新增加专用命令工具。
- Components 保留进程、超时、中断、日志、环境变量过滤、真实代码差异和 execution_record；固定 Git 等内部操作仍直接调用组件。
- Runtime 复用唯一工具回执、批准快照、原 Session 恢复和共享 Run 总预算。自动审核在执行前发生；硬拒绝不交审核，人工恢复不二次审核，allow 后重验授权及启动快照。
- 审核无执行工具和 Session，单次输出协议与 AgentLoop 分离。预算、期限、取消与权威用量保存错误不会通过转人工绕过。
- 删除固定验证完成门槛符合 ADR-0030；模型判断检查是否充分，固定代码仍检查真实变更、系统执行事实和已登记交付物。
- 最终报告的 Non-completed delegated work 只列该范围内的委派任务结果和记录错误，不把空列表解释成全程无工具失败或无人工介入。

两项未提交小修的事实、对照和受控短链见 [L3 后续](AUTO_REVIEW_L3_FOLLOWUP_2026-10-16.md)。本次跨执行链、自动审核及文档的独立只读审查没有发现需要扩展产品范围的阻塞问题。

## 当前工作区复验

使用 `/home/cyl/miniconda3/envs/ResAgent2/bin/python`，执行前后保留既有 L3 后续修改，没有回退或覆盖旧证据。

| 检查 | 实际结果 |
| --- | --- |
| 9 个项目包源码指针 | 全部来自 /home/cyl/ResAgent2 |
| python -m pytest tests apps/cli/tests -q | rc=0，2230 passed / 1 skipped，40.32 秒 |
| python -m e2e.mock_e2e | rc=0，completed，8 件工件 |
| python -m pip check | rc=0，无依赖冲突 |
| 分支内容与收尾文档 whitespace 检查 | git diff --check main，rc=0；变更 Markdown 本地链接目标无缺失 |
| 远端 main 基线 | 只读 ls-remote 返回 2bcde651，与本地主分支相同 |

本次没有新增 provider 调用或重跑 L3。既有服务器机制回归、语义探针与真实拒绝/调整/中断链路分别见 [修复验收](AUTO_REVIEW_FIXES_ACCEPTANCE_2026-10-16.md) 和 [L3 后续](AUTO_REVIEW_L3_FOLLOWUP_2026-10-16.md)。后者明确保留研究 8/10、人工协助及原审核语义未通过；有限合成对照不算统计显著提升，也不能代替原 L3 复测。

本地检查日志和退出码：`E:/dshWorkSpace/implementation/unified-run-shell-merge-review-20261017/`。此前服务器材料没有在本次改写。

## 文档同步

已核对 docs/current 的架构、设计原则、上下文和契约，以及根 README、CLI 和相关包 README。本次补齐如下遗漏：

1. contracts README 的旧 schema27 更新为29。
2. packages 总览删除环境管理/环境 Tool 职责，明确唯一 run_shell 和模型管理环境。
3. orchestrator README 改正旧 Compiler 任务草图描述，明确只路由原 Tasks、固定代码组装；旧 schema25 恢复说明改为引用当前契约。
4. 历史索引增加本次收尾与最新 L3 后续入口，保留先前记录当时的状态。

current 已准确同步目录授权语义和报告统计范围，没有把可行性探针写成正式隔离功能。

## 沙箱后续取舍

沙箱不属于本分支的产品实现。独立 WSL2 原型已验证显式挂载下的文件/网络限制、两个无 pip venv 的复用、现有 ProcessRunner 的正常/异常退出、超时、两次父子进程取消及文件保留。未测试 Docker 负载、GPU、依赖安装、受控联网或完整 Agent 沙箱链路；测试版 bubblewrap 不代表正式选型。

当前服务器所测 namespace 方案受外层平台限制；较旧 Landlock 只有部分保护。决定暂缓正式接入，保留探针和最小执行约定，待选定首个实际后端并具备部署环境后，再提取接入接口。没有预先增加空框架、环境管理器或容器编排；也不为适配服务器自动弱化隔离或静默退回宿主。

本地独立原始证据与详细评估：`E:/dshWorkSpace/implementation/sandbox-research-20261017/`。本次产品合并就绪判断不依赖这些探针来证明任意 shell 安全。
