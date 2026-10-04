# M27｜Langfuse 自托管平台与容量关卡

**状态：** PLANNED；依赖 M25/M26 隔离持久方案。对象存储采用单机自托管SeaweedFS，不购买托管存储；先验证它与锁定 SDK/服务版本的兼容，再做隔离最小接入与组合容量试验。

**前置：** M23 当前拓扑/恢复材料和 M25 实例配置；运行或检查真实依赖前读 LOCAL_SETUP/.local/DEPENDENCIES。见 [计划](../../planning/M27/PLAN.md)。

## 选定组件

选择 Langfuse 自托管作为新路线的 LLM 工程平台。2026-10-04核对的官方文档为 v4；实施时重新核对稳定版本、Python SDK/服务兼容矩阵及镜像 digest，不使用浮动 latest。LangSmith 保留为将来托管需求的候选，不同时建设第二套主平台；完整 LangSmith 自托管属于 Enterprise 方案。

| 组件 | 职责/部署选择 |
|---|---|
| Langfuse Web + Worker | 私有 UI/API 与异步处理，两个实际容器均纳入验收 |
| PostgreSQL | Langfuse 元数据用独立库，可复用 M25 实例；应用事实不混入此库 |
| ClickHouse | traces/observations/scores 分析存储，必须配置、持久化、备份和恢复 |
| 专用 Redis | 队列/cache，noeviction；与 DeepHelp 的可丢弃缓存实例分开，持久策略按锁定官方要求验证 |
| SeaweedFS S3兼容对象存储 | 官方支持的开源单机容器，使用现有磁盘/私有网络；持久原始事件，必需组件，不购COS/S3托管服务 |

对象存储是强依赖，付费云对象存储不是：Langfuse Web先把原始事件保存到对象存储，队列引用由Worker读取后写入ClickHouse。普通文本trace也经过这条路径，不能以“不上传附件”为理由省略。S3是接口协议，SeaweedFS在现有服务器上提供这个接口。PostgreSQL同样自托管，不使用付费RDS。

Langfuse官方文档将SeaweedFS列为Official，并在Helm chart v2.0.0起采用它作为默认对象存储，另提供直接Docker接入示例。本项目仍用现有Compose，不引入Kubernetes或分布式存储集群。实施时锁定SeaweedFS稳定镜像/digest和Langfuse兼容配置，实际验证List/Get/Put/Delete、path-style及当前启用功能所需签名操作，不凭支持列表宣称锁定组合已经通过。

单机运行S3 gateway及必要master/volume/filer，明确持久挂载对象内容与元数据的所有实际目录，不能只照临时示例启动而丢数据。配置有认证的S3 identities文件、私有网络及受保护凭据；不使用无认证gateway或把开发示例密钥提交仓库。分别说明应用内部事件端点与浏览器/SDK可访问的媒体端点；首轮文本trace不额外开启多模态上传/批量导出，不把可选媒体能力当原始事件存储的开关。必要功能开启后才验证对应presigned URL和入口。

不默认安装旧社区MinIO，其官方仓库当前已归档、不再维护。SeaweedFS不产生新增托管服务订阅费，但占用现有CPU/内存/磁盘并由本项目维护备份。容量或兼容性不足时停止并报告；不自动开通COS/RDS、购买新服务器、省略blob store/ClickHouse或退回旧Langfuse v2。

现有 Redis 为 allkeys-lru、64MiB、无持久化，不能把 Langfuse 队列直接放进去。不同逻辑 db 不能隔离实例级淘汰策略。专用队列 Redis 不增加企业角色/配额体系；隔离的理由是丢失语义和容量不同。

## 试验与容量

隔离 compose project/网络/持久目录/端口，私有 SSH 入口，不改变旧 M23 的对外业务路由。官方 Docker Compose VM 推荐至少 4 核/16GiB及足够存储；现有约7.51GiB只能作为个人低负载试验，截图和历史空闲 RSS 不证明全组合可运行。对象存储计入同机峰值和磁盘预算，不沿用托管存储不占本机资源的假设。

先把合成 trace 经原生 SDK 发入 Web→SeaweedFS/队列→Worker→ClickHouse，再用 API 与浏览器读回 observation/usage；重建SeaweedFS容器后对象及元数据仍可读，验证卷挂载有效。查看 HTTP200/容器健康不算存储通过。锁定资源参数后测当前应用、两MCP、PostgreSQL、Milvus/etcd、缓存、Langfuse与SeaweedFS全组合，覆盖后台消费、ClickHouse merge、对象写入/恢复、索引/CPU训练、模型等待、积压后恢复及重启。不得靠截断业务上下文/降模型能力或停止必要依赖换通过。

报告实际持续时间、样本/请求/队列数、主机 MemAvailable/swap、同时峰值、cgroup OOM/重启、CPU/磁盘增长与查询延迟；阈值在试验前按负载写入运行参数，并含失败停止边界。失败不卸载旧服务、反复扩大内存或自动购买资源。

## 验收与停止

原生 SDK 异步/短进程 flush、超时/取消、断网/恢复和直接导出关闭不阻塞业务；平台重启后实际 trace/score可读，观测备份恢复到隔离目标后关联可读，SeaweedFS对象内容/元数据/队列/分析存储关系明确。对象存储停止时如实暴露不可用/积压，恢复后据实际处理结果记录，不宣称仅有ClickHouse即可正常接新trace。首特性合成 observation，模型 0 次；主业务接入留 M28。现网仍旧路线，MySQL/Collector/Tempo 暂不删除。完成后停止。

依据：[架构](https://langfuse.com/self-hosting)、[Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose)、[队列要求](https://langfuse.com/self-hosting/deployment/infrastructure/cache)、[官方SeaweedFS/Blob接入](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage#seaweedfs)、[SeaweedFS](https://github.com/seaweedfs/seaweedfs)、[MinIO 维护状态](https://github.com/minio/minio)、[LangSmith 自托管](https://docs.langchain.com/langsmith/self-hosted)。
