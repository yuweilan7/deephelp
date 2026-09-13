# S06｜独立集成复核与M00退出

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
全部核心设计/工具产物可读；S01–S05可已有报告，也可由本任务对现有交付独立复核。不能仅因缺少五次历史会话就要求重做文件；必须实际验证对应退出条件。

## 允许修改的路径
docs/MODULES/M00.md、docs/PROJECT_STATE.md、handoffs/M00.md、reports/M00/S06.md。其它文件只读；需要修复契约/架构时退回对应owner。

## 具体执行
核对SOURCE_MAP/DECISIONS/ARCHITECTURE/CONTRACTS/M00_ACCEPTANCE/模块manifest一致。读取当前git状态、工具测试结果和真实资源变化，不继承旧PASS。
执行文档verify与tests/docs单测；检查本次提交差异不含原PDF/凭据，未改infra/根锁。用两个独立接口视角写缺槽位、跨用户拒绝、重复审批的请求/响应与状态/工具预期，记录输入依据与对照结果。若能启动两个独立会话才称“两会话验证”；单会话双视角明确标注，不伪装。
逐个走查订单更正、双问题交错、审批重放，确认没有临时新造状态。若不一致，保持REVIEW_PENDING，写最小冲突单；不要在本任务改其它owner的契约绕过审核。
通过后将M00设为DESIGN_ACCEPTED（仅设计，不是实现），记录实际commit/命令/退出码/尚未执行的业务integration/live/e2e与Windows导入。交接M01工作包布局和M02语义输入，下一步只启动M01。

## 必须验证的输入与期望
已有workspace不等于M01完成；历史P00不等于本轮在线健康；文档工具unit通过不等于业务unit；本轮没有真模型/进程重启就必须NOT_RUN。共享contract/IDs/状态必须三个场景兼容。未定saver归M15门禁，不偷偷新增数据库。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。本任务是唯一可以更新PROJECT_STATE的M00集成人。
