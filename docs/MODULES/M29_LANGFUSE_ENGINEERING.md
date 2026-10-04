# M29｜Prompt版本、评测实验与审核回流

**状态：** FIRST_FEATURE_LIVE_VERIFIED；报告映射已交付，Prompt版本/审核映射继续同一实施会话。依赖M28；推荐学习扩展，不阻塞纯基础设施迁移后的M30。

**前置：** M17/M18/M22 现行冻结评测、审核/资产/完整发布接口和 M28 observation映射。见 [计划](../../planning/M29/PLAN.md)。

## 把已有工程机制接到平台

Langfuse不只展示 trace；本模块学习原生 Prompt版本、Dataset/Experiment、Score 和人工审核入口。复用现有评测器、冻结样本、事实守卫和回流用例，不再建第二套分类/业务引擎，不把平台平均分代替发布门禁。

首特性把一份已有合成冻结子集和一次适用报告映射到 Langfuse Dataset/Experiment/Score。数据集项记录来源/split/组/turn、预期语义、版本/hash；run、observation、原报告及每行结果可相互定位。写入有确定性ID/幂等映射和重试状态；取消、部分成功、离线上传失败可恢复，不以“少了一部分行”报告全量成功。上传历史报告明确是历史导入，不称本次模型验收，不重发业务。

保留审批/越权/重放、实际 execute/query/effects次数与零效果闭合路径、缺槽位禁调用、金额/出处等硬门禁，空分母及未启用能力范围保持。非冻结在线数据进入候选区；人工标签和留出隔离复用原M18，trace/高分不能自动证明标签。平台 annotation/score映射到明确候选revision，不能绕过原审核/版本CAS。

## Prompt管理与发布权威

报告映射特性验收交付后，在同一会话继续接一份现有 Prompt：Langfuse不可变版本用于编辑/追踪，发布前将确切版本内容导出成受hash绑定的工件，纳入 M18 ReleaseManifest。运行始终消费已发布版本，不跟随可变 latest/production label在线热切换；标签用于编辑候选选择，不成为第二个active指针。

当前核心Prompt可能在源码中定义；先梳理格式变量、模型参数/schema/tool约束、模板引擎与多轮输入语义。纯格式搬运也核对最终请求等价；确有行为变化则做受影响固定样本/反例真实内容，再按M18形成新完整发布。Langfuse不可达不阻断已发布本地Prompt，也不退到未经验证文本。manifest绑定Prompt版本/hash、runtime/tooling与模型/策略，审批仍消费原资产。

## 验收与停止

先用已有真实报告/合成观察验证API+浏览器实际Dataset/Experiment/Score行映射、幂等重传、部分失败、硬门禁及审核拒绝。首特性模型0次，无新评测；不在平台UI启动无预算批量judge。Prompt特性live前按ROADMAP列行为变化、样本/调用/token及共享上限；LLM-as-judge如另启，仅补充软质量诊断，不能替代确定性事实/授权门禁。

页面复用M22评测飞轮入口，显示来源平台/报告/版本及未实现能力；旧报告不可改写。实际Prompt发布/回滚使用M18原CAS/active/previous，任意Langfuse label改变不改变线上版本。“完成M29”覆盖报告映射及Prompt版本/审核映射，逐项验收交付并继续，全部特性完成后停止；若用户明确跳过本模块，M30不能宣称Prompt/评测平台已接入。

依据：[Prompt版本](https://langfuse.com/docs/prompt-management/data-model)、[Dataset](https://langfuse.com/docs/evaluation/experiments/datasets)、[评测概览](https://langfuse.com/docs/evaluation/overview)。
