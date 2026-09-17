# ADR-0015：Tool 与普通组件分离

- 状态：accepted
- 日期：2026-09-17
- 范围：细化 [ADR-0006](0006-runtime-capabilities-boundary.md) 的实现位置；保留 Runtime 不承担具体操作的决定。

## 问题

Capabilities 同时容纳模型 Tool、普通操作、资源配置、上下文投影与领域验证策略。调用方很难从目录判断某个类是否是 Tool；单纯按文件名归类不能解决职责混用。

## 决定

1. 新增 `resagent2_components`，放非 Runtime、非模型入口的普通实现。Tool、Agent 准备/完成检查和组合根可直接使用。
2. `resagent2_capabilities` 聚焦通用模型 Tool、输入 schema 和小型工具专用逻辑。按 workspace/artifacts/environment/literature 分目录；不强制每个 Tool 一个文件。
3. 两包不一一对应，不强制逐层调用。组件可服务多个 Tool，也可完全不经 Tool；简单操作不为拆分而新增服务类。
4. Runtime 保持 Loop/Session/LLM/上下文分配/工具协议机制；Components 的共享呈现按需使用 Runtime 类型和选择函数。Runtime 不依赖 Components；Components 不依赖 Capabilities、领域 Agent 或 Orchestrator；Capabilities 不依赖领域 Agent 或 Orchestrator。
5. 领域工作流仍归 Agent。Coding 的 run_verification 与 VerificationCommandPolicy 一起归 Coding；与 Experiment 自有 run_command 一样，共用 Components 的 ProcessRunner。控制工具不为了目录一致性迁入 Capabilities。

## 合并与保留

- 硬件检查与安装命令规则并入环境实现；镜像设置从 dataset 移到 environment。
- 报告生成、媒体类型和登记接口形状并入 artifacts。
- 失败命令诊断与工作区读取投影并入 context；文本窗口仍独立供多个调用方使用。
- 文献用一个 backends.py 集中现有两个来源、规范化记录和选择逻辑；共用 HTTP 节奏仍是目录内私有文件。
- Git、仓库物化和工作区 snapshot 仍有不同职责，不硬塞进单个文件。

## 不变与代价

不改 prompt、模型工具名称/参数/说明、ToolObservation、环境/权限/证据语义、预算、业务状态或恢复协议；公共 schema 保持 10.0。Python 导入位置改变，调用方同步更新，不保留旧服务类转发导入。新增一个安装包，所有 editable 安装须包含 Components。

不引入组件基类、对应关系表、自动注册、插件发现或新状态机。Components 不是“任何共用代码”的垃圾箱：运行机制仍归 Runtime，领域规则仍归 Agent。

## 验证

基线工具 schema/说明指纹、原有确定性测试、包依赖与导出检查、CLI/E2E 装配回归。真实补验只用小型标准库任务，见 [验收单](../reviews/TOOL_COMPONENTS_ACCEPTANCE.md)。当前导航见 [架构](../../current/ARCHITECTURE.md#modules)。

## 2026-09-17 收尾：工具实现与包导出分开

四组工具最初直接定义在各自 `__init__.py`；收尾时统一移到同目录的 `tools.py`，`__init__.py` 只显式导出 Tool 与输入模型。实现文件更容易定位，原有公开导入保持可用；不改变上述职责边界，也不为每个 Tool 新建文件。
