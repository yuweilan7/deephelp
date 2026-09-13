# 模块目录：按需读取，不是运行时模块

阶段计划不自动注入每个实施会话；只读当前模块和直接前置交接。每个文件保留原模块专属范围/失败案例，公共底座已去重；旧PG/环境/单pyproject约束显式修订，详见 docs/DECISIONS.md。

P00 已有历史验收，入口改为 handoffs/P00.md，不提供旧环境重装指令。

| 模块 | 范围 | 直接前置 | PDF文件页 |
|---|---|---|---|
| [M00](modules/M00_ARCHITECTURE.md) | 源文档核对、系统边界与架构章程 | 无 | 15–24、31–35、55–69、71–76 |
| [M01](modules/M01_PYTHON_ASYNC_SKELETON.md) | 最小异步能力、工程骨架与测试底座 | M00 | 18–24、69–71 |
| [M02](modules/M02_CONTRACTS_DATA.md) | 领域契约、意图目录与从第一天开始的评测 | M00, M01 | 25–30、51–54、55–66、71 |
| [M03](modules/M03_MODEL_GATEWAY.md) | 模型与 Embedding 网关、结构化输出和调用预算 | M01, M02 | 16–18、21–24、35–41、67–69 |
| [M04](modules/M04_TEXT_ENTITY.md) | 文本清洗、并行实体提取与规则前置 | M02, M03 | 19–24、31–35、53 |
| [M05](modules/M05_DENSE_INGEST.md) | 意图语料导入、Milvus 数据模型与 Dense 基线 | P00, M02, M03 | 25–30、35–40、70 |
| [M06](modules/M06_MCP_MOCK.md) | 真实 MCP 协议与可控下游 Mock | M02 | 16–18、67–69、74 |
| [M07](modules/M07_SOP_MINIMAL.md) | 最小可配置 SOP 与有界 ReAct 执行器 | M02, M03, M06 | 67–69、73 |
| [M08](modules/M08_MVP_INTEGRATION.md) | 第一个可运行纵向闭环与阶段验收 | P00, M04, M05, M07 | 19–24、31–40、67–74 |
| [M09](modules/M09_HYBRID_RETRIEVAL.md) | 中文 BM25、Dense/Sparse 融合与检索对照 | M05, M08 | 35–41、53、70–71 |
| [M10](modules/M10_MEMORY_LIFECYCLE.md) | 多层记忆、Question 生命周期与状态事实源 | M02, M08 | 55–66、69 |
| [M11](modules/M11_EVENT_CLUSTER.md) | 多轮事件聚合、补充消息归属与上下文构建 | M04, M09, M10 | 55–61，重点 p57–58 |
| [M12](modules/M12_CASCADE_PIPELINE.md) | 四阶段意图决策与完整 13 段 Pipeline | M04, M08, M09, M10, M11 | 19–20、31–35、53、57–60、69 |
| [M13](modules/M13_FASTTEXT.md) | FastText 本机训练、量化、拒识与兜底接入 | M02, M09, M12 | 41–52、53–54、71 |
| [M14](modules/M14_SOP_GOVERNANCE.md) | SOP 配置治理、工具边界与场景扩展 | M07, M10, M12 | 25–30、67–70、74 |
| [M15](modules/M15_DURABILITY_APPROVAL.md) | 持久执行、人工审批、幂等重放与故障恢复 | M10, M12, M14 | 57–60、62–69、74（恢复细节为工程补充） |
| [M16](modules/M16_OUTPUT_DEBUG.md) | 事实约束回复、全链路追踪与三类调试视图 | M09, M11, M12, M15 | 7–12、55–61、69–71 |
| [M17](modules/M17_EVALUATION.md) | 500+ 评测集、消融实验与发布门禁 | M13, M16 | 41–54、70–74 |
| [M18](modules/M18_DATA_FLYWHEEL.md) | 有审核的数据回流、三路增量与可回滚发布 | M17 | 53–54、70–71、74–76 |
| [M19](modules/M19_DEPLOY_FAULTS.md) | 受限部署、容量控制、备份恢复与故障验收 | P00, M15, M17, M18 | 18、53、69–75（硬配额和故障协议为工程补充） |
| [M20](modules/M20_LOGISTICS_TRANSFER.md) | 迁移到物流场景、能力证明与展示材料 | M17, M19 | 全篇机制；物流场景为新的工程设计 |
| [M21](modules/M21_AGENTSCOPE_COMPARE.md) | 可选：AgentScope SOP 执行器与框架对照 | M07, M15, M17, M19 | 67–69；框架比较依官方现行文档 |

M08 先交付真实纵向MVP；M09/M10之后再深化多轮，M13训练、M15恢复、M17评测、M19故障与发布。M21可选。前置边描述开发依赖，不是运行时图，不能把有界ReAct回路加到开发DAG中。
