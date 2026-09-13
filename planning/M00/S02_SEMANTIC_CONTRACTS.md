# S02｜冻结跨会话语义，不提前实现DTO

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
S01的源/实仓裁决已可用；先读docs/DECISIONS.md、docs/MODULES/M00.md、现有docs/CONTRACTS.md及相关代码（当前可能没有业务代码）。

## 允许修改的路径
docs/CONTRACTS.md、reports/M00/S02.md。不得修改ARCHITECTURE、STATE、锁或迁移。

## 具体执行
给M02留下唯一的语义输入：tenant/user/session/question/message/request/run/trace/operation标识、消息幂等范围与payload冲突、实体provenance/version、Question/Run/Operation/Approval状态，outcome、错误/next_action、EvidenceRef、VersionManifest和共享ExecutionBudget。
逐项区分未知/缺失/不适用与非法；question未明确归属允许为空，无权限不建他人case。WAITING_SLOT是业务结果，本轮run可正常结束；审批恢复才定位原run。外部结果未知禁止盲目写重试。
非top1最终意图须通过可验证override规则而非随便写理由。score类型不能都叫confidence。交付三个可比较的JSON语义样例及约束说明，写在报告；不创建Pydantic类、不安装库、不提前定完整数据库Schema。

## 必须验证的输入与期望
缺订单：CLARIFY/WAITING_SLOT、工具0、下一条新run不resume。跨用户case：FORBIDDEN、业务写0、工具0。重复message同payload：复用既有run/结果；不同payload：IDEMPOTENCY_CONFLICT。非top1无合法override：拒收而非成功。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。不要更新PROJECT_STATE（S06除外）。
