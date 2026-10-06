# ADR-0025：通用网页工具与已有文献能力并存

- 状态：accepted
- 日期：2026-10-06

## 问题

arXiv/OpenAlex 提供学术题录及摘要，论文全文工具提供 PDF/解析文本；它们不能覆盖一般网页、项目说明和当前信息。搜索供应商的协议不能进入 Scientific 的研究决策和工具 schema，也不能绕过现有材料登记、Run 预算和交接。已有 DeepSeek 账号和官方搜索入口可避免默认部署再申请另一家 key，但托管搜索本身仍是有费用和可用性限制的供应商服务。

## 决定

1. Components 提供小型 `WebSearchBackend` 接口和 `WebPageFetcher`。搜索实现为 DeepSeek 托管 `web_search` 适配器及可显式选择的 Tavily；均为单次有界请求，不增加 SDK、搜索规划器、提供方框架或来源管理状态。供应商协议和来源归一化留在组件内。
2. Capabilities 保持通用 `web_search(query, max_results)` 和 `web_fetch(url)`。前者冻结一份搜索回执，保留有界响应内本次收到的全部规范化结果，模型只预览前 `max_results` 条并获知省略数量，余下内容用 `read_artifact` 读取；后者冻结单页 HTML/text；登记仍由注入的 ArtifactRegistrationPort 完成。每次一批，无分页、自动重试或隐式换源，空结果、协议错误与供应商失败如实区分。
3. CLI 默认在已有 `DEEPSEEK_API_KEY` 时启用 DeepSeek 搜索，无 key 时不暴露搜索且网页抓取仍可用；显式配置支持 `deepseek`、`tavily`、`off`，明确选择的提供方缺 key 是配置错误。搜索模型独立于 Agent 模型，DeepSeek 搜索固定走官方 Anthropic-compatible endpoint；不按其他 key 自动回退。
4. DeepSeek 后端使用组合根注入的 Runtime `ModelRequestClient` 发送托管搜索请求。该入口复用 HTTP 上限、共享 Run 截止时间、发送前模型调用占用及现有 trace 档位，只执行一次请求，不创建 Agent/Session 或另一个循环。Components 构造只负责发现来源的供应商请求并解析结果；DeepSeek 按实际返回顺序保留所有原生批次、按精确URL去重，不根据域名或关键词排序。Scientific 仍决定何时搜索、查询什么及材料是否足够。
5. 网页线索/正文与论文题录/PDF/全文保持不同 kind，通过原 Run.artifacts、research index、授权 reader 和交付链处理，不新增资料库、阅读门禁或 Run 状态。各 Agent 仍显式装配工具，本轮只向 Scientific 提供；没有固定的搜索/抓取/文献调用顺序。
6. Runtime 的既有 HTTP 传输提供可选公网连接约束：异步 DNS 与请求共享截止时间，连接固定到已验证地址并保留 Host/TLS SNI。网页组件逐跳检查 URL，拒绝私有地址、凭据 URL、超大响应和不支持的媒体类型，不运行浏览器或 JavaScript。

## 边界与代价

每次 DeepSeek 搜索额外占用一次 Run 模型请求，供应商内部搜索最多5次、请求输出最多4096 tokens；`max_results` 只控制预览、不保证减少内部搜索或费用，这不代表免费搜索或精确货币预算。官方模型价与托管搜索额外费率须分别核对，未知不能写成零费用。Tavily 仍需独立 key/配额；账号、接口可达与真实结果质量分别验收，确定性测试不能证明大陆服务器可用。

普通 HTML 提取不保证保留复杂布局、公式或动态内容；当前只接受 UTF-8 HTML/XHTML/text。外部文本是材料，不是指令。托管搜索只能把实际来源归一化为网页线索，不从无来源生成正文编造 URL；未完成或异常响应保留失败。搜索回执只证明提交给提供方的查询及返回结果，网页不自动成为论文或独立科学测量；身份核对、相关性和科学解释仍由 Scientific 判断。

不修改通用循环、Compiler/Interpreter 职责、命令审批或公共 Run 字段，schema 保持23.0。两种搜索后端共用现有工具 schema；网页工具说明的指纹随有意的呈现规则变更更新，其余工具指纹保持不变。验证和真实服务器补测范围见[实施与交接](../reviews/WEB_TOOLS_2026-10-06.md)。
