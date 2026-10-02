# M08 实施计划

判断：PROBE_FIRST → DIRECT。基线 `ba5ee03638be525390eb0221fb6281c5fee9c401`，工作区干净，分支 `feature/m08-mvp-integration`。

1. **这次做成什么**：三类只读客诉从 `/converse` 贯通清洗/实体、显式单消息事件、600唯一意图服务、SOP、真实MCP与事实回复；MySQL接收和终结落账，同消息不重复执行。提供本机CLI/调试页。跨消息合并、Hybrid、审批和企业接口不在本次范围。
2. **从哪里改**：复用M04 processor、M05 DenseRetriever、M06 ToolGateway、M07 SOPExecutor和唯一DTO；新增主流程、意图策略、异步MySQL Repository、项目限定迁移、运行/验收入口。保留Python3.14.7、根workspace/单锁和既有infra；新增aiomysql依赖。
3. **怎样证明**：开发前现有云库health、选定chat原生工具内容及Embedding/Milvus探针；36条M02固定样本完整离线执行（报告实际差异），真实小子集验证三类、缺槽位、未知、失败/注入和数据库幂等/重启。检查13段耗时/调用数、真实ledger、事实及三表终态。两分支执行ROADMAP全量检查、锁/文档/差异检查。
4. **什么时候停**：专用合成身份/消息、`dh_m08_*`业务表、复用M05签名集合；本轮累计上限及原始报告留`.local/m08`，超时/有限重试共享预算。无法恢复的真实鉴权/服务/网络/能力问题即停止，不合并；正常模型解析问题先留结构并修复。
5. **怎样交付**：通过后中文提交、推特性、合并main并复验、推main、特性快进同步并核对两远端SHA；更新M08 handoff/STATE、运行文档及契约，冻结`mvp-v0`。完成后停在干净main，不启动M09。
