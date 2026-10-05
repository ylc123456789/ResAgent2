# 文献基础及通用工具：合并收尾（2026-10-05）

用户授权先合并已验收基础，再继续搜索优化。代码切点e03a151，公共schema23.0；合并范围为main00fb5df之后的七个提交：

| 提交 | 已实现范围 |
|---|---|
| ef5f836 | 单篇论文元信息/摘要、PDF/正文分离、访问历史作为诊断日志 |
| 61f4592 | 新建/paused Run 的外部论文清单及本地PDF导入 |
| 361e9fc | 文献基础与导入验收文档 |
| 0997b51 | 冻结证据、验证事实、工作输入和Git边界修复 |
| b0bd426 | 统一artifact_加完整hash的登记身份（schema23） |
| 7529233 | PDF默认300秒及CLI配置 |
| e03a151 | 工作区统一10 MiB文本边界、字符窗口、CRLF和搜索skip反馈 |

本次收尾只补文档，不修改上述代码。1faab0f起的目标驱动搜索参数/分页/摘要改进及其后续修正独立留在开发分支；不随基础合入main。当前规范继续见docs/current，历史记录按实测阶段保留。

## 已有验收与独立复核

- 文献基础/导入schema22：服务器1641 passed / 1 skipped、mock13、pip check干净；真实MobileNets导入与实际阅读、扫描件解析失败诚实报告通过，原件/来源链与hash核对，见文献验收收尾。
- 统一ID后的schema23：测试方报告真实30epoch GPU校准L3自主完成、科研评分10/10和逐样本复算通过。主开发先前独立核验65次调用、10次命令批准、实际CUDA与45k/5k/10k切分、冻结/单次test时序、92/92工件hash及引用链。实际文献为13篇摘要预览及1篇正文前120行，另有2次旧120秒解析超时；这些恢复与阅读范围已在服务器报告纠正。此案例不代表e03的新文本工具也重新跑过GPU L3。
- e03的PDF/文本补测：主开发本次只读核验服务器regression.log，固定e03a151，1727 passed / 1 skipped、mock completed/13工件、pip check与diff check干净。public66项、private5项清单hash全部匹配，A/B两个Run completed，39/39登记工件hash一致、无terminal error和完成违规。
- PDF场景：Guo14页正文提取成功、5次范围读取成立。测试方报告独立parse耗时139.6秒，但目录未保留该计时stdout，因此只能引用该报告值，不能说主开发独立复算了139.6>120<300。现存799ms是LLM请求latency，不是PDF解析耗时。配置/超时进程清理及短Run截止已有确定性回归。
- 文本场景：约2.6 MiB单行JSON字符窗口、128000返回限制、Unicode/CRLF替换、search skip3及incomplete成立，7项通过。

证据分别保留在服务器：
- /root/autodl-tmp/resagent2/runs/literature-foundation-61f4592-20261003/
- /root/autodl-tmp/resagent2/runs/l3-schema23-20261004/
- /root/autodl-tmp/resagent2/runs/pdf-text-tools-20261005/

本地合并前在e03代码重新运行：1727 passed / 1 skipped（54.16秒），mock run_golden completed / 13工件，git diff --check通过。9个editable包仍使用WSL /home/cyl/ResAgent2的src。已知本地额外安装的pdfminer-six缺cryptography问题保留；服务器pip check干净，不因合并修改环境。

## 保留边界

没有通用PDF解析性能保证，不自动OCR；文本分页控制模型返回量，底层不承诺任意大小文件流式读入。公网搜索服务、论文可下载性及正文提取质量仍是外部限制。访问日志不等于看完整篇，功能/工件校验不证明科研结论正确。

旧schema22及更早Run拒绝恢复，原数据不迁移或删除。旧L3交接中的120秒描述属于当时基线；当前300秒配置和用法以PDF文本交接与current为准。后续搜索增量需要独立质量对照，本基础通过不替其召回效果背书。
