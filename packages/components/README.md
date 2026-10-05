# Components：普通调用可复用的实现

这里放不属于 Runtime、也不是模型 Tool 的操作、资源与内容呈现。Tool、Agent 的准备/完成检查、CLI/E2E 都可直接调用；**不与 Tool 一一对应，不要求每个 Tool 提取组件，不新增管理层。**

## 从哪里找实现

| 文件 / 目录 | 内容 |
|---|---|
| [workspace.py](src/resagent2_components/workspace.py) | 工作区授权、路径与软链边界 |
| [git.py](src/resagent2_components/git.py) | Coding 使用的 GitBaseline、恢复与 Attempt 相对变化 |
| [repo.py](src/resagent2_components/repo.py) | 仓库物化 |
| [process.py](src/resagent2_components/process.py) | 直接 argv / Linux Bash 执行、凭据过滤、有界日志尾部、进程树超时终止 |
| [environment.py](src/resagent2_components/environment.py) | 环境准备/绑定/认证、安装命令规则和显式环境清理 |
| [dataset.py](src/resagent2_components/dataset.py)、[resources.py](src/resagent2_components/resources.py) | 数据集登记与可用性；部署目录 |
| [artifacts.py](src/resagent2_components/artifacts.py) | 授权工件读取、明确输出名存在检查、报告生成、媒体类型、登记接口形状 |
| [context.py](src/resagent2_components/context.py) | 环境事实、读取/诊断投影 |
| [text.py](src/resagent2_components/text.py) | 共享工作区文本大小与编码规则、有界读入、写入前验证、行字符窗口与长行呈现 |
| [materials.py](src/resagent2_components/materials.py) | 通用工件读取、作用域与冻结 hash 校验；Scientific 专用反馈呈现在 Scientific 上下文 |
| [literature/](src/resagent2_components/literature/) | 规范化论文、本地导入清单、平级文献来源、共用 HTTP 节奏、全文获取与 PDF 文本提取 |

`process.py` 不决定“该做训练还是测试”：它运行已获准的命令并返回事实。Coding 的验证命令策略和 revision 配对在 [Coding verification](../agents/coding/src/resagent2_coding/verification.py)，Shell 是 Capabilities 的共享模型入口，Experiment 从实际执行回执生成实验记录。多个 Tool 可以共用同一 ProcessRunner；没有“一个 Tool 配一个服务”的规则。

## 依赖与状态

公开导入入口为 `resagent2_components`；实现内部小函数跟随相关文件，不为单个辅助函数建文件。基础操作只使用实际需要的依赖；共享上下文投影和现有论文模型可使用 Runtime 的类型/选择函数。不得 import Capabilities、具体 Agent 或 Orchestrator，不启动 AgentLoop 或直接调用 LLM。

不要求所有组件纯函数：环境绑定、资源 IO 和文献来源索引保持既有状态；但不新建一份 Run/Session，不替代 Controller/Scheduler 的状态归属。ArtifactRegistrationPort 由组合根注入，登记实现仍在 Orchestrator；Components 不反向依赖它。

WorkspaceBoundary 只使用 WorkspaceGrant.access：read_paths/write_paths/denied_paths 均为相对前缀，空允许列表拒绝全部，排除项优先。每次文件操作检查解析路径；写范围是读范围的子集。`.git`、`.resagent2` 受保护，普通可重建缓存只是展示时忽略，可按写权限清理。prepare_delete/delete_prepared 先快照准确删除集合，再重验执行；只 unlink 最终链接自身，内容变化或中断时保留部分完成记录。

底层工作区、进程和环境边界由 Components 提供；Capabilities 的 OperationPermissionPolicy 组合这些边界与 Run 授权，返回 allow/ask/deny。确认匹配本次恢复 answer 工件与 Session 中的动作快照，不解析历史问题文案或全局确认标志。run_shell 每次精确审批；验证和安装分别由各自 Capability 策略限定范围。批准不能扩权，实际执行前仍校验范围。无隔离后端时，仅完整可读写且无用户排除路径的可信工作区允许任意脚本执行；shell-free 和路径 Tool 都不是 OS 沙箱。

ProcessRunner 与内部 run_process 将操作超时裁到共享 Run 截止时间，批量操作逐次计算余量；到期终止受控进程树。Git、仓库物化、环境准备也使用这一执行路径。LOCAL/COPY 来源须是实际 Git 仓库根；COPY 拒绝外部 .git 指针或链接，LOCAL 仍可绑定 linked worktree。可读范围中的 submodule 不在当前快照支持范围，明确报错，避免遗漏内容后声称工作区未变。数据集/环境身份和缓存机制不变，不新增通用资源配额或镜像预检。

DatasetCatalog 读取部署登记，resolve_dataset_refs 区分登记与目录可用性；上下文和脚本映射使用同次结果。不下载数据集，目录存在也不保证内容完整。包缓存仍归 pip/conda，不归 DatasetCatalog。

workspace_context 消费原事件和真实环境绑定，不读旧缓存猜环境状态；材料按 Runtime 的统一权重/优先级分配，旧读取保留时序和后续内置修改标记。规则与预算只在 [CONTEXT](../../docs/current/CONTEXT.md) 维护；调用语义见 [CONTRACTS](../../docs/current/CONTRACTS.md#components)。

`missing_required_artifacts` 共用 `RegisteredArtifactReader` 的 Run 授权与冻结 hash 检查，精确比较已登记 Ref 的 `output_name`；存在检查通过 `verify` 分块计算文件 hash，不解码文本或全量载入二进制产物；不扫描工作区、不按文件名猜测，也不把存在检查写成 Scientific 的观察记录。Scientific 和 Registry 复用此事实规则，登记权威仍在 Orchestrator。

text.py 的工作区处理默认上限为10 MiB，严格 UTF-8 且拒绝 NUL；read_text_file 预检大小后最多读取上限加1字节，encode_text 在写盘前核对编码及最终字节数。slice_text_lines 先选物理行，再选字符窗口，最后限制返回字符数，保留原换行；它不是流式读取。read_file 与 read_artifact 共用窗口规则，后者仍校验整份冻结 hash，不受工作区10 MiB限制。授权继续由原调用边界负责。

<a id="literature"></a>

## 文献实现与失败规则

[literature/](src/resagent2_components/literature/) 提供规范化论文记录、LiteratureSearchBackend、arXiv/OpenAlex 平级来源、论文呈现，以及全文获取和 PDF 提取。[backends.py](src/resagent2_components/literature/backends.py) 处理检索来源，[_http.py](src/resagent2_components/literature/_http.py) 提供共享 HTTP 规则。[imports.py](src/resagent2_components/literature/imports.py) 提供 load_literature_manifest，返回规范化论文与可选本地 PDF 的 PreparedLiteratureImport 列表。清单拒绝未知字段，PDF 路径相对清单目录解析并校验普通文件及 %PDF 签名；不从文件名或正文猜作者/摘要，不联网。Tool 在 Capabilities，不在这个目录。

CLI/E2E 将 arXiv、OpenAlex 作为平级来源装入列表，互为备份。初次按配置顺序尝试（目前 arXiv 在前）；成功后继续用该源，不可用时依次试其他源，每次最多遍历一轮。只保存实例内索引；没有探活、健康表、持久选择记录，也不同时查询/合并两个源。

统一 query 接收普通关键词和双引号短语，不承诺提供方字段或 Boolean 语法。arXiv 将各词句显式转换为 all 字段并用 AND 连接，再加年份条件；未闭合引号或空短语明确报错。OpenAlex 使用官方 search 参数。

仅 `LiteratureUnavailableError` 触发换源；合法空结果算成功。HTTP 406 表示当前来源无法提供该请求的结果，不在同源重试，直接尝试下一来源；不保证该故障是暂时的。HTTP 其余 4xx（除 408/429）、损坏 XML/JSON 和编程异常不静默换源。全部不可用汇总原因报错，不登记空工件伪装成功。保留各来源真实 ID/URL，不按同名合并论文。

HTTP 使用 User-Agent，进程内按来源串行：arXiv 请求结束后至少间隔 3 秒，OpenAlex 1 秒。429 立即进入至少 60 秒冷却；Retry-After 支持秒数/HTTP 日期，更长则遵守。OpenAlex 仅在响应明确给出 Remaining=0 和有限正 Reset 秒数时，额外遵守额度恢复时间；缺失、非法或非零余额不推断日额度耗尽。5xx/408 有 Retry-After 时同样冷却；其余超时/网络/5xx/408 最多三次尝试，退避 3/6 秒，耗尽后冷却。冷却期直接换源，不在 Agent 内长睡眠。

HTTP 响应诊断沿用应用日志：失败为 WARNING，成功为 INFO。记录来源、状态码、尝试序号/上限、Authorization 是否配置、Retry-After 及实际冷却秒数，以及有限非负的 X-RateLimit-Limit、Remaining、Credits-Used、Reset。日志不含查询 URL、认证值、响应正文或其他响应头；单凭 429 不断言原因。OpenAlex 额度控制只使用可确认的余额耗尽事实。

HTTP 复用 Runtime 的 httpx 总超时传输；节奏等待、退避和请求都沿用 Run 剩余时间。Run 截止不作为普通来源不可用继续切换，耗尽后停止发新请求。论文 HTTP 不消耗模型请求次数，但消耗 Run 时间。

OpenAlex 可选 API key 由组合根读取，仅经 Authorization header 发送，不进 URL、工件或模型上下文；匿名额度由服务端决定。摘要缺失就留空；检索记录明确区分元信息、摘要和可用全文入口，不新增 LLM 摘要。每篇论文独立登记，搜索回执只连接本次查询与论文引用；规范化 key 用于识别论文并保留 arXiv 版本；同 Run 仅复用相同元信息快照，同 key 内容变化则登记新不可变快照，不按标题猜测合并。

全文获取使用已登记论文的来源，按需下载公开可获取的 PDF；PyMuPDF4LLM 解析文本，显式关闭 OCR。原始 PDF 与解析文本分别冻结，直接来源链为论文→PDF→文本；解析失败仍保留已登记原件，重试可复用。无法获取或提取时返回明确结果，不把检索摘要冒充全文，不绕过访问限制。外部论文清单的格式和本地来源规范化也在此目录；Components 不修改 Run，不更新索引，导入冻结和归属由 Controller/Registry 完成。read_artifact 继续只读取严格 UTF-8，PDF 提取不塞进通用文件读取器。

节奏/冷却只协调同进程，重启不保留；多进程及同出口其他程序由部署方协调。不轮换 IP，不新增跨 Run 下载缓存、队列或多源融合框架；同 Run 复用来自已登记且通过 hash 校验的材料，不维护第二份论文库。

既有来源规范：[arXiv 使用约定](https://info.arxiv.org/help/api/tou.html)、[OpenAlex 鉴权](https://help.openalex.org/api/authentication/)、[Work 字段](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/work-object/README.md)。

测试入口：[Components](../../tests/components/)、[含 Tool 的文献集成](../../tests/capabilities/test_literature.py)、[依赖边界](../../tests/components/test_components_boundary.py)。

文献 PDF 默认解析上限为 300 秒，仍受 Run 剩余时间约束。普通调用方可传 `parse_pdf(timeout_seconds=...)`；CLI 使用正整数配置 `RESAGENT2_PDF_PARSE_TIMEOUT_SECONDS` 绑定解析器，经 Scientific 注入全文工具。Components 不自行读取该环境变量。增加 Run 总超时不会自动扩大解析上限，模型也不能通过 Tool 参数扩大它。300 秒是初始工程值，不是所有论文都能成功的性能保证。解析超时仍终止受控进程并保留原件；历史 120 秒计时见[文献验收收尾](../../docs/history/reviews/LITERATURE_FOUNDATION_ACCEPTANCE_2026-10-03.md)。
