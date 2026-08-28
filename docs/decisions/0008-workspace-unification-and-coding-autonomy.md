# ADR-0008：统一工作区、Coding 自主代码细节与 Run/缓存分离

**状态**：accepted

**日期**：2026-08-29

**取代**：ADR-0004 中「每个 capability 经固定 WorkspaceGrant 绑定单一工作区」、ADR-0005 中「ExperimentRunInput 自带仓库来源字段」的部分

## 背景

服务器真实 E2E 暴露出一类根本性问题：**Compiler 与执行 Agent 的职责错位**。早期设计让 `WorkflowCompiler` 决定代码文件、验证命令和物理目录，但 Compiler 是「语义翻译器」，它不读项目、不知道 `import` 关系，也不清楚仓库真实结构。它生成的验证命令（如 `from models import se_resnet18`）会引用不存在的模块；它选择的文件列表与实际项目脱节。与此同时，`CodingAgent` 拥有最强的项目上下文（它真实读文件、改文件、看 diff），却被禁止决定验证命令，只能跑调用方预先声明的一组命令。

此外还有一组工作区与缓存的结构性问题：

- 一个 capability 被假设只有唯一固定工作区（`ModuleBinding.workspace`），无法表达「一个 Run 有多个工作区」或「Coding 改完后 Experiment 在同一工作区看到改动」；
- `CodeModifyInput` 同时携带 `allowed_paths`（与 `WorkspaceGrant.allowed_paths` 重复）和 `verification_commands`（与 Compiler 职责重叠）；
- `ExperimentRunInput` 自带 `repository_url`/`copy_from`/`external_repo_path`，与新工作区模型重复；
- `RepoMaterializer` 把 `.resagent2/materialized_source.json` 写进目标源码仓库，污染用户仓库；
- Run 产物（日志、Session、Patch）与共享缓存（数据集、环境）没有明确的目录边界。

## 决策

### 1. 代码细节归 CodingAgent，不归 Compiler

`WorkflowCompiler` 只把科学 `WorkRequest` 编译成可执行任务图，负责：任务类型、高层目标、依赖、输入输出证据流，以及任务落到哪个**逻辑工作区** `workspace_id`。它不负责扫描源码、指定具体文件、生成 `import`、生成验证命令、决定物理目录或执行 `git clone`。

`CodingAgent` 是代码领域的执行者：准备/复用仓库、自己读项目结构、决定改哪些文件、改代码、**根据项目实际选择验证命令**、执行验证、按错误反馈修复，并输出 patch/变更文件/验证结果。

`ScientificAgent` 只做科学判断，不决定代码文件和具体测试命令。

### 2. 统一工作区模型

一个 Run 可以有多个逻辑工作区，同一任务的不同 Attempt 共用项目工作区，但日志、Session、Patch 等运行产物相互隔离。

新增最小契约：

```python
class WorkspaceSourceKind(StrEnum):
    GIT = "git"          # location 是 Git URL，clone 到受管目录
    LOCAL = "local"      # 直接绑定已有本地目录，不搬运
    COPY = "copy"        # 复制已有本地 Git 工作树到受管目录
    GENERATED = "generated"  # 创建空的受管工作区

class WorkspaceSpec(ContractModel):
    workspace_id: str
    source_kind: WorkspaceSourceKind
    location: str | None = None

class WorkspaceRecord(ContractModel):
    workspace_id: str
    root: str                       # 已解析的物理目录
    source: WorkspaceSpec
    managed: bool                   # True = ResAgent2 创建并管理
    initial_commit: str | None = None
```

`WorkspaceGrant`（每次 Attempt 的物理授权边界）改为由 `WorkspaceRecord` 派生，`source` 字段改用 `WorkspaceSourceKind`。`WorkspaceSource`（EXISTING/CLONE）废弃，其语义分别并入 LOCAL/GIT。

`ResearchRun` 增加 `workspaces: dict[workspace_id, WorkspaceRecord]`；`TaskProposal`/`WorkflowTask` 只保存 `workspace_id`，不保存物理路径。`ModuleBinding` 移除固定的单工作区绑定，只保留 capability/owner/port。Scheduler 按 `workspace_id → ResearchRun.workspaces → WorkspaceRecord → WorkspaceGrant` 解析。

若 Run 只有一个工作区，Compiler 可不输出 `workspace_id`，由编译后的确定性校验层自动填入唯一工作区；多个工作区时 Compiler 只能从给定逻辑 ID 中选择，不能编造。

### 3. 仓库准备复用 RepoMaterializer，不写仓库

在 Coding/Experiment 开始 agentic loop 之前，执行确定性的准备步骤：

```text
WorkspaceRecord → RepoMaterializer.materialize(...) → 获得或复用 repo_path → 开始 AgentLoop
```

这不是让 LLM 调 `git clone`，而是 Agent 外层的确定性准备。第一个使用工作区的 Agent 完成 materialize，后续 Agent 检查元数据后复用；来源不一致时拒绝复用。

工作区来源元数据不再写进目标源码仓库（删除 `.resagent2/materialized_source.json`），改写到 Run 自己的目录：`runs/{run_id}/workspaces/{workspace_id}/workspace.json`。

### 4. Coding 动态验证

`RunVerificationTool` 改为接受 Agent 选择的命令（`RunVerificationInput.commands: list[str]`），执行链路为：Agent 提出命令 → 命令权限/解析检查（shell-free）→ ProcessRunner 执行 → 返回 stdout/stderr/exit code。继续复用 ProcessRunner 的限制（不用 shell 拼接、超时、进程树清理、日志、结构化解析）。

`CodeModifyInput` 只保留 `instructions`（可加 `suggested_paths` 提示，但仅是提示、不是权限）；删除 `allowed_paths` 和 `verification_commands`。Coding 完成条件同时要求：有代码改动、最近修改后运行过验证、验证成功、验证后工作区未再变化、变更未越界、Patch 和验证日志已成证据。

### 5. Run 数据与共享缓存分离

两类根目录：

- `data_root`（Run 专属）：`runs/{run_id}/state`、`runs/{run_id}/workspaces/{ws}/`、`runs/{run_id}/attempts/{task}/attempt_{n}/`、`scientific/sessions/`、`artifacts/`；
- `resource_root`（跨 Run 复用）：`datasets/`、`envs/`、`models/`（预留）。

实现 `RunLayout`（归 orchestrator，根据 `data_root` + `run_id` 返回标准目录，不承载调度逻辑）和 `ResourceLayout`（归 capabilities，根据 resource_root 补全 dataset/env 目录）；contracts 只保留跨模块数据模型，不读取环境变量、不决定物理目录。`RESAGENT2_DATA_ROOT`/`RESAGENT2_RESOURCE_ROOT`/`RESAGENT2_DATASET_ROOT`/`RESAGENT2_ENV_ROOT` 按「显式参数 > 环境变量 > 派生默认」的优先级覆盖。`ExperimentRunInput` 删除 `repository_url`/`copy_from`/`external_repo_path`，数据集/环境目录来自 `ResourceLayout`，实验输出写入 Attempt 目录或 ArtifactRegistry，不写入共享缓存。

落地时的三个硬化：`workspace_id` 用严格 `WorkspaceId`（`^ws_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`）防目录逃逸；Coding 改动相对 `GitBaseline`（临时 index 的 `git write-tree`，不 commit/不改真实 index）计算，使多个 Coding Task 共享工作区且只登记本 Attempt 增量；验证命令经 `VerificationCommandPolicy` 默认拒绝破坏/包管理/网络/Shell 命令，并清理子进程的敏感环境变量。

### 6. Attempt 隔离与重试

同一 Run 中同一 `workspace_id`：多个 Task 和同一 Task 的多个 Attempt 共用代码工作区。每个 Attempt 的 Session、日志、Patch、tool observation、验证结果独立保存到 `attempts/{task_id}/attempt_{n}/`。

保留安全原则：Attempt 失败且未改工作区时可自动重试；已改工作区后失败必须生成诊断 Patch；不允许在脏工作区上无脑自动重试（返回不可自动重试错误，由上层决定重规划/继续/请求用户）。

## 为什么不让 Compiler 决定代码细节

Compiler 是无状态、有界的语义翻译器，输入里只有自然语言证据需求和能力列表，没有项目结构、`import` 图或真实文件内容。让它生成验证命令或文件清单，等于让一个不读项目的人指定外科手术的切口。CodingAgent 在 agentic loop 里真实读文件、改文件、看 diff、跑命令，拥有最强的局部上下文，把「改哪些、怎么验证」交给它是把决策权放到信息最充分的一层。

## 不变的原则

- Scientific Agent 不输出执行图字段（含 workspace/path/env/命令）；
- WorkflowCompiler 不形成科学结论、不读文件、不决定物理目录；
- 专业 Agent 不直接互调，Coding 与 Experiment 通过同一个 `workspace_id` 操作同一工作区；
- 状态转换和 Artifact 登记仍由确定性代码决定；
- 共享资源缓存（数据集/环境）不进 Run 数据目录，Run 产物不污染用户源码仓库。

## 迁移影响

- `WorkspaceSource` 改名 `WorkspaceSourceKind`，值改为 GIT/LOCAL/COPY/GENERATED；
- `WorkspaceGrant` 不再由 `ModuleBinding` 固定携带，改为 Scheduler 从 `ResearchRun.workspaces` 派生；
- `CodeModifyInput.allowed_paths`/`verification_commands` 与 `ExperimentRunInput` 的三个 repo source 字段删除；
- `RepoMaterializer` 元数据位置从 `.resagent2/` 移到 Run 目录；
- mock/real E2E 的 composition root 改为声明 `WorkspaceSpec` 并注入工作区。

## 明确不做

- GitHub push、PR、Issue、自动 commit、自动分支；
- 每个 Attempt 一个 Git worktree、自动回滚、跨 Run 仓库缓存；
- 分布式工作区、远程机器工作区同步、通用 DAG 资源调度器、复杂 WorkspaceManager 基类体系。
