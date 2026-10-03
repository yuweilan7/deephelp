# M17 实施计划

判断：DIRECT。基线 `f646c87`；前置M13/M16已交付。

1. **这次做成什么**：交付一个独立冻结数据审计、四组增量评测和发布判定特性。输入公开合成单消息/会话，输出指标、混淆矩阵、逐条错例、硬门禁和调用差异。旧排错数据明确为regression；新test首次运行后如用于改代码，登记污染。500+扩量是后续单独特性，本次不启动M18。
2. **从哪里改**：新增evaluation数据/manifest、评测器/CLI及专项测试；复用唯一DTO、原13段Conversation、真实模型/Milvus/MySQL/MCP、FastText产物及M16观察。四组使用同一Hybrid语料、同一模型/Prompt/SOP/预算，依次Dense、Hybrid、Memory/EventCluster、FastText。无新依赖/迁移/锁变更。
3. **怎样证明**：离线全量明确为固定适配器的编排验证；真实固定抽样分别跑四组和持久回读/零调用重放，金额/对象/证据/工具参数逐条核验。报告accuracy/macro-F1/Recall@K、实体、事件、澄清/接管、工具、完成率、P50/P95及真实调用/token/估算费用；空分母用null及原因。执行ROADMAP完整检查，特性与main均复验。
4. **什么时候停**：使用原专用依赖和模型候选，开发前health与内容探针；累计调用/token/费用上限与复验余量只留.local运行状态，单请求90秒/40次/2重试，整轮1800秒。不可恢复真实服务问题或硬门禁失败停止合并。测试正确性问题修复后保留失败证据，污染的test改称regression，不据test调参。
5. **怎样交付**：`feature/m17-frozen-evaluation`；审查/验收后按AGENTS提交、推送、合并并将两分支同步到同一远端SHA，最终干净main。更新应用运行入口、M17规格/契约、handoffs/M17和PROJECT_STATE；完整500+及企业质量保持未完成。
