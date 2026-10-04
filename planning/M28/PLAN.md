# M28 实施计划

判断：`PROBE_FIRST`。M26/M27已交付；首项真实关联试验通过，证据见[M28交接](../../handoffs/M28.md)。当前剩余业务覆盖与工作台特性，规格见 [M28](../../docs/MODULES/M28_NATIVE_OBSERVABILITY.md)。

1. **这次做成什么：** 顺序完成剩余13段/模型网关内容覆盖与原工作台替换：一个客诉检索路径、一个工具选择/回复路径，用户在原受保护观测页读取原生generation及真实usage/流程/检索来源。
2. **从哪里改：** 复用已交付原生SDK、官方callback、v2 observations游标及新PG登记表；补gateway实际发送/解析后的脱敏IO、主run登记、工作台来源/usage/前序展示。SDK公开接缝没有Span Link参数，恢复用独立trace及持久前序。`feature/m28-native-business-workbench`从同步main建立。
3. **怎样证明：** 首试验固定动作、模型0次；真实Langfuse读父子关系、分页及暂停/新进程恢复的可查前序，脱敏、并发callback隔离、外主体404、观测停用/取消和重复receipt无新generation。全覆盖特性才选实际受影响客诉检索/工具选择内容样本，live前列调用/token/共享上限与无需重验项。
4. **什么时候停：** 丢失关键跨进程/恢复关联、暴露敏感IO、重复用量或导出改变业务效果时停止。SDK仍需OTel依赖，不强删。现网Collector/Tempo暂留M30退役，不把不支持的Span Link标成通过。
5. **怎样交付：** M28 handoff/STATE列实际可见范围、未覆盖接缝与反例；各特性分别合入main并按AGENTS同步。首项后在同一会话继续业务覆盖/页面特性，无需用户另启；M28全部特性交付后停止。
