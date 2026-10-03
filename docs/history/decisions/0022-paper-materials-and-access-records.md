# ADR-0022：论文粒度资料与访问事实记录

状态：accepted。日期：2026-10-03。

## 背景

此前一次检索的多篇论文摘要共用一个 literature_search 工件。检索回执、单篇论文、
摘要与全文的身份混在一起，既不便按论文复用，也不便描述原始资料与解析内容的关系。

observed 原本用于要求引用前先调用读取工具，但它只记录访问历史，不能证明读完整篇、
当前上下文保有正文或论断受到支持。对已经随上下文展示的内容强制补一次读取，会把
获取访问标记变成任务目标，并可能阻塞必要的提问、委托或完成。

## 决定

- 每篇论文独立登记为 literature_paper，保存来源元信息与摘要；literature_search
  只保存查询及论文引用。规范化论文 key 保留 arXiv 版本，同 Run 中元信息快照
  相同才复用；同 key 内容变化登记新不可变快照，不按标题猜测合并，不改写已冻结材料。
- Scientific 按需使用 fetch_literature_fulltext(paper_artifact_id)。Components
  获取公开可用 PDF，并使用 PyMuPDF4LLM 提取文本，关闭 OCR；Capabilities
  提供模型入口，Registry 分别冻结 literature_pdf、literature_fulltext。
  metadata.paper_artifact_id 表达所属论文，source_artifact_id 表达直接来源，
  科研目录只投影此关系。同 Run 复用已冻结材料，解析失败可复用 PDF 重试。
- 保留真实访问日志与 observation_trace，但不以 observed 作为提问、委托、引用
  或完成的前置门槛。Scientific 判断现有内容是否充分、是否需要读原文及如何解释
  证据；代码继续检查引用身份、Run 授权、登记来源和冻结 hash。
- required_evidence_kinds 要求引用精确的 literature_paper 或 literature_fulltext，
  搜索回执不满足论文证据要求；required_artifacts 继续检查精确 output_name 的
  登记存在与完整性。结构要求不证明科学有效性。

这是资料身份与信息可达性的改进，不恢复 Interpreter 的 LLM 转述，也不新增语义
验证器、阅读资格状态机、向量库、完整来源图或独立论文管理服务。通用 read_artifact
仍只读取严格 UTF-8；PDF 提取留在文献组件。本轮不增加任意外部资料导入，不绕过
付费墙或访问限制，提取失败与局限如实返回。

schema 升为 21.0，旧 Run 不支持恢复或迁移，原 state/session/trace/工件保留。
本决定取代 ADR-0014、0017、0018、0020 中将正文观察作为引用资格的部分，以及
ADR-0021 对 Shell 输出不授予“已读资格”的旧门禁表述；原访问事实记录、权限、
原件追溯、固定 Interpreter 和单次命令审批边界继续有效。旧 ADR 原文不改写。

## 验证边界

需要验证论文身份与版本、同 Run 复用、搜索回执与论文证据区分、PDF/文本来源和
冻结 hash、下载/提取失败，以及无访问标记时仍能依法提问、委托与完成。权限、
非法引用和明确交付缺失仍应失败。确定性测试只证明这些结构事实与流程，不证明
模型真正理解原文或论断正确；真实模型效果需另行记录，既有 L3 不作为本轮验收。
