# ADR-0027：只维护 DeepSeek 托管网页搜索

状态：已实施，2026-10-10。基线：fix/native-agent-receipts@287453b。

## 问题与目标

项目当前使用 DeepSeek 托管搜索。Tavily 是可配置调用的生产实现，并非不可达的死代码，但保留另一供应商需要持续维护认证、配置、装配、响应处理、测试和使用说明。用户明确选择只维护 DeepSeek，以减少当前没有使用需求的实现。

## 决定

1. 删除 Tavily 搜索适配器、公开导出、CLI 配置与装配分支、专用测试及当前支持说明，不保留兼容层或自动回退。
2. `RESAGENT2_WEB_SEARCH_PROVIDER` 只支持 `deepseek` / `off`。默认在有 DeepSeek key 时启用搜索；显式选择 `deepseek` 而缺 key 仍是配置错误。旧 `tavily` 值与其他非法值一样明确报配置错误，不改用另一个出口。
3. 保留小型 `WebSearchBackend` 接口及通用结果、错误契约，支持现有能力层依赖倒置和确定性测试注入。保留 Runtime HTTP 传输和独立 `web_fetch`。
4. DeepSeek 的单次请求、共享 Run 预算、全部来源保留、预览与部分结果语义继续沿用原契约。网页抓取只占 Run 时间，搜索关闭时仍可使用。

本决定取代 [ADR-0025](0025-general-web-capability.md) 中可选 Tavily 提供方的决定。旧 ADR 和验收记录保留当时事实，不改写历史配置或测试数字。

## 影响与验证

减少一个供应商支持，不表示搜索质量提升；部署若需要另一独立搜索服务，须重新提出具体需求和依据。没有 Tavily 专用 SDK 依赖可移除，HTTP 依赖仍被其他组件使用。

验证关注配置拒绝、默认 / 显式 DeepSeek / off 装配、DeepSeek 搜索与独立抓取回归、公共导出和当前文档残留检查，以及全量离线回归与 mock E2E。本次不改变工件、权限、ask_user、schema 或真实 E2E 的装配方式，不以旧服务器或 L3 结果代替本次验证。

本轮验证与独立复核命令见 [实现与验证](../reviews/TAVILY_REMOVAL_2026-10-10.md)。
