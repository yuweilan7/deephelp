# 本地 Codex 会话只读读取入口

适用于 Windows ChatGPT 客户端中的本地 Codex 会话审查。复用技能名：`codex-local-session-reader`。用户级标准安装位置为 `$CODEX_HOME/skills/codex-local-session-reader`；未设置 CODEX_HOME 时通常是目标用户的 `~/.codex/skills/codex-local-session-reader`。

支持这一技能目录的本地 Codex 客户端可发现它，也可显式调用 `$codex-local-session-reader`。其他 agent 不一定自动加载技能；直接阅读该目录的 SKILL.md，或按本页独立执行官方流程即可。新安装若未进入当前会话技能目录，在后续新会话检查发现结果，不为此重启正在工作的客户端。

## 使用现成只读脚本

先确定目标桌面用户的 CODEX_HOME，勿把沙箱账号的 USERPROFILE 当目标用户目录。以下变量表示经过核实的目标位置：

```powershell
$taskCodexHome = $env:CODEX_HOME  # 必须先确认属于目标桌面用户
$taskSkillRoot = Join-Path $taskCodexHome 'skills\codex-local-session-reader'
$taskReader = Join-Path $taskSkillRoot 'scripts\read-sessions.cjs'
node $taskReader --help
node $taskReader --discover
node $taskReader list --title 'M10' --cwd (Get-Location).Path
node $taskReader read --thread-id '<列表中已核实的ID>' --metadata-only
node $taskReader read --thread-id '<列表中已核实的ID>' --last-turns 1
```

Node 不在 PATH 时，用系统上已核实的 node.exe 完整路径。脚本从其安装位置推导 CODEX_HOME，支持显式 `--codex-home` 和 `--codex-exe`；只输出结果，不保存全文。标题是大小写敏感子串，可用短片段处理标题空格差异。只读授权仍须覆盖目标会话，不搜索不相关私人资料。

## 不依赖技能的官方流程

1. 若已有官方本地会话只读工具，优先使用。工具未暴露不意味着本机会话不可读。
2. 定位客户端自带 codex.exe：已知路径存在时先复用；失效再读取运行中 codex 进程的**可执行文件路径**，或目标用户本地应用数据目录下 `OpenAI/Codex/bin` 的直接子目录。不要读取命令行中的敏感参数，也不全盘搜索或安装另一版本来替代。
3. 为临时读取进程选择正确 CODEX_HOME，运行 `codex app-server --stdio`。只创建读取进程，不启动/重启共享 daemon，不恢复任何任务。
4. 按行发送 JSON-RPC：`initialize`（clientInfo 含 name/version），收到成功响应后通知 `initialized`。接着 `thread/list`，限定 searchTerm/cwd，显式包含 cli/vscode/exec/appServer/unknown 来源，并使用 `useStateDbOnly=true` 避免默认扫描修补元数据。
5. 核对目标标题、目录和 ID，再调用 `thread/read`。仅需配置时 `includeTurns=false`；需公开消息时 `includeTurns=true`，只输出 userMessage/agentMessage，过滤隐藏推理、工具原始项与不相关内容。
6. 关闭并仅结束自己创建的临时读取进程。不要使用 resume、start、steer、interrupt 或发送消息来测试读取。

## 必须保留的边界

**独立 stdio 返回的 notLoaded 和历史 turn 的 interrupted 不代表桌面实时状态，不能据此停止、恢复或自动推进任务。** 脚本输出 `liveDesktopStatus=false`。实时判断需要正在运行的客户端官方接口或明确用户观察证据；没有则报告未知。会话结束也不自动证明项目已验收、合并或交付。

沙箱内握手超时不等于权限拒绝。正确用户目录的只读请求可能需要正常沙箱审批；获准后再试，拒绝则停止，不改权限、不挖数据库、不换入口绕过。精确报告超时、接口错误、未暴露工具或明确拒绝，不猜鉴权/版本原因。

云端任务列表为空不能否定本地定时任务。CLI 没有 Scheduled 管理界面；调度配置/最近 heartbeat 消息需另行获准读取，并核对新旧规则。此说明和技能不授权修改项目、定时任务、调度状态或发送推进指令。

官方来源：[App Server 协议](https://learn.chatgpt.com/docs/app-server)、[Scheduled](https://learn.chatgpt.com/docs/automations)。协议变化时核对已定位 CLI 的帮助及官方文档，不读取凭据或隐藏模型推理。
