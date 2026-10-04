# M23 个人云运行

一个应用进程提供同源工作台，Business/Retrieval 各一个独立 Streamable HTTP MCP 进程；MySQL/Redis/Milvus/etcd 和 M21 观测栈复用现有服务器。可选的第四个进程只演示合成权益与 MySQLSaver，不修改订单/券业务。服务仍只绑定服务器 loopback，通过严格 host key 的加密 SSH 端口转发访问；浏览器不取得 MCP、模型或数据库凭据。

## 构建与配置

在包含根锁的 Linux 构建目录安装 Python 3.14.7 / uv 0.12.13，运行根锁检查并构建三个包。构建目录是临时的，服务器版本目录只保留运行 wheels、根锁导出的依赖清单、启动文件和 SHA256SUMS。正式目录不包含源码检出、开发环境、完整 `.local`、PDF 或过程报告。

```bash
uv lock --check
uv export --locked --all-packages --no-dev --no-emit-workspace \
  --format requirements-txt --output-file /tmp/BUNDLE/requirements.lock --quiet
uv build --package deephelp-app --out-dir /tmp/BUNDLE/wheels
uv build --package deephelp-services --out-dir /tmp/BUNDLE/wheels
uv build --package deephelp-tools --out-dir /tmp/BUNDLE/wheels
```

将 requirements.lock、wheels 和启动文件加入 SHA256SUMS。传输缓慢时可以从已核对根锁哈希的离线 wheel 目录安装；临时构建依赖和开发检查环境不成为第二份运行锁。`install.sh ABSOLUTE_BUNDLE_DIR VERSION` 拒绝覆盖版本，实际核对 Python、哈希、三包、FastText 与 OTel 安装。先由管理员创建 `/opt/deephelp-app/releases` 并授予运行用户写权限；本机模板使用现有 Ubuntu 用户，其他主机须相应调整 service 的 User/Group。安装中断时保留现场，核对原清单后只完成缺失安装步骤，不覆盖已安装版本或自动切换 current。

持久根 `/srv/deephelp-personal` 与版本根分开：`config` 存只允许运行用户读取的环境/凭据映射；`state` 存 auth、两个服务凭据、累计预算、任务/追踪及合成权益 key；`assets` 可存新版本不可变运行工件，`legacy` 只交付已引用旧文件的最小闭包。当前语料也在 legacy 的受绑定闭包内，`DEEPHELP_CORPUS` 必须指向实际发布文件，不能假设模板目录存在。现有中间件连接模板位于该根的 `infra/config/connections.env`。配置模板是 `runtime.env.example`；所有真实配置文件权限 0600、目录 0700，秘密独立传输。

`DEEPHELP_STATE_ROOT` 必须为绝对路径，兼容旧 CLI 的 `.local/...` 参数并将写入限制在状态根内。`DEEPHELP_ARTIFACT_MAP` 是绝对 JSON 配置路径，例如 `[{"source":"D:\\source\\deephelp","target":"/srv/deephelp-personal/legacy"}]`。映射只改变读文件的位置，不改旧 JSON、release hash、向量签名或文件指纹；未声明根、重叠映射、遍历或链接越界拒绝。缺少工件不回退到其他目录。新指纹统一使用 `/` 分隔符，旧清单仍按原完整发布门禁拒绝不适用字节。

## 启动、版本与访问

`start.sh` 复用现有业务 CLI 与可选学习扩展。`--release-channel` 通过原 ReleaseRuntime/MySQL active 选择完整版本，保留历史 run 的版本绑定，接入两个独立 HTTP MCP；不绕过 `verify_release`。学习实验使用启动时选定的装配，完整业务每个请求仍按原发布协议选择版本。

把 `deephelp-personal@.service` 安装到 systemd，配置独立 environment file，启用 business、retrieval、app；启用合成工作流时另启 rights。安装/切换/备份脚本也交付到 `/opt/deephelp-app/bin`，不依赖临时构建目录。`switch.sh INSTALLED_VERSION` 只切换安装版本及重启三个进程，不更新 release channel、不重放业务；启动不健康时保留现场，显式切回先前已验版本。完整内容 active/previous 回退仍使用原 `release_cli rollback --live --channel ... --expected-revision ...`；安装版本必须与所选清单匹配。包回退与内容回退分别报告。

本机运行 `infra/client/tunnel.ps1 -Profile personal -Action start`，复用已有 SSH key / known_hosts，将本机 18080、23000 映射到服务器 8000、13000；`-Action health` 检查远端应用，原默认 infra profile 保持中间件隧道。打开 `http://127.0.0.1:18080/` 即为服务器页面；本机没有应用/MCP/前端进程，SSH 仅传输字节。Grafana 是个人管理员入口，继续使用其独立凭据。当前入口是私有 SSH，没有宣称公网 TLS；公开访问须另配并实测证书与可信源配置。

当前Grafana12.0.2打开既有`traceId`深链接后，需选择一次 **TraceQL**，保留自动填入的同一trace ID，即可显示实际trace。此浏览器路径已实测81 span；旧深链接直接展示的兼容项未在本次改动观测代码。学习页的受保护Tempo查询可直接返回同次span；不要把旧链接初始No data当作业务/Tempo丢失。

## 备份恢复与资源

管理员运行 `backup.sh`：停止唯一应用写者、使用现有事务备份入口，再备份运行状态/必要工件和当前安装版本，最后恢复应用。备份期间暂停人工维护与合成权益执行；独立 MCP 和合成下游仍是可写入口，不将停止 app 解释成数据库全局只读。秘密包含在受保护备份中，不提交 Git。恢复先按 SHA256SUMS 检查，原 `infra/scripts/restore-mysql.sh` 只允许 `deephelp_restore_NAME` 隔离新库。运行目录解包到另一个明确隔离根，修改它自己的数据库配置、middleware secret 路径、状态根、映射 target、实际 corpus 和端口；不覆盖现行库、现行 state 或预算。实际比较业务、消息/问题/操作、发布和 checkpoint 表，并通过隔离服务查询订单、回读原 receipt；文件存在或 gzip 检查不足以证明恢复。

Redis 投影可由 MySQL 窗口/outbox 重建，Milvus 意图集合按原 corpus、scope、Embedding signature、FP32 向量清单重建或核对；事件投影仍先回查 MySQL。不要混用向量空间或删审批/UNKNOWN/active/previous 引用。容量验收同时观察应用、两 MCP、原中间件和观测栈，覆盖真实索引/查询与 CPU 训练；区分 RSS、cgroup 当前/峰值、磁盘与观察窗口。个人短时低并发结果不能外推长期稳定性。

当前实际交付状态和最终证据由 PROJECT_STATE / M23 handoff 记录；此运行入口本身不是验收 PASS。
