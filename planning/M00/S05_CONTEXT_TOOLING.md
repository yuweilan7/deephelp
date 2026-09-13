# S05｜文档自检、最小上下文和本地源导入

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
S01源manifest与模块manifest稳定；输入现有scripts/project_context.py、tests/docs、AGENTS和planning/manifest.json。可与S02/S03独立路径并行。

## 允许修改的路径
scripts/project_context.py、tests/docs/、reports/M00/S05.md。本地运行产物只写.local，不修改infra/锁/用户原PDF。

## 具体执行
复核现有stdlib工具的verify/export/import-source三个命令：模块ID/依赖DAG/文件路径/相对链接/源manifest检查；export只包含白名单工程文档、当前模块、直接前置handoff、root锁与必要包元数据，不含密钥或全量repo；缺前置明确列缺失，不能编造完成。
源导入只从用户指定目录按已知SHA-256选择PDF，复制到.local/references/deephelp-original.pdf，保留原文件、拒绝覆盖异内容、复制后复验。先确认目标被gitignore忽略，拒绝符号链接越界/路径逃逸。
写并运行正常与负面单元测试；默认0网络/0模型/0依赖安装。Windows实际复制只有连接目标机器且有授权时执行，否则明确NOT_EXECUTED。

## 必须验证的输入与期望
正常manifest22模块通过；加回边成环失败；未知依赖/缺模块文件失败；非法module“../../.env”拒绝；源hash不同不复制；目标同hash幂等不改原件；目标不同hash拒绝覆盖；导出遇到指向仓库外部的symlink拒绝。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。不要更新PROJECT_STATE（S06除外）。
