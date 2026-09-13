# DeepHelp：工程入口

当前远程基线是基础设施与 uv workspace；本次新增架构/规划资料，不冒充已实现 Agent。真实状态只看 [PROJECT_STATE](docs/PROJECT_STATE.md)。

## 三种工作只读三种上下文

| 工作 | 最小必读 | 不默认读取 |
|---|---|---|
| Astra 实施一个子任务 | AGENTS、CONTRACTS、PROJECT_STATE、当前子任务、直接前置 handoff 和相关代码 | 全部 22 份模块 Prompt、合集、原 PDF 全文 |
| Pro 规划一个模块 | [通用规划 Prompt](planning/PLAN_MODULE_PROMPT.md)、当前模块、上述工程基线、相关 PDF 页 | 其它模块全文、全部历史报告 |
| 架构/来源核对 | ARCHITECTURE、DECISIONS、SOURCE_MAP 和有疑问的原图 | 无关网页或推测出来的内部源码 |

## 现在怎么继续

M00 详细设计见 [模块设计](docs/MODULES/M00.md)，六个可独立复制的实施/核对子 Prompt 见 [M00 子任务](planning/M00/README.md)。仓库已经写入对应设计文档，执行时只检查和补差异，不重新生成一套。先完成 S06 的集成复核，再开始 M01 实施；M01 的规划可先进行，但不得把 M00 的设计状态冒充运行验收。

后续在新 Pro 会话上传原 PDF 和当前 `Mxx_*.md`，粘贴同一份 [通用规划 Prompt](planning/PLAN_MODULE_PROMPT.md)。它应从仓库读取最新状态；没有仓库读取能力时，上传本地导出的上下文包：

```powershell
uv run --locked python scripts/project_context.py export --module M01
```

输出位于 `.local/context/M01_CONTEXT.md`，只含白名单工程文件和缺失前置说明，不含原 PDF、密钥或数据库连接配置。将它与 PDF、当前模块文件一起上传。

## 原始资料在本地

原 PDF 的统一位置为 `.local/references/deephelp-original.pdf`。执行以下命令会在指定目录中按 SHA-256 找到本次原件，复制并校验；不会删除原文件，不会上传：

```powershell
uv run --locked python scripts/project_context.py import-source --source-dir D:\BaiduNetdiskDownload
```

原启动包保留在下载目录作为历史原件。`planning/modules/` 是按当前工程修订且去除重复底座的工作副本；合集、原校验清单和旧 P00 初始化指令不作为当前执行入口。文件取舍见 [导入记录](reports/M00/import-map.md)。

## 当前已有与未来目标

保留 MySQL/Redis/Milvus、现有 uv workspace 和 infra 原文件。PG、云端 4 GiB、重建单一 pyproject 等旧要求已在 [DECISIONS](docs/DECISIONS.md) 明确修订。框架持久恢复后端尚待验证，不通过添加新数据库掩盖这个缺口。

开发顺序、直接依赖和源页码见 [模块目录](planning/README.md)。到 M08 先验证 3–5 个合成场景的真实纵向链路，再做多轮、FastText、审批和评测；不必等 22 个模块全部完成。

仓库文档自检：`python scripts/project_context.py verify`。这只检查文档结构和规划一致性，不是 DeepHelp 业务验收。
