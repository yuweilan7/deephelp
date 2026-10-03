# M02 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：`DIRECT`。基线 `c1caabb5d3221b7f71459769d7e791719df67812`，工作区干净。
本次把类型、目录和固定样本作为一个可运行的离线契约验收特性；目录同时约束合法 code，样本直接消费类型，不拆成独立交付。

1. **这次做成什么**：发布 M03–M08 共用的 `0.2.0-m02` 类型，以及三类客诉目录和 36 条合成固定样本。演示“缺订单”输出 CLARIFY / WAITING_SLOT / 工具 0；有效工具结果必须带事实来源。范围外：主分类、SOP 执行器、模型、数据库、审批及业务写工具。
2. **从哪里改**：原位扩展 `domain/models.py`；新增目录、合成样本加载/校验及指标口径；复用 M01 预算、fake repository 和 API 错误边界。更新应用 README、CONTRACTS、验收矩阵、M02 规格定位、唯一 handoff / PROJECT_STATE。保留 Python、根 workspace 和 uv.lock，不新增依赖或迁移。
3. **怎样证明**：JSON round-trip、非法枚举/code、实体来源/更正、Decimal/前导零、非 top1 策略、缺槽位禁止工具、跨归属、同会话多问题、内存重复消息冲突和写工具禁用；校验样本/业务 fixture 引用、split 与 variant_group 隔离，提供离线演示入口。特性和合并 main 均运行 ROADMAP 的 Ruff check/format、mypy、pytest、git diff --check；检查相关文档链接。
4. **什么时候停**：本特性外部调用/token/费用均为 0。配置、凭据、云库及 P00 不参与；发现用户修改、冲突、验收或 Git 同步失败即按 AGENTS 停止交付并报告。不启动 M03。
5. **怎样交付**：`feature/m02-offline-contracts`，单一主写者。通过验收后中文提交、推送特性，合并并复验 main、推送；再快进特性并核对两本地分支、两跟踪分支、两远端 SHA，最后回到干净 main。handoff 只记录实际证据，未来功能保持未实现。
