# 个人工作台前端

唯一源码为本目录，Vue 3 / TypeScript / Vite / Element Plus。Node需要22.12以上，本次使用24.19.0；依赖精确版本与完整依赖树见package.json/package-lock.json，Python继续使用根uv.lock。

```powershell
cd modules/deephelp-app/frontend
npm ci
npm test
npm run build
npm run dev
```

开发服务只绑定本机，代理原应用8000；生产仅由Python应用同源提供built，服务器不运行Node。路由使用hash，刷新仍读取根入口；所有脚本/样式本地打包，无CDN。需要网页或构建正式Python包时先build；仅源码editable安装和本地核心course无需Node/built。wheel仅包含built；sdist包含可再构建源码和同一built，不含node_modules。

src/app管理路由，features管理页面，components承载共用展示，api定义原服务契约，composables拥有读取取消、个人连接和待核对消息，styles维护布局。仅身份/预算、选中run及未确认原消息跨页共享。前端不判断意图、补槽归属或审批合法性。

页面刷新/历史/诊断0模型，写操作不会自动重试。网络发送不确定时在当前工作台保留原message_id/正文/时间，先明确核对原消息；关闭页面后可从持久历史核对，不能把浏览器取消等待当服务器取消。

全部原工作台能力已迁入八个学习任务入口，旧HTML/JS/CSS与legacy路由已删除。业务资料复用原CAS；评测继续使用受控目录、唯一任务、累计预算和显式发布。个人身份不能接管独立合成审批练习，未配置的平台映射给出实际空态。官方要求依据：[Vite](https://vite.dev/guide/)、[Vue](https://vuejs.org/guide/quick-start.html)、[Element Plus](https://element-plus.org/en-US/guide/installation.html)。
