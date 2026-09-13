# 架构决策记录（ADR 汇总）

状态：M00 设计基线；任何后续改动都要写原因、受影响接口、验证和回退。不用“仓库优先”掩盖源码缺陷：仓库用于证明现状，规范变更在这里显式裁决。原图页码均为 78 页 PDF 的文件页码。

| ADR | 依据 / 冲突 | 本项目决定 | 验证和影响 |
|---|---|---|---|
| 001 关系库 | 启动包写 PG；实际 infra 是 MySQL，根 README 已采用该环境 | 业务事实源改为现有 MySQL；不增加 PG、不重写历史报告 | M02 设计 MySQL 事务/唯一约束/版本；M15 另验 checkpoint 后端 |
| 002 部署 | 启动包云端 4 GiB 试验/本机 Milvus；实际 compose 为 5 GiB 云端实验 | 保留现有拓扑与配额，应用和 Mock 默认本机；无权宣称长期稳定 | P00 历史报告不是现测；真实模型/大数据接入前复测；本机后备需另授权 |
| 003 Python 布局 | 启动包个别文字要求单 pyproject；已有 uv workspace | 单仓库、单根锁；业务首包 modules/deephelp-app，内部 domain/application/adapters；不按 22 阶段建 22 包 | M01 增量创建，不重建根工程；infra 保持独立 |
| 004 流水线 | p19 概述 12 步；p57 详细图 13 段 | 采用 p57 的 13 段顺序；早期 EVENT_CLUSTER 显式 passthrough | 步骤表和 trace 用同一标识；不把概述纠正成“原文从来只有13步” |
| 005 单次主分类 | p58 先聚合，p60 主意图服务由600统一进入 | 只有 INTENT_RECOGNIZE 调用主识别；内部多级尝试单独计量 | 聚合/槽位/SOP 不得再次调用主分类器 |
| 006 兜底顺序 | p31–35 与 p41–52 的 FastText/LLM 位置表述不完全一致 | 外层 Rule→Dense/Hybrid→Memory增强→Fallback；Fallback策略可配置，缺模型时禁用 | 不猜作者隐含顺序/阈值；对每层独立做命中和拒识测试 |
| 007 分数 | p40–41 的加权公式是示意；p52 最终code与top1不同 | 分数保留类型；非top1选择必须有经规则校验的 override 原因/版本/证据；否则拒收决策 | 不把 cosine/BM25/fusion/分类概率当同一confidence；不仿制矛盾输出 |
| 008 清洗 | p21–24 存在128字符限制和多层提取 | 保留原文与来源，先提关键实体；有界裁剪/分段，不能截丢否定和尾部订单 | 长文尾实体、金额、前导零、更正回归；阈值仅是源示例 |
| 009 向量 | p35–40 提到BGE-M3、1024、Float16；P00仅4维协议探针 | M05 用真实API向量与明确 signature，新集合；初版FP32是工程替代 | 同维不同模型仍不兼容；旧p00_acceptance不当生产意图集合 |
| 010 状态权威 | p55–69 分层记忆/状态维护；未给完整事务恢复协议 | MySQL账本+同事务outbox；Redis/Milvus最终一致，命中后回查归属与版本 | 不承诺跨库事务或外部exactly-once；失败修复见架构章程 |
| 011 审批恢复 | 原文SOP/会话处理，不足以定义可靠崩溃恢复 | 普通补槽与审批分入口；thread按namespace+run_id隔离；业务账本与checkpoint分开 | M15 做持久saver兼容试验、授权复核与未知结果对账；不能假定官方有MySQL支持 |
| 012 反馈 | p53–54 描述自动高置信回流 | 自动收集只进入候选区；审核后训练/入库/规则发布，保留冻结测试集 | 高分不是正确标签；近义组不能跨split；可回滚三路资产 |
| 013 公开资料边界 | 原PDF截图包含内部页面和真实业务标识 | 公共仓库只提交机制摘要、合成例子、文件哈希和页码；原件仅.local | 忽略规则不等于已跟踪秘密被清除；发布前检查差异 |
| 014 任务粒度 | 固定3–7个S把小变更拆成额外规划 | 使用DIRECT/DECOMPOSE/PROBE_FIRST/VERIFY_EXISTING；不按数量凑步骤 | 一个M默认主会话串行，测试随实现；旧S不必重生成 |
| 015 开放问题 | 原图ACTIVE过滤与新增WAITING_SLOT枚举可能不兼容 | 上下文候选包含等待补槽；审批等待只读上下文，不自动resume | M10/M11反例测试；实体更正和批准失效仍走版本校验 |
| 016 分期接口 | M01/M02类型、M08/M10账本职责有重叠或空白 | M02补全M01类型；M08最小账本，M10增量扩展，M15恢复 | 不重建已有接口/表；缺测试分层说明 |
| 017 事实与评测 | 自由文本校验和检索语料混用可产生假通过 | 关键事实模板化；train/reference与dev/test变体组隔离 | M05/M16/M17独立反例；不以模型自评或回归集充当未见效果 |
| 018 审批版本与回放 | S06逐事件走查发现“任何version变化失效”未区分计划变化与领取自身状态推进，且重复决定缺明确结果 | 绑定领取前计划；批准CAS与执行唯一领取分责；领取记录新version，重复决定鉴权后复用结果，外部更正不能撤销已发出效果 | CONTRACTS 0.1.2 / ARCHITECTURE v1.2；M02类型/示例、M15并发与崩溃测试；M00只做设计对照，不修改现有数据库；若运行探针不满足则禁用写入并再裁决 |

## 当前未定，不阻塞 M00 的事项

实际模型 provider/model/额度、真实 embedding 签名、应用依赖精确版本尚待对应模块探针。MySQL saver 候选必须审核维护状况、依赖、服务端版本、取消/恢复与迁移，不因名字带 LangGraph 就视作官方或已兼容。不得为解决后端缺口自动增加 PostgreSQL/SQLite 服务或自己造工作流引擎。

## 查证入口（工具/API 事实，不替代锁定版本试验）

- AGENTS 发现规则：https://learn.chatgpt.com/docs/agent-configuration/agents-md
- uv workspace：https://docs.astral.sh/uv/concepts/projects/workspaces/
- LangGraph persistence：https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- 社区 MySQL saver 候选（非官方保证）：https://github.com/tjni/langgraph-checkpoint-mysql

上述文档只说明能力与接入规则。本轮未安装/验证应用依赖，不冻结未经测试的API版本。
