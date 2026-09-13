# S01｜核对源材料与实仓裁决

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
无子任务前置。输入docs/SOURCE_MAP.md、docs/references/source-manifest.json、原PDF相关页、README/pyproject/uv.lock、infra/compose.yaml和P00验收摘要。

## 允许修改的路径
docs/SOURCE_MAP.md、docs/DECISIONS.md、docs/references/source-manifest.json、reports/M00/S01.md。其它路径只读。

## 具体执行
核对原PDF的78页与SHA-256，不按同名文件盲信版本。复查文件页19/57的12与13段差异、58/60单次主识别、52的final_code/top1不一致、62–69记忆/SOP。只能提取机制，不复制内部地址/客户号/截图。
核对实仓MySQL、Milvus5GiB受限实验、根workspace和空业务锁，保留历史P00已验收与本轮未复测的区分。记录旧包→当前工作副本的明确裁决，包括M01单pyproject、M19旧配额与M15持久saver缺口。不能修改部署来迎合文档。
若原PDF未归位，仅可使用上传原件或本地导入工具校验后读取。完全没有原图则标SOURCE_NOT_VERIFIED，不将页码摘要写成已看原图。

## 必须验证的输入与期望
输入：p19/p57计数冲突；期望保留冲突记录且基线13段。输入：compose明确MySQL；期望所有有效规范不要求安装PG。输入：另一份同名但不同哈希PDF；期望拒绝当本原件。输入：历史P00报告；期望不标本轮live通过。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。不要更新PROJECT_STATE（S06除外）。
