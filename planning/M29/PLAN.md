# M29 剩余实施计划

判断`DIRECT`，前置`READY`。报告映射已交付，成果及有效证据见[M29 handoff](../../handoffs/M29.md)，不重复实施。同一主写者继续模块全部范围，完成后停止，不启动M30。

从同步main建立`feature/m29-prompt-review-release`：选择现有意图system Prompt，核对变量/schema/参数与多轮输入，逐字节比较最终请求，再接不可变Langfuse版本→本地hash工件→原ReleaseManifest/运行装配；运行不请求平台标签/文本。审核只将明确候选revision的平台人工annotation作为原M18 review的依据，显式确认、留出隔离/独立标签及CAS保持，高分不自动批准。

实际版本发布/回退走原精确内容门禁及单一active/previous。按行为变化选择必要真实样本，声明共享预算与不需重验范围；平台label改变或停用不能改变已发布请求。验收全部在特性分支，中文提交/推送/合并/六SHA同步后回干净main。只保留规格/handoff未覆盖的必要设计；模块结束清理重复PLAN并修正链接。
