# M29 实施计划

判断：`DIRECT`。就绪：`BLOCKED`（M28未交付）；本模块为推荐学习扩展，可跳过而先做M30。规格见 [M29](../../docs/MODULES/M29_LANGFUSE_ENGINEERING.md)。

1. **这次做成什么：** 首特性将一份已有适用合成冻结子集/报告映射到Langfuse Dataset/Experiment/Score，逐行可回查、幂等上传；不新跑评测。后续Prompt版本/审核映射由用户另启，不预定模型范围。
2. **从哪里改：** 复用M17报告/评测器、M18审核/ReleaseManifest、M22飞轮与M28观测ID映射，在tools增加最小平台适配。正式运行不加载评测器。`feature/m29-langfuse-evaluation-mapping`从同步main建立。
3. **怎样证明：** 模型0次，平台API/浏览器真实读行/score、硬门禁/空分母、历史来源、原报告/hash、部分失败/取消/重复上传与越权。后续Prompt须核对确切请求及行为，使用不可变版本导出工件、M18门禁/active；按影响选择必要真实内容和新发布，标签变化不得热切线上。
4. **什么时候停：** 只能平均分而丢原逐例门禁、部分行未上传、无法绑定报告/资产或审核revision时停止；不无预算开judge、不让Langfuse成为第二个active事实源。
5. **怎样交付：** 仅标实际接入能力，M29 handoff/STATE与main按AGENTS同步；首特性完成停止。若跳过此M，M30报告Prompt/评测平台尚未接入，不妨碍基础设施退役。
