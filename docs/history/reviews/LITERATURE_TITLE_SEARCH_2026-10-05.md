# 文献标题查询修复与质量对照（2026-10-05）

## 分支和合并范围

已验收基础的代码切点为e03a151，补齐阶段验收文档后以64de57f合入并推送main。生产代码与e03a151一致，目标驱动搜索增量没有混入这次基础合并。

后续开发位于fix/literature-search，继承原refactor/literature-foundation的1faab0f、59a276f、3f9ee3d，并通过e85705e合入基础收尾文档。原分支保留；搜索开发分支推送供验收，尚未合并main。公共schema仍为23.0。

## 修什么，为什么这样修

1faab0f新增title范围，却把完整标题拆成每个词各一个ti条件再AND。服务器已发现Guo完整标题因此返回0条；Scientific改用topic能够恢复，但不能把这种恢复当作title查询质量合格。

title现在将按既有查询语法解析后的完整输入组织为一个标题短语。arXiv发送一个ti短语，OpenAlex发送带引号的search.title。双引号、反斜杠按原查询语法转义；不增加停用词词表或自动改词。topic查询行为保留，匹配相似标题仍可能发生，需核对题目、作者及标识。

Components负责查询转换和真实来源事实。Capabilities维护参数说明和使用指引：已知标题用title，空结果不等于论文不存在，按用户目标和回执判断是否改词或换源。Scientific仍负责目标、材料缺口和证据判断；本次没有改写其角色提示词，也没有增加固定检索流程、请求配额、Provider、依赖或检索Agent。来源选择、分页、HTTP节奏、冷却、Run预算和论文冻结保持已有主线。

## 确定性验证

WSL Ubuntu-D /home/cyl/ResAgent2：全量1765 passed / 1 skipped（56.23秒），mock run_golden completed / 13工件，git diff --check通过。新增测试覆盖完整裸标题/加引号标题、停用词、冒号/连字符、转义引号/反斜杠/撇号、空白归一和topic语义保留；既有年份/页号、请求前校验、来源/失败、冻结交接测试继续通过。

工具指纹仅shared/literature_search变化；公共数据schema、其他工具输入和指引不变。本地pip check仍为既有额外pdfminer-six缺cryptography，未更改依赖；服务器环境须独立核对，不把本地pip检查写成通过。

## 同服务器真实标题对照

比较1faab0f的裸标题title表达式与本次修复表达式。由本地生产查询函数生成候选参数，在26089服务器直接请求公开API，不经Scientific或付费模型。两版本同匿名认证、相同来源、page=1、最多5条、不加年份；每标题成对并交替新旧先后。arXiv请求完成后至少间隔3秒，OpenAlex至少1秒；每请求一次尝试，遇429/Retry-After停止该来源，无主动压测。

20/20请求均HTTP200。arXiv按不含版本的目标ID核验；OpenAlex按规范化标题及已知作者核验，并保留其他相似结果。

| 已知目标 | arXiv旧→新目标排名 | OpenAlex旧→新目标排名 |
|---|---|---|
| On Calibration of Modern Neural Networks（1706.04599） | 未命中→1 | 1→1 |
| Attention Is All You Need（1706.03762） | 未命中→1 | 1→1 |
| BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding（1810.04805） | 未命中→1 | 1→1 |
| Language Models are Few-Shot Learners（2005.14165） | 未命中→1 | 1→1 |
| A Simple Framework for Contrastive Learning of Visual Representations（2002.05709） | 未命中→1 | 1→1 |

前5条目标命中：arXiv 0/5→5/5，OpenAlex 5/5→5/5；新查询两源均首位命中。OpenAlex的SimCLR仍有同名/同作者的不同Work条目，不按标题强制合并。总命中数下降不单独作为质量改善依据。

这里比较的是新增title入口的修复，不是e03旧产品完整Agent的失败率；e03没有title参数，原topic已能定位Guo。五标题样本支持该入口确实改善，不能推广为主题检索总体效果或商业搜索水平。

### 搜索基础增量的已有小样本

此前还在同服务器对e03旧查询与1faab0f新topic做过3查询×2来源×2版本共12次成功请求，每次5条。arXiv多数前5相同或重合；OpenAlex按标题/摘要收窄后结果有变化。温度缩放局限查询的匿名版本题目/摘要审阅：旧组2篇明确直接相关、3篇需补材料，新组4篇明确直接相关、1篇无关的空气温度传感器校准。缺摘要不直接判为无关；新组一篇临床编码论文完整摘要后半段含TS的ECE改善/MCE恶化，保存完整摘要有实际价值。

这是查询参数的小样本材料对照，不是Scientific提示词A/B；其他查询效果混合，不能据此宣称全部搜索更好。已有来源/分页控制、完整摘要保存和真实失败反馈验收见[1faab0f记录](AGENTIC_LITERATURE_SEARCH_2026-10-05.md)。

## 证据位置

- 服务器：/root/autodl-tmp/resagent2/runs/lit-title-comparison-20261005/，含脚本、20份原响应、20份逐请求事实、汇总和hash清单。
- WSL保留副本：/home/cyl/ResAgent2/.resagent2/audits/lit-title-comparison-20261005/。
- 此前topic对照：/home/cyl/ResAgent2/.resagent2/audits/literature-search-comparison-20261005/，含comparison.md、query_comparison.py与12份响应。
- 1faab0f真实Agent验收：服务器/root/autodl-tmp/resagent2/runs/lit-search-20261005/；基础合并证据见[收尾](LITERATURE_BASELINE_ACCEPTANCE_2026-10-05.md)。

这些公开API脚本/响应没有API key和模型trace。测试集合是验收输入，不是生产系统的论文或查询配额。

## 给测试AI的后续步骤

先冻结origin/fix/literature-search的HEAD、工作区、9个editable源码指针及模型/来源配置，仅记录密钥有无。运行pip check、pytest tests apps/cli/tests -q、python -m e2e.mock_e2e和git diff --check；预期1765 passed / 1 skipped、mock13。无需GPU训练L3。

真实模型至少补两个目标：
1. 联网定位Guo《On Calibration of Modern Neural Networks》，核对作者和来源，说明摘要能确认什么。核对title实际发完整短语、论文身份、完整摘要保存和最终阅读范围。
2. “温度缩放有哪些局限？找相关方法或反例，给依据并说明不足。”保持Scientific自主选词、阅读、换源、翻页或停止；检查工具指引没有变成固定检索任务。保留自然失败，不要求触发429或下载所有论文。

若要判断搜索增量整体是否优于main，再对main64de57f与候选做相同用户目标的成对测试：固定上述目标及一个分布漂移校准目标，使用相同模型、预算、认证、材料和可达来源；记录日期及服务差异。main没有source/scope/page参数，因此比较用户交付目标，不强行要求它调用不存在的参数。人工评价目标论文、相关材料、关键文献覆盖、误召回、证据支持和失败恢复；工具调用数、总命中数和completed只能作为辅助事实。已知论文不能丢失，主题任务不能明显退化，改善须有实际材料依据。

完整Agent的候选提示词尚未在真实模型上复测。完成上述定向验收和必要质量对照，再决定合并搜索分支；此前1faab0f的真实通过不冒充本次候选的Agent通过。
