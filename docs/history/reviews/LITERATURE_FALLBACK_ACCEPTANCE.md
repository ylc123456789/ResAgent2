# 文献请求节奏与 OpenAlex 备用：服务器补验

日期：2026-09-15。分支：`fix/literature-fallback`。产品提交：`7f3411d`；最终同步分支 HEAD（后续文档提交可能更新 SHA）。旧正式产品基线为 `47f6231`，其上的 L3 文档提交不改变产品行为。

状态：**本地验证完成，服务器补验和 L3 正式 Run 尚未执行。** 本文不是验收通过报告。

## 1. 改了什么、没改什么

- arXiv 显式应用 User-Agent；同进程所有实例共用请求串行/间隔，至少 3 秒。429 不在同次搜索里短重试，冷却至少 60 秒；Retry-After 秒数/HTTP 日期给出更长等待时遵守它。
- 暂时网络故障和无 Retry-After 的 5xx/408 最多 3 次 HTTP 尝试，退避 3/6 秒；带 Retry-After 的 5xx/408 直接冷却。冷却期调用快速返回不可用。
- CLI/E2E 都显式装配 arXiv 主源 + OpenAlex 备用；只有服务不可用触发备用。有效空结果、400/401/403 等坏请求/授权错误、损坏 XML/JSON 不切换。两源都失败不登记伪成功工件。
- OpenAlex 仍返回 LiteraturePaper，真实 OpenAlex Work ID/链接随 Markdown 工件交付；可用摘要最多 2000 字符，缺失就留空，不冒充全文。切换原因写应用日志，不另加业务字段。
- 可选 `OPENALEX_API_KEY` 从组合根读环境，经 Authorization header 发送；不写 URL/工件/模型上下文。未配置时匿名访问，额度/授权以服务端实际返回为准。既有子进程清理规则会剥离含 API_KEY 的变量。
- 未改 Agent/prompt、Runtime、状态机、schema、LLM 额度或 JSON 恢复；未加 SDK、源注册框架、融合排序、全文下载或新缓存。L3 **案例预算**改为 12/2/200/14400，产品默认预算不变。

边界：节奏/冷却是进程内的，不跨机器或进程持久化；不要并发运行多个预检/Agent 去撞同一服务额度。它不能保证恢复当前出口的 arXiv 访问。429 响应能证明请求被拒，但仅凭 CDN 头不能证明具体限流键、持续封禁时长或 User-Agent 是根因。

## 2. 本地已完成的验证

- `python -m pytest tests apps/cli/tests -q`：**903 passed, 1 skipped**；唯一 skip 仍是原有 opt-in arXiv 网络测试。
- 文献三文件 + 双组合根测试：**53 passed, 1 skipped**。
- `python -m e2e.mock_e2e`：completed；`git diff --check` 干净。
- 虚拟时钟覆盖：跨实例间隔、429 单次请求、冷却跳过网络/到期恢复、Retry-After 秒数/日期、503 长等待、超时有限重试、400/401/403 不重试。
- 规范化/切换覆盖：查询日期范围、摘要重建/缺失、来源/去重、坏响应不冒充空结果、有效空结果不切换、两源失败不登记工件、API key 只进 header、CLI/E2E 接同一策略。
- 本机 WSL 对 OpenAlex 做一次真实匿名查询（无 LLM）：检索 SENet 返回 `openalex:W2752782242`、`openalex:W2963420686`，均带真实标题与摘要。**仅证明本机这一次可用，不证明服务器可用或服务永不限流。**

## 3. 服务器先做小补验

### 3.1 同步与基线

1. 保留旧预检根 `/root/autodl-tmp/e2e-l3-47f6231-K7mQ2x/`，包括 PRE-CHECK、研究仓库、准备 commit、smoke、日志；不要重做或覆盖中性准备。
2. 用 Git/bundle 同步最终分支 HEAD，建干净产品 worktree；记录实际 SHA、diff-check、8 个 editable import 前后路径。禁止 scp 零散产品源码。
3. 在一个新验收根收齐 `MANIFEST.md / protocol / logs / traces / ops`；记录模型和资源环境变量，但不打印密钥值。若用 OpenAlex key，只有“已配置/未配置”可进清单。
4. 重跑上面的全量确定性、文献焦点与 mock E2E。不要求重跑 GPU/Pro/完整九场景矩阵。

### 3.2 一次真实后端预检（不用 LLM）

使用与组合根相同的：

```python
backend = FallbackLiteratureBackend(
    ArxivLiteratureBackend(),
    OpenAlexLiteratureBackend(api_key=os.environ.get("OPENALEX_API_KEY")),
)
papers = backend.search("Squeeze-and-Excitation Networks", max_results=3)
```

在独立驱动里记录时间、返回条目/来源及 stderr 切换日志。不要打印 Request headers、环境全集或 key。这里查询固定论文只检查服务，不将这些记录作为 L3 研究任务的输入或候选答案。

- arXiv 429 → OpenAlex 真实记录：判备用可用，不宣称 arXiv 恢复。
- arXiv 本次成功：判主源可用；如要补真实备用证据，单独一次驱动仅让 arXiv 的 `_request` 抛 HTTPError(429)，其余 HTTP 策略与 OpenAlex 仍真实运行。明确标记注入，不伪造/替换论文记录，不改产品源码。
- 若两源都不可用，或 OpenAlex 要求有效授权/额度：保存原错误，停在预检交用户决定；不反复运行到绿、不轮换出口绕过限制、不擅自申请付费服务。
- 不因真实空结果直接宣称网络失败或换源成功。无需并发请求/压力测试，节奏与分支机制已有确定性覆盖。

### 3.3 一次真实 CLI 文献闭环（Flash，无训练）

在小补验的新 data-root/trace-root，用唯一 run_id，调用真实 CLI：

```bash
resagent2 run --run-id run_literature_fallback_check \
  --data-root /root/autodl-tmp/replace-with-check-root/data \
  --goal '请检索并阅读 Squeeze-and-Excitation Networks 的资料，说明其核心机制，引用实际读取的来源，并区分检索摘要与论文全文。' \
  --max-tasks 2 --max-attempts 1 --max-llm-calls 20 --timeout-seconds 900
```

路径是占位符，替换后运行；设置既有 full trace 环境变量，模型仍为 Flash。这里不给工作区、不安装训练依赖；若系统请求额外任务/权限，按实际情况报告而不替它完成。

核查完整 request/response 与 Session，不能只看退出码：

1. 工具实际检索，冻结 `literature_search.md`；条目的真实 paper_id/source_url、作者、日期、摘要对应响应，OpenAlex 不伪装成 arXiv。
2. Scientific 实际 `read_artifact`，摘要正文进入 `workspace_reads`，最终意见引用该工件、说明摘要级证据；不能因 ID 出现在预览就认定读过正文。
3. 来源切换原因从应用日志核对；LLM trace 不承诺保存论文 HTTP 的全部头/正文。报告可记录状态/次数/耗时，但不得暴露 Authorization。
4. `llm_calls_used` 对得上按 call_id 去重的 HTTP **LLM** attempts；论文 API 请求不算 LLM 调用。JSON/schema 恢复如实单列，沿用已有机制，不借本轮修改。
5. 两源失败时，报告真实错误和 Agent 是否诚实 ask_user/暂停，不能把“暂停正确”写成“检索成功”。不将模型遵循 prompt 说成确定性保证。
6. trace 权限 0700/0600；密钥定值扫描但不打印值。正常退出、坏输出、外部失败都保留原现场，不用重跑覆盖。

## 4. 然后才启动 L3

小补验确认实际文献链路可用后，更新原 PRE-CHECK 的**产品身份、外部服务与成本确认**。复核旧研究仓库/数据/准备 SHA 未变即可，不重新写准备补丁。若最初 workspace 尚无正式 Run 修改，可以继续使用；若已变动，先报告，不清空或覆盖。

执行最新版 [L3 规程](../../guides/L3_RESEARCH_TEST.md)：

- Flash 单配置；1M 窗口、256K 输出上限、Agent 128K 均不变。
- 冻结 `--max-tasks 12 --max-attempts 2 --max-llm-calls 200 --timeout-seconds 14400`。
- 4 小时是 Run 执行预算，显式用户等待单独计量；依赖安装计入执行耗时。不保证精确抢占，不是服务器自动关机或 GPU 计费硬上限，200 次也不是货币硬上限。
- 用原短 goal、两条操作边界、新 Run ID/data-root/trace-root；不把测试协议、预检论文或候选答案喂给系统。
- **成本重新确认后**正式跑一次。ask_user 只按 L3 §6 有限代答；研究决策不代做，扩预算/权限/费用必须交用户。两源再次故障则如实报告，不静默取消查资料要求。
- 不重跑 Pro/GPU 矩阵，不混入 JSON 或额外功能修复。所有失败保留，不重跑到绿；不合并 main、不 push、不清理旧现场。

最终报告把两件事分开：A. 新文献能力补验；B. L3 研究交付及评分。请求节奏修好、备用可用，不代表 L3 一定能自主完成。
