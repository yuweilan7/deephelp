# M28 实施计划

判断：`PROBE_FIRST`。就绪：`BLOCKED`（M26/M27未交付）。规格见 [M28](../../docs/MODULES/M28_NATIVE_OBSERVABILITY.md)。

1. **这次做成什么：** 首特性用原生SDK和真实审批图callback验证应用→两MCP、pause/resume与受保护查询关联。后续范围为13段/模型网关全覆盖和工作台替换；API/SDK能力先证实再细化。
2. **从哪里改：** telemetry接缝、httpx模型gateway、两个MCP客户端/服务、approval/官方saver、观测关联仓储、API与workbench；不改模型传输/预算/解析，不安装全框架patch或新LLM代理。`feature/m28-native-trace-proof`从同步main建立。
3. **怎样证明：** 首试验固定动作、模型0次；真实Langfuse读父子关系、分页及暂停/新进程恢复的可查前序，脱敏、并发callback隔离、外主体404、观测停用/取消和重复receipt无新generation。全覆盖特性才选实际受影响客诉检索/工具选择内容样本，live前列调用/token/共享上限与无需重验项。
4. **什么时候停：** 丢失关键跨进程/恢复关联、暴露敏感IO、重复用量或导出改变业务效果时停止。SDK仍需OTel依赖，不强删。现网Collector/Tempo暂留M30退役，不把不支持的Span Link标成通过。
5. **怎样交付：** M28 handoff/STATE列实际可见范围、未覆盖接缝与反例；合入main并按AGENTS同步后停止。后续业务覆盖/页面特性按首结果更新PLAN，用户另启。
