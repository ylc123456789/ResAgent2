# contracts

跨模块稳定类型和接口。

当前已实现：

- Workflow、WorkflowTask、WorkflowPatch；
- ModuleTaskRequest、ModuleResult；
- Attempt；
- ArtifactRef、ArtifactCandidate；
- Question/Answer；
- Capability；
- CodeUnderstandResult、CodeModifyResult、VerificationResult；
- 公共 status 和 error code。

本包只表达语义，不执行 LLM、文件、进程、Git 或工作流。它不得依赖 runtime、orchestrator 或任何具体 Agent。

权威字段说明见 `docs/CONTRACTS.md`。

## 安装与测试

从仓库根目录执行：

```bash
conda activate ResAgent2
python -m pip install -e 'packages/contracts[test]'
python -m pytest tests/contracts
```

稳定导入路径是 `resagent2_contracts`。包版本为 `0.1.0`，当前 wire schema 版本为 `1.0`。
