# M19短计划：工程分层与离线学习入口

规划日期：2026-10-04。读取基线为干净main，HEAD `73edc5e2274f697e4a1e76dda056988ba2d5df9e`；暂存/未暂存差异均为空。本轮仅替换路线与规格，没有实施或验收模块；本文件的步骤均待执行。

判断：`DIRECT`，一个结构/安装边界特性；离线实现可启动，完整发布若进入本次交付仍有真实内容门禁。来源为用户工程要求，不依赖新的PDF结论。本轮未核对PDF、未调用模型或检查云端现况。

## 1. 这次做成什么

按 [M19规格](../../docs/MODULES/M19_STRUCTURE_LEARNING.md) 将平铺运行源码按职责归位，把学习/探针/评测与demo从必装运行包中分开，保留一套领域与引擎，建立可跟随的学习导航。先核验已有运行边界，不重做已完成清理；不实施M20数据库、前端、模型降级或部署。

## 2. 从哪里改

必读AGENTS、STATE、ROADMAP、M19、M18运行边界/清理handoff；再按消费者读实际源码、测试、pyproject/根锁/打包/CLI/动态资源与指纹。跨包/入口接口变化才增量读CONTRACTS。核对`conversation.py`、`mvp_runtime.py`、`app.py`、`tool_gateway.py`、`asset_integrity.py`、`release_assets.py`及learning/probes/evaluation/demo的消费者。

允许修改modules/deephelp-app、拟新增modules/deephelp-tools、datasets/demo的实际资源、根workspace配置与唯一锁、CI/网络守卫及对应docs/learning/README/运行入口；不动现有infra拓扑、凭据、来源与业务库。拆包不是复制实现，先列迁移与引用清单，再改导入/资源/命令/测试；冻结数据字节与split不变。权威类型只在domain；应用通过Port复用适配器。目标树中的modules/deephelp-services由M20在实现真实业务/检索MCP时创建，M19只保留依赖方向和调用端接缝，不提前实现或建空包。

## 3. 怎样证明

从现有`test_runtime_boundaries.py`、`test_scoped_acceptance.py`、`test_imports.py`及受影响CLI/stdio/调试/发布消费者选择离线用例；迁移后按实际新路径执行。检查运行wheel独立安装/装配且不导入工具/冻结集，现有合成闭环通过显式独立demo MCP运行；工具包可单独运行，默认帮助/预览/pytest不发远程请求，尚未实现的数据库模式不得静默回退。静态与动态消费者、资源内容/路径、完整发布指纹范围都核对。

适用命令：根约定的Ruff、mypy、`uv lock --check`、分别构建包和安装检查、相关pytest、`git diff --check`。共享装配/跨包迁移影响广时，一次完整离线pytest并核对收集数量合理；摘要保留退出码/通过失败数/耗时，失败才定点读取诊断，不读取所有测试源码或复制全量输出。

结构等价本身不必新增模型API。旧完整manifest必须拒绝变化后的运行字节；若交付新完整运行版本，沿既有发布入口重建与精确内容验收，执行前说明本次失效范围、必要代表/发布样本、调用和token范围、复验共享上限。不能拿旧清单PASS充当新包通过，也不能关闭门禁。具体live数字依据实施后的路径给出，不在纯规划里编造。

## 4. 什么时候停

消费者、资源、网络隔离、权限/幂等/事实、审批UNKNOWN或发布引用回归失败即定位修复，无法恢复则停止交付；真实依赖失败按AGENTS处理，不降级假通过。保留旧不可变工件/报告和恢复配置；回滚代码/工具包，新增目录只清理本次已核对可再生项，不回滚或删除业务事实。完成结构与导航就停止。

## 5. 怎样交付

实施时从同步干净main创建`feature/learning-runtime-structure`。按AGENTS在特性分支验收、中文提交/推送/合并/六SHA同步，最后干净main。更新当前STATE和实施后的handoffs/M19.md，报告实际迁移、命令/退出码、模型调用次数、未执行内容、包边界与下游路径。本轮纯规划不提交、不推送，不提前创建handoff。
