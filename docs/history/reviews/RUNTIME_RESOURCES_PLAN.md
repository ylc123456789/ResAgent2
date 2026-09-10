# 运行期资源管理（schema 6.0）

状态：实施中。基于 `95f965f`，分支 `fix/runtime-resources`。

目标：调用方只提交研究意图；系统提供数据集目录，Agent 在运行中发现需求。
复用现有 DatasetCatalog、环境安装/审计和 ask_user，不引入统一 Resource 框架。

## 分阶段提交

1. 数据集引用从 ResearchRequest 移到 Run 内部状态，同步内部输入及两个组合根，schema 升到 6.0。
2. 共享数据集检查区分登记和可用；不相关目录缺失不阻塞；回答后重新检查，复用原 Session/Attempt。
3. 所有 ask_user 等待统一不计入 Run 超时；不重置已用时间或 LLM 预算。
4. 全量本地验证、当前文档与服务器验收要求。

## 边界

- 目录路径属于部署配置，不是 ResearchRequest；Run 引用快照不等于实际使用清单。
- 数据集由用户放置并登记；系统不下载、不猜路径、不替换数据集。
- 路径越界、非法目录配置仍报错；目录存在不保证数据内容完整。
- 依赖继续使用已有环境准备、run_setup、audit_env 及 pip/conda 缓存。
- 旧记录原样保留，不迁移、不添加旧 schema 兼容层。
- CLI 与 E2E 保持各自组合根；不自动合并 main 或推送。
