# M31 实施计划

判断：`PROBE_FIRST`。就绪：`READY`（仅首特性；已有M03/M09/M12/M17/M18/M20/M22，独立于数据库/观测迁移）。规格见[M31](../../docs/MODULES/M31_RERANK_INTEGRATION.md)。

1. **这次做成什么：** 首特性给qwen3.7-text-rerank增加专用Rerank端口/DTO与httpx适配，证明请求/响应映射、内容、usage和预算/取消。后续按序为召回后重排/同条件质量对照，再接Retrieval MCP/学习页/正式发布；不把三候选接口测试当主链交付。
2. **从哪里改：** domain models/ports、gateway/AsyncCalls预算及相邻适配测试；只查本机该候选。先读M03/M09/M20直接handoff及现有hybrid/retrieval_protocol代码，`feature/m31-rerank-protocol`从同步main建立。后续需核对本地/远端检索、签名profile/allowance/validator、600、M17/M18和M22消费者，按实际接口变化更新CONTRACTS。
3. **怎样证明：** 首试验离线覆盖错index/重复/缺项/NaN、重试/usage、超时/取消；真实选2–4条query及reference候选读回内容，实施前按真实文档长度列调用/token范围与含修复余量的共享上限。后续固定同候选/同向量对照、独立dev/test和代表会话+远端MCP证明模型实际调用；不开全量历史live，不混淆召回扩大与重排收益。
4. **什么时候停：** 鉴权/服务不可恢复、内容映射或必要效果验收失败即停；不自动换模型、默认开启或改active。索引/分数/签名错误拒收，普通不可用fallback须版本绑定/可见。首特性不做PG迁移/Langfuse部署，重排也不改变唯一600/审批账本边界。
5. **怎样交付：** 首特性handoff/STATE仅列端口和实际小样本证据，仍标主链未接入。按AGENTS中文提交/推送/合并main并同步后停止；依次更新此PLAN启动后续接入/质量与正式发布特性。M28前后用现有/新观测接缝，不新增第二套引擎。
