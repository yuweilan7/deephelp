# M15｜持久执行、人工审批、幂等重放与故障恢复

**阶段：** D 执行与治理

**前置：** M10, M12, M14

**来源：** 原 PDF 文件页 57–60、62–69、74（恢复细节为工程补充）。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
将“可暂停的 Agent”做成“进程重启后知道自己做到哪、不会重复副作用、不能被错误消息批准”的系统。这些细节是工程补充，原文未提供完整事务/恢复实现，必须明确标注。

## 身份和持久化边界
MySQL 是case、operation ledger、approval、outbox的业务事实源。LangGraph checkpointer放独立表/命名空间，保存执行游标与必要状态引用；不能把checkpoint表当唯一业务账本。thread_id按工作流namespace+run_id映射，恢复必须定位原run，不能用user_id/session_id将多个待审批问题揉在一起。

普通用户追加消息经过正常intake，由case/event路由处理；缺槽位正常返回WAITING_SLOT。只有专门approval/resume端点可恢复持久审批，中断payload只含可展示的脱敏内容。一个人说“好的”不等于他明确批准任何一个挂起的写操作。

## 审批与执行协议
审批绑定 tenant/user/case/run、operation_id、tool名、规范化参数hash、SOP版本、业务状态版本、有效期、审批者与授权。参数或订单变化、SOP变化、状态已失效时不得复用审批。拒绝/过期/撤销都要能结束或重规划，不无限挂起。

LangGraph interrupt恢复时可能从该节点开头重跑；节点前半部分必须无副作用或幂等。将“生成待批准计划”“审批等待”“执行确定操作”“查询操作结果”明确分开。用业务幂等键和下游Mock的幂等接口保证重试不重复发券/改权益，不宣称框架天然 exactly-once。

## 事务与补偿
同一MySQL事务登记operation状态与outbox；外部工具调用与MySQL提交不能伪装单个本地事务。处理超时后结果未知：先按operation_id查询下游执行状态，确认失败才按策略重试；不因没收到返回就立刻再次发券。补偿有业务可逆条件和审批边界，不将所有操作都“撤销再重做”。

避免持有数据库事务/行锁跨等待人类或长LLM调用。case并发更新用版本检查等策略；Redis锁只能优化，不作为唯一正确性保证。checkpoint与业务提交间的裂缝需通过ledger对账修复。

## 必须给出故障注入矩阵
中断前崩溃、approval已入库但checkpoint未推进、工具执行前崩溃、工具成功但响应丢失、工具成功但MySQL未更新、重复resume、两个审批者并发、参数被改、旧审批过期、Redis清空、Milvus投影滞后。每个案例写最终case状态、允许的工具调用次数、幂等记录、恢复入口与证据。

交付经过兼容性门禁的持久checkpointer适配（不能因业务库是MySQL就假定后端已可用）、审批schema/端点、幂等ledger、恢复/对账脚本、权限测试及真实进程重启测试。未能实际模拟重启就标未验收。只操作合成权益系统，不接真实支付/退款。

## 当前仓库补充门禁
MySQL 业务账本已定，持久 checkpoint saver 未验收。先以锁定版本和真实 MySQL 做最小兼容/崩溃恢复试验；社区 saver 不等于官方支持。不得自动添加 PostgreSQL/SQLite 或新工作流引擎。未通过该门禁只能交付非持久试验，不能标记持久审批完成。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M15.md`、`handoffs/M15.md`、`reports/M15/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
