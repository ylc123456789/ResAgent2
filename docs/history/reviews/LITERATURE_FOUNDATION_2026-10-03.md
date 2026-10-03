# 文献基础改造：实现与测试交接（2026-10-03）

> 以下保留 schema 21 本地阶段的原结果与当时待验收范围。后续 schema 22 外部导入、服务器验收、真实论文补测和解析超时发现见[验收收尾](LITERATURE_FOUNDATION_ACCEPTANCE_2026-10-03.md)。

分支：`refactor/literature-foundation`，基线 `main@00fb5df`，公共 schema 21.0。
实现及本地确定性验证已完成；尚未进行本轮服务器真实文献/模型验收，不借用旧 L3 结果。
设计取舍见 [ADR-0022](../decisions/0022-paper-materials-and-access-records.md)。

## 本轮交付

- 搜索回执与论文分开：`literature_search` 记录查询、结果 ID 和失败；每篇论文以
  `literature_paper` 保存规范化元信息和摘要。arXiv 版本保留；同 key 相同快照复用，
  元信息变化形成新冻结快照，不覆盖旧引用。
- Scientific 通过 `fetch_literature_fulltext(paper_artifact_id)` 按需获取全文。
  Components 使用来源给出的 PDF URL 下载，Registry 先冻结 PDF，再登记解析文本。
  文本保留物理页码、解析器版本和提取局限，目录投影 paper → PDF → fulltext 来源关系。
- 解析使用 PyMuPDF4LLM，OCR 关闭。默认下载限制 32 MiB / 60 秒 / 最多 5 次跳转；
  解析 120 秒，均不扩展 Run 预算。解析复用现有进程部件，可超时终止，不另建执行框架。
- 缺少 PDF URL、下载失败、扫描/无文本或解析失败分别如实反馈。解析失败仍保留原 PDF，
  重试复用已冻结原件；重启后通过 Run 的既有登记表恢复复用，不建第二个论文数据库。
- 访问事件及 observation_trace 保留为日志，移除提问、委托、引用、完成的“已读”前置门槛。
  代码仍检查引用登记、Run/输入授权、冻结 hash 和明确交付；Scientific 判断证据含义。
- 本轮不增加专门的外部论文导入入口，不改检索排序/供应商策略，不实现 OCR 或图片/公式理解。

## 本地验证

在 Ubuntu-D 的 `/home/cyl/ResAgent2`、Conda `ResAgent2` 环境中执行：

```bash
PYTHONNOUSERSITE=1 python -m pytest tests apps/cli/tests -q --tb=short
PYTHONNOUSERSITE=1 python -m e2e.mock_e2e
PYTHONNOUSERSITE=1 python -m pip check
git diff --check
```

最终全量：**1612 passed / 1 skipped / 0 failed**；mock 为
`run_golden completed`、13 工件；隔离项目依赖检查通过，diff 无空白错误。
跳过项是默认不联网的 arXiv live smoke，不能把跳过解释成实时搜索通过。

覆盖包含真实合成 PDF 的逐页提取、空页/损坏文件、超时终止，以及受控 HTTP 的
429/超限/跳转；下载真实公共论文、实际服务器出口和真实 LLM 行为尚未覆盖。
文献整链测试使用真实 Controller / Scientific / Registry，下载解析边界采用受控数据；
重启复用、来源不覆盖、哈希篡改拒绝、跨 Run 与隐藏控制工件隔离均已检查。

本机用户级 `~/.local` 中预存 `pdfminer-six 20260107` 缺少 `cryptography`，
默认 `python -m pip check` 因此失败；加 `PYTHONNOUSERSITE=1` 后无缺依赖。
本轮没有改该用户级安装，也不把此结果写成默认环境完全干净。
新增项目依赖是 `pymupdf4llm>=1.28.2,<1.29`，未修改 torch/GPU 环境。

## 服务器复测

### 1. 同步并冻结版本

同步本分支的最终提交后，保存 `git rev-parse HEAD`、工作区状态、解释器及 editable
导入路径。依现有部署方式同步 Components 的新依赖（例如在项目环境中
`python -m pip install -e packages/components`），再执行上节的回归命令。
先检查普通 `pip check`；若因用户级包失败，记录原因，再单独报告隔离环境结果。

schema 20 及以前的 Run 不迁移、不恢复；使用新 Run ID 和新证据目录。
模型、预算、full trace 配置使用 [CLI 部署入口](../../../apps/cli/README.md)，
每次 run/answer/resume 读取同一配置，不改产品代码来制造通过。

### 2. 真实文献短 Run

不需要训练或 GPU。准备一份目标文件，例如：

> 检索温度缩放与神经网络校准的相关论文，选取一篇有公开全文的论文，
> 从原文核对一个具体的实验设置，给出结论、论文及正文工件引用和页码。
> 区分摘要、正文与自己的判断。获取失败时保留真实原因，不凭摘要补写原文细节。
> 本任务只做文献分析，不委托训练或代码实现。

在已配置的 CLI 中运行，建议预算 40 次调用、900 秒；例如：

```bash
bash apps/cli/run-configured.sh /absolute/path/cli-config.sh run   --goal-file /absolute/path/goal.txt   --max-llm-calls 40 --timeout-seconds 900   --data-root /absolute/path/new-test/data
```

核对实际 trace/Session/工件，而不只看 completed：

1. 一次检索的每篇结果有独立 paper ID，搜索回执只引用论文，不混入全文。
2. 至少一个成功案例形成 paper / 原始 PDF / 解析文本，并独立重算三者 hash。
3. 原始 PDF 与全文来源关系、页码、抽取内容可追溯；最终事实确实来自标出的正文。
4. 同论文再次获取复用 ID/文件，不重复下载；先下载不等于已有正文访问日志。
5. 模型不把同一论文的摘要/PDF/文本当作多份独立研究。
6. 问答、委托与完成不再因缺少“已读”标记被挡住；非法引用仍由确定性测试保护。

若真实来源限流或服务器网络失败，记录来源、状态码、阶段和模型处理结果；
可验收诚实失败处理，但应标记“实时全文成功路径未覆盖”，不能换为伪造正文后报全 PASS。
无需为了网络失败反复重跑到成功，也不以本短 Run 宣称完整科研 L3 通过。
