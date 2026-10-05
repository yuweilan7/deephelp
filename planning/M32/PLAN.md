# M32实施计划

实施基线：2026-10-05，同步main 983ff33；工作区干净。前两特性已验收交付，证据见handoffs/M32.md，继续同一主写者实施第3项。当前正式安装/模型见PROJECT_STATE，规划不改变服务/数据/验收状态。

**decision=DECOMPOSE；readiness=READY（最终清理特性离线实施）。** [规格](../../docs/MODULES/M32_CODE_ORGANIZATION.md)的三个特性按序由同一主写者交付。目录/类型/CLI依赖已存在；待验证的是迁移等价性、可删对象当前引用及本轮离线基线，不是已验收结论。

## 这次做成什么

主链职责可定位、类型唯一、适配器/工具按子系统归组；实际删掉有证据的废文件与冗余用例，保护运行/恢复/审计。范围外：换模型/语料、业务策略重写、云数据/旧恢复卷删除、框架重建。

## 从哪里改

先读AGENTS/STATE/ROADMAP、本规格和M19/M30/M31直接handoff；按迁移需要读当前domain/models.py、domain/ports.py、application/conversation.py/event_cluster.py、adapters、bootstrap、三包入口/pyproject、相关tests。接口不变时不全读CONTRACTS；发现公开接口变化才补对应段落。

| 顺序/分支 | 改动及允许路径 | 独立交付条件 |
|---|---|---|
| 3 feature/m32-evidence-cleanup | 三包tests、根/包README、docs/learning/README.md；经当前引用核查的精确忽略对象 | 冗余断言有保留映射；实际删除/暂留理由明确，入口/资源/链接无失效 |

根锁只在真实依赖改变且有必要时改，不为目录迁移重建依赖；共享文件只由当前主写者写。每项后更新同一PLAN的剩余步骤，不能停在“盘点完成”。

## 怎样证明

先获取适用离线基线和pytest收集/公开入口清单。使用现有`py -3.14 -m uv run --locked ruff check conftest.py modules`、`ruff format --check conftest.py modules`、`mypy`，并按本次责任迁移选择相关pytest；共享DTO/装配影响较广时安排一次必要完整离线pytest。检查三包sdist→wheel/独立import、CLI帮助/离线实验、资源字节与runtime边界，不把旧基线故障藏在删测试里。

删测试逐项对应独立断言，删除文件核对动态/资源/发布引用及进程锁。演示原输入经同一主链得到原类型/事实/证据、缺槽工具0、取消资源退出、原receipt重投0调用。纯等价组织模型0次；发现行为变更先补专项影响/真实范围，不自动跑历史live。当前云版本不随本M源码交付自动替换；若明确增加新安装交付，先更新PLAN并做精确完整内容发布。

## 什么时候停、怎样交付

冲突、必要验证失败、引用/锁归属无法确认时停止受影响对象并保留现场，继续可独立确认的清理。真实工具拒绝不能绕过，说明精确动作；优先逐文件/空目录操作。三个特性各自按AGENTS验收/中文提交/推送/合并/六SHA同步，然后续下一特性；整个M完成停止。

handoffs/M32.md仅在实际实施后写迁移映射、保留断言/删除结果、实际命令退出码、安装/模型边界与未完成项；STATE只写实际结果。受控迁移可revert，保留旧完整发布及云安装；无引用、可重建的过程产物清理后不搬archive。完成后删除被规格/handoff覆盖的重复PLAN并修正链接。
