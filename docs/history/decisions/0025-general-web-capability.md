# ADR-0025：通用网页工具与已有文献能力并存

- 状态：accepted
- 日期：2026-10-06

## 问题

arXiv/OpenAlex 提供学术题录及摘要，论文全文工具提供 PDF/解析文本；它们不能覆盖一般网页、项目说明和当前信息。直接依赖某个模型供应商的内置搜索会把能力绑定到模型 API，也不能统一现有材料登记与交接。

## 决定

1. Components 提供小型 `WebSearchBackend` 接口和 `WebPageFetcher`。首个搜索适配为 Tavily，使用已有 HTTP 客户端直接发送有界请求，不增加 SDK、搜索规划器或来源管理框架。
2. Capabilities 提供 `web_search(query, max_results)` 和 `web_fetch(url)`。前者冻结一份搜索回执，后者冻结单页 HTML/text；登记仍由注入的 ArtifactRegistrationPort 完成。搜索每次一批，无分页和自动重试，空结果与失败如实保留。
3. CLI 按部署配置装配搜索 provider，网页抓取可独立使用；各 Agent 仍显式装配工具，本轮只向 Scientific 提供。是否搜索、抓取、查论文、委托工作或停止由 Scientific 根据用户目标决定，没有固定调用顺序。
4. 网页线索/正文与论文题录/PDF/全文保持不同 kind，通过原 Run.artifacts、research index、授权 reader 和交付链处理，不新增资料库、阅读门禁或 Run 状态。
5. Runtime 的既有 HTTP 传输增加可选公网连接约束：异步 DNS 与请求共享截止时间，连接固定到已验证地址并保留 Host/TLS SNI。网页组件逐跳检查 URL，拒绝私有地址、凭据 URL、超大响应和不支持的媒体类型，不运行浏览器或 JavaScript。

## 边界与代价

网页搜索需要独立 provider 密钥和配额，不能承诺总是可用或达到商业平台的完整质量。普通 HTML 提取不保证保留复杂布局、公式或动态内容；当前只接受 UTF-8 HTML/XHTML/text。外部文本是材料，不是指令。搜索回执只证明实际查询/结果，网页不自动成为论文或独立科学测量；身份核对、相关性和科学解释仍由 Scientific 判断。

不修改通用循环、Compiler/Interpreter 职责、命令审批或公共 Run 字段，schema 保持23.0。工具面增加两个显式 schema，并纳入指纹基线；已有工具指纹保持不变。验证和真实服务器补测范围见[实施与交接](../reviews/WEB_TOOLS_2026-10-06.md)。
