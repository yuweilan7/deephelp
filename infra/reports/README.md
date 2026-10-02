# 本地报告输出

此目录仅保留本说明。服务器/合成验收脚本生成的日志、JSON、CSV 与版本锁由根 `.gitignore` 排除，避免将内部地址、诊断原文和过时结果放进公共上下文。只读客户端 health 默认通过隧道入口写到 `.local/infra-health/`。

原 P00 报告移到 `.local/archive/context-cleanup-20261002/retired/infra/reports/`，归档清单记录 SHA-256；公共仓库可从清理前提交 `e042c57162304b72739573f6227ed3a154e71428` 追溯原文。历史结果的用途与限制见 [P00 handoff](../../handoffs/P00.md)，当前状态见 [PROJECT_STATE](../../docs/PROJECT_STATE.md)。
