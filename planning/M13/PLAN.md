# M13 FastText 训练、量化与统一兜底

判断：PROBE_FIRST → DIRECT。基线 `f77b166`，特性 `feature/m13-fasttext-fallback`。

1. **这次做成什么**：在 Windows/Python3.14.7 真正训练、量化、回读小型 FastText；同一600 FallbackPort可选模型，低把握/未知/多诉求仍进入既有强模型。公开合成组按来源/改写组隔离，训练/dev/test分栏；完成关闭/原版/量化对照及原子指针回退，默认关闭，不宣称小组合成数据泛化质量。
2. **从哪里改**：应用依赖及唯一根锁增加经探针验证的 fasttext-community0.11.8、Jieba0.42.1；共享预处理、训练/校准/评测CLI、manifest/指针、唯一领域DTO的可选诊断与版本、M12适配器和显式live装配。无迁移，不改Python、infra、Embedding空间或主分类入口。
3. **怎样证明**：绑定探针已在本机保存/量化/重新加载/预测通过。公开固定样本审计组/文本泄漏，dev仅选配置/门限；真实原版与量化test全量留错例，真实强模型对照、同一HTTP业务链事实/重放及工具零调用守卫。未知标签/非法code、损坏/签名、空/OOV/编号、低把握、取消/预算、回退和旧消费者离线回归，特性/main均执行ROADMAP检查及必要真实复验。
4. **什么时候停**：训练最多两组候选，单组模型矩阵估算32MiB、10线程、epoch≤120、bucket≤8192、dim=20；每次CLI有外层超时。绑定0.11.8的单线程初始化异常已实测，固定10线程并检查全部矩阵有限性，记录seed=42而不承诺逐字节重现。模型API共用.local/m13累计上限/deadline/有限重试，额度/余额授权遵循AGENTS；不可恢复依赖停止合并，报告保留。
5. **怎样交付**：一份handoffs/M13.md与PROJECT_STATE写实际证据，运行命令在应用README，模型、原始报告/预算留.local。验收后按AGENTS提交、推送、no-ff合并、main复验、同步两个分支SHA并回到干净main；不启动M14。
