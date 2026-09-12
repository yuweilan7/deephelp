# 后续本地开发约定

MySQL 使用 utf8mb4、UTC DATETIME(6) 或明确约定的 UTC 时间戳、InnoDB 事务与唯一约束。本轮验收支持这些基础能力，但尚未创建完整业务模型。

| 后续实体 | 建议职责 |
|---|---|
| session | 会话归属、生命周期与用户关联 |
| question | 单次问题、处理阶段和最终状态 |
| event | 不可随意覆盖的业务事件与关联标识 |
| sop | SOP 版本、内容定位、启停状态 |
| tool_execution | 工具调用输入摘要、状态、结果引用与失败原因 |
| idempotency_record | 幂等键唯一约束、请求指纹、执行结果引用 |
| approval | 审批决定、审批人、版本与事务边界 |
| audit_log | 审计事件、操作者、关联对象与 UTC 时间 |

Session/Question/Event 的事务边界、外键和索引在对应业务模块确定。不能把业务事实只写 Redis，缓存失效不能改变业务结果。

LangGraph 开发阶段使用本机 SQLite / AsyncSqliteSaver；本轮没有部署 checkpoint 数据库。M15 再评估 community MySQL saver、自定义 BaseCheckpointSaver 或其他持久化方案。业务事实仍统一存 MySQL。

主机为 4 vCPU / 8 GB，只验收小数据和低并发实验。Milvus 资源稳定并不等于达到官方生产推荐配置或证明可承载大规模数据。应用与模型调用仍在本机，M19 才另行练习应用镜像构建、部署与回滚。
