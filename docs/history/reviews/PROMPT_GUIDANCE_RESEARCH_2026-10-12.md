# 通用提示词改进：官方指南和开源实例

日期：2026-10-12。目的：为权限反馈方案的第二、第三、第四项提供写法依据，保持 ResAgent2 的 LLM 判断、统一接口和四部分上下文设计。此记录是调研及候选方案，不表示已改产品或已证明模型效果改善。

## 1. 调研方式与结论边界

实际读取官方页面正文，以及已克隆的 Pi / OpenHands 固定版本源码。没有用搜索摘要替代正文，也没有声称读到了闭源 Claude Code 的内部系统提示词。

官方网页 HTML、提取正文和抓取脚本保存在 `E:\dshWorkSpace\survey-read\prompt-guidance-20261012\`。Google 搜索只返回重定向占位，Bing 请求连接重置；随后通过官方页面已知地址取得完整正文，本文依据是实际页面，不引用这些无效搜索结果。urllib 抓取连接重置后，curl 成功取得 OpenAI 官方页面。

OpenAI / Anthropic 的模型专用建议和开源提示词提供写法参考，不是 DeepSeek 上的效果证据。本轮不迁移模型、改变供应商、不采用专用推理参数或建立提示词优化平台。

## 2. OpenAI 官方文档

实际读取 [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering) 和 [Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)。前者由 `platform.openai.com/docs/guides/prompt-engineering` 取得，页面 canonical 指向上述开发者文档。

相关原文：

> Markdown headers and lists can be helpful to mark distinct sections of a prompt, and to communicate hierarchy to the model.

> When providing examples, try to show a diverse range of possible inputs with the desired outputs.

> Generative AI is variable. Models sometimes produce different output from the same input, which makes traditional software testing methods insufficient for AI architectures.

> Adopt eval-driven development: Evaluate early and often. Write scoped tests at every stage.

应用到本项目：保留已有角色、决策、工作方式和交接章节；明确规则、当前任务、工具观察及材料之间的边界。先写清判定标准，再检查实际生成指令和行为。示例仅在需要时加入，并检查跨任务泛化；不按一次成功或 prompt 中出现关键词宣称有效。

HTML SHA256：prompt engineering 为 `d83c1b61a51aff6eb20eee48fe52559ebde5e589f92ec4e786f8b536ec32a8db`；evaluation best practices 为 `6fbea1161cc316bd5d2423c6d0a0ed83f6320f3fdbe612fc86a3e4b00d6f38f3`。

## 3. Anthropic 官方教学

实际读取 [Prompt engineering overview](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview)、[Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)及工程文章 [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)。原 clear / XML 教学地址已重定向到合并的 best-practices 页面。

overview 要求先有明确成功标准、实测方法和待优化提示词；同时指出不是所有失败都适合由 prompt 解决。best-practices 的通用部分说明：

> Be specific about the desired output format and constraints.

> Provide instructions as sequential steps using numbered lists or bullet points when the order or completeness of steps matters.

> Providing context or motivation behind your instructions, such as explaining to Claude why such behavior is important, can help Claude better understand your goals and deliver more targeted responses.

示例要求相关、多样，避免模型学到无意的模式。应用到本项目：Scientific 明确动作、先后和条件；Compiler 保留它们的适用范围；解释重要边界的目的，但不用“必须认真”等空泛强调代替可操作说明。

不照搬固定示例数量、XML 标签或针对某个 Claude 版本的过度探索调参。当前 Markdown 分节和原生结构化参数已经提供边界；换一种标签形式本身不是本轮目标。

上述工程文章直接讨论本轮的通用性问题：反对脆弱的 prompt if/else，也反对模糊指导；应做到 “specific enough ... yet flexible enough”。它要求 “minimal set of information that fully outlines your expected behavior”，并明确 minimal 不等于单纯短；不要堆 “a laundry list of edge cases”，而应使用多样的典型例子。这支持改清已有规则、保留自主判断，不添加一串针对已发生事件的禁令。

另实际读取 [Define success criteria and build evaluations](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)，用于先写清具体、可测、相关的成功标准。评价也应检查任务完成能力，避免把限制遵守优化成一律不行动。官方 GitHub 交互教程下载失败，未读、不引用其代码。

## 4. Pi：短规则、工具对应与去重

固定源码：`e4c75a73222ae2c72abb5f5314fa35ee8effc508`。

- [system-prompt.ts](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/system-prompt.ts#L81)：只装配当前工具贡献的指引，加入前对完全相同的规则去重；角色、工具和项目指令有明确位置。
- [read.ts](https://github.com/earendil-works/pi/blob/e4c75a73222ae2c72abb5f5314fa35ee8effc508/packages/coding-agent/src/core/tools/read.ts#L20) 的指引为 “Use read to examine files instead of cat or sed.”；是一条具体的工具行为说明。

借鉴：先清理和改写现有职责附近的规则，不在每个 Agent、工具说明和完成段落重复几种措辞。不为此移植 Pi 的提示词装配器或引入新的去重框架。源码展示写法，未提供本项目的可靠性对照数据。

## 5. OpenHands：职责明确，执行前看已有支持方式

固定源码：`97dbce5c37865dd7a3020d2b9662f4a76d11fa71`。

实际读取 [static.py](https://github.com/OpenHands/software-agent-sdk/blob/97dbce5c37865dd7a3020d2b9662f4a76d11fa71/openhands-sdk/openhands/sdk/context/prompts/sections/static.py)。通用段落包括：

> Make focused, minimal changes to address the problem

> First, look around in the repository for existing dependency files...

角色段落区分用户询问原因与要求修复；问题处理段落要求先看相关文件和上下文。模型专用段落的 “don't make extra or fewer actions if not asked” 仅在 Anthropic 分支启用，不描述为所有模型共用规则。

借鉴：任务细化忠实于上游，操作前确认已有入口和实际行为，按任务需要实施。故障处理段落中的固定“考虑 5–7 种原因”、额外用户确认等，不照搬到本项目。

另核对父 Agent 的 `tasks: dict[str,str]` 和 delegate 实现：父 Agent 已明确提供各任务，代码逐项发送。它说明有明确工作项接口时可以代码分发，不能证明 ResAgent2 当前自然语言 `objective` 可以按编号机械拆 Task。

## 6. 本轮采用与验证

采用的原则：角色和边界清楚；动作、顺序和条件明确；保留关键原意；规则各归所属职责；基于真实工具观察和用户反馈判断；交接区分事实与建议；改动最小并通过实际行为验证。

不采用：案例专用禁令、固定工作阶段、强制先 smoke、固定故障分析次数、指定优化器或阈值、通用停止字段、提示词优化平台、为了照搬教程而重排上下文。

实施时优先修改已有句子。若通用措辞仍不能消除某类歧义，再评估一个简短、跨业务的示例，并实测；不默认把本次校准案例写进长期提示词。

确定性回归证明结构、反馈通道和装配正确；真实模型案例检验忠实细化、执行边界、反馈纠正和交接质量。原始失败保留，不仅检查提示词关键词，不据单次运行归因。完整方案见 [权限反馈与任务指令方案](PERMISSION_FEEDBACK_AND_TASK_INSTRUCTIONS_PLAN_2026-10-12.md)。
