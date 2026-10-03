# M12 完整聚合与意图级联

判断：PROBE_FIRST → DIRECT。基线 `82c934e`，特性 `feature/m12-cascade-pipeline`。

1. **这次做成什么**：在现有 Conversation 中接通自动事件归属和规则→当前 Hybrid→已确认事件记忆增强→统一 FallbackPort；交错的优惠/券问题补槽后查询正确对象，歧义澄清、未知转人工、缺槽位工具零调用。FastText disabled；不启动 M13、不加审批/业务写。
2. **从哪里改**：复用 M04 TextEntityResult、M11 EventAggregationService、M10 接收/CAS/outbox、M09 意图集合、M07 SOP。增量 DTO/计数、600 策略、同一13段主链和 live assembly；配置、固定 dev/回归样本、probe及消费者测试。无新依赖、根锁变化或迁移。
3. **怎样证明**：先 health、flash四能力/max真实任务schema；dev仅校准分数种类独立的阈值和差距，冻结来源及选择，回归不参与选参。离线验证每层命中/下探、未知code、实体反证、预算/取消、图与短路、重放、旧MVP。真实HTTP验证三类事实、自动交错补槽/更正/歧义、MySQL回读/幂等及真实模型/Milvus/MCP；main同入口最小复验。最后执行 ROADMAP 全部检查、锁/打包及文档入口核对。
4. **什么时候停**：只用现有专用环境及合成数据，所有模型/外部调用共用 `.local/m12` 累计上限、deadline、有限重试；余额授权沿 AGENTS。额度不是不可用；可修复解析/断言先保存脱敏结构并修复。不可恢复的鉴权/网络/服务/能力故障停止推进和合并；套餐/购买/大批量调用另需授权。
5. **怎样交付**：通过特性验收后按 AGENTS 提交、推送、no-ff合并、main复验、推送及两个分支SHA核对，回到干净main。一份 handoffs/M12.md 与 PROJECT_STATE 记录实际证据，命令放应用 README，原始日志/运行上限留.local。

验收记录：特性完整15条真实HTTP消息/165项检查全通过，四层实际命中；旧MVP十条真实内容回归通过。当前与增强dev分开校准，未使用test选参；全工程627 passed/1 skipped，42项新增M12用例。配置/决策表/图与必要证据见规格及handoffs/M12.md；不启动M13。
