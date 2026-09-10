# ADR-0013：运行期资源与人工等待预算

- 日期：2026-09-10
- 状态：accepted；分支实现，本地验证；真实服务器验收待完成。
- 局部取代 ADR-0011 中 ResearchRequest 持有资源引用、Run 超时包含人工等待的约定；其余状态/Attempt/环境规则不变。

## 问题

资源需求可能在读代码或执行中出现，调用方无法在第一次请求前列出全部资源。
旧代码虽已从部署 catalog 自动读取，但把结果写回 ResearchRequest；已登记目录缺失还会在 Agent 开始前阻塞无关任务。
等待用户准备大型数据集也会消耗整个 Run 超时。

## 决定

1. ResearchRequest 仅含研究意图、约束、输入工件和预算；删除 dataset_refs，schema 升到 6.0。
2. DatasetCatalog 经已有 DatasetRefSource 注入 Controller；Run.dataset_refs 保存累计已知引用，内部请求自动传递。目录位置仍是部署配置，不改成研究请求里的 catalog_path。
3. 相同 ID 在 Run 内不得重映射；保存引用不是实际使用清单，也不是冻结数据内容。已有引用不因 catalog 删除条目被自动撤销，实际目录可用性仍复查。
4. capabilities 的 DatasetAvailability 只是一次检查结果。缺目录是不可用状态，非法格式/路径仍报错；三个 Agent 上下文和脚本映射消费同一结果。
5. 缺少当前任务需要的数据时复用 ask_user。人工放置并登记、回答后重查；无后台监视、无自动下载、无新资源状态机。模型选择 ask_user 属于行为策略，不宣称确定性保证。
6. Run 用 user_wait_seconds 结算已结束问答等待；当前等待从 PendingQuestion 推导。Controller 和 Scheduler 共用剩余时间函数。等待结算、答案、Task 恢复同一次保存；按主机时钟，不信任 UserAnswer.answered_at。
7. 安装、推理、执行和普通宕机仍计时；已用 LLM 次数不重置。依赖继续使用既有安装/审计和 pip/conda 缓存。

## 不选的方案

- 不要求调用方预填数据集列表或资源库路径。
- 不把数据集、虚拟环境和包下载缓存强塞进统一 Resource 类。
- 不做目录自动识别、资源 Agent、大目录搜索、环境池或数据集下载器。
- 不做旧记录迁移或兼容恢复；旧文件原样保留。

## 后果与验证

新增一个轻量可用性结果和一个 Run 累计等待字段，不增加 Agent/调度状态。
目录存在不保证内容完整、不可变或安全；普通函数和 prompt 不构成 OS 沙箱。
跨进程测试覆盖原 Session、原 Attempt 复用和资源重新检查；受控时钟测试覆盖长等待、多个问题和重复答案。
实施记录见 [阶段计划](../reviews/RUNTIME_RESOURCES_PLAN.md)，真实模型门槛见 [服务器验收](../reviews/RUNTIME_RESOURCES_ACCEPTANCE.md)。

