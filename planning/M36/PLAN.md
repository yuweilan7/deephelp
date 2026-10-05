# M36实施计划

基线：2026-10-05，规划分支feature/learning-experience-roadmap，HEAD f7bbc336b6ab0594b6b4ca71d2d075b95c8110c4。

**decision=DECOMPOSE；readiness=BLOCKED（等待M35组件/数据读取入口；M33完整课程已交付，尚未启动本M）。** [规格](../../docs/MODULES/M36_EXPLAINABLE_WORKBENCH.md)定义run投影、机制实验及完整学习场景三项特性。已存在原决策/账本/原生观测，未知字段覆盖须先核对，不能假设全部span完整。

## 这次做成什么、从哪里改

读者沿一条真实run理解诉求/资料/决策/等待/证据/回复，并能进入对应教程/源码。先读AGENTS/STATE/ROADMAP/M36及M33/M35实际handoff；直接基础证据读M28/M29/M31。核对M32迁移后的application/debug、conversation、api历史/观测、Langfuse查询、tools.learning的analysis/protocol/evaluation、M35页面及当前CONTRACTS相关段。

允许增量修改上述只读投影/DTO/路由与tests、frontend对应features/教学内容入口、docs/learning、包README/必要部署说明；不新增第二套业务引擎、任意trace搜索、模型生成解释或回放后端。新增投影接口先设计来源/状态/有界查询并更新CONTRACTS，未观察字段明确unknown。

| 顺序/分支 | 内容 | 独立验收 |
|---|---|---|
| 1 feature/m36-run-explanation | 同run摘要、13段路径/实体归属/事实证据/真实时间线及失败状态 | 原数据来源可追溯，外身份先拒绝，读取0模型，不把旧trace当本次执行 |
| 2 feature/m36-mechanism-labs | 同候选四路检索、记忆/outbox、MCP双端、图恢复、generation/指标分母的可读页面 | 真实分支/分数/调用去重/UNKNOWN/不完整观测均可解释，与教程链接一致 |
| 3 feature/m36-learning-delivery | 完整场景引导、实际浏览器阅读核验、精确新发布/安装/恢复及清理 | 规格8场景逐项闭合；课程/页面/代码入口对应，个人云可直接使用 |

## 怎样证明

离线覆盖投影来源/版本、actual/unknown/预留、attempt去重、重叠耗时、前序恢复、权限/分页/平台缺失、取消/UNKNOWN和UI状态；与实际账本/同run Langfuse数据逐项对照。故障和审批用固定动作证明协议/效果边界，不反复经过模型。

浏览器用已有适用真实run及必要固定新业务动作，走规格的正常查询、补充/更正、交错、拒识、重投、失败/取消/UNKNOWN、pause/resume、平台不可用8场景。浏览读取0模型；确需新内容时live前记录样本/预计调用/token/修复余量共享上限及不重验项。质量归于实际目标样本，不凭页面解释宣称Rerank收益或企业能力。

最终安装字节改变须新精确完整内容门禁与manifest，保留旧发布/恢复；核心策略未变不扩大为四组消融。验证关键组件/接口/包资源/静态指纹和必要相关Python/前端检查，命令在实际实现后记入运行入口，计划命令不当通过证据。

## 停止与交付

来源映射不可信、拟解释信息无观测、数字重复/时序错误、必要守卫/真实内容/安装失败时停止相关步骤；缺诊断不重发业务补数据。三个feature按AGENTS独立验收/中文提交/推送/合并/同步后连续完成同M。handoffs/M36.md写实际场景/来源/命令退出码/限制，STATE只记实际；回退已验安装与完整版本，不恢复陈旧业务库。临时截图/JSON副本按消费者清理，整个M完成停止。
