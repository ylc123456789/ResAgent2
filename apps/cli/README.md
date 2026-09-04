# ResAgent2 CLI

`resagent2` 是现有 `ResearchController` 最外层的命令行适配器。它提供两种使用方式：

- 交互 shell：适合日常使用，可以实时看进度、回答问题和恢复 Run；
- 一次性命令：适合脚本、自动化和明确的单步操作。

CLI 不实现另一套研究控制、调度、Agent 或证据逻辑；两种入口最终都调用同一个 `ResearchController`。

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

常用 Run 参数可用 `resagent2 run --help` 查看，包括 `--hypothesis`、重复的 `--constraint`、Python 版本和 Run 预算。`--goal` 的自然语言文本会原样进入 `ResearchRequest`。

## 4. 数据集资源库

数据集是部署资源，不是每次 Run 都要填写的路径参数。先指定共享根目录：

```bash
export RESAGENT2_DATASET_ROOT=/data/datasets
```

然后在 `/data/datasets/catalog.json` 注册已经准备好的数据集目录：

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
- 只有 catalog 中已注册且真实存在的目录会进入 Run；catalog 缺失表示当前没有已注册数据集；
- 非法 JSON、越界路径或不存在的已注册目录会在执行前明确报错；
- Scientific、Coding、Experiment 使用同一份只读目录策略，不自行下载、不猜路径、不静默替换数据集；
- 缺少必需数据集时，对应 Agent 应通过 `ask_user` 暂停。部署者准备目录并更新 catalog 后，回答问题即可让同一个 Run 继续并看到新增资源；
- 同一个 Run 已绑定的 `dataset_id` 不允许被悄悄改到另一个目录。

因此，普通用户运行任务时不需要也不能传 `--dataset ID=PATH`。

## 5. 数据、资源与 trace

Run 状态默认写入 `.resagent2/data`。推荐在服务器上显式指定独立目录：

```bash
export RESAGENT2_DATA_ROOT=/data/resagent2
# 或对单次命令使用：--data-root /data/resagent2
```

CLI 从这个根创建一个共享 `ResourceLayout`，并注入 Coding 与 Experiment，使顺序任务复用同一环境和资源根。可按部署需要覆盖：

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

## 6. 模型与上下文预算

模型配置：

- `RESAGENT2_MODEL`：默认 `deepseek-v4-flash`；需要时可改为 `deepseek-v4-pro`；
- `RESAGENT2_API_BASE`：默认 `https://api.deepseek.com/v1`；
- `RESAGENT2_API_KEY_ENV`：保存 API key 的环境变量名，默认 `DEEPSEEK_API_KEY`。

上下文容量是显式配置，不依赖 provider 查询：

- `RESAGENT2_CONTEXT_WINDOW`：默认 `65536`；
- `RESAGENT2_RESERVED_OUTPUT_TOKENS`：默认 `4096`；
- `RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS`：默认 `1024`；
- `RESAGENT2_SCIENTIFIC_CONTEXT_TOKENS`：默认 `4096`；
- `RESAGENT2_CODING_CONTEXT_TOKENS`：默认 `4096`；
- `RESAGENT2_EXPERIMENT_CONTEXT_TOKENS`：默认 `4096`；
- `RESAGENT2_COMPILER_CONTEXT_TOKENS`：默认 `4096`。

实际输入预算取“模块限制”和“模型窗口扣除输出、action schema 与安全余量后”两者的较小值。切换到更小窗口的模型时，应把 `RESAGENT2_CONTEXT_WINDOW` 改成该模型的真实容量。Compiler 复用同一个 context composer 和预算算法，但仍是无状态的一次性编译器，不进入 Agentic Loop。

## 7. 退出码与常见情况

| 退出码 | 含义 |
|---|---|
| `0` | Run completed，或 `show` 成功 |
| `1` | Run failed 或 CLI 输入错误 |
| `3` | Run paused，等待用户输入 |
| `4` | Run 仍在运行 |

常见处理：

- `Status: paused`：查看 `Pending question` 和 `Fields`，使用 `answer`；不要用空 `resume` 代替回答；
- catalog 报目录不存在：准备好目录或修正 `catalog.json`，不要注册一个尚未落盘的占位路径；
- 非交互环境找不到 Conda：设置 `RESAGENT2_CONDA_EXE` 为绝对路径；
- `/trace` 显示 `No trace records.`：确认 trace level 为 `full`，并且 shell 与执行进程使用同一 trace 目录。
