# 文献基础与外部导入验收收尾（2026-10-03）

产品分支 refactor/literature-foundation：ef5f836 完成论文粒度材料分离，
61f4592720ca26985f0e598038611eb963806926 增加外部论文导入，公共 schema **22.0**。
本轮服务器验收与独立只读复核完成；本记录只收尾文档，不改变产品，不表示已合并或推送。
[原本地阶段交接](LITERATURE_FOUNDATION_2026-10-03.md)保留 schema 21 的当时结果。

## 回归与证据

服务器测试方报告普通 pip check 无缺依赖，全量 **1641 passed / 1 skipped / 0 failed**，
e2e.mock_e2e 为 run_golden completed、13 工件。跳过的 live smoke 不算实时检索成功。
服务器产品 HEAD 与上述 SHA 一致；九个 editable 包指向服务器产品源码。
证据根为 /root/autodl-tmp/resagent2/runs/literature-foundation-61f4592-20261003/，
含 protocol/literature_61f4592_results.md、protocol/literature_gapclosing_results.md、
脚本、Run/Session、日志、full trace 和冻结工件。复核没有重新执行模型或改产品源码。

## 首轮覆盖与补测

- A run_literature_import_a 创建时导入，completed / not_applicable，真实模型 4 次调用。
  B run_literature_import_b 暂停后追加再回答，同 Session 完成，inconclusive，真实模型 4 次调用。
  两场景输入由 make_materials.py 生成合成 PDF，并非下载的真实论文；其 arXiv URL
  不能证明材料真实性。它们验证导入、冻结、来源链、读全文及摘要不足处理。
- C 六类失败边界覆盖非法清单、缺文件、签名错误、完成 Run 禁止追加和无文本 PDF。
  C6 的 Client.next_action() 使用固定响应，只是确定性控制器测试，不算真实模型诚实处理证据。
- 补测 run_literature_real_b 使用公开 MobileNets（Howard et al.，1704.04861v1），
  9 页，completed / supports，真实模型 6 次调用。题录、八位作者和摘要与公开元数据核对；
  trace 显示获取全文后分页读取，并报告 Page 4 的 RMSprop、depthwise weight decay 与
  Table 2 的 1×1 卷积占比等摘要外细节。目录名 d2-real-paper-adam 是试验沿用名，
  最终成功 Run 输入是 MobileNets，不能据目录名写成 Adam 成功 Run。
- 补测 run_literature_parse_fail 使用合成栅格、无文本层的合法 PDF，真实模型 2 次调用，
  completed / inconclusive。观察明确 parse_failed / OCR disabled；原件保留，
  不生成 fulltext，模型如实说明无法读取。这里的“真实”修饰模型调用，不修饰扫描论文来源。

两个阶段各 **27 个登记工件**的冻结 hash 经独立重算全部匹配；paper→PDF→fulltext
来源链、科研索引与 B 的同 Session 延续正确。B 导入后快照未包含全部控制字段，
其日志和会话记录交叉支持“未调用 Agent、未回答、未重置控制状态”，不能称全部字段
已由完整前后快照逐字证实。单次模型通过不代表普遍行为保证，也不构成完整科研 L3 验收。

## 保留的真实失败与性能边界

Guo et al.（1706.04599，14 页）的 run_literature_real_a 保留为 paused，3 次真实模型调用：
两次 fetch 均观察到 PDF parsing exceeded 120 seconds，没有 fulltext，随后 ask_user。
不删除失败现场，不把后续换论文成功倒写为此 Run 成功。
测试方计时：Guo to_markdown 121.3 秒；Adam 15 页 parse_pdf 118.4 秒；
MobileNets 9 页 parse_pdf 110.1 秒。耗时受内容与环境影响，不能概括为固定页数阈值。
当前 120 秒解析上限对这些真实资料存在风险，Run 总超时更长也不会自动扩大该上限。

后续性能工作尚未实现：由组合根提供解析超时配置，Components 保留受控执行与
Run 剩余时间约束；Capabilities 不向 LLM 开放扩大超时的权力。先测代表性资料，再选择
默认值。不为此新增流式解析、OCR 或缓存框架；本轮不能称已实现可调解析超时。

## 证据脚本与报告收尾

独立复核发现原验证脚本仅打印 FAILED 而未非零退出，MANIFEST.txt 为空白，
首轮报告未明确披露合成材料。上述缺陷已在证据层收尾，产品和原运行现场未修改：

- 根目录原始验证脚本保持原样；protocol/checkers_exit1/ 保存七个严格退出码副本，
  全部通过语法 compile。只读重验 verify_a、b_verify_final、verify_d2、verify_e 均 rc=0；
  verify_d 对未完成的 Guo Run 返回 rc=1，保留这一真实失败证明。
  没有重跑 b_verify_import，避免覆盖已完成 Run 对应的旧阶段快照。
  结果见 protocol/closeout_checker_results.json 与各 closeout_*.log。
- protocol/closeout-originals/ 保留原报告、原补测报告及空白 MANIFEST 副本。
  首轮报告首页追加勘误，并另存 literature_61f4592_erratum.md；补测报告说明
  不能将少数论文计时概括为“≥14 页”固定阈值。旧失败、原文与后续说明可区分追溯。
- protocol/MANIFEST.txt 现在列出 171 个普通文件的 SHA256；排除该列表自身及
  MANIFEST.md 说明，避免循环。MANIFEST.md 说明范围与列表指纹：
  e4eca0f7ba4d1f61164ce0fcbce319ee9b975e2c4f2235ec783cbc9193e7d72d。
  全部 171 项已核对，mismatch=0。

以上收尾没有修改原 Run、Session trace 或冻结工件，也没有新增 LLM 调用。
通过结论依据原始状态、会话、工件、trace 的独立复核及严格退出码重验；
不把历史脚本的正常退出倒写为当时已具备严格失败退出。
