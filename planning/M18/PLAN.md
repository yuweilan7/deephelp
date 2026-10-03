# M18 实施计划

判断：DIRECT。基线2884ab3；M17修复、真实main比较与远端同步已完成。

1. **这次做成什么**：人工触发的采集→审核→三路构建→固定回归/独立发布样本→显式启用/回退。输入专用合成客诉的真实MySQL run，输出脱敏候选、独立三路审核记录、派生物来源与完整演示版本。高分不自动变金标。自动发布加固、企业运营数据和写业务不在本次范围。
2. **从哪里改**：新增反馈DTO/CLI、MySQL候选/历史/任务表，复用M10短事务领取/令牌确认机制、M05真实Dense导入、M13训练/量化接口及M17评分器。规则只接受有界字面短语，不执行生成代码。新集合使用原Embedding签名；无新依赖/根锁/infra修改。
3. **怎样证明**：重复采集、拒绝/高置信候选、独立路线、dev/test及编号/变体组隔离、更正/撤回与派生追踪、CAS/任务幂等和失败不发布离线验证。真实原模型/专用MySQL/Milvus/SDK工具采集三类业务，训练/量化/全文向量回读，复用冻结M17回归与另组三条发布样本，上一版切换/回退/新资源回读；特性和main均跑ROADMAP检查及必要内容复验。
4. **什么时候停**：现有账号及模型，用户后续授权切换至qwen3.8-max，先补验chat/schema/tool再进入业务；Embedding保持原签名。使用独立.local累计预算并给足两阶段与诊断余量，单请求90秒、有限重试，训练180秒、整轮1800秒。不可恢复依赖、内容硬失败或容量不足停止，不替换可用资产；失败原件保留。演示资产不替换默认业务指针。
5. **怎样交付**：feature/m18-reviewed-feedback；验收后中文提交/合并并同步特性和main到同一远端SHA，干净main结束。更新M18契约/运行入口/handoff/PROJECT_STATE。完整500+、15+规模及单一事实源发布加固继续单列未完成。

## 完整版本发布与审批资产保留

用户2026-10-03启动全部已登记剩余项。基线`6aa0bb0`，`feature/m18-complete-release`；一主写者顺序实现。新增不可变ReleaseManifest及MySQL发布/active修订/运行引用表，绑定完整语料、Embedding、FastText/词典、规则、SOP/Prompt、provider与代码。准备/远端回读/固定回归在切换事务前完成；短事务CAS切换完整active/previous，并在同一事务核对审核来源。不得热换运行中半套资源。

每次请求选一个完整版本；receipt事务登记引用，跨版本续接回到原问题绑定版本，审批计划保持原参数和快照。新批准/再次发送仍核对当前SOP；UNKNOWN允许先按原操作对账。active/previous、非终态run和待审批/对账引用禁止退休；保留共享工件和来源原件。真实新建两套Dense资产、完整回读/容量、M17+独立发布样本、新MySQL池、并发CAS/回退、发布中RUNNING、待审批/UNKNOWN保持及单一效果验证。故障前/后指针必须为完整版本。

仅特性分支跑ROADMAP检查和真实验收；依用户最新永久约定，main合并/推送后不复验。无新依赖/根锁/infra变更，增量SQL不得覆盖旧表，原本机演示入口保持兼容。保留一份M18 handoff，原始报告与本次预算放.local/m18-release；完整500+与M14业务覆盖接续为独立特性。

## 运行、验收与学习边界重整

判断：VERIFY_EXISTING → DIRECT。基线 `2cc3aee`，分支 `feature/runtime-learning-boundaries`。用户仅启动本特性，完成交付即停止。

1. **目标**：提供 `python -m deephelp_app` 业务入口；学习、fake、故障注入、探针和评测按职责归位。正式组装不导入探针/评测命令，不读取 dev/test；三类合成业务、补槽、隔离、幂等及 M15 审批保持。
2. **改动**：抽取独立本机路径、资源定位、文件指纹及代码来源工具；运行指针校验拆分发布时完整数据校验与启动时已冻结证据校验。保留完整内容/hash/签名/版本守卫。无依赖、单锁、Python、workspace、数据库迁移或 infra 变化。
3. **证明**：ROADMAP 全工程检查；冻结数据迁移逐字节核对、文档链接/命令及 wheel 资源核对；在禁止导入学习/评测/探针及禁止读评测数据的条件下测试业务组装。真实 HTTP 验三类、补槽、隔离、零调用重放；完整发布用新清单和新内容评测，复验审批/UNKNOWN保留，不拿旧代码清单通过新二进制校验。
4. **停止**：不可恢复真实依赖、验证失败、Git冲突或同步失败按AGENTS停止。保留凭据、PDF、当前资产、历史账本及审批/待对账引用；不整体清理.local，不部署，不启动M19–M21或新业务。
5. **交付**：仅本分支验收后中文commit/push/merge，六SHA同步，干净main结束。更新本PLAN、M18 handoff、STATE及现行运行文档。原始报告和临时参数放 `.local/runtime-boundaries/`。

### 具体迁移清单（先核对内容与引用，再执行）

以下路径均相对 `modules/deephelp-app/src/deephelp_app/`；数据仅移动，不改标签/冻结内容。

| 原位置 | 目标/职责 |
|---|---|
| `experiments.py`, `fakes.py`, `model_fakes.py`, `cases_fake.py`, `mvp_replay.py`, `sop_replay.py`, `event_replay.py` | `learning/`，异步实验与离线回放/fake |
| `live_probe.py`, `text_entity_probe.py`, `mvp_probe.py`, `memory_probe.py`, `event_probe.py`, `cascade_probe.py`, `fasttext_probe.py`, `governed_sop_probe.py`, `debug_probe.py`, `checkpoint_probe.py`, `approval_probe.py`, `flywheel_probe.py`, `release_probe.py`, `mcp_smoke.py` | `probes/`，显式验收；子进程命令同步更新 |
| `evaluation.py`, `evaluation_cli.py`, `evaluation_runtime.py`, `evaluation_approval.py`, `hybrid_eval.py`, `hybrid_cli.py`, `cascade_tune.py`, `fasttext_training.py`, `fasttext_cli.py`, `mvp_acceptance.py`, `sop_acceptance.py`, `flywheel_validation.py` | `evaluation/`，冻结评测/开发选参/训练/内容验收；evaluation.py改为core.py；不进入启动依赖链 |
| `mcp_mock.py` | `demo/mcp_server.py`，真实SDK协议下的合成业务下游；配置/fixture类型独立，故障行为抽取至 `learning/` |
| `samples.py` | 业务fixture类型/加载拆入 `demo/fixtures.py`；固定样本/DTO教学移至 `evaluation/samples.py` |
| `sample_data/business.json`, `m09_corpus.jsonl` | `assets/demo/`，合成下游事实与只读索引来源，不能当冻结test |
| `sample_data/m12_policy.json`, `m13_dictionary.txt` | `assets/runtime/`，运行策略与训练/推理共用词典，保留字节/hash |
| `sample_data/cases.json`, `text-golden.json`, `m09_dev.json`, `m09_test.json`, `m13_fasttext.json`, `m17_cases.json`, `m17_manifest.json`, `m17_scale_cases.json`, `m17_scale_manifest.json`, `m17_approval.json`, `m18_demo.json` | `assets/evaluation/`，按split冻结/训练/验收来源，保留既有版本/hash |
| `sample_data/mcp-faults.json`, `event_sequences.json`, `examples.json`; `sop_data/model-replay-v1.json` | `assets/learning/`，故障配置/回放/DTO示例 |
| `sop_data/scenarios-v1.json` | `assets/evaluation/`，SOP场景验收；SOP/schema/Prompt及已有业务规模注册表留原职责路径 |
| `sample_data/README.md` | `assets/README.md`，明确运行策略、SOP/模板、demo事实、冻结集及学习数据口径 |
| `live_probe.local_path`, `evaluation.file_digest`, `evaluation_cli.provenance` | 独立 `local_paths.py`, `asset_integrity.py`，启动可用，不导入验收器 |

发布影响：移动代码/资源将改变完整包代码指纹与词典路径键；旧完整清单仍严格拒绝新包。不改旧清单、旧评测或旧工件，不切既有业务channel。复用字节未变的已验证Dense/FastText构建，生成专用新完整版本并重新内容/发布验收；旧引用保留。清理仅考虑确认无引用的可再生构建残留，未确认项目一律保留。

逐文件核对后补入本清单：`sop_probe.py`→`probes/sop_probe.py`；离线回放入口`event_cli.py`→`learning/event_cli.py`；`sop_data/business-fixtures-v2.json`→`assets/demo/`，`business-catalog-v2.json`→`assets/evaluation/`，其SOP注册表仍保留sop_data。额外抽出`demo/tool_config.py`、`demo/scenarios.py`及learning两份故障实现；合成业务服务默认启动不加载故障包。完整发布的新指纹明确区分runtime-v2与评测tooling_digest，旧完整清单不改写。
