# M25｜PostgreSQL 业务事实与迁移闭环

**状态：** PLANNED；首特性可启动。旧路线已保存在bak/mysql-otel。实施先验证驱动/事务，再按结果细化全库迁移特性，不在探针前锁定 SQL 实现。

**前置：** 核对bak/mysql-otel仍指向旧快照；M08/M10/M15/M18/M20/M22/M23 的实际持久接口及直接 handoff。分支交付沿用AGENTS的main流程。见 [计划](../../planning/M25/PLAN.md)。

**目标：** 新路线最终只使用 PostgreSQL 承载所有应用关系数据；不长期保留“业务 MySQL + Agent PostgreSQL”两个事实后端。M26 单独处理官方 checkpoint 保存器。

## 迁移边界

复用唯一领域 DTO 和业务用例，替换持久适配器和装配。盘点 `ledger.py`、`cases.py`、`approval_store.py`、`history.py`、`release_store.py`、`feedback_store.py`、tools 的 flywheel_store、services 的 business_store，以及合成权益服务/调用与效果表、观测关联表、所有迁移/CLI/维护/备份消费者。不能只迁订单表或修改连接 URL。

应用事实库与 Langfuse 元数据库为不同数据库；个人环境可复用一个 PostgreSQL 实例，但独立连接配置、迁移、备份和恢复。业务事实、outbox 与其必要原子更新留在同一应用库；checkpoint 独立 schema/表，由 M26 的官方 saver 管理。Langfuse 不读写应用表。保留 Redis/Milvus 派生投影及其归属/status/version 回查。

首特性在隔离 PostgreSQL 库验证 Python3.14.7、Windows/Linux、`psycopg` 异步池、取消/连接关闭、事务回滚、唯一键冲突、CAS 与短行锁。核对锁定版本，成功后才替换正式适配；不换 SQLAlchemy、不重写领域层来扩大范围。

## 数据和事务语义

逐项转换 MySQL `ON DUPLICATE KEY`、`UNHEX`/二进制字段、JSON 函数、占位符/驱动行为、`FOR UPDATE SKIP LOCKED`、DDL 和索引。PostgreSQL 默认 Read Committed，不能直接假设原 MySQL Repeatable Read 的读写语义；为接收/终结、审批领取、outbox、审核租约及发布切换选择并证明所需隔离/锁策略，不统一提升隔离级别代替分析。

金额用精确 numeric，带前导零标识保留文本，时间明确 UTC/时区；核对 NULL、大小写/排序规则、唯一键、布尔、sequence、自增及 JSON 字段。参数、receipt、证据和资产签名沿用既有规范化算法；JSONB 改变键顺序或重复键语义，受签名原文/不可变 JSON 字节不能无条件转成 JSONB。分别验证关系值一致与原始签名字节/hash 一致。

迁移入口接收显式源/目标，源只读、目标隔离；导出有稳定快照/水位，导入可中断续跑和幂等，不读取源后回写。记录完整表清单、主键、行数、逐表规范化校验、外键/关系、版本/归属、outbox 消费状态、active/previous/run 资产引用与待审批/UNKNOWN。M23 的历史 27 表计数是盘点起点，不是新库必须凑出的固定数字。

运行 byte/schema 变化使旧完整发布证据不适用于新运行；保存旧清单/receipt 原样作为历史证据。历史读取/重放与继续执行按原受绑定版本处理；不能改旧 manifest/hash 冒充新包通过。兼容旧运行恢复的策略由 M26/M30共同验证，新请求必须取得适用的新完整发布。

## 验收与停止

先验证隔离库迁移与同一固定输入的事实输出、历史回读、重复消息零模型/工具、并发唯一接收/终结、CAS、outbox 故障修复、审批领取与效果/ABSENT 读写锁、发布并发切换。以确定性动作验证真实 PostgreSQL 协议，普通 pytest 仍离线；数据库 SELECT 成功不代表迁移通过。

隔离实际备份→恢复到另一新库→校验关系与代表查询；失败保留源、快照及续跑记录。M25 不切现网，不卸载 MySQL，不使未验 saver 承担写操作；新路线审批恢复未通过 M26 前保持禁用。正常模型请求/解析/决策未变时模型可为 0 次；若实际改变内容路径按 ROADMAP补必要代表内容，不串跑全模块探针。交付后停止。

官方依据：[事务隔离](https://www.postgresql.org/docs/current/transaction-iso.html)、[JSONB 语义](https://www.postgresql.org/docs/current/datatype-json.html)。网页不代替锁定驱动及实库证据。
