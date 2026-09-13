# S04｜用三个反例检验架构

你是DeepHelp的Astra实施工程师，本轮只执行这个M00子任务。仓库为D:\IdeaProject\deephelp（实际执行环境没有该路径时先定位授权工作区，不假装访问Windows）。先读AGENTS.md、docs/CONTRACTS.md、docs/PROJECT_STATE.md、当前子任务及列出的输入。运行git status --short、git rev-parse HEAD；输入设计基线为15d9ac9cd03251f7c32242cbdf1279abbffb8df9，但不得checkout旧版本覆盖后续工作，需确认当前内容是否已更新并记录实际commit。

当前仓库已有本次生成的设计产物；先复核，再只修正缺失或错误，不为了执行任务重写全部文件。MySQL/Redis/云Milvus与uv workspace保留，默认模型调用0、云变更0。M00不创建业务包、DTO实现、数据库迁移或应用依赖。

## 前置与输入
S02语义与S03架构已完成且无未裁决冲突；输入CONTRACTS、ARCHITECTURE、M00设计第4节。

## 允许修改的路径
docs/MODULES/M00_ACCEPTANCE.md、reports/M00/walkthroughs.json、reports/M00/S04.md。不得改业务代码或绕过S02/S03重定契约。

## 具体执行
把订单信息更正、双问题交错、审批后重放写成逐事件的表/JSON设计fixtures：每步message/question/run、实体版本、业务状态、响应outcome、允许工具/效果次数、证据和禁止行为。
更正A→B保留q1但实体version递增并废止旧参数审批；券问题A→活动问题B→补券回A，不能只归最近；工具已成功后crash/resume先对账，效果计数应为1，网络查询可多次。
补充跨用户、同订单不同诉求、歧义补充、已结束旧问题、新SOP版本、审批过期/并发等反例。明确哪些本轮能设计走查、哪些M02/M11/M15才运行验证；不要写一个假业务引擎来生成PASS。

## 必须验证的输入与期望
三个主场景不能靠每场新造状态/ID解释。订单更正不把A与B静默union。歧义补充可未绑定question并CLARIFY。普通“好的”不能触发待审批写操作。重复审批最多一个有效状态转移，不能用取消日志隐藏第二次效果。

## 执行、失败与回滚
从仓库确认可用解释器后运行 `python scripts/project_context.py verify`；工具尚未存在时记录待S05，不假装已运行。只执行本任务允许的离线检查，不安装额外框架。每条命令记录实际路径、退出码和简要结果；提出命令不等于执行成功。
发现源证据不足、前置契约不成立、允许路径外必须修改、用户未提交冲突或核心断言失败，停止相关写入并给最小修正请求。保留用户文件；回滚只撤销本任务自己的变更/提交，经确认使用git revert，不reset/clean。
报告输出到指定的reports/M00子任务文件，列输入commit、改动、通过/失败/未执行、资源变化、限制及下一任务。仅在本轮已有提交授权时提交自己的允许路径，推送不得force；没有权限明确未提交。不要更新PROJECT_STATE（S06除外）。
