# 10｜Dense、BM25、Hybrid、Rerank与最终意图

[上一章](09-agent-basics.md) · [课程入口](README.md) · [下一章](11-memory.md)

## 业务问题与目标

用户说“券用不了”，语料既有优惠也有券。如何找到资料、排序，再决定最终意图？目标是区分文档召回、文档重排、每意图归并和分类。前置为模型端口、DTO和唯一600。

## 普通话解释与Java类比

Dense将查询/资料编码为同一向量空间，用相似度找语义接近资料；Embedding模型、维度、预处理签名改变就要新集合/reindex。BM25使用词项匹配与词频/文档长度等统计，能补编号、关键词等词面信号；不是另一种主意图模型。Hybrid融合同一查询的两路排序。向量检索类似候选索引，BM25类似搜索引擎词项索引，但不是SQL精确主键查询。

Rerank把查询和已经召回的文档一起评分，重新排序，不能找回没进候选集的文档。当前路径是宽召回N→重排全部N→每意图取最佳证据→最终K；提前裁成K或先把每意图合成一条会丢可重排的证据。分数带score_kind：cosine、bm25、fusion、rerank量纲不同，不能当统一置信概率。

600的Cascade使用规则、当前已确认问题、检索证据及必要模型判断给最终意图/拒识。最相似文档所属类只是候选，未知物流不能因为语料里一定存在某个“最近”样例就强制选择它。

## 实验与预期

离线、固定合成向量与重排端口、远程模型0次；复用生产HybridRetriever，不证明模型效果：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course retrieval
```

预期candidate_ids为discount-a、discount-b、coupon；embedding_actions=1、rerank_actions=1；前三路的最佳意图证据为discount-a/coupon，重排后为coupon/discount-b；raw_scores_preserved=true。fusion_example约0.872，只是排序分数。

按[retrieval_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/retrieval.py)读：

1. 创建完整HybridScope，签名是明确的合成二维空间，不混入正式1024维集合。
2. 三份DenseHit携带raw_dense/raw_sparse/fusion_score/matched_sources，不只写“成功”或分数。
3. compare只获取一次查询向量，再传给同一Store.routes，三路共享候选上限。
4. FixedReranker故意把coupon排前、discount-b排在discount-a前，返回原文档的index。
5. rerank_result映射回本地不可变文档，保留融合原分数；按排序先后每意图取一份，再取K=2。

```mermaid
flowchart LR
    Q[查询] --> E[一次Embedding]
    E --> D[Dense]
    Q --> B[BM25]
    D --> H[Hybrid宽候选N]
    B --> H
    H --> R[同一候选全量Rerank]
    R --> G[每意图最佳证据]
    G --> K[最终K]
    K --> C[600最终确认或拒识]
```

## 当前源码与证据

[hybrid.normalized_fusion/result_for/HybridRetriever.compare/rerank_result](../../modules/deephelp-app/src/deephelp_app/application/hybrid.py)分别处理分数、每意图归并、共享查询和重排；[DenseHit/RerankPolicy](../../modules/deephelp-app/src/deephelp_app/domain/models/retrieval.py)要求来源/范围，top_n必须等candidate_budget。[cascade](../../modules/deephelp-app/src/deephelp_app/application/cascade.py)负责最终确认，不把排序名次直接当分类概率。

正式Milvus端见[milvus_hybrid](../../modules/deephelp-app/src/deephelp_app/adapters/retrieval/milvus_hybrid.py)；独立服务见[Retrieval backend](../../modules/deephelp-services/src/deephelp_services/retrieval/backend.py)。当前正式是否开启重排、历史内容通过与退化都以[PROJECT_STATE](../PROJECT_STATE.md)和[M31交接](../../handoffs/M31.md)为准，本章不改变策略或启用模型。

## 常见误区与反例

- 一类资料多就把分数加总会偏向资料多的类，当前取最佳证据。
- “Embedding维度一样”不等于向量空间一样，签名含模型/供应商/预处理等字段。
- 0.9融合分数不是90%正确率；拒识门限需要固定内容样本与校准证据。

## 练习与答案

预测先只保留discount-a/coupon再Rerank，是否还能选discount-b？只把top_n从3改1呢？

答案：被裁掉的discount-b无法恢复，实验要先保留全部3份。top_n=1与N=3不匹配，RerankPolicy拒绝。正确扩大N也需要实际覆盖/延迟/预算验证，不能拿本合成排序证明真实质量提升。

## 语料作者、导入与审核（M44）

在“识别与检索”读取工作区，新建候选时选择当前目录叶子标签，填文本、split、来源说明和来源/近义组。保存后读取真实revision；编辑已审核记录退回DRAFT。删除只影响作者候选。PG按owner+目录隔离，短事务锁内核对revision、完整校验后提交；模型等待不占此锁。

JSONL沿用CorpusRecord；CSV用冻结字段名或字段→表头映射；Excel用字段→A/B列映射，单工作表、文本单元格，公式拒绝。必需字段doc_id/content/intent_code/split/source_group/variant_group/synthetic；v3还须registry_version=complaints-v3，来源说明可写metadata.source。先预览逐行修正错标签、缺列、重复ID/跨split近重复，再明确导入全部行；之后人工核对并填写理由批准所选记录。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course corpus-authoring
```

预期父标签INVALID_ARGUMENT、失败导入0行、预览0条草稿、导入DRAFT、审核APPROVED，模型0。实验复用纯变换，不证明PG持久协议。Java可类比聚合version乐观锁及单事务批量写；数据库事务不与前端编辑时长绑定。

常见错误：dev/test进入业务索引、同义改写冒充独立split、高置信自动金标、响应超时直接重投。练习：10行中第7行父标签，保存几行？答案0。提交响应丢失时先读同preview_id的IMPORTED/imported_revision对账，不能清零revision绕过冲突。
