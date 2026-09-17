# Components：普通调用可复用的实现

这里放不属于 Runtime、也不是模型 Tool 的操作、资源与内容呈现。Tool、Agent 的准备/完成检查、CLI/E2E 都可直接调用；**不与 Tool 一一对应，不要求每个 Tool 提取组件，不新增管理层。**

## 从哪里找实现

| 文件 / 目录 | 内容 |
|---|---|
| [workspace.py](src/resagent2_components/workspace.py) | 工作区授权、路径与软链边界 |
| [git.py](src/resagent2_components/git.py) | Git 快照与 Attempt 相对变化 |
| [repo.py](src/resagent2_components/repo.py)、[snapshot.py](src/resagent2_components/snapshot.py) | 仓库物化；Git/非 Git 工作区变化观察 |
| [process.py](src/resagent2_components/process.py) | shell-free 命令解析/执行、凭据过滤、日志、进程树超时终止 |
| [environment.py](src/resagent2_components/environment.py) | 环境准备/绑定/认证、硬件检查、镜像配置、安装命令规则 |
| [dataset.py](src/resagent2_components/dataset.py)、[resources.py](src/resagent2_components/resources.py) | 数据集登记与可用性；部署目录 |
| [artifacts.py](src/resagent2_components/artifacts.py) | 授权工件读取、报告生成、媒体类型、登记接口形状 |
| [context.py](src/resagent2_components/context.py)、[text.py](src/resagent2_components/text.py) | 共享读取/环境/诊断投影；文本窗口与长行呈现 |
| [literature/](src/resagent2_components/literature/) | 规范化论文、平级文献来源、共用 HTTP 节奏 |

`process.py` 不决定“该做训练还是测试”：它运行已获准的命令并返回事实。Coding 的验证命令策略和 revision 配对在 [Coding verification](../agents/coding/src/resagent2_coding/verification.py)，Experiment 的执行策略在其 Agent。多个 Tool 可以共用同一 ProcessRunner；没有“一个 Tool 配一个服务”的规则。

## 依赖与状态

公开导入入口为 `resagent2_components`；实现内部小函数跟随相关文件，不为单个辅助函数建文件。基础操作只使用实际需要的依赖；共享上下文投影和现有论文模型可使用 Runtime 的类型/选择函数。不得 import Capabilities、具体 Agent 或 Orchestrator，不启动 AgentLoop 或直接调用 LLM。

不要求所有组件纯函数：环境绑定、资源 IO 和文献来源索引保持既有状态；但不新建一份 Run/Session，不替代 Controller/Scheduler 的状态归属。ArtifactRegistrationPort 由组合根注入，登记实现仍在 Orchestrator；Components 不反向依赖它。

DatasetCatalog 读取部署登记，resolve_dataset_refs 区分登记与目录可用性；上下文和脚本映射使用同次结果。不下载数据集，目录存在也不保证内容完整。包缓存仍归 pip/conda，不归 DatasetCatalog。

workspace_context 消费原事件和真实环境绑定，不读旧缓存猜环境状态；材料按 Runtime 的统一权重/优先级分配，旧读取保留时序和后续内置修改标记。规则与预算只在 [CONTEXT](../../docs/current/CONTEXT.md) 维护；调用语义见 [CONTRACTS](../../docs/current/CONTRACTS.md#components)。

<a id="literature"></a>

## 文献实现与失败规则

[backends.py](src/resagent2_components/literature/backends.py) 集中现有 LiteraturePaper、LiteratureSearchBackend、arXiv、OpenAlex、MultiSourceLiteratureBackend 和条目呈现；[_http.py](src/resagent2_components/literature/_http.py) 是两个来源共用的 HTTP 实现。Tool 在 Capabilities，不在这个目录。

CLI/E2E 将 arXiv、OpenAlex 作为平级来源装入列表，互为备份。初次按配置顺序尝试（目前 arXiv 在前）；成功后继续用该源，不可用时依次试其他源，每次最多遍历一轮。只保存实例内索引；没有探活、健康表、持久选择记录，也不同时查询/合并两个源。

仅 `LiteratureUnavailableError` 触发换源；合法空结果算成功。HTTP 4xx（除 408/429）、损坏 XML/JSON 和编程异常不静默换源。全部不可用汇总原因报错，不登记空工件伪装成功。保留各来源真实 ID/URL，不按同名合并论文。

HTTP 使用 User-Agent，进程内按来源串行：arXiv 请求结束后至少间隔 3 秒，OpenAlex 1 秒。429 立即进入至少 60 秒冷却；Retry-After 支持秒数/HTTP 日期，更长则遵守。5xx/408 有 Retry-After 时同样冷却；其余超时/网络/5xx/408 最多三次 HTTP 尝试，退避 3/6 秒，耗尽后冷却。冷却期直接报不可用，不在 Agent 内长睡眠。既有 `max_retries` 参数指总尝试数。

OpenAlex 可选 API key 由组合根读取，仅经 Authorization header 发送，不进 URL、工件或模型上下文；匿名额度由服务端决定。摘要缺失就留空，每篇仍最多 2000 字符，不抓 PDF、不新增 LLM 摘要。生成 Markdown 明示检索摘要不等于全文或独立测量。

节奏/冷却只协调同进程，重启不保留；多进程及同出口其他程序由部署方协调。不轮换 IP，不新增缓存、队列或多源融合框架。

既有来源规范：[arXiv 使用约定](https://info.arxiv.org/help/api/tou.html)、[OpenAlex 鉴权](https://help.openalex.org/api/authentication/)、[Work 字段](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md)。本次仅整理实现位置，不改变这些访问策略。

测试入口：[Components](../../tests/components/)、[含 Tool 的文献集成](../../tests/capabilities/test_literature.py)、[依赖边界](../../tests/components/test_components_boundary.py)。
