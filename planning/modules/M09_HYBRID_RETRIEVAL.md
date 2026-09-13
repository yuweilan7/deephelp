# M09｜中文 BM25、Dense/Sparse 融合与检索对照

**阶段：** C 核心深化

**前置：** M05, M08

**来源：** 原 PDF 文件页 35–41、53、70–71。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
复刻原文最值得做对照实验的混合检索：Milvus 原生中文 Analyzer + BM25 Function 稀疏向量，与 Dense 结果融合。不能用向量搜索套个“混合”名字，也不能用字符串包含匹配假装 BM25。

## 版本和实现细节
检查锁定 Milvus/PyMilvus 对 text analyzer、BM25 Function、Sparse 索引与 hybrid_search/WeightedRanker 的真实支持。建立最小服务端能力探针；仅 Lite 单测通过不等于该能力成立。沿用 M05 的 dataset/model 签名，准备迁移/重建方案并遵守双版本峰值磁盘门禁。

中文分词必须可测试：领域词、订单号、SKU、优惠券、免息、收银台、否定短语和中英混合。是否加自定义词/停用词由当前支持接口决定；不能为“清洗”把 not/未/不 等语义词全部删掉。记录 analyzer 配置版本，导入与查询保持一致。

## 评分契约
原文写了线性权重示意，但实现不能把 COSINE 原值与无界 BM25 分数直接相加。核对当前 WeightedRanker 的分数归一化和权重语义，保存 raw Dense、raw Sparse、fusion score、rank、匹配来源。fusion score 不自动等于分类正确概率，不能照搬原文的 0.95 阈值。

比较 Dense-only、BM25-only、Hybrid 的统一候选输入输出；同一请求、同一语料版本、相同候选预算，明确是否允许 query reformulation。按意图聚合多样本证据，区分检索召回错误与最终决策错误。权重/阈值只在 dev 集调，test 集固定不参与调参。

## 对照和边界
构造两类有区分度样本：口语近义表达（应有利语义召回）和准确业务词/编号（应检查 BM25 价值）；加入两者矛盾、错字、OOV、多意图、无关问题。无需预设混合检索必然更好，必须允许某类退步，输出按类分析。

## 验收
接口真实执行 Dense、BM25、融合查询；同 query 三方案可并排输出；分词结果能检查；候选及分数可追踪；删除/版本切换后不会继续检索旧样本；召回用户历史时仍必须带 namespace/state 过滤。报告 Recall@K、MRR 或既定排序指标、后续分类指标、延迟与资源，不只展示两条成功截图。

交付 schema/迁移、HybridRetrieverPort、可复现 A/B 脚本、dev 参数选择记录和 test 报告。不要加第二套 Elasticsearch 或外部检索服务。为 M11 留通用相似候选接口，但不能让事件集合与意图集合混查。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M09.md`、`handoffs/M09.md`、`reports/M09/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
