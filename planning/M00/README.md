# M00 六个实施/核对子 Prompt

现有文档已由本次交付生成，执行以复核/差量修正为主，不要求重写。完整规格在每个文件，可单独复制到新的Astra执行会话。

| 子任务 | 交付/文件所有权 | 前置 |
|---|---|---|
| [S01](S01_SOURCE_BASELINE.md) | 源图与实仓差异裁决 | 无 |
| [S02](S02_SEMANTIC_CONTRACTS.md) | 语义契约，不提前实现DTO | S01 |
| [S03](S03_ARCHITECTURE_BOUNDARIES.md) | 13段/目录/故障所有权 | S01 |
| [S04](S04_WALKTHROUGHS.md) | 三个走查与分层验收 | S02,S03 |
| [S05](S05_CONTEXT_TOOLING.md) | 文档工具和负面测试 | S01 |
| [S06](S06_ACCEPTANCE_HANDOFF.md) | 独立复核、交接与状态 | S02,S03,S04,S05 |

S02/S03/S05只有在独立工作树且路径互斥时可并行；否则串行。S06最后更新PROJECT_STATE。接收本次已归位交付时，可以直接以S06复核全部现有产物，有缺口再退回对应子任务；不能把“有文件”直接等同“通过”。
