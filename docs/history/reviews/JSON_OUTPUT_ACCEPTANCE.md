# JSON 格式反馈验收

目标：验证坏 JSON 不执行、错误原因确实回到模型、在同 Session/Attempt 内有限恢复，预算与 trace 不失真。此轮不迁移原生 tools、不提高额度、不改业务 prompt。

本地基线（2026-09-11）：独立 cwd、PYTHONPATH 指向仓库，**825 passed, 1 skipped**；mock_e2e completed，git diff --check 干净。相对 main 的 805/1 新增 20 个确定性用例。服务器真实与注入验收尚未执行。

## 1. 同步与确定性检查

使用待验收分支 `fix/json-output-feedback` 的实际提交，新建干净 checkout/worktree，记录 HEAD、8 个包的 editable 指针与前值。不要 scp 零散源码，不合并 main、不清理旧数据。所有新增产物放一个独立验收根目录。

在 ResAgent2 环境、隔离 cwd 下运行（令 repo_dir 指向本次 checkout）：

```bash
PYTHONPATH="$repo_dir" python -m pytest "$repo_dir/tests" "$repo_dir/apps/cli/tests" -q
PYTHONPATH="$repo_dir" python -m e2e.mock_e2e
git -C "$repo_dir" diff --check
```

重点测试：tests/runtime/test_llm_recovery.py、test_llm.py、test_llm_attempt_trace.py；tests/orchestrator/test_compiler.py。持续坏 JSON 的 5 次上限、预算/超时、不执行非法前缀等由确定性测试验证，不用真实模型反复烧预算。

## 2. 真实回归

fresh workdir + full trace，沿用现有模型、目标、预算、数据集和镜像配置：

- code-experiment：Flash 两次、Pro 一次；Coding 改+验证，Experiment 真训练，冻结指标与报告对应。
- repair：Flash 一次；保留最初 stderr，正确修复后重跑，不把重跑成功抹成未失败。
- literature、direct：Flash 各一次；分别核对实际观察/引用和无图直接完成。
- ask-start → ask-resume：两进程、同 workdir，按实际 requested_fields 填答案；同 Session、答案消费正确。
- CLI 独立组合根：问答 run/show/answer 冒烟即可；不因此新装大型训练环境。

失败照实保留；重跑必须新目录并列报告。自然运行未出现坏 JSON，只能证明未复现，不能单靠全绿声称纠错路径生效。

## 3. 定向注入一次坏正文，后续仍用真实 LLM

验收驱动可在 ops/ 中包装测试进程的 urlopen，不能改产品文件、prompt、场景目标或预算。选定 agent 的一次真实成功 HTTP 响应，将交给客户端的 message.content 替换为纯空白或“合法动作 JSON 后跟额外文字”；原响应保存在权限受控的独立文件，并明确标记 injected，不能把注入内容当成 provider 自发失败的证据。保留其它返回元数据，后续调用不再改写。不得输出凭据。

分别覆盖 Coding、Experiment、Scientific（使用相应已有场景）；Compiler draft 可独立运行原编译入口，注入一次坏正文后观察现有重编，不能执行草图任务。每项只注入一次，禁止一直替换到模型过关。

核对原始 prompt、trace、Session、Run：

1. 坏正文对应 action_valid=false、主记录 validation_error 非空，原始正文仅在 full trace；该响应不产生 action/工具执行事件。
2. Agent 下一次调用 included_sections 中恰有一个 runtime_feedback，正文有解析原因及纠正要求；同 Session/Task Attempt，之前已完成操作不被重放。Compiler 则在 compiler_request 的既有 rejection feedback 中出现解析原因，无 AgentLoop/Session。
3. 下一次请求为新 call_id，而非同一主记录内原样 HTTP retry。若发生真实网络重试，仍按每次 attempts 正确记录。
4. 原始模型是否按反馈产出合法动作、恢复后真实结果是否正确，分别报告；没有恢复也保留失败。不要把“反馈正确到达”等同于“模型一定遵循”。
5. Run.llm_calls_used 与去重 call_id 后 attempts 长度求和一致，schema 校验补充行不算新调用。注入是在一次真实 HTTP 返回后替换正文；若使用完全模拟的响应，必须单列模拟次数，不能冒称实际 provider 消耗。

## 4. 汇总与安全

解析错误、schema 错误、HTTP retry、Task Attempt retry 分开统计。检查 trace 0700/0600、秘密扫描，原响应备份同样限制访问。输出 MANIFEST、逐项结果、真实/注入分类、证据路径与 call_id；记录环境安装指针变化，不合并、不删除现场。
