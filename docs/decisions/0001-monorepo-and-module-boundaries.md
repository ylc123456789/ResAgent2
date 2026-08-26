# ADR-0001：使用 Monorepo，保留逻辑模块边界

**状态**：accepted
**日期**：2026-08-26

## 背景

旧系统把 ResAgent、ExpAgent、CodingAgent、reproagent 放在四个 Git 仓库中。当前由同一开发者维护、统一测试，跨仓接口和实现经常需要同步改变，物理拆分增加了版本、文档和集成成本。

## 决策

ResAgent2 使用一个 monorepo：

```text
contracts
runtime
orchestrator
agents/scientific
agents/coding
agents/experiment
```

逻辑边界继续保留：

- 独立 request/result；
- 独立状态和 completion check；
- 子 Agent 禁止互相直接调用；
- 不访问其他模块私有文件；
- 可以独立测试；
- 只通过 contracts 通信。

## 原因

- 减少跨仓 lockstep 修改；
- 文档、代码和测试可以在同一 commit 保持一致；
- 共享 runtime 只实现一次；
- 仍可通过 package 边界保持模块化；
- 以后出现独立用户/发布节奏时仍可拆包或拆仓。

## 后果

- 模块不是源码级完全独立，会共享 contracts/runtime 版本；
- 必须用依赖规则和 contract tests 防止隐式耦合；
- 旧仓库冻结保留，不用 Git submodule 接入新主线。
