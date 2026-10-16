# Tavily 支持移除：实现与验证

日期：2026-10-10。分支：`fix/native-agent-receipts`；修改基线：`287453bbdc8bb2a6fefd87442b911669e25497bc`。schema 保持 24.0。

## 改动与部署影响

删除 Tavily 适配器、公开导出、CLI 配置/密钥读取/装配、专用测试及当前支持说明。保留 DeepSeek 托管搜索、`off`、独立网页抓取和通用组件边界。旧 `RESAGENT2_WEB_SEARCH_PROVIDER=tavily` 明确报配置错误，在创建客户端或目录前停止，不静默回退。现有 DeepSeek 请求、预算、结果/partial 保存和抓取实现不变。

没有 Tavily 独立 SDK 依赖；不修改环境或删 HTTP 依赖。历史决定和服务器验收保留当时事实；新取舍见 [ADR-0027](../decisions/0027-deepseek-only-web-search.md)。本轮不改工件、权限、ask_user 或真实 E2E 的装配。

## 本地验证

环境：Ubuntu-D，`/home/cyl/ResAgent2`，既有 `ResAgent2` conda 环境；只运行确定性测试，没有付费模型、真实网页请求或 GPU 实验。

| 检查 | 结果 |
|---|---|
| 网页/DeepSeek/工具/CLI 配置 4 文件专项 | 170 passed，0.70s |
| `python -m pytest tests apps/cli/tests -q` | 2191 passed / 1 skipped，58.27s |
| `python -m e2e.mock_e2e` | `run_golden completed`，13 工件，Coding/Experiment 各一次 |
| `git diff --check` | 通过 |
| 非历史生产代码及当前文档 Tavily 检索 | 无实现/支持残留；测试只留非法 provider 值拒绝用例 |
| `python -m pip check` | rc=1：既有 `pdfminer-six 20260107 requires cryptography, which is not installed` |

相较此前 2207 passed / 1 skipped 的验收记录，净减少 16 个用例来自 Tavily 专用测试、其参数化分支及旧回退选择检查的移除；同时新增非法 `tavily` 配置的拒绝覆盖。测试数量减少不表示现存用例失败。

本地依赖缺项与本次修改无关，本轮未安装或修改环境；不能沿用此前服务器 clean 将本地写成 clean。交叉审查核对了共享解析/错误类型、抓取、DeepSeek 预算和坏响应断言仍保留。

## 若需要服务器独立复核

从最终分支提交检出并记录完整 HEAD、工作区、editable 导入和 schema，使用声明安装的既有项目环境，在仓库根运行：

```bash
python -m pytest tests/components/test_web.py tests/components/test_web_deepseek.py tests/capabilities/test_web_capability.py apps/cli/tests/test_web_configuration.py -q
python -m pytest tests apps/cli/tests -q
python -m e2e.mock_e2e
python -m pip check
git diff --check
```

逐项保存输出和退出码。预期专项 170、全量 2191/1 skip、mock 13 工件；pip check 以服务器实际环境为准。配置拒绝测试证明旧值不会换成 DeepSeek，也不会创建客户端或数据目录。

本次只删除供应商分支且 DeepSeek 路径不变，不要求收费搜索或重新跑 L3；若后续修改权限、问答恢复或材料呈现，再按对应冻结版本安排真实验收。
