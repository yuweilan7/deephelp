# M44 实施短计划

2026-10-06；DECOMPOSE，基线 main c881cb8。F1/F2作者/PG/冻结/实际候选索引/同向量内容/原MCP协议/浏览器/课程/打包已验，证据见handoffs/M44。继续F3，一主写者，不启动M45。固定16查询候选与正式召回一致，Dense有标签6/6、Hybrid/BM25 5/6；最终分类与双端业务尚未验，不伪称候选改善。

1. **草稿/导入审核**：复用 CorpusRecord、原 JSONL/CSV/XLSX 有界解析与 BrowserAuth，在 application 放纯校验/状态变换、PG adapter 放owner/revision事务，在原识别与检索页面放编辑、分页、映射预览、逐行诊断、明确导入/审核/导出。新增044表仅保存作者资料/审核，不改变业务事实。中文解释聚合CAS、split泄漏、来源与事务；10章及course复用同核心。固定反例/HTTP同源、真实PG竞争与重载、实际浏览器、相关静态/前端/打包检查后交付。
2. **候选索引/比较**：审核记录冻结为独立候选，复用原Embedding/Hybrid索引、LearningJobs和累计writer；核对原可续跑实现后补必要中断记录。同实际向量的三路/配置Rerank及候选/正式版本在原页面显示。真实固定查询与双端MCP语义、取消/重载/不重复向量证明；10章/源码导航同步，保护正式与旧集合。
3. **发布/反馈**：复用原反馈run采集/审核及完整ReleaseManifest/prepare/PG CAS，绑定作者审核/冻结字节和门禁，提供版本diff与改善/退化。固定样本在调参前冻结，实际候选内容、正式精确安装与旧安装恢复，历史run/审批不变。与原发布页和课程连通。

F3当前落点：原m18-collect隔离身份采集新固定run，浏览器人审corpus→作者草稿再审核→新冻结版本；当前policy/权重保持，用原16同向量观察及8 test两版本分类，并用原catalog_probe验证六叶子+2未知的实际PG/HTTP工具事实。原资产增加corpus_binding（审核/索引/split/门禁闭包），ReleaseManifest旧格式/hash保持；完整字节守卫及PG提交时原反馈revision锁共同放行。新LearningJobs动作corpus_gate沿唯一writer/取消恢复。原lineage保存精确来源与衍生hash，不接管旧三路构建租约。新增Python讲解、同页diff/质量/发布与10章实验同步。

F3 live在调用前核对当前安装/原审核配置，累计M44预算275次基础上max_calls一次调整900（含Milvus读回及安装/恢复RPC），max_tokens600000/max_cost5和原累计/未知预留保持。预期新增模型40–60请求、输入2万–8万、输出含推理1万–6万，有限修复余量；不因FAIL重复加预算或改盲测。源码先形成干净commit，再精确内容/打包/安装；最终文档不冒充新运行字节。

真实依赖前读LOCAL_SETUP、DEPENDENCIES和所需模型摘要；每次live先列明确样本/调用/token及修复余量上限。同会话同事实路径复用证据。未配置Rerank显示未运行，语料召回不升级为业务事实。

每feature独立feature分支验收、中文commit/push、main no-ff保存、六SHA同步后继续。handoffs/M44与STATE记录必要最终证据/未验范围；本轮临时资源在Temp或.local/tmp/m44，退出后精确清理。完成PLAN被规格/handoff覆盖后删除。
