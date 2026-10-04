# DeepHelp独立业务服务

M20第1特性复用应用的唯一DTO、只读工具schema与会话引擎，Business服务拥有订单/券/活动的MySQL维护事务及查询快照。Retrieval MCP为下一顺序特性，当前检索仍由应用直接调用既有Embedding/Milvus/BM25适配器。服务不依赖deephelp-tools，不提供任意SQL或中间件命令。

## 启动与凭据

从仓库根使用Python3.14.7与根单锁；`uv sync --locked --all-packages`安装三个workspace包。服务凭据是两个不同的随机256-bit秘密，只保存在忽略目录；init拒绝覆盖。Bearer保护HTTP连接，另一个持久key签发60秒、audience绑定的委托身份/请求。它不是原stdio父子进程的临时key；MCP session和trace不授权。

```powershell
py -3.14 -m uv run --locked python -m deephelp_services.business init --credentials .local/m20/service.json
py -3.14 -m uv run --locked python -m deephelp_services.business migrate --credentials .local/m20/service.json
py -3.14 -m uv run --locked python -m deephelp_services.business serve --credentials .local/m20/service.json --port 8011
```

migrate增量创建`dh_m20_orders/coupons/activities/order_activities/evidence`，不重建数据库、不导入样例；重复迁移安全。配置继续来自现有infra连接与本机秘密，独立安装时可用`--root`指定这些配置的根目录。

另一个终端启动应用：

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app serve --business-mcp-url http://127.0.0.1:8011/mcp --service-credentials .local/m20/service.json --pointer .local/m09/active.json --corpus demo/data/m09_corpus.jsonl --auth .local/m20/auth.json --budget-state .local/m20/budget.json --trace-path .local/m20/runtime.trace.jsonl
```

应用`init`仍提供身份/预算初始化；专项验收使用下方探针init准备两个测试身份。应用只需要app包，Business进程只需要app+services；显式demo stdio才需要tools。两种业务来源互斥，缺失/错误配置或数据库故障关闭请求，没有JSON回退。当前两个进程只绑定loopback，校验Host/Origin；跨机器TLS/反向代理属于M23，尚未部署。客户端每个有限请求使用新SDK会话，服务独立重启后下一次初始化恢复；断开的旧调用不自动重放。

## 维护与查询

使用应用的Bearer身份调用`/business`，应用经受保护委托请求同一个Business服务。正文不能自报tenant/user；模型目录只保留`get_order_benefits`与`check_coupon`，没有维护工具。

| 应用路由 | 内容 |
|---|---|
| `POST /business/{orders,coupons,activities}` | 创建；同主体/ID/内容重复返回既有记录，不同内容409 |
| `GET /business/{kind}?offset=0&limit=20` | 当前身份分页，limit 1–100，next_offset |
| `GET /business/{kind}/{id}` | 详情含identity/version/data |
| `PUT /business/{kind}/{id}` | `{expected_version,data}`完整更新；ID须与路径一致，旧版本409 |
| `GET/PUT /business/orders/{id}/activities` | 关系读取/整体替换；写入需expected_version，订单版本递增 |
| `GET /business/evidence/{evidence_id}` | 原查询的不可变记录版本快照与ToolResult，仅原主体可读 |

订单创建示例：`{"order_id":"M20-001","paid":{"amount":"173.45"},"discount":{"amount":"12.30"},"discount_status":"applied","sku_ids":["SKU-1"]}`。券另含coupon_id/order_id/status/minimum_spend/带时区valid_from与valid_until；活动含activity_id/label/rules/owner scope/status/有效期。请求的完整schema由应用OpenAPI提供，唯一类型在[domain/models.py](../deephelp-app/src/deephelp_app/domain/models.py)。金额只接受十进制字符串，CNY最多两位，SQL使用DECIMAL(20,2)。停用通过显式状态；没有删除接口。

订单、券和活动关联均校验完整tenant/user，关系事务与查询快照有一致读取边界。券返回当前状态/有效期/门槛下的可用性；工具活动事实沿用既有IDs和名称schema，详细规则/有效期由维护详情查看。维护订单版本、券版本及活动版本会保存在查询快照，content_hash校验返回事实；更新后新message_id读取新记录，重投旧message_id返回原receipt/事实/证据及本次零用量。

## 协议与验收

使用锁定MCP2.2.0的正式Streamable HTTP，客户端明确使用initialize模式并验证协商版本2025-11-25，实际tools/list、tools/call和structuredContent；服务端与调用端各校验schema、版本、参数及事实。输入schema不含身份、连接地址或trace；可信身份及关联ID在受保护元数据。协议依据为[官方HTTP传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)和[工具schema](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)，实际版本兼容与取消由锁定SDK探针验证。本服务使用SDK流式响应路径以传递取消，不能仅把客户端超时当作服务端查询已经取消。

离线：`py -3.14 -m uv run --locked pytest modules/deephelp-services/tests --tb=short -q`，包括真实loopback SDK、委托/篡改/重放、金额/有效期/CAS、超时取消及Windows进程树文件句柄退出；不会访问云库或模型。

明确真实验收先检查隧道health，再根据本轮样本、调用数和修复余量设置共享上限。以下变量由本轮验收计划给出，只用于启动新验收；已有累计文件拒绝重置，按AGENTS/ROADMAP选范围，不复用历史额度作为永久规则：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.probes.business_probe init --max-calls $AcceptanceMaxCalls --max-tokens $AcceptanceMaxTokens --max-cost $AcceptanceMaxCost
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.business_probe run --live --output .local/m20/business-new.json
```

探针显式创建JSON没有的新记录，通过原引擎的三类客诉核对事实，证明至少一条实际检索/MCP；固定动作验证CAS、归属、旧消息零调用、独立进程重启。失败保存必要状态，只有完成了Business重启而应用重启未结束的报告可用`--resume-report`补测重启相关动作；沿用同一auth/服务凭据/累计预算，不重新跑已通过模型样本。最终证据见[M20交接](../../handoffs/M20.md)。个人合成持久记录不等于企业订单/支付；synthetic_rights不修改这些表。完整发布仍须精确清单和专项发布验收，本特性没有注册新发布。
