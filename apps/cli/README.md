# ResAgent2 CLI

本页是命令与部署配置的使用参考。[项目文档导航](../../docs/README.md) · [理解一次任务](../../docs/guides/UNDERSTANDING.md) · [CLI 的架构边界](../../docs/current/ARCHITECTURE.md#modules)

`resagent2` 是现有 `ResearchController` 最外层的命令行适配器。它提供两种使用方式：

- 交互 shell：适合日常使用，可以实时看进度、回答问题和恢复 Run；
- 一次性命令：适合脚本、自动化和明确的单步操作。

CLI 不实现另一套研究控制、调度、Agent 或证据逻辑；两种入口最终都调用同一个 `ResearchController`。

当前 contracts schema 为 6.0。旧 5.0 及更早 Run 不支持 resume，请用新 data root/新 Run 开始；旧记录原样保留作查阅，不要求删除或迁移。CLI 和 E2E 保留独立装配入口，使用相同的资源组件，而非合并成一个总入口。

## 1. 安装与基本配置

推荐从仓库根目录创建项目环境；`environment.yml` 会以 editable 模式安装全部包和 CLI：

```bash
conda env create -f environment.yml
conda activate ResAgent2
resagent2 --help
```

已有环境可同步更新：

```bash
conda env update -n ResAgent2 -f environment.yml --prune
conda activate ResAgent2
```

调用 LLM 至少需要 API key。下面是默认 DeepSeek 配置的最小示例：

```bash
export DEEPSEEK_API_KEY="..."
export RESAGENT2_MODEL="deepseek-v4-flash"
```

也可以通过 `RESAGENT2_API_BASE`、`RESAGENT2_API_KEY_ENV` 切换兼容服务；不要把 API key 写进命令、配置文件或 Git 仓库。

## 2. 快速开始：交互 shell

直接运行 `resagent2`（或 `resagent2 shell`）：

```text
$ resagent2
resagent2> /run --workspace /path/to/repo --goal "验证 SE 模块是否提升 CIFAR-10 准确率"
resagent2> /artifacts
resagent2> /trace
resagent2> /quit
```

常用命令：

| 命令 | 用途 |
|---|---|
| `/run ...` | 创建并执行新 Run；参数与一次性 `resagent2 run` 相同 |
| `/show <run_id>` | 显示已持久化 Run 的当前快照 |
| `/attach <run_id>` | 只监看现状，不启动或恢复 Run |
| `/resume <run_id>` | 继续一个已持久化且尚未稳定的 Run |
| `/answer <value>` | 当前问题只有一个字段时的简写 |
| `/answer name=value ...` | 显式回答一个或多个字段 |
| `/artifacts` | 列出当前 Run 的 Artifact |
| `/trace [run_id]` | 查看 full trace 中的原始请求、响应和可选 reasoning |
| `/help` / `/quit` | 查看帮助 / 退出 shell |

边界需要特别注意：

- `Ctrl-C` 只停止当前监看并回到提示符，不取消 Run；
- `/attach` 只读，不会偷偷启动后台执行；没有执行者推进时应显式使用 `/resume`；
- shell 启动时确定一个 data root，内部命令不能再切换，以免混用两个 Run store；
- 默认实时视图只显示 Run 快照和 metadata trace 中的 Agent/Tool 名称，不读取 full trace 的敏感正文。

## 3. 一次性命令

创建并执行一个 Run：

```bash
resagent2 run \
  --workspace /path/to/repo \
  --goal "验证 SE 模块是否提升 CIFAR-10 准确率"
```

较长目标可以放进 UTF-8 文件。`--goal-file` 只做显式文件读取，CLI 不猜测一段文本是不是路径：

```bash
resagent2 run --workspace /path/to/repo --goal-file goal.txt
```

查看、回答和恢复：

```bash
resagent2 show run_20260901_120000_ab12cd34

resagent2 answer run_20260901_120000_ab12cd34 \
  --field primary_evaluation_metric=accuracy

resagent2 resume run_20260901_120000_ab12cd34
```

`answer` 只回答当前 pending question，并随后继续同一个 Run；`resume` 不制造答案，只继续可恢复的执行。若 Run 在 workspace 持久化前就暂停，回答或恢复时需要再次提供相同的 `--workspace` 或 `--git`。

摘要在没有最终意见时显示 `Scientific assessment (interim)`（最近一次过程判断）；有 `Final opinion` 后不再默认展示旧过程判断，避免把已解决的问题当作当前结论。原始过程判断仍保留在 Run 状态中，显示不会改写记录。一次性命令与 shell 共用此规则。

常用 Run 参数可用 `resagent2 run --help` 查看，包括 `--hypothesis`、重复的 `--constraint`、Python 版本和 Run 预算。`--goal` 的自然语言文本会原样进入 `ResearchRequest`。

## 4. 数据集资源库

数据集是部署资源，不是每次 Run 都要填写的路径参数。先指定共享根目录：

```bash
export RESAGENT2_DATASET_ROOT=/data/datasets
```

然后在 `/data/datasets/catalog.json` 登记数据集 ID 和目录（目录尚未准备好时会标为不可用）：

```json
{
  "cifar10": "cifar10",
  "imagenet1k": "imagenet-1k"
}
```

对应目录示例：

```text
/data/datasets/
├── catalog.json
├── cifar10/
└── imagenet-1k/
```

规则保持简单：

- key 是 Agent 看到的稳定 `dataset_id`，value 是相对共享根的目录；
- Run 保存系统发现的目录引用，不改写用户目标，也不表示全部数据集都要用；catalog 缺失表示当前没有新登记；
- `available_dataset_ids` 是已登记且目录存在的 ID，`unavailable_dataset_ids` 是已登记但目录不存在的 ID；两张列表都没有的 ID 在当前视图中尚未登记，不能当作可用；
- 当前任务需要的数据集不在 available 列表时，Agent 应先询问用户，再做依赖该数据的工作；不因无关数据集缺失阻塞其他工作。目录存在不等于内容已校验；
- `catalog.json` 位于共享数据集根下；`RESAGENT2_DATASETS_JSON` 是传给脚本的 ID→绝对路径 JSON 内容，不是 catalog 文件路径，且只包含可用目录；
- 非法 JSON、登记格式或越界路径仍是配置错误，不当作普通数据缺失；
- Scientific、Coding、Experiment 使用同一份只读目录策略，不自行下载、不猜路径、不静默替换数据集；
- 缺少必需数据集时，对应 Agent 应通过 `ask_user` 暂停。部署者准备目录并更新 catalog 后，回答问题即可让同一个 Run 继续并看到新增资源；
- 同一个 Run 已绑定的 `dataset_id` 不允许被悄悄改到另一个目录。

因此，普通用户运行任务时不需要也不能传 `--dataset ID=PATH`，不必在开始前知道最终需要哪些资源。

遇到缺数据集时：

1. 用 `show` 查看当前问题及实际 `requested_fields`。
2. 将数据放到共享根目录，在 catalog 登记 ID→相对路径；不要让 Agent 临时下载或替换。
3. 用 `answer --field 实际字段=已准备好` 回答当前 Run（命令格式见上节），系统继续原任务并重新检查。

只回答“好了”不会让缺失目录变为可用。目录存在也不保证内部文件完整，读取失败仍需据实处理。
系统不后台监视目录，`resume` 不代替回答。等待用户的 paused 时间不计入 Run 超时，安装/执行等时间照常计入，已用 LLM 次数不重置。

Python 依赖不进 catalog。Coding/Experiment 按项目和运行需要使用现有安装、审计工具；pip/conda 自己管理下载缓存，镜像由部署配置负责，不要求每个 Run 填写依赖或缓存参数。

## 5. 数据、资源与 trace

Run 状态默认写入 `.resagent2/data`。推荐在服务器上显式指定独立目录：

```bash
export RESAGENT2_DATA_ROOT=/data/resagent2
# 或对单次命令使用：--data-root /data/resagent2
```

CLI 从这个根创建一个共享 `ResourceLayout`，注入三个 Agent。Scientific 只检查数据集，不获得执行环境工具；Coding 与 Experiment 在同 Run、同 Workspace 复用环境。可按部署需要覆盖：

- `RESAGENT2_RESOURCE_ROOT`：共享资源根；
- `RESAGENT2_DATASET_ROOT`：数据集根；
- `RESAGENT2_ENV_ROOT`：受管 Conda 环境根；
- `RESAGENT2_CONDA_EXE`：Conda 可执行文件路径。

LLM trace 默认关闭：

```bash
export RESAGENT2_LLM_TRACE_LEVEL=metadata   # off / metadata / full
export RESAGENT2_LLM_TRACE_DIR=/data/resagent2/traces
```

`metadata` 不保存消息正文；`full` 会保存 request、response 和模型提供时的 reasoning，适合调试但可能包含源码和用户输入。full trace 目录和文件分别按 `0700` / `0600` 创建，仍应只放在可信存储上并按需清理。

失败排查时看 `llm_traces.jsonl`：每个逻辑调用一条记录，`attempts` 列出最多三次尝试各自的 `finish_reason`、`usage` 和错误；full 档还有每次原始 response/reasoning。顶层响应字段对应最后一次尝试。`request_max_tokens` 是实际发送的输出上限，null 表示未指定；`retry_number + 1` 就是该调用的 HTTP 尝试数，不要再加 attempts 长度。即使最终 JSON 为空，用量和结束原因仍会保存（前提是 provider 返回了它们）。`/trace` 展示顶层最终响应；逐次失败细节查看 JSONL 的 attempts。

## 6. 模型与上下文预算

模型配置：

- `RESAGENT2_MODEL`：默认 `deepseek-v4-flash`；需要时可改为 `deepseek-v4-pro`；
- `RESAGENT2_API_BASE`：默认 `https://api.deepseek.com/v1`；
- `RESAGENT2_API_KEY_ENV`：保存 API key 的环境变量名，默认 `DEEPSEEK_API_KEY`。

上下文容量是显式配置，不依赖 provider 查询：

- `RESAGENT2_CONTEXT_WINDOW`：默认 `1000000`；
- `RESAGENT2_RESERVED_OUTPUT_TOKENS`：默认 `256000`（思考 + 最终输出的请求上限，不是目标长度）；
- `RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS`：默认 `1024`；
- `RESAGENT2_SCIENTIFIC_CONTEXT_TOKENS`：默认 `8192`；
- `RESAGENT2_CODING_CONTEXT_TOKENS`：默认 `8192`；
- `RESAGENT2_EXPERIMENT_CONTEXT_TOKENS`：默认 `8192`；
- `RESAGENT2_COMPILER_CONTEXT_TOKENS`：默认 `4096`。

网络等待参数：`RESAGENT2_LLM_TIMEOUT_SECONDS` 默认 `600`，传给现有客户端的 `urlopen(timeout=...)`。它不是整次 Run 的硬截止时间；超时仍走既有有界失败/重试路径，不新增自动扩容或无限等待。

三个 Agent 复用同一套读取工作集：Coding/Experiment 分别保留最多 6000 字符的文件正文和 6000 字符的工件正文；Scientific 只使用工件正文这一组，总共最多 6000 字符，不持有执行环境。8192 tokens 是包含任务、工具说明、反馈和正文的总输入上限，不是每次都填满。两类局部额度不相互借用。显式模块配置和模型可用容量仍是硬上限；如果调小到 required 内容装不下，会明确报预算错误，不会自动扩容或静默省掉整个读取工作集。

实际输入预算取“模块限制”和“模型窗口扣除输出、action schema 与安全余量后”两者的较小值。1M 是默认 V4 模型容量，不会把模块输入自动扩到 1M：各 Agent 仍为 8192、Compiler 仍为 4096。切换到其他模型/网关时，应同时配置真实 `RESAGENT2_CONTEXT_WINDOW` 和 provider 接受的 `RESAGENT2_RESERVED_OUTPUT_TOKENS`；不合法组合会在调用前拒绝，不按模型名字猜容量。Compiler 复用同一个 context composer 和预算算法，但仍是无状态的一次性编译器，不进入 Agentic Loop。

`RESAGENT2_RESERVED_OUTPUT_TOKENS` 不只是输入预算里的预留值：它也作为请求的 `max_tokens` 发给 provider。思考模型如何计算输出额度以该 provider 的定义为准；如果思考计入输出额度，就要为思考和最终 JSON 一起留空间。“输入没有超限”不代表“输出不会被截断”。遇到空 JSON，先看 trace 的 `finish_reason` / `usage` / `request_max_tokens`，不要仅凭重跑成功归因模型抖动。确认输出额度不足后可调整这一个现有配置；系统不会自行扩容，仍须满足总窗口约束。

默认值采用 DeepSeek 官方 Harness 的 1M 容量 / 256000 输出额度策略；依据、取舍和验收见 [模型输出默认配置](../../docs/history/reviews/MODEL_OUTPUT_DEFAULTS.md)。更大上限不强迫输出到上限，但允许长思考消耗更多时间和 tokens；这不是对任意任务永不截断的保证。

**升级注意**：环境变量优先于代码默认值。如果部署脚本仍显式设置输出 `4096` 或容量 `65536`，更新代码不会覆盖它。使用新默认时应移除这两个旧覆盖，或成对设置 `1000000` / `256000`；只保留旧的小容量会被现有校验拒绝。不要打印 API key 来核对配置。程序化客户端和独立 E2E 组合根不会自动继承 CLI 的部署默认值。

## 7. 退出码与常见情况

| 退出码 | 含义 |
|---|---|
| `0` | Run completed，或 `show` 成功 |
| `1` | Run failed 或 CLI 输入错误 |
| `3` | Run paused，等待用户输入 |
| `4` | Run 仍在运行 |

常见处理：

- `Status: paused`：查看 `Pending question` 和 `Fields`，使用 `answer`；不要用空 `resume` 代替回答；
- 所需数据集不可用：准备目录并登记，按当前问题字段回答；无关条目暂缺不会提前使整个任务失败；
- 非交互环境找不到 Conda：设置 `RESAGENT2_CONDA_EXE` 为绝对路径；
- `/trace` 显示 `No trace records.`：确认 trace level 为 `full`，并且 shell 与执行进程使用同一 trace 目录。
