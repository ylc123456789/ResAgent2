# 上下文语义、128K预算与文献呈现验收

状态：2026-09-13本地 **859 passed, 1 skipped**，mock E2E completed，diff-check通过；真实模型验收待执行。同步最终提交并记录HEAD，不把旧f2d4421服务器结果算成本轮通过。

分支 `fix/context-budget-presentation`，产品提交 `3efce21`，基于既有 `fix/semantic-handoffs` 的f2d4421。同步时包含随后文档提交；本轮未合并或推送main。

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
