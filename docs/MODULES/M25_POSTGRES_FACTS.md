# M25｜PostgreSQL 业务事实与迁移闭环

**状态：** 全持久迁移与隔离备份恢复已完成；两项实际证据见 [M25交接](../../handoffs/M25.md)。旧路线已保存在bak/mysql-otel，运行入口见 [PostgreSQL](../../infra/postgres/README.md)。

**前置：** 核对bak/mysql-otel仍指向旧快照；M08/M10/M15/M18/M20/M22/M23 的实际持久接口及直接 handoff。分支交付沿用AGENTS的main流程。剩余实施事项为空，交付证据与下游边界见交接。

**目标：** 新路线最终只使用 PostgreSQL 承载所有应用关系数据；不长期保留“业务 MySQL + Agent PostgreSQL”两个事实后端。M26 单独处理官方 checkpoint 保存器。

## 迁移边界

复用唯一领域 DTO 和业务用例，替换持久适配器和装配。盘点 `ledger.py`、`cases.py`、`approval_store.py`、`history.py`、`release_store.py`、`feedback_store.py`、tools 的 flywheel_store、services 的 business_store，以及合成权益服务/调用与效果表、观测关联表、所有迁移/CLI/维护/备份消费者。不能只迁订单表或修改连接 URL。

应用事实库与 Langfuse 元数据库为不同数据库；个人环境可复用一个 PostgreSQL 实例，但独立连接配置、迁移、备份和恢复。业务事实、outbox 与其必要原子更新留在同一应用库；checkpoint 独立 schema/表，由 M26 的官方 saver 管理。Langfuse 不读写应用表。保留 Redis/Milvus 派生投影及其归属/status/version 回查。

首特性在隔离 PostgreSQL 库验证 Python3.14.7、Windows/Linux、`psycopg` 异步池、取消/连接关闭、事务回滚、唯一键冲突、CAS 与短行锁。核对锁定版本，成功后才替换正式适配；不换 SQLAlchemy、不重写领域层来扩大范围。“完成M25”覆盖协议资格、全持久迁移/导入和隔离备份恢复，首项交付后继续同模块剩余特性，全部交付后停止；已验首项不重复实施。

## 数据和事务语义

逐项转换 MySQL `ON DUPLICATE KEY`、`UNHEX`/二进制字段、JSON 函数、占位符/驱动行为、`FOR UPDATE SKIP LOCKED`、DDL 和索引。PostgreSQL 默认 Read Committed，不能直接假设原 MySQL Repeatable Read 的读写语义；为接收/终结、审批领取、outbox、审核租约及发布切换选择并证明所需隔离/锁策略，不统一提升隔离级别代替分析。

金额用精确 numeric，带前导零标识保留文本，时间明确 UTC/时区；核对 NULL、大小写/排序规则、唯一键、布尔、sequence、自增及 JSON 字段。参数、receipt、证据和资产签名沿用既有规范化算法；JSONB 改变键顺序或重复键语义，受签名原文/不可变 JSON 字节不能无条件转成 JSONB。分别验证关系值一致与原始签名字节/hash 一致。

迁移入口接收显式源/目标，源只读、目标隔离；导出有稳定快照/水位，导入可中断续跑和幂等，不读取源后回写。记录完整表清单、主键、行数、逐表规范化校验、外键/关系、版本/归属、outbox 消费状态、active/previous/run 资产引用与待审批/UNKNOWN。M23 的历史 27 表计数是盘点起点，不是新库必须凑出的固定数字。

运行 byte/schema 变化使旧完整发布证据不适用于新运行；保存旧清单/receipt 原样作为历史证据。历史读取/重放与继续执行按原受绑定版本处理；不能改旧 manifest/hash 冒充新包通过。兼容旧运行恢复的策略由 M26/M30共同验证，新请求必须取得适用的新完整发布。

## 验收与停止

先验证隔离库迁移与同一固定输入的事实输出、历史回读、重复消息零模型/工具、并发唯一接收/终结、CAS、outbox 故障修复、审批领取与效果/ABSENT 读写锁、发布并发切换。以确定性动作验证真实 PostgreSQL 协议，普通 pytest 仍离线；数据库 SELECT 成功不代表迁移通过。

隔离实际备份→恢复到另一新库→校验关系与代表查询；失败保留源、快照及续跑记录。M25 不切现网，不卸载 MySQL，不使未验 saver 承担写操作；新路线审批恢复未通过 M26 前保持禁用。正常模型请求/解析/决策未变时模型可为 0 次；若实际改变内容路径按 ROADMAP补必要代表内容，不串跑全模块探针。交付后停止。

官方依据：[事务隔离](https://www.postgresql.org/docs/current/transaction-iso.html)、[JSONB 语义](https://www.postgresql.org/docs/current/datatype-json.html)。网页不代替锁定驱动及实库证据。

## 持久消费者与类型映射

下表是迁移范围，完成与否只以STATE/handoff为准。表结构以实库只读快照及源码迁移双向核对，不能按历史27表数量判断覆盖。

| 事实/关系 | 全部表 | 直接适配器与必要迁移责任 |
|---|---|---|
| 接收/运行/问题 | `dh_m08_messages`、`dh_m08_runs`、`dh_m08_questions` | app `adapters/ledger.py`；完整身份/channel/message唯一接收、payload冲突、终态CAS与原响应回放；008增量DDL和010取消run/question唯一关系 |
| 会话/成员/outbox | `dh_m10_sessions`、`dh_m10_members`、`dh_m10_outbox` | app `adapters/cases.py`、`application/memory.py`、`adapters/milvus_memory.py`；session→question锁序、generation、回查归属/status/version、唯一question/version事件与lease token确认；010回填 |
| 操作/审批审计 | `dh_m15_operations`、`dh_m15_audit` | app `adapters/approval_store.py`、`application/approval.py`；session→question→operation锁序，批准/期限/版本/参数/SOP绑定、唯一领取、UNKNOWN查询优先；015 |
| 历史checkpoint | `dh_m15_checkpoints`、`dh_m15_checkpoint_writes` | tools `evaluation/legacy_checkpoints.py`只读reader；typed bytes、parent、pending writes、write_index语义；M25原样保留历史数据，M26已验证转换/恢复，实际范围见其handoff |
| 合成效果/调用 | `dh_m15_synthetic_effects`、`dh_m15_synthetic_calls` | tools `demo/synthetic_rights.py`、`demo/approval_prepare.py`；独立短事务登记调用，operation锁持有到effect提交，query不得错误ABSENT；非订单/券真实写操作 |
| 反馈/审核/租约/工件 | `dh_m18_candidates`、`dh_m18_reviews`、`dh_m18_tasks`、`dh_m18_derivatives` | app `adapters/feedback_store.py`、tools `evaluation/flywheel_store.py`；revision审核、有限lease/attempts、四路派生完整引用；018_flywheel |
| 完整发布/切换/引用 | `dh_m18_releases`、`dh_m18_active`、`dh_m18_run_releases`、`dh_m18_release_events` | app `adapters/release_store.py`；channel行锁+revision CAS、active/previous与event同事务、run/待审批引用阻止退役；018_release；不能改旧manifest/hash |
| 业务维护/证据 | `dh_m20_orders`、`dh_m20_activities`、`dh_m20_coupons`、`dh_m20_order_activities`、`dh_m20_evidence` | services `business_store.py`及`resources/020_business.sql`；Decimal、完整身份复合键/外键、更新版本与不可变call/evidence快照；两个MCP仍各自独立进程 |
| 观测关联 | `dh_m21_observation_links` | app `adapters/telemetry.py`；operation→trace/span持久关联；021；平台记录不参与业务事务 |
| P00只读探针 | `p00_probe` | infra旧备份/恢复与健康入口；保留源快照，明确登记为运维数据，非应用事实，不为凑表数塞入新运行仓储 |

其他持久消费者必须同时替换或显式保留为旧源读取入口：

- app `adapters/history.py`与`api/workbench.py`的身份/session分页与原消息回投；`bootstrap/mvp_runtime.py`、`event_runtime.py`、`release_runtime.py`的仓储装配，`mvp_cli.py`/`memory_cli.py`的迁移/窗口/投影命令。Redis/Milvus仍为派生数据，不修改Embedding签名。
- services `business.py`维护API/MCP的配置、证据source标识、独立池及启停；Retrieval服务仍消费原检索Port，涉及身份装配但不自行拥有应用关系表。
- tools `evaluation/evaluation_cli.py`、`mvp_acceptance.py`、`flywheel_cli.py`、`flywheel_assets.py`、`flywheel_validation.py`的SQL/仓储/完整发布资产消费；learning `workbench.py`、`workbench_protocol.py`、`workbench_analysis.py`、`workbench_evaluation.py`的审批、历史、outbox、审核及release查询。学习页面来源文字要与实际后端一致。
- tools `probes/{memory,event,cascade,governed_sop,approval,checkpoint,debug,fasttext,business,retrieval,observability,flywheel,release}_probe.py`；旧固定场景保留独立断言，实库装配/直接SQL须适配。`postgres_probe.py`的源盘点仅只读MySQL，不作为新运行后端。
- infra `client/settings.py`、`test-connections.py`、`learning-access.py`、SSH端口转发和连接/密码模板；`scripts/backup-mysql.sh`、`restore-mysql.sh`、`smoke-infra.sh`、`update-mysql-access.py`及`deployment/{start,install,backup,switch}.sh`。M25新增隔离PG迁移/备份入口；旧脚本留原路线，M30再切换或退役。根workspace/单锁、CI离线守卫、包资源与运行/工具指纹须检查。

| 原存储特性 | PostgreSQL转换规则与验收 |
|---|---|
| `VARCHAR`/`CHAR`及`utf8mb4_bin` | 原标识用text/限制长度的varchar及明确区分大小写排序，保留前导零；先盘点CHAR尾空格与原collation等价边界，不自动lower/strip |
| `DECIMAL(20,2)`、unsigned版本 | numeric(20,2)与Decimal；bigint配合法范围检查，源unsigned超bigint需显式更宽数值类型，不静默溢出 |
| `DATETIME(6)`/`TIMESTAMP(6)` | 源连接按UTC解释，目标timestamptz/UTC；保留微秒/NULL，不能给已带时区时间重复replace |
| MySQL `JSON`及规范化签名 | 签名所需原JSON文本/字节用json/text/bytea保存；原manifest/body签名原文与关系值分别核对。可检索投影另用jsonb，不能从jsonb重构原签名字节；`->>`替代JSON_UNQUOTE/JSON_EXTRACT |
| BLOB/`UNHEX` | bytea绑定原bytes；typed checkpoint和值类型/parent/write_index原样留历史，不通过字符串hex往返猜编码 |
| BOOLEAN/NULL | 原tinyint布尔显式转bool，NULL不转空串/0；金额、状态/版本与领域DTO一致 |
| AUTO_INCREMENT | GENERATED BY DEFAULT/ALWAYS AS IDENTITY；导入保留event/job ID并按实存最大值重置sequence，验证中断续跑及下一写入不撞号 |
| ON DUPLICATE KEY | 显式ON CONFLICT目标，DO NOTHING或EXCLUDED更新；原MySQL affected_rows与PG RETURNING不同，逐用例裁决，不能靠rowcount旧约定继续判断 |
| INSERT IGNORE/日期/JSON构造 | IGNORE只转换目标唯一冲突的ON CONFLICT DO NOTHING，不吞其他约束错误；DATE_ADD改UTC时间或interval绑定，CURRENT_TIMESTAMP(6)改PG精确时间；JSON_OBJECT改json_build_object/原规范化文本 |
| 驱动JSON/事务/异常 | PyMySQL JSON读取为文本，psycopg默认解为dict；原model_validate_json/签名消费者须显式`::text`或逐项适配，不能只改SQL关键词。两者均可用%s参数但参数类型/错误类/rowcount不同；池connection上下文自动commit/rollback，不能残留旧SELECT事务后复用 |
| 默认Repeatable Read | PG Read Committed用于短事务；唯一接收/CAS/锁序和FOR UPDATE重检守卫，outbox SKIP LOCKED、lease/version token确认；多表导出采用独立REPEATABLE READ READ ONLY快照，不将所有业务统一升隔离 |

异步池显式open/close、连接归还事务结束；Windows直接协议探针使用Selector Runner；应用池独立拥有Selector线程，正常Uvicorn/CLI保留Proactor对子进程的支持，不改变全局policy；启动前/服务端观察后取消与池关闭均已实测。驱动依据见[官方异步说明](https://www.psycopg.org/psycopg3/docs/advanced/async.html)、[官方池](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)。
