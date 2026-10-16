# P3 网页转换与分段读取：验收收尾

状态：服务器验收通过，2026-10-10。分支 `fix/native-agent-receipts` 保留，尚未合并。

## 验收对象与结论

冻结提交为 **`9e31735904232d3642a1b88d5a68202c98595f93`**，schema **24.0**；前阶段运行时修复为 `74a59fc`，此前服务器原生协议验收对象为 `2ea65d3`。本次收尾只更新文档，不改产品代码，也不把后续文档提交称为重新测试过的版本。

本记录依据用户交回的服务器报告与复算摘要整理，未重新登录服务器、重跑模型或独立复算服务器原件。原报告与全部证据留在服务器；本地结果与服务器结果分开记录。

验收覆盖[测试交接](P3_WEB_READING_TEST_PLAN_2026-10-10.md)中的范围。HTML→Markdown 转换、链接与代码保真、读取范围事实、上下文投影和搜索覆盖提示符合契约；真实 Scientific 能从冻结网页分段读取并准确引用原文。本轮没有暴露需要再修改产品的缺陷，可以进入已计划的最终 L3。

## 服务器证据与回归

产品仓库为 `/root/autodl-tmp/projects/ResAgent2`。证据根：

```text
/root/autodl-tmp/resagent2/runs/p3-web-reading-20261010/
```

原报告为该目录下的 `protocol/P3_WEB_READING_ACCEPTANCE_2026-10-10.md`。MANIFEST 记录 **34 个公共文件 + 2 个私有文件**，均有 SHA256；full trace 与 Session 按既有私有边界留存。本收尾不另复制私有记录，也不声称已独立复算这些清单。

测试方通过 `pip install -e packages/components` 安装已声明的 `beautifulsoup4 4.15.0`、`markdownify 1.2.3`。源码 import、schema 与依赖核对为 `SOURCE_SCHEMA_AND_DEPENDENCIES_OK`，产品工作区干净、无 stash。

| 验证 | 服务器结果 |
|---|---|
| 完整回归 | **2207 passed / 1 skipped**，85.19s，与本地一致 |
| mock E2E | `run_golden completed`，13 工件 |
| pip check | clean，服务器独立核验 |
| git diff --check | clean |
| 交接列出的七文件专项 | **247 passed** |
| 四类网页原问题的生产 Fetcher 探针 | **12/12 PASS** |
| 读取范围与搜索覆盖核对 | 报告列出的范围事实与覆盖提示通过，探针 **6/6 PASS** |
| 真实材料任务独立复算 | **23/23 PASS** |

专项、探针与完整回归有重叠，不相加计算新增覆盖。未闭合 script/style/noscript/template/title 五种均返回 `parse_failed`；链接包代码块保留格式；SVG/MathML title 不污染页面标题；嵌套 pre 各段分行、缩进保留。

本地完整回归为2207/1 skipped、mock13工件、diff clean；本地 `pip check` 仍报告既有 `pdfminer-six 20260107 requires cryptography, which is not installed`。服务器 clean 与本地缺项是两个环境的独立事实，不相互替代。

## 真实网页材料消费

Run `run_p3_web_reading_20261010` 为 **completed**，CLI exit 0，**10/16 次模型调用**，verdict 为 supports，无提问、无人工介入。

- 网页登记的 parser 为 `markdownify/html.parser`，source/final URL、标题、fetched_at 正确；冻结正文为 Markdown，**45903 字节 / 874 行**，SHA256 与 ref 一致。
- 测试方在抓取后1分46秒另取官方 HTML，存证后定位对应 pre 并取原文。冻结 Markdown 去掉围栏后的代码正文与其逐行一致，包括四空格缩进、输入键6在前和排序输出键4在前。
- 示例有5条可见行，源码末尾另含空行；服务器按6行核对。前期文档称“五行”指可见示例，验收仍要求按实际原文保留尾部空白，不能通过 strip 正文取得一致。
- 7次读取回执均带 `total_lines=874` 和 `selected_chars`，已读内容包含目标示例；最终意见与报告引用了相同代码。
- Run账本、trace、Session均为10次，原生 assistant/tool 配对 **10/10**。10份模型请求均展示原生 tools、含 web_fetch 而不含 web_search；这是工具可用性检查，不表示进行了10次网页抓取。

模型正确区分输入键顺序与排序输出，解释 `>>>` 提示符和末尾空行，并排除了相邻、不带 indent=4 的单行示例。这个任务支持“转换后的材料能被准确消费”，不证明整体科研质量或搜索质量普遍提高。

## 测试更正与覆盖边界

测试方保留两次预期更正：先删除换行再检查嵌套 pre 的 bc，误把原本分行的内容视为粘连；另把 selected_chars 当成返回长度，并误认为 `abc\ndef\n` 有3行。按实际文本与已文档化契约更正后通过，未修改产品代码或冻结工件来获得 PASS。

`selected_chars` 是所选行段在字符窗口之前的源字符数，`total_lines` 为物理行数；上述文本为2行。达到搜索上限时后续范围未检查、总匹配数未知；有跳过文件时零匹配不能证明不存在。

坏 HTML、搜索上限和越界读取未被真实模型自然触发，相关行为由确定性测试和生产组件探针证明。搜索 provider 关闭，本轮未测试搜索后端质量；无 GPU、未跑 L3。测试后产品工作区干净，测试方未推送或修改产品。

## 下一步

本轮修复验收已完成，继续按[既有 L3 规程](../../guides/L3_RESEARCH_TEST.md)在 **9e31735** 上独立运行最终科研任务，保留原输入、预算、所有尝试和负结果，并与旧版记录对照。L3 尚未运行；不新增测试框架、不因短网页任务通过而预判 L3 质量。墙钟回拨仍按用户决定暂缓，其他设计优化另行讨论；分支合并仍等待用户决定。
