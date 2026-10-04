# M35实施计划

基线：2026-10-05，规划分支feature/learning-experience-roadmap，HEAD f7bbc336b6ab0594b6b4ca71d2d075b95c8110c4。

**decision=DECOMPOSE；readiness=BLOCKED（M34直接进入、M32最终接口/路径、M33首批教程未交付）。** [规格](../../docs/MODULES/M35_FRONTEND_FOUNDATION.md)选择Vue3/TypeScript/Vite/Element Plus；版本与构建兼容性在实施时核对。两个顺序特性，不按每个tab拆M或创建S。

## 目标、前置及改动范围

先读AGENTS/STATE/ROADMAP/M35及M22、M32/M34实际handoff/M33内容入口；接口增量时读CONTRACTS对应段。按现行frontend/index.html/workbench.js/workbench.css、api/workbench.py/business.py、tools.learning各router及包构建边界迁移；不能照旧M22的MySQL/Tempo历史配置装配当前版本。

允许改modules/deephelp-app/frontend、app静态资源路由/指纹/pyproject、三包相关安装/页面tests、CI必要前端检查、docs/learning和包README、infra/deployment的精确静态交付入口；新增一个前端清单/锁，不另建Python锁或Node常驻服务。共享文件一主写者，新增依赖先核对官方当前要求。

| 顺序/分支 | 实施结果 | 独立闭环 |
|---|---|---|
| 1 feature/m35-vue-conversation | 导航/视觉组件、typed API、免登录、对话/历史/事实，Vite构建与同源打包 | 新源→安装wheel→实际页面；原message重试/补槽/选中run语义一致 |
| 2 feature/m35-vue-learning | 原全部业务/协议/工作流/识别/检索/记忆/观测/飞轮能力迁入，中文指引/状态统一 | 原M22能力迁移矩阵全部闭合；删除旧巨型文件/过时路由，正式个人安装可用 |

## 怎样证明

前端类型检查/构建、关键行为测试覆盖读取竞争/旧结果覆盖、retry ID、取消并非未执行、分页/CAS及任务预算；实际浏览器走查宽/窄布局、键盘、空态/失败/未运行，不以截图或DOM代替真实内容。按影响跑原工作台/学习router/安装和runtime边界回归，核心主链未改变不跑全历史live。

检查sdist→wheel静态字节/哈希资产/路由刷新/CSP、独立安装与包指纹；真正服务的资源全绑定完整发布，开发缓存/node_modules/临时dist不进入wheel。构建命令由前端package提供，实施后写运行README，不在本计划伪造npm脚本已存在。

专用合成订单和已授权真实原run验证页面，浏览/刷新/教程0模型；新正式安装的精确内容门禁另执行，live前说明调用范围/token/余量共享上限、配置证据复用与未重验依据。冻结数据/Prompt不随美化改变；实际改变模型请求才扩大内容范围。

## 停止与交付

前置缺失、原功能丢失、资源指纹/内容门禁/安装失败、费用/权限真实阻塞即停，不以禁用有效功能换通过。每项独立feature验收和Git交付后继续同M下一项；handoffs/M35.md记录迁移矩阵、浏览器/构建/安装命令退出码、未执行和恢复入口，STATE记录实际。保留旧已验安装/完整发布回退闭包，原事实/预算不回滚；清理本轮构建与截图过程副本，全部特性交付后停止。
