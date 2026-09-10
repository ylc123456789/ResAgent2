# 运行期资源管理（schema 6.0）

状态：阶段 1–4 本地完成；真实服务器验收待执行，未合并/推送。基于 `95f965f`，分支 `fix/runtime-resources`。

目标：调用方只提交研究意图；系统提供数据集目录，Agent 在运行中发现需求。
复用现有 DatasetCatalog、环境安装/审计和 ask_user，不引入统一 Resource 框架。

## 分阶段提交

1. 数据集引用从 ResearchRequest 移到 Run 内部状态，同步内部输入及两个组合根，schema 升到 6.0。
2. 共享数据集检查区分登记和可用；不相关目录缺失不阻塞；回答后重新检查，复用原 Session/Attempt。
3. 所有 ask_user 等待统一不计入 Run 超时；不重置已用时间或 LLM 预算。
4. 全量本地验证、当前文档与服务器验收要求。

## 最终本地验证

- 795 passed, 1 skipped；mock_e2e completed；git diff --check 干净。
- 最终全量使用 WSL --cd 显式隔离在 `/tmp/resagent2-resource-final.QBUI1J`，避免跨 shell 变量展开干扰测试 cwd。
- 127 个文档本地文件链接目标检查通过；未宣称渲染器/所有标题锚点均做 UI 验证。
- 第一阶段 `bafe18b`：schema 6.0 / Run 引用归属。
- 第二阶段 `ccf4b90`：三个 Agent 的共享可用性与恢复检查。
- 第三阶段 `f61488c`：统一人工等待预算。
- 第四阶段：当前架构/契约、CLI、教程、ADR 和验收单同步。

本地测试未调用真实模型或安装外部依赖；未上服务器、未清理旧数据。
服务器仍须按 [验收单](RUNTIME_RESOURCES_ACCEPTANCE.md) 检查真实模型的资源选择与 ask_user 行为。

## 分阶段验证说明

阶段 3 调度回归：208 passed。ResearchRun 用一个累计 user_wait_seconds 和
remaining_timeout_seconds(now) 统一剩余超时；Controller 结算回答时用系统时钟，
与答案、Task 恢复一次保存。当前开放暂停从 PendingQuestion.created_at 推导。
覆盖跨日等待、重建控制器、多次暂停、伪造 answered_at、重复答案、普通宕机计时。

阶段 2 本地全量：789 passed, 1 skipped。新增检查使用真实目录和 JsonSessionStore，
Scientific / Coding understand / Coding modify / Experiment 各用三个独立进程核对
“未登记 → 登记但缺失且用户确认 → 实际补齐”的上下文及 Session 复用。
模型动作是脚本驱动；不能据此宣称任意真实模型都会选择 ask_user。

DatasetAvailability 是一次目录检查的结果（可用路径、不可用 ID），不是新的管理器或状态机；
三个 Agent 的上下文和脚本环境映射消费同一检查结果，恢复调用重新计算。

## 边界

- 目录路径属于部署配置，不是 ResearchRequest；Run 引用快照不等于实际使用清单。
- 数据集由用户放置并登记；系统不下载、不猜路径、不替换数据集。
- 路径越界、非法目录配置仍报错；目录存在不保证数据内容完整。
- 依赖继续使用已有环境准备、run_setup、audit_env 及 pip/conda 缓存。
- 旧记录原样保留，不迁移、不添加旧 schema 兼容层。
- CLI 与 E2E 保持各自组合根；不自动合并 main 或推送。
