# 项目代码审查与开发基线（2026-10-04）

审查对象：`refactor/literature-foundation@361e9fc180acaa1146d69911f038440268f6831d`，公共 schema 22.0。
审查和本地检查均在 WSL Ubuntu-D 的 `/home/cyl/ResAgent2` 完成，未使用 Windows 仓库副本。
该分支已推送，尚未合并。审查阶段只写审查及测试交接文档，没有修改产品代码、测试或运行记录，也没有运行服务器 L3。用户随后授权修复；原发现与复现保留如下，修复结果见文末“修复收尾”。

## 结论

现有架构适合作为继续开发科研 Agent 的单机 Linux 底座。Scientific 的科研判断、Compiler 的需求翻译、Scheduler 的任务执行、Controller 的 Run 控制、Interpreter 的固定反馈整理已经形成完整链路；三个 Agent 共用 Runtime，没有各自复制执行循环。

本次没有发现需要替换整个框架、增加一层注册器或为了文件长度拆分模块的依据。主要问题是少数边界输入和执行异常下，实际事实、登记身份、验证记录没有完全对齐。全量测试通过仍存在这些覆盖缺口，应先做局部修复，再以真实 L3 建立新基线。

这是一次系统级代码审查和边界复现，不是形式证明或科研成功率评估。以下描述审查时的缺陷；后续已完成局部修复，不代表当前版本仍有同一问题，见文末收尾。

## 审查范围与验证

- 按模块分工审查 Contracts、Runtime、Components、Capabilities、三个 Agents、Orchestrator、生产 CLI；核对 current 设计原则、架构、契约和上下文，以及 guides 的开发/L3 规程。
- 生产 Python 范围：packages 87 文件、14,034 行；CLI src 6 文件、1,222 行。另检查独立 E2E 装配和 CLI 的配置启动脚本。
- 追踪创建、编译、执行、工件登记、反馈、完成、问答恢复、审批、预算和失败路径；查看相关行为测试，在系统临时目录独立复现遗漏边界。
- 主审查者独立运行全量：`python -m pytest tests apps/cli/tests -q`，**1641 passed / 1 skipped**，51.46 秒。
- 主审查者独立运行 mock：`run_golden completed`，13 工件。
- 九个包的实际 import 指针全部位于该 WSL 仓库源码。
- 本地 `pip check` **未通过**：额外安装的 `pdfminer-six 20260107` 缺少 `cryptography`。该包不在项目声明的依赖链中，属于本地环境问题，不能写成本轮依赖全绿。另发现 editable 的 Components 安装元数据尚未更新到当前 pyproject 中新增的 PDF 依赖，虽然所需 pymupdf4llm 1.28.2 已安装、测试通过；部署预检必须同步安装元数据。审查没有安装、删除或升级依赖。
- 没有调用真实模型、访问服务器、执行 GPU/L3，也没有重做先前论文解析验收。旧验收事实见[文献收尾](LITERATURE_FOUNDATION_ACCEPTANCE_2026-10-03.md)，不能当成本 SHA 的新 L3 结果。

## 确认的问题

### 1. P1：重复导入失败会删除此前冻结的 PDF

位置：[ArtifactRegistry.register_import](../../../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)，191–214 行；[Controller](../../../packages/orchestrator/src/resagent2_orchestrator/controller.py)，130 行。

工件 ID 包含内容 hash、kind 和 metadata，落盘路径却包含来源文件名。先导入 paper.pdf，再把相同字节改名为 renamed.pdf 导入，两个文件属于同一个工件目录。第二次复制发生磁盘满等错误时，异常处理会递归删除整个目录，连首次已经冻结的 PDF 一并删除；持久 Run 仍保存指向旧文件的 Ref。

同一根因的成功路径也不合理：相同 ID/hash/metadata 的再次导入会改写 Ref URI，旧文件仍留在目录中，research_index 因不记录 URI 可能完全不变。它违背重复导入稳定引用与不改写冻结来源的约定。

已在临时目录通过真实 paused Controller 导入路径复现：注入 `OSError(28, "No space left on device")` 后，旧 Ref 仍存在，旧冻结文件和目录已经消失。成功路径确认 `same_id=true, same_hash=true, ref_changed=true`。

最小修法：相同身份复用既有冻结文件和稳定 Ref；失败只清理本次临时文件或本次新建目录，不删除已有正式工件目录。不要仅将用户文件名加入 ID，避免同字节改名被当成新材料。补成功改名复用、失败保留旧证据的确定性测试。

### 2. P2：验证可使用另一套 Python，却归到绑定环境

位置：[verification.py](../../../packages/agents/coding/src/resagent2_coding/verification.py)，63、80–92、177、227 行。

验证策略只检查执行文件的 basename，因此允许 `/usr/bin/python3 -m unittest`。给它加上 `conda run -p <binding>` 不会把绝对解释器替换成环境内的 Python。工具仍使用已认证 binding 的 generation，完成记录可以标为当前有效验证，但实际依赖来自系统 Python。

独立真实 unittest 复现：绑定 prefix 是 ResAgent2 Conda 环境；stdout 却为 `ACTUAL_PREFIX=/usr, ACTUAL_EXE=/usr/bin/python3`，同时 `ok/certified/covers_current_workspace/passed` 均为 true。

最小修法：有绑定环境时确定性使用绑定 Python；可以接受裸名 python/python3 并解析到绑定解释器，或只允许属于绑定环境的显式路径；pytest 同样通过绑定 Python 的 `-m pytest` 运行。保留现有验证范围和审批，不新增第二套权限框架。补实际解释器与认证范围一致的测试。

### 3. P2：submodule 修改被快照遗漏，验证可能误报“没有变化”

位置：[git.py](../../../packages/components/src/resagent2_components/git.py)，98–109 行。

Git 列出的是 submodule 的 gitlink 目录，而快照构建将目录过滤掉。工作区工具仍能修改 submodule 内文件，因此实际允许操作的范围大于记录覆盖的范围。

已独立创建临时 submodule 仓库：修改源码后 Git 显示 `M vendor/model`，组件却返回空 diff。进一步让真实 unittest 修改该文件，工具仍返回 `passed=true, workspace_unchanged=true`。

最小修法：先检测 gitlink，明确拒绝或标明当前无法认证该范围，避免把漏掉的变化当作未变化。当前没有必要新增递归 submodule 管理框架；若今后支持，再统一快照、变更、验证和产物范围。补修改 submodule 不可获得“完整未变化”认证的测试。

### 4. P2：验证后置诊断失败，实际命令回执未保存

位置：[verification.py](../../../packages/agents/coding/src/resagent2_coding/verification.py)，226–236 行。

ProcessRunner 已返回命令退出/超时结果，但工具随后重新计算 Git digest。Run 截止时间耗尽或该诊断失败时，异常发生在 observation 和 memory_updates 返回之前，已执行命令的结构化结果丢失。stdout 文件可能还在，后续消费者却没有对应的 verification_results。

独立复现中 unittest 确实启动，stdout 含 `execution_started`；随后得到 `DeadlineExceededError`，memory 中没有验证回执。

最小修法：保留已经发生的命令事实，将后置新鲜度诊断作为独立结果处理。诊断失败应记录真实 command、exit_code、timed_out 和日志路径，并标为“新鲜度未确认”，不能认证当前有效验证，也不放宽 Run 期限。补命令后期限耗尽和多命令部分执行的测试。

### 5. P2：工作输入 ID 错误在工具端没有反馈，直接终结 Run

位置：[Scientific tools.py](../../../packages/agents/scientific/src/resagent2_scientific/tools.py)，48–54 行；[Controller](../../../packages/orchestrator/src/resagent2_orchestrator/controller.py)，324–325 行。

RequestWorkTool 检查 assessment 的证据引用，却不检查 work_request.input_artifact_ids。一个格式正确、实际不存在的 ID 可以让工具成功并结束本轮；Controller 接收时才拒绝，将整个 Run 置为不可恢复的 failed。与其他可纠正的模型参数错误处理不一致。

独立复现给出 `artifact_typo`，并预留第二次合法模型响应：Run 在第一调用后 failed，error 为 `work request references unauthorized materials`；再次推进仍 failed，第二响应没有机会被调用。

最小修法：工具端复用同一个授权 reader 校验工作输入，错误返回 `ok=False`，在原 AgentLoop 中反馈纠正；保留 Controller 的接收复验。补未知、跨 Run、损坏输入和同 Session 修正测试。

### 6. P2：COPY 的 Git 来源边界不完整

位置：[repo.py](../../../packages/components/src/resagent2_components/repo.py)，49–64、244–256、270 行。

两个已复现触发：

- 来源是 linked worktree 时，直接复制 .git 指针文件；副本仍与原 worktree 共用 Git 管理目录、索引及分支。临时副本执行 git commit 后，原 linked checkout 的 HEAD 也推进。
- 来源是仓库子目录时，`--is-inside-work-tree` 通过，COPY 复制一个没有 .git 的目录并报告成功、commit 为空；后续 Coding 才发现不是仓库。LOCAL 也接受子目录，与 GitWorkspace 的根目录要求不一致。

最小修法：入口检查请求路径是否等于 Git 顶层；COPY 暂时明确拒绝指向外部元数据的 .git 文件来源。先把不支持的输入清楚拒绝，不必现在实现完整独立 worktree 复制。补原仓库不受副本操作影响，以及子目录在物化前拒绝的测试。

该问题涉及基础组件/公共 API；当前单次 CLI 的 --workspace 是 LOCAL，常规仓库根 L3 不会通过 COPY 路径，不能用 L3 成功证明此问题不存在。

### 7. P2：合法长 TaskId 无法登记输出工件

位置：[ArtifactRegistry.register](../../../packages/orchestrator/src/resagent2_orchestrator/artifacts.py)，87–90、120–144 行；[ID 契约](../../../packages/contracts/src/resagent2_contracts/models.py)，57–65 行。

TaskId 允许前缀后 128 个字符，工件 ID 又将完整任务后缀与 attempt/index 拼接。合法任务身份因此生成超长 ArtifactId，登记在构建 Ref 时才抛 ValidationError，正式文件已经写入。

主审查者独立使用已通过 TaskId 验证的 `task_ + 128 个 a` 和正常 metrics 候选复现，确认 Ref 创建失败、正式 metrics.json 已存在。

最小修法：沿用已有 Session 命名思路，为可能超长的完整身份使用有界且不碰撞的 hash 名称，创建文件前验证最终 ID。不要缩短公共 TaskId 支持范围来掩盖问题。补最大长度、不同尝试及不同任务不碰撞的测试。

默认 LLM Compiler 的 draft key 上限为 80，通常不会触发；这是公共扩展接口的真实边界问题。

## 文档一致性问题

访问记录已经实际定位为日志，读取、引用和控制动作不再由 observed 标记授予资格，但仍有旧措辞：

- [CONTRACTS](../../current/CONTRACTS.md) 399 行写“已观察引用”，407 行写最终 gate 校验“观察记录”。
- [L3 规程](../../guides/L3_RESEARCH_TEST.md) 191 行写“报告展示不自动赋予原件引用资格”。

应分别说明登记授权与冻结证据校验，以及报告不能代替原件内容依据，避免后来开发者重新加入阅读门禁。此项是文档收尾，不需要改变当前日志机制；审查时仅记录，后续修复已同步现行规范。

## 已实现并应保留的基础

| 范围 | 已有基础 | 后续开发保持什么 |
|---|---|---|
| 运行机制 | 三个 Agent 共用 Loop、上下文、工具协议、历史压缩和 Session | 领域判断留在 Agent，不向 Runtime 塞业务分支 |
| 编排 | Controller / Scheduler / Compiler / Interpreter 职责分开 | 不增加平行控制循环；解释报告不改写执行事实 |
| 公共 IO | instruction + input_artifacts / report + artifacts，控制字段结构化 | 新能力沿现有边界接入，不读取其他 Agent 私有 memory |
| 预算与恢复 | Run 请求总账、逐次 HTTP 计量、暂停不消耗执行时间、原生中断不自动重放 | 不重置用量或暗中重试外部副作用 |
| 操作与审批 | Components 普通操作、Capabilities 模型工具、精确单次批准 | 修复参数及执行事实，不重复新增权限分类层 |
| 科研材料 | 单篇 paper、独立 PDF/fulltext、来源链和冻结 hash、统一研究索引 | Registry 为唯一登记来源，索引只导航 |
| 交接与判断 | Interpreter 固定组织原报告；Scientific 解释证据；访问仅日志 | 不把报告数量当独立测量，不让“已读”成为主线门禁 |
| 验收 | 明确逻辑输出与证据类型有确定性校验，科学质量另审 | completed 和测试全绿均不等于研究结论正确 |

## 已知限制与尚未验证的范围

- PDF 解析仍是固定 120 秒上限，受 Run 剩余时间约束；复杂 9 页也可接近上限，不存在可靠的“14 页阈值”。部署级可调超时是已记录的后续工作，尚未实现。
- arXiv/OpenAlex 外部可用性、全文可下载性和排序质量不是本次确定性回归验证的结果。外部导入提供替代材料入口，不等于在线搜索已经达到商业工具水平。
- 工作区 read_file 仍有整文件大小限制；不为此在本次增加通用文件格式框架。
- 当前是可信单机、单写入者模型；不是 OS 沙箱，没有并发推进同一 Run、掉电事务、任意外部副作用 exactly-once 或完整取消机制保证。这些是已声明的支持边界，不列为这轮新缺陷。
- schema 22 的定向文献验收有效，但本 SHA 尚无新自然科研 L3。一次 L3 不能证明通用稳定性，也不能估计 prompt 的因果提升。

## 后续顺序

1. 先局部修复冻结证据破坏及验证事实不一致；为已确认边界补行为负例，避免仅修改 prompt 或降低断言。
2. 补工作输入的原循环纠错，明确 Git 不支持的来源/快照范围，并修长身份输出；同步过时文档。
3. 回归全绿后冻结新产品 SHA，再启动一次自然科研 L3。L3 与这些确定性边界测试互补，不能替代它们。
4. 之后单独优化 PDF 性能/部署超时和在线文献可用性。保持现有层次，先针对实际问题做小改动，再考虑更大的扩展。

若 L3 已开始，保留当次 SHA/输入，不在运行中修产品或改预算。它仍能作为当前版本的一次案例，但不能用成功结论关闭本报告中的专项缺陷。

测试 AI 的具体交接见[本版 L3 说明](L3_SCHEMA22_HANDOFF_2026-10-04.md)。

## 本地复现附件

下列脚本只使用临时仓库、离线响应或受控真实命令，不调用模型/服务器。主审查者已独立执行核对；退出 0 表示“上述缺陷按预期被复现”，不是产品通过验收。未来修复应另写正确行为的回归断言。

| 问题 | WSL 临时脚本 |
|---|---|
| 导入改名改写引用 | /tmp/resagent2-code-review-20261004/reproduce_import_rename.py |
| 导入失败删除旧 PDF | /tmp/resagent2-code-review-20261004/reproduce_import_enospc.py |
| 验证解释器错配 | /tmp/resagent2-review-verification-interpreter.py |
| submodule 验证遗漏 | /tmp/resagent2-review-submodule-verify.py |
| 验证后期限耗尽 | /tmp/resagent2-review-verification-deadline.py |
| 错误工作输入 ID | /tmp/resagent2-review-workids.py |
| COPY linked worktree / 子目录 | /tmp/resagent2-review-copy.py |
| 长 TaskId 输出失败 | /tmp/resagent2-code-review-20261004/long_task_artifact_probe.py |

运行方式为 `/home/cyl/miniconda3/envs/ResAgent2/bin/python <脚本路径>`；临时脚本不作为永久产品依赖，关键触发、事实及修法已写入本报告。

## 修复收尾（同日，schema 22.0）

用户授权后，在原分支完成以下局部修复；没有合并，没有增加权限框架、阅读门禁、额外调用或时间预算。原审查 SHA 与上文缺陷复现保留为历史；本报告随修复一起提交，以最终交接 SHA 为复测版本。

| 审查问题 | 实际修复 | 新增验证 |
|---|---|---|
| 重复导入破坏证据/改写 URI | 同身份复用原冻结文件与 URI；新材料独立 staging 校验后发布，失败只清理 staging | 改名时不调用 copy；新导入复制失败保留旧字节；损坏原件连同改名重导均拒绝；paused Controller 重导索引/Ref 稳定 |
| 验证使用外部 Python | 绑定环境下默认策略审批前拒绝显式 Python/pytest 路径；裸名解析为绑定 bin/python，pytest 使用 -m pytest | 真实 ProcessRunner 在 PATH 被替代程序遮蔽时仍使用绑定解释器；不改变精确审批 |
| submodule 内容遗漏 | 当前快照明确拒绝可读范围中的 gitlink，不做递归支持 | 当前快照与历史 baseline 检查均拒绝；未初始化也拒绝；排除/无关范围仍可正常快照 |
| 后置诊断失败丢回执 | 保留已返回结果，独立记录新鲜度诊断失败；部分批次列明缺回执命令，不虚构执行结果 | Git/截止错误、批次中断、runner IO；原生 Loop 真实命令后期限耗尽仍保存 Session 事件、memory 和 tool_results，并返回 TIMEOUT |
| 错误工作输入 ID 直接终结 | request_work 先复用授权 reader 检查登记与冻结 hash，ok=False 在原循环反馈；Controller 保留接收复验 | 未知/跨 Run/损坏/缺失拒绝；同 Session 修正成功；合法未读输入不新增访问日志 |
| COPY/LOCAL 来源边界 | 来源必须是仓库根；COPY 拒绝外部 .git 指针/链接；LOCAL 仍可绑定 linked worktree | 子目录在创建前拒绝；拒绝 COPY 不改变原 HEAD/index/文件；正常 LOCAL linked 绑定通过 |
| 长 TaskId 无法登记 | 有界前缀加完整身份 hash；最终 ArtifactId 在文件创建前校验；短 ID 保持格式 | 最大长度任务及不同 Task/Attempt/Index 不混淆；非法最终 ID 不产生正式文件 |

同步了 current 契约/上下文、Components/CLI 说明、L3 规程和本版测试交接；旧“已观察引用/观察记录 gate”表述已改为登记授权、冻结完整性和访问日志语义。公共 schema 未变化。

主审查者集成检查：
- 全量 pytest：**1675 passed / 1 skipped**，55.17 秒，比审查基线增加 34 个行为用例。
- mock E2E：**run_golden completed / 13 工件**。
- 独立交叉审查相关工件/Scientific 边界：80 passed；Coding 真实执行/Loop 与 Git 来源/snapshot 负例也纳入全量检查。
- 工具指纹只更新 Coding run_verification 的绑定解释器用法说明；没有重置其他工具预期。
- 未安装/更新依赖。本地额外 pdfminer-six 缺 cryptography 与 Components editable 元数据未刷新仍是部署预检项，不写成 pip check 全绿；服务器独立检查。
- 未执行真实模型、GPU 或服务器 L3。新版本自然科研验收仍交给[本版 L3 测试](L3_SCHEMA22_HANDOFF_2026-10-04.md)。

遗留支持边界：固定 PDF 解析 120 秒、在线论文来源可用性/排序、工作区大文件读取、无 OS 沙箱、同 Run 单写入者、非跨存储事务均未改变。历史 bug 若已把某导入目录污染为多个文件，本轮明确拒绝，不猜测修复或添加迁移框架；测试应创建新 Run 并保留旧现场。
