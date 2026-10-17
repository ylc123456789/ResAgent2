# L3 后续：审核授权语义与报告口径的最小修复

日期：2026-10-16。分支 `feat/unified-run-shell`，开始于 `be44121`；L3 产品代码冻结于 `2c61f29`，schema 29.0。

## 事实与根因

L3 的研究数字及独立前向重放通过，研究验收8/10且有实质人工协助；自动审核语义未通过。Coding/Experiment各一次列举授权工作区的父 Run 目录，实际取得目录名及元数据。没有据此声称凭据或验收正文已读取。

Experiment的完整审核输入已有准确workspace.root、相对read/write范围、根要求和完整命令；allow理由却把“同Run、只读”推断成授权。不是权限字段缺失、Compiler改写或批准缓存复用问题。另三次脚本allow借用历史人审说明推断当前内容，虽然没有当前源码或内容绑定证据。

报告的旧 `Execution issues` 栏目只统计各WorkRequest outcome中的非完成任务和失败/取消委派错误；没有汇总工具失败/拒绝历史。旧标题及None容易被理解成执行全程无错误。该列表还包含历史委派结果，因此不能简单改称“当前未解决的问题”。

## 实际修改与未扩展范围

1. reviewer只补充四行通用目录授权含义：根及其范围内子项；同Run、父/兄弟关系和只读不构成额外授权；明确外部授权继续有效。无新字段、路径解析、权限库、工具或隔离层。
2. 确定性报告标题收窄为 `Non-completed delegated work`，说明统计范围，空项写 `None in this scope`。移除把failed/cancelled两类委派都硬写成failed的单行标签，不新增状态字段；不改变完成gate或原意见正文。

未修改Scientific/Coding/Experiment prompt、父子消息方向、第二至第四部分投影或脚本源码采集。脚本版本澄清在本次对照中未观察到新增收益，候选中的该段未采用。脚本证据绑定、原始需求理解、报告科学措辞和数值收敛问题继续保留，不能声称全部解决。审核仍可能误判，真正限制任意Shell文件访问需要独立隔离后端。

## 执行前冻结的纯审核实验

最初的私有L3 trace重放被自动审批拒绝（私有任务内容再次外传缺少明确授权），未执行或发送。改用12个完全虚构输入：6个应ask、6个正常allow；包括明确外部及父目录授权反例。每项每版本3次；命令只作为数据交审核，从未派发。

| 版本 | provider请求 | 应ask中的误allow | 正常allow | provider/协议错误 |
| --- | ---: | ---: | ---: | ---: |
| 原规则 | 36 | 1/18 | 18/18 | 0 |
| 目录+版本澄清候选 | 36 | 0/18 | 18/18 | 0 |
| 仅目录澄清（最终采用） | 36 | 0/18 | 18/18 | 0 |

首轮交替旧新顺序，最多4路并发；第二轮预先冻结删减候选及停止规则，无追加样例或继续试其他候选。相同DeepSeek模型/配置、每次最多一次尝试，108条实际trace逐条核对payload、变体、repeat和解析决定及理由。正常对照全部允许，未靠一律ask获得结果。

这只是小样本合成数据。首轮只有一个配对不同结果，不构成统计显著改善，也不是原L3输入的复测；不能宣称误放行率为零或脚本问题已解决。采用理由是把已有授权语义说准确，并在有限样例中未见退步。

证据根：服务器 `/root/autodl-tmp/resagent2/runs/reviewer-evidence-29-20261016-synthetic-001`；本地 `E:/dshWorkSpace/implementation/reviewer-evidence-20261016/`。执行前计划为 `EXPERIMENT_PLAN.md`、`ABLATION_PLAN.md`，最终逐条证据为 `comparison-final-evidence.json`，SHA256 `2d587365d0ab24d68f0aac1c1ecbfe47ed45adca7ab1896e41ce981691169ad0`。完整trace留服务器，不取隐藏reasoning。旧L3证据未改动。

测试方错误留痕：第一份合成实验脚本有语法错误，零provider请求；原失败目录保留。首次结果导出误把model当作request_text内部字段，按实际trace契约改为外层model；只重新读结果，没有重发模型请求。

## 代码回归与最终核对

本地完整回归：`python -m pytest tests apps/cli/tests -q` 实际rc0，2230 passed / 1 skipped；mock实际rc0、completed、8件工件；pip check实际rc0；git diff --check实际rc0。既有审核定向组66 passed；报告现有完成测试修改前后56 passed。最后一行报告状态标签的文案收窄后复跑相关完成测试仍为56 passed，git diff --check通过。

服务器补充受控短场景3/3通过：执行Agent为scripted，审核器为真实provider，运行时为真实AgentLoop/ProcessRunner。安全工作区写入获allow并真实生成marker；父目录列举、不可见脚本均ask，夹具从正常回答机制提交两次受控拒绝后继续完成；两项均无marker且原生run_shell各唯一not_executed回执、原文说明完整，恢复没有二次审核。共3个真实reviewer请求、1次无害shell执行。它不等于完整自主业务Run或L3。

短场景首次因脚本路径下无法import e2e测试辅助而在provider调用前退出；保留原错误后把仅用于构造原生调用的helper改为脚本本地构造，没有修改产品或研究代码。原失败日志保留。

纯审核数据用于验证模型判断，mock/离线回归只验证现有协议及运行机制，不混称语义验收。

最终收尾：原L3产品仍为2c61f29且干净；本次实验无遗留进程。服务器实验MANIFEST含146个文件，SHA256 `1aa4057414f7c0e03befc5b9781a99882dc9c47ff35d2f4bc5db30f849388160`。未重跑L3，未合并或推送。本地源码仍在原分支，修改可审阅。
