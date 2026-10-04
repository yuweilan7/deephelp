# M34实施计划

基线：2026-10-05，规划分支feature/learning-experience-roadmap，HEAD a740bbc（实际开始时main）；现行BrowserAuth/LocalAuth及私有个人云已实现，当前页仍有登录表单。

**decision=DECOMPOSE；readiness=READY（个人入口可先做），正式安装验收仍待执行。** [规格](../../docs/MODULES/M34_PERSONAL_ACCESS.md)两项按序完成；无需等待M32/M33。首项已验：显式 --personal-identity，GET /auth/session自动会话，旧登录/退出界面及路由删除。

## 目标和改动入口

打开页面直接进入本人工作区，无密码/令牌操作，刷新/重启仍读取原PG历史。先读AGENTS/STATE/ROADMAP、本规格、M22/M30及当前模型M03交接；实际部署前读LOCAL_SETUP/.local/DEPENDENCIES。

复用app/api/workbench.py的BrowserAuth、bootstrap/mvp_runtime.py的LocalAuth、api/app.py的可信身份入口，所有业务/学习router仍消费同一身份。计划新增显式个人identity配置/启动选项；不猜第一条token身份、不让正文选owner。允许改上述文件及相关bootstrap/运行模板、frontend旧登录部分、tools.learning装配、相关tests/说明；对外接口变化在CONTRACTS对应段增量描述。

| 顺序/分支 | 内容 | 完整验收 |
|---|---|---|
| 1 feature/m34-personal-entry | 显式固定identity、自动会话建立、删除登录/退出与失效文案，保留Origin/CSRF/内部身份 | 无登录UI/浏览器密钥；缺配置启动失败、刷新/重建/跨站/伪造/外身份守卫通过 |
| 2 feature/m34-personal-deployment | 当前云配置/精确新完整发布/安装/直接进入教程及恢复 | 实际浏览器直接进入本人历史，重启及固定业务/原receipt/同run通过 |

## 怎样证明

先选test_m22_workbench、原API/权限/业务/学习router及M15恢复相邻测试，按改变路径补必要用例；删除登录用例逐项保留其独立安全断言。页面/历史/启动不调用模型；固定操作验证owner/CAS/审批/同源/CSRF，服务凭据不改。

真实浏览器用专用合成订单：空浏览器直接进入→维护资料→缺槽位/补充→查询事实→刷新/重启历史→相同消息重投0调用，外身份读取/维护/操作/观测拒绝。安装前保留旧完整发布与配置，源码/前端字节变化按既有精确11会话14消息门禁生成新manifest；不沿用旧PASS、不重跑Embedding能力或Rerank dev/test。live前核对现行模型/endpoint、样本影响、预计调用与token范围、含修复余量的共享上限，参数只存本机；普通固定故障0模型。

## 停止与交付

身份配置歧义、必要守卫失败、真实内容/安装/推送失败即停；不为免登录开放公网或创建企业账户系统。两个特性分别验收/中文提交/推送/合并/同步，同M继续。handoffs/M34.md记录实际UI/HTTP/安装和恢复证据，STATE只记实际；受保护的旧安装/manifest及个人配置可恢复，不能恢复陈旧数据库或清零预算。全部完成停止，不自动启动M35。

首项适用离线76 passed、Ruff/245格式/mypy172与JS语法通过。第二项从交付后的同步main继续；核对服务器实际owner/channel/installation与恢复闭包，再建立新精确发布。首项没有联网/模型调用，不把当前云运行写成已免登录。
