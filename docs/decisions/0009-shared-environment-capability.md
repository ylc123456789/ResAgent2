# ADR-0009：共享环境能力与 Agent 自主选型

**状态**：accepted
**日期**：2026-08-29

## 背景

Phase 7.7 收尾后，Coding 与 Experiment 的环境处理不一致：Coding 的 `run_verification` 直接跑宿主机 Python（无环境 provisioning）；Experiment 用 `EnvironmentManager.ensure` 内容寻址自建环境并自动安装 `requirements.txt`/`environment.yml`，且 `audit_env` 硬编码 `import torch`；legacy reproagent 有完整的 manifest/锁/漂移检测，但同样内容寻址且过重。三者都不满足「Agent 决定项目需要什么，系统决定如何落实」。

## 决策

- 环境能力改为 Coding 与 Experiment **共用**的通用能力，职责固定：Agent（LLM）理解项目需要什么 Python 和依赖；确定性代码负责创建、隔离、绑定和审计环境。ResAgent/Compiler 不生成安装命令，Scientific 不处理环境。
- 环境归属改为 `run_id + workspace_id`（放弃内容寻址）：同 Run 同 Workspace 共用（Coding/Experiment 共用、Task 重试复用）；不同 Workspace 不同环境；不同 Run 不同环境；不跨 Run 复用可变环境。
- `env_id = resenv_<sha256(run_id + "\0" + workspace_id)[:12]>`，哈希派生，不透明、目录名安全。
- Python 版本选择优先级：用户/上游硬约束 > `.python-version` > `pyproject.toml` 的 `requires-python` > `environment.yml` > README/安装文档 > 系统默认。用户硬约束不能被 Agent 覆盖（冲突则 `ask_user` 或返回版本冲突）；每个 Attempt 最多两次版本切换。
- `EnvironmentManager` 接口收窄为 `inspect` / `prepare` / `audit`，内部对象 `PreparedEnvironment(env_id, prefix, python_version)`（不进公共契约）。不再自动解释/安装 `requirements`/`environment.yml`。
- 三个共享 Tool（capabilities）：`prepare_environment` / `run_setup` / `audit_env`。`run_setup` 当前支持 `python -m pip install ...` / `pip install ...` / `conda env update -f ...`（conda 由工具注入 `-p <绑定 prefix>`）；暂不支持 `uv`/`poetry`；禁止 `sudo`、`conda create/remove`、指定其它 `--prefix/-p/--name/-n`。`audit_env` 只证明基础环境正确（sys.executable / sys.prefix / Python 版本 / pip 可用 / prefix 匹配），不硬编码 torch。
- 半成品环境用简单规则：目录不存在 → 创建；基础检查失败 → 确认在受管 env_root 内 → 删除 → 重建。marker 改 `.resagent2_base_ready`，只表示基础 Python 健康（不代表项目依赖装完）。

## 不采用

- 复用 reproagent 的完整 ENVIRONMENT_*_V1 manifest/锁/漂移检测（过度设计，且本方案已明确不跨 Run 复用可变环境）。
- 内容寻址环境复用（跨 Run 复用 env 有污染风险，且 env_id 依赖 repo 绝对路径导致每个 workdir 重建 env）。
- EnvironmentManager 自动安装 `requirements`/`environment.yml`（依赖判断是 Agent 职责，确定性代码不该解释项目依赖）。

## 后果

- 删除 `ExperimentRunInput.python_version`，改 `ModuleTaskRequest.environment_spec`（`EnvironmentSpec.python_version: str | None`）。
- 删除内容寻址函数 `env_spec`/`env_id`(旧)/`project_slug`（先确认无其它调用方）；服务器已存在的 `resenv_*_sha12` 环境废弃但无害。
- 每次 Run 重建 env（复用 pip/conda 下载缓存），隔离更强但比内容寻址跨 Run 复用慢。
- Coding 成为环境能力的第二个真实使用者，替代 ADR-0005 里「Phase 8 计划」的说法，满足「两个真实使用者」纪律。
- 本 ADR 部分取代 [ADR-0005](0005-experiment-agent-and-content-addressed-env.md) 的「内容寻址环境」决策。
