# 模型输出默认配置：按模型容量留余量，不逐级试探

## 决定（2026-09-07）

CLI 当前默认面向 DeepSeek V4，采用以下部署配置：

| 配置 | 旧默认 | 新默认 | 含义 |
|---|---:|---:|---|
| RESAGENT2_CONTEXT_WINDOW | 65536 | 1000000 | 模型总容量声明，不是实际填入长度 |
| RESAGENT2_RESERVED_OUTPUT_TOKENS | 4096 | 256000 | 同时用作 max_tokens 请求上限及输入计算中的输出预留 |
| RESAGENT2_LLM_TIMEOUT_SECONDS | 未暴露，客户端默认 120 | 600 | 沿用 urlopen 的网络等待参数，允许部署覆盖 |
| 各 Agent 输入上限 | 8192 | 8192 | 不变 |
| Compiler 输入上限 | 4096 | 4096 | 不变，含 draft/review |
| 安全余量 | 1024 | 1024 | 不变 |

不改 Run/Task 预算、重试次数、thinking 设置、prompt、工具或 schema 5.0。生产改动仅在 `apps/cli/src/resagent2_cli/composition.py`：两个既有默认值和一个网络参数透传。所有 CLI 客户端共用 `_client()`，不逐 Agent 打补丁，不引入模型注册表、远程能力发现、自动升档或 JSON 修补。环境变量仍优先；换用其他模型需配置符合其真实容量与输出限制的值，runtime 不按模型名分支。独立 E2E/程序化客户端的配置不隐式改变。

## 依据与边界

查阅日期 2026-09-07；下列是当时官方文档/源码，部署升级后应重新核验，不把数字当永久标准：

- [DeepSeek 模型规格](https://api-docs.deepseek.com/quick_start/pricing/)：V4 总容量 1M、最大输出 384K。采用 1M 容量声明，模块自己的小输入上限仍保留。
- [DeepSeek Harness 官方适配器](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/README.md)：默认输出额度 256000、容量 1000000，并允许部署/模型/请求覆盖。本项目借鉴明确、宽裕、可配置的额度策略，不搬入其插件或流式架构。
- [DeepSeek 官方 Pi 配置示例](https://github.com/deepseek-ai/awesome-deepseek-agent/blob/main/docs/pi_mono.md)：V4 模型条目声明 contextWindow=1000000、maxTokens=384000。此处是模型声明，不能冒充 Pi 每次请求的实际默认值；也不是小样本找到“刚够用”的预算。
- [Anthropic Python SDK 超时说明](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python#timeouts)：默认十分钟，并提示长非流式请求的网络风险。本项目取 600 秒作为可配置的网络等待余量；urllib 与其 SDK/DeepSeek Harness 流式 idle timeout 的语义并不相同，不宣称完整等价。

真实重放中四次 length 的 completion_tokens=reasoning_tokens=4096，最终 content 为空；16384 对照成功，但实际只用 3421 tokens。这证实小额度会截断，**不证明 16384 是必要或足够的阈值**，更不能恢复旧版未记录的历史 finish_reason。按用户要求，不继续 8K→16K→32K 试探；直接选择已有官方实现使用的宽裕额度，而非本项目发明一个经验阈值。

256000 是生成上限，不是要求模型用满；[官方计费](https://api-docs.deepseek.com/quick_start/pricing/)依据实际输入/输出 tokens。增大上限仍会允许更长思考和更高消费，不承诺成本/延迟不变，也不能保证任意任务永不截断。系统目前没有全 Run 的货币或总输出 token 硬预算，本次不新增这类机制。

600 秒传给 `urlopen(timeout=...)`，不是整次逻辑调用/Run 的严格 wall-clock deadline；重试和持续收到数据都会影响总耗时。Run/Task 的时限检查仍位于既有同步执行边界，本次没有实现可抢占取消。有限等待、有限尝试、真实 trace 保留原样；网络故障、长思考和输出截断必须按证据分开报告。

## 本地验证

新增 14 个配置测试覆盖：五类 action/draft/review 的输入上限不扩大；Flash/Pro wire 都发送 256000、timeout=600；自定义模型/小容量/输出/timeout 可覆盖；非法组合在网络前拒绝；实际 CLI adapter + LLMWorkflowCompiler 的 draft/review 全链在新默认下计量、trace、两次消费一致。全量 775 passed, 1 skipped，mock E2E completed。

确定性测试不调用真实服务。服务器补验收只检查新默认下的完整编译及 CLI 问答，不跑训练、不创建受管环境。上一轮诊断结果见 [LLM 诊断验收单](LLM_DIAGNOSTICS_ACCEPTANCE.md)，其 4096/16384 是历史对照配置，不是现在的默认值。
