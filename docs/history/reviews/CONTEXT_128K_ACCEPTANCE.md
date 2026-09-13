# 上下文语义、128K预算与文献呈现验收

状态：2026-09-13 阶段验收完成；服务器实测 `ba84547`，本地与服务器 **859 passed, 1 skipped**，mock E2E completed。原始请求/回答、Session 与工件已复核，文献回放补验已完成；实时 arXiv 仍有外部依赖限制。结果、勘误与未证明事项见[最终复核](#verified-closeout)。以下第 1–4 节保留可复跑要求，不把旧 f2d4421 的结果算成本轮通过。

验收分支 `fix/context-budget-presentation`，产品提交 `3efce21`，基于既有 `fix/semantic-handoffs` 的 `f2d4421`；实测包含随后文档提交 `ba84547`。验收方未合并或推送；后续收尾仅同步文档，不改变已验收产品代码。

范围及当前约定见[CONTEXT](../../current/CONTEXT.md)，原始问题见[审查](CONTEXT_REVIEW_2026-09-13.md)。不修改公开业务schema 7.0、状态机、完成门禁、JSON恢复协议、训练目标或模型默认名。

## 1. 同步、环境和确定性基线

- 使用git/bundle同步完整提交到干净worktree；记录8包editable指针及前值。不要scp零散源码，不合并或推送main。
- 使用既有ResAgent2环境，保留旧环境、缓存、数据集、Run和失败现场。新产物统一收进一个新根，含MANIFEST、logs、traces、workdirs、ops。
- key仅运行期加载，不打印或写入脚本。full trace沿用目录0700、文件0600。先记录代码加载到的默认值；不要只检查环境变量。
- CLI如有旧覆盖，取消三个Agent的 `*_CONTEXT_TOKENS=8192`，或显式设置128000；记录采用哪种。Compiler保持4096。模型容量、输出、thinking、HTTP重试、step/Run预算不变，不同时测试自动扩到256K。
- E2E和CLI各有组合根；原生Agent默认同源，但E2E不会自动采用CLI的模块环境变量覆盖。核实真实装配，不能把两者说成完全同配置。

在独立临时cwd中执行（repo_dir为此次checkout，已激活既有Python环境）：

```bash
PYTHONPATH="$repo_dir" python -m pytest "$repo_dir/tests" "$repo_dir/apps/cli/tests" -q
PYTHONPATH="$repo_dir" python -m e2e.mock_e2e
git -C "$repo_dir" diff --check
```

重点测试：

- tests/coding/test_control_state.py
- tests/capabilities/test_workspace_context.py
- tests/capabilities/test_text_windows.py
- tests/capabilities/test_module_report.py
- tests/e2e/test_native_context_capacity.py
- tests/e2e/test_scientific_context_capacity.py
- tests/e2e/test_literature_artifact_windows.py
- tests/scientific/test_prompt_responsibilities.py
- tests/runtime/test_context.py、test_agent_loop.py
- apps/cli/tests/test_model_configuration.py

这里已覆盖同批命令中间失败、stdout错误、空stderr、诊断裁剪、省略数量、旧失败被较新结果替换但原事件不变、目录事件号、模型容量先缩小再装箱、128K下大材料共存，以及过小必需预算仍失败。此为代码保证，不是LLM行为保证。

## 2. 真实回归矩阵

沿用 `python -m e2e.real_e2e <场景>`，工作目录由REAL_E2E_WORKDIR指定。RESAGENT2_LLM_TRACE_LEVEL=full，RESAGENT2_LLM_TRACE_DIR每次独立；RESAGENT2_MODEL选择相应模型。不得改e2e产品文件或目标。

| 场景 | 次数 | 核验重点 |
|---|---:|---|
| code-experiment / deepseek-v4-flash | 2 | 修改→验证→正式实验；读/改循环、错误恢复、指标 |
| code-experiment / deepseek-v4-pro | 1 | 同链路，确认大材料和工具规则无回归 |
| repair / Flash | 1 | 真实失败stderr、修复、重跑；不把历史诊断当当前失败 |
| direct / Flash | 1 | 无图完成，不因更大容量额外制造任务 |
| literature / Flash | 2 | 每篇条目、实际读取、来源/摘要级别、无需新记忆 |
| ask-start→ask-resume / Flash | 两个进程 | 同Run/Session，按实际字段回答accuracy，原题配对 |

共9次E2E入口。除问答恢复共用目录外每次独立；E2E若固定Run ID，隔离workdir并记录环境复用。训练可复用现有torch环境；需要大型安装先报告，不为验收重建/删除环境。

另跑CLI独立组合根run→show→answer冒烟（显式data-root，读实际requested_fields，不猜字段名），确认128K新默认被CLI采用，暂停/回答与产物位置不回归。

## 3. 必须检查原始LLM请求和工具事件

逐项给出call_id、trace路径、Session事件号。不要仅凭rc=0、included_sections或模型reasoning自述宣称通过。

### 3.1 控制与诊断

- code_modify的control_state用edited_since_verification；验证完成后它为false仍可存在真实patch，required_next_action可为finish。
- 最新编辑或环境代次变更后，较旧失败不会覆盖“需重新验证”的指引；实际判定仍来自completion gate。若真实流程未触发代次变更，只列确定性覆盖。
- directory含真实observed_at并解释历史性；创建文件后旧清单不自动改写。不因旧清单缺文件就断言文件不存在。
- run_verification/run_setup/run_command有记录结果后，command_results进入实际请求：命令、退出/超时、失败stdout/stderr尾部对应原事件。
- 检查有多条结果时失败在中间也可见；若真实矩阵没有该形态，明确“本轮未触发，确定性测试覆盖”，不要强称实测。
- 多次read/search之后，最近的验证失败仍在诊断段；较新同工具成功后旧失败不再作为最新结果展示，但原日志/事件仍存在。
- prompt不再建议被权限拒绝的python -c验证。验证工具权限没有放开；标准库检查使用unittest/py_compile等既有白名单。

### 3.2 额度

- 记录三个Agent实加载上限128000、Compiler4096，以及Profile/hook实际采用的有效额度；estimated_tokens按完整渲染文本重算（不含客户端追加的action-schema说明，该部分在Profile路径另预留）。
- 检查workspace_reads实际正文：Coding/Experiment各组不超过有效额度×4×25%；Scientific工件不超过有效额度×4×50%。元数据仍计入总额。
- 记录是否存在截断/片段省略，不能因上限提高就报告“没有截断”。原事件保持不变，observed_at与后续编辑标记仍对应。
- 新读取工具上限128000是字符，不是token。大材料共存、小模型容量先收缩由容量测试证明；真实场景若输入不大，应报“没有自然用满”，不要添加无关材料凑128K。
- 记录调用次数、实际usage、耗时。128K是上限，不是目标长度，不承诺费用或时延与旧版一样。

### 3.3 文献

- 新literature_search工件是text/markdown，literature_search.md，每篇有标题、ID、来源、作者、日期、Retrieved abstract；正文明确不是全文或模型阅读结论。
- 对照metadata中的规范化论文记录和冻结正文：仅确定性排版/换行，不添加新事实；引用摘要的结论不能冒充已读PDF全文。
- 看原始read_artifact返回及下一轮workspace_reads，至少一个被引用论文的实际摘要可见，不是只读工件编号/搜索短预览。
- 既有行读取、hash验证、Run授权仍有效。没有新摘要模型调用、reading notes、向量库或额外记忆字段。
- 若外部超时/429：保留原请求、工具错误和已有工件；区分“证据已在本地但模型又联网”与“确实没有必要证据”。暂停不记作正常文献完成，也不自动定性框架回归。

### 3.4 相关模块风险（单独小探针）

沿用[语义交接验收§3](SEMANTIC_HANDOFFS_ACCEPTANCE.md)的标准库任务，不安装torch。生成一个有实际局限的Experiment或Coding结果，module_report里有非空residual_risks。通过正常交付链送Scientific，不旁路注入私有Session或把完整风险复制进它的研究目标。

核验报告入口及新用途提示进入原始prompt→真实read_artifact读取相关报告→最终意见考虑对应局限，并与真实指标分清。若未读或忽略，明确记录模型消费缺口；本轮只改prompt，没有确定性必读门禁，不能把提示存在当成行为通过。不要求读遍所有历史报告。

## 4. 交付与停止边界

- MANIFEST分别报告：原记录、实际上下文、模型行为、真实结果。未触发/未执行/外部依赖故障单列。
- 解析错误、schema错误、HTTP重试、Task Attempt重试分别统计；按call_id去重后累加attempts，与Run.llm_calls_used核对。
- JSON继续沿用既有有界恢复，不为本轮增加修JSON、关闭thinking、改变模型或抬高执行预算。遇失败保留现场，诊断重跑使用新目录并与首跑并列。
- 不因为某模型通过就断言另一模型失败是随机；不因为一次全绿就宣称“永久稳定”。
- 凭据扫描只报告命中与否；报告包含源码或用户内容时按私有证据保存。不得清理历史文件、环境、缓存和旧worktree。
- 完成后仅交报告和产物根，未经授权不合并、不push、不恢复旧editable指针。

<a id="verified-closeout"></a>

## 5. 最终复核与收尾（2026-09-13）

**结论：本轮获批的语义、预算和呈现改动可以收尾；实时 arXiv 可用性不在通过结论内。** 复核读取了原始 request/response、provider 返回的 reasoning、Session 和冻结工件，不只采信摘要或退出码。收尾不再改产品代码、schema 7.0、预算、JSON 恢复、执行权限或状态机。

### 5.1 提交与证据位置

- 产品 `3efce21`，服务器实测 `ba845472a5e2c161fcaf267a900ab9a886dd6ba6`；干净 worktree `/root/autodl-tmp/projects/ResAgent2-ba84547`。
- 统一证据根 `/root/autodl-tmp/e2e-output-ba84547-E5ierm/`：原 `MANIFEST.md`、新增 `ERRATUM.md`、`REPLAY_SUPPLEMENT.md`，以及 logs/traces/workdirs/ops。原报告未覆盖。
- 8 包 editable 指针由验收方核对指向该 worktree，之前的 `f2d4421` checkout 与现场保留；收尾不恢复指针、不清理服务器。
- 服务器全量 859 passed、1 skipped，焦点 11 文件 131 passed；本次文档收尾在本地隔离 cwd 再跑全量，同为 859 passed、1 skipped，mock E2E completed。唯一 skip 为 opt-in 文献网络 smoke，不计作已通过。

### 5.2 原始矩阵与语义验证

| 范围 | 实际结果与证据边界 |
|---|---|
| code-experiment，Flash 两次、Pro 一次 | 三次完成；冻结 baseline/candidate 指标分别为 0.4252/0.5512、0.4386/0.5406、0.4392/0.5382；编码、验证、正式实验顺序正常。 |
| repair、direct、ask-start → ask-resume | 修复保留真实 totla traceback 并重跑；direct 无图完成；跨进程同 Session 记录并消费 accuracy，原题配对不回归。 |
| literature，Flash 两次实时检索 | arXiv read timeout / HTTP 429 后暂停，均无文献工件；不是正常文献完成，也不能据此断言框架回归。 |
| CLI 独立组合根冒烟 | run → show → answer 完成，采用三 Agent 128K 默认，产物位置正常。字段名本轮含选项描述，按实际 requested_fields 回答；这是操作体验观察，未顺带改接口。 |
| experiment-risk 标准库探针 | Experiment 产出 value=42 与非空风险报告，Scientific 实际读取并纳入局限，区分模块说明与独立测量。 |

关键原始证据：

- `reg-codeexp-flash1` 的 `79eeae0512b545b8a0113880ea1602e5`：control_state 中 edited_since_verification=false、required_next_action=finish，真实 patch 仍存在。状态名不再被定义为“任务没有修改”。
- `reg-repair` 的 `aa000f1b48e74f799daedee7d12714d8`：command_results 由原事件 10 投影，包含 python train.py、exit_code=1 和 totla 的真实 stderr。批次中间失败形态本轮未自然触发，仅由确定性测试覆盖。
- `experiment-risk-deepseek-v4-flash` 的 `34fb01add1244979becb7f6f21820a0e` 读取 module_report；`f26121a720d04c75908573fdb8d18b2f` 的原始请求包含报告正文，最终意见承认固定输入、非统计性能及覆盖写入等局限。提示到达与本次模型遵循分别有证据，不保证以后每次都遵循。

### 5.3 额度与装配的实际边界

三个 Agent 加载 128000 输入上限，Compiler 4096。E2E 的 OpenAICompatibleClient **有 context_budget hook**；没有 ModelProfile 时它返回模块上限。CLI 注入 Profile 后经同一 hook 取模块上限与模型可用输入的较小值，本轮仍为 128000。

E2E 的 request_max_tokens=None 只表示未显式发送该输出限制，不表示没有输入预算 hook；CLI 的 request_max_tokens=256000。两个组合根不能说成配置完全相同。

本轮真实输入没有自然用满 128K，未观察到上下文片段裁剪或整段省略。峰值 estimated_tokens=8496，对应实际 prompt_tokens=9127；字符估算不是精确 tokenizer，不承诺硬精确 token 边界。大材料共存、模型额度先收缩与过小必需额度失败由确定性容量测试覆盖，不能用小输入回归宣称已做 128K 全长压力测试，或把行为改善完全归因于提额。

### 5.4 文献回放补验：真实记录，真实模型，非实时检索

来源为此前成功检索的冻结 JSON：`/root/autodl-tmp/e2e-output-f2d4421-lqdEcp/workdirs/reg-literature-v3/artifacts/run_literature/artifact_sci_1060cd91f3520b53/literature_search.json`。测试驱动只替换组合根的检索 backend，返回其中 10 篇规范化记录；保留原 SENet 问题，真实 Flash 执行正常 Scientific → literature_search → Registry → read_artifact → finish。新目录独立，单次运行，未修改产品代码、目标或预算。

| 验证 | 复核证据 |
|---|---|
| 按论文条目冻结 | 新工件 artifact_sci_35f95b8a7b8b741c 为 text/markdown、literature_search.md，共 120 行、10 个 Paper 条目。metadata.papers 与历史来源完全一致；标题、来源、摘要仅新增排版换行，hash 与冻结字节匹配。 |
| 真正读取并进入输入 | `a5ae143c5b604cb0a20fdd22239cfbb8` 搜索；`63dba5d263894d16849216d827b3cda7` 读取该工件；后续 workspace_reads 含完整 SENet 摘要，truncated=false，Run 已观察集合含该工件。 |
| 结论与材料层级一致 | 最终 `fea85633e76948c386139a87fb8c9c39` 返回 supports，明确摘要级、未读全文和对照消融、主要为作者自报，未声称独立复现。 |
| 原机制恢复与计量 | 5 个逻辑调用、5 次请求尝试，Run.llm_calls_used=5；一次 Extra data、一次 schema 拒绝后恢复，非法动作没有执行。实际工具仅 search/read/finish，没有新摘要调用、阅读笔记或记忆机制。 |

本补验验证的是**已有真实检索结果的呈现与消费**，没有验证实时搜索质量或 arXiv 恢复。来源和注入方式在 ops/replay_literature_ctx128k.py 中留档，不把回放当真实联网成功。

### 5.5 勘误、计量和保留事项

原 11 个 trace 目录：155 个唯一 call_id、155 次请求尝试，与各 Run.llm_calls_used 之和一致。解析错误为 **16**，不是原报告的 15：Extra data 9、Expecting value 5、缺分隔符 1、Invalid control character 1（漏计调用 `13af4e3131e6465a93a70664e9b0988d`）。schema 校验补充行 8，不另算模型调用。

回放另增加 5 次调用、1 次解析错误、1 条 schema 补充行。12 目录合计 **160 次逻辑调用 / 160 次请求尝试、17 次解析错误、9 条 schema 补充记录**；没有 HTTP retry。JSON 仍是已有机制下的已知限制，不因本轮完成就宣称上游已可靠，也不追加新修复。

experiment-risk 最终意见中“日志未保留”是模型措辞不准确：系统保有 Session 的命令结果及命令 stdout/stderr 文件，只是未作为独立工件交给 Scientific。应区分“消费者未得到独立日志证据”和“系统没有保存日志”；不以模型意见代替存储事实，本轮只记录，不新增日志交付机制。

原 11 目录与回放 trace 权限均为目录 0700、文件 0600；原矩阵验收方定值凭据扫描零命中，私有原始消息不复制进仓库文档。旧报告、失败 Run、schema 旧记录、服务器 checkout、环境、依赖缓存和数据集全部保留。代码理解与三个 Agent 原题配对的前阶段结果另见[语义交接记录](SEMANTIC_HANDOFFS_ACCEPTANCE.md#verified-closeout)，不混用不同提交的通过数。
