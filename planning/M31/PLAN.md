# M31 后续实施计划

已交付专用端口和同链核心/冻结dev-test对照，证据归[M31交接](../../handoffs/M31.md)，不作为待执行步骤。只剩下方特性，由同一会话、同一主写者继续。规格见[M31](../../docs/MODULES/M31_RERANK_INTEGRATION.md)。

1. **下一项：远端、学习页与正式发布。** 冻结N=6/default/fail_closed，最终top_k=3；dev排序改善、test总体持平且test-15退化，不默认全量开启。核对RemoteRetriever/服务端同一策略、profile/delegation/allowance/receipt/validator、唯一600、M17/M18和M22消费者；默认关闭保持原协议结果，明确开启的受控发布与四路学习对照实际重排一次。真实代表会话、历史零调用及同条件评测说明有效/退化范围。离线验证签名/预算/关闭/故障/取消/幂等，实库固定动作单独验证持久协议；形成新的完整ReleaseManifest并通过适用内容门禁，保留既有active及恢复闭包。M31全部特性交付后停止。

每项从同步main另建feature，一个主写者；真实验收前列目标、样本、调用/token范围与含修复余量的共享上限。不可恢复的鉴权/网络/能力、必要内容失败或预算不足按AGENTS停；旧接口样本不当主链/企业效果证据，不默认换模型、迁库或部署Langfuse。
