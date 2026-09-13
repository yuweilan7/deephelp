# S03｜目录、13段和故障所有权

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
S01裁决已可用；输入ARCHITECTURE、DECISIONS、M00设计、CONTRACTS草案、现有pyproject/uv.lock与infra compose。可与S02分工作树并行，契约分歧只提案。

## 允许修改的路径
docs/ARCHITECTURE.md、docs/RISKS.md、reports/M00/S03.md。不得改CONTRACTS、STATE、infra或锁。

## 具体执行
明确modules/deephelp-app未来包及domain/application/adapters方向，不按22阶段建22包。固定13段输入输出职责/早停finalizer，只有600进入主级联；500只归属聚合、1000只SOP执行。
写清MySQL事务+outbox、Redis/Milvus派生、checkpoint非账本的所有权。逐项给缓存更新失败、stale ACTIVE、工具成功后崩溃、checkpoint落后账本的修复者。不能承诺跨库事务或外部exactly-once。
状态图允许有界循环和人工等待，开发DAG不能有环。写模型/工具/重试总预算、默认live关闭与资源门禁。保留真实云容器配额、不擅自给API/worker加云端预算。M15持久saver标待验，不因MySQL事实源就宣称后端存在。

## 必须验证的输入与期望
某图给500再加主意图调用：拒绝。缓存失败但MySQL已提交：事实可返回、outbox修复。Milvus ACTIVE而MySQL RESOLVED：不合并。工具效果已发生但ledger未完成：UNKNOWN→查询对账，不二次效果。Run SUCCEEDED输出CLARIFY不视为矛盾。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。不要更新PROJECT_STATE（S06除外）。
