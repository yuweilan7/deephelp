# S06｜核验既有成果、补齐证据与M00退出

本任务不是跳过S01–S05，而是用它们的退出条件检查已经存在的成果。默认一个主会话串行核验；有缺口时执行对应S，不再为“执行S”生成另一轮任务。

## 输入与范围

在授权的deephelp工作区读取AGENTS、CONTRACTS、PROJECT_STATE、M00设计、M00_ACCEPTANCE、DECISIONS、SOURCE_MAP和S01–S05。记录实际分支、HEAD和未提交差异。历史commit只说明来源，不作为覆盖当前工作区的理由。原PDF从source-manifest指定位置读取；不能读原图时明确SOURCE_NOT_VERIFIED。

仅核验或修补M00，不实施M01、不安装业务框架、不修改infra与根锁、不调用模型或云服务。只按本轮明确授权提交修改。

## 覆盖表：要求—产物—本轮检查—结果—缺口

在reports/M00/S06.md记录以下五项，已有内容正确就引用证据，不重生成。

| 来源任务 | 现有产物 | 必须核对 |
|---|---|---|
| S01 | SOURCE_MAP、DECISIONS、source-manifest、原PDF | hash、12/13段差异、600唯一主分类、原文与工程改造分开、实仓环境 |
| S02 | CONTRACTS | ID、幂等、状态、错误、证据、预算；缺槽位、跨用户、非top1及UNKNOWN行为 |
| S03 | ARCHITECTURE、RISKS | 13段职责、早停、存储权威、预算；WAITING_SLOT候选及审批上下文限制 |
| S04 | M00三个走查、M00_ACCEPTANCE | 更正、双问题交错、审批重放的ID/版本/状态/效果次数，无临时新造状态 |
| S05 | project_context.py、tests/docs、源同步记录 | 实际自检/测试、幂等导入、不覆盖异内容、白名单导出、依赖无环和路径边界 |

没有五份单独的执行报告不自动失败，集中S06报告可以承载核验；没有对应证据也不能自动通过。有缺口标明对应S，再在同会话按该S范围补齐。

## 实际检查与兼容性复核

用项目Python3.12执行 `python scripts/project_context.py verify` 和 `python -B -m unittest discover -s tests/docs -v`，记录解释器、命令、退出码、失败和跳过原因。Windows优先解析当前项目的`.venv/Scripts/python.exe`或既有uv入口，不把系统解释器冒充项目版本。

检查差异及已跟踪路径，确保原PDF、原ZIP和凭据不在公开提交内。资料归位不证明架构正确；历史P00报告不证明当前云机已重新验收。

分别从CONTRACTS与ARCHITECTURE两个接口视角写缺槽位、跨用户拒绝、重复审批的输入输出/状态/工具次数预期，再逐项比较。可同会话完成并标“单会话双视角”；实际另开会话才标“独立会话审查”。语义兼容是门禁，会话数量不是门禁。订单更正、双问题交错、审批后重放三个走查必须覆盖。

## 修改与退出

默认修改reports/M00/S06.md、docs/PROJECT_STATE.md、handoffs/M00.md及M00设计的必要说明。设计有实质问题时先记录，再在同会话切到对应S所列文件范围补齐并复核；新业务范围和新服务不在本任务内。

覆盖表没有未解决的关键缺口，走查和接口视角一致、适用文档测试通过后，才标M00 DESIGN_ACCEPTED。它只表示设计验收，不表示业务已实现。关键源证据缺失或失败不能按跳过通过；业务unit/integration/live/e2e与恢复能力保留真实状态。

有明确提交授权时只提交本任务路径并验证远端结果；否则保留可审查差异。记录真实commit，不自引用未知SHA。完成后交接M01骨架及M02唯一类型输入，不自动实施下一模块。
