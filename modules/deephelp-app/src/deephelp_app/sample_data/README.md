# M02 合成数据与指标口径

`cases.json` 是固定开发基线，不是模型预测记录。36 条样本、18 个来源/近义变体组，三类可执行客诉各 12 条（包括该类的否定/不路由反例）。所有身份、订单、券、SKU、活动均为合成值；实体标注保留消息 ID 和原文片段。

reference 6 条可用于规则/召回参考，dev 12 条用于阈值与策略调试，regression 18 条用于固定回归。同一 source_group / variant_group 不跨 split，完全相同文本也不跨 split。模板实例属于其来源组；新增近义句必须沿用原组及 split。regression 已公开、已用于开发，不得充当未来未见 test。M17 须另建独立来源、冻结版本的评测集；这里不满足 500+ 规模目标。

`business.json` 提供 13 个订单、4 张券和 1 个活动，包含优惠已应用/未享受/不合条件、券可用/过期/门槛不足、无活动及他人归属。金额只用 CNY、最多两位小数的十进制字符串，ID 全部是字符串。M06 可在已验证身份范围内实现两个只读工具，当前文件读取不执行工具。

`examples.json` 给出缺订单的 RequestEnvelope / ResponseEnvelope，以及未来审批禁用说明。审批枚举保留，当前响应/Question 禁用等待审批，ToolRequest 只允许只读白名单；普通“好的”没有批准或恢复字段。

## 后续评测统一口径

按 split、dataset/registry/policy/SOP 版本分别统计。下面是定义；M02 不产生准确率成绩，也不实现模型或 500 条评测平台。

| 指标 | 分母与判定 |
|---|---|
| 意图准确率 | gold intent 非 null 的样本；final_code 与 gold 相等算对，澄清/人工/拒识时未选中正确 code 仍算错。另报未知/否定样本的正确不路由率，不混入三类准确率 |
| macro-F1 | 固定三类分别算 precision/recall/F1，再等权平均。某类 TP=FP=FN=0 时 F1 记 0；拒识已知类计 FN，未知样本错接某类计该类 FP；候选 top1 与 final_code 分开报告 |
| 覆盖与错误接管 | 接受覆盖率 = accept 数 / 全部样本；安全工具覆盖率 = 实际合法工具调用场景数 / gold 允许工具的场景数。错误接管率 = 在 gold 为未知、缺槽位或无权时仍 accept/调用工具的样本数 / 这些不应接管样本数；分母为 0 记不适用 |
| 实体保真 | 槽位名称、字符串值（含前导零）、message_id 与出处均匹配才算该项正确；报告槽位 precision/recall 和整样本 exact match。更正另检查旧值/新值及两次出处；金额逐 Decimal 值、币种和精度校验，不容忍浮点近似 |
| 工具参数正确率 | 以 gold 工具调用场景为分母，名称、顺序、次数和全部参数严格匹配算对；漏调用算错，额外/无权/缺槽位调用单列安全失败，不因业务返回成功豁免 |
| 脚本场景完成率 | 所有已声明脚本场景为分母；每一步意图/实体、归属、工具及 outcome/question_status 全部符合 gold 才算完成。CLARIFY/HANDOFF/REJECTED 可以是正确场景完成；不解释为线上问题已解决或一次解决率 |

只有运行被测系统并产生实际观测，才能按这些口径评分。校验 gold 引用一致、手工构造 DTO 或回放预期，不算业务效果/模型准确率。

## M05导入数据字典

M02样本不修改；`corpus.frozen_preview()`适配非空标签记录，原文件SHA-256、split/来源/近义组及索引digest保存于本机manifest。JSONL一行一对象，CSV必需字段同名，metadata/label_path使用JSON字符串，synthetic为文本`true`；XLSX映射覆盖必需字段，不推断标签或前导零。

| 字段 | 语义与约束 |
|---|---|
| doc_id | 稳定字符串主键，最多128 UTF-8字节；同版本不改义 |
| content | 客诉原句，1–2000字符；content_hash为NFC/strip后JSON字符串的SHA-256 |
| intent_code | M02登记的actionable叶子；未知/父节点拒收 |
| split | train/reference入库；dev/test/regression仅查询/保留manifest |
| source_group / variant_group | 显式来源/近义组，同组不得跨split；不能靠改ID宣称独立 |
| synthetic | 必须true；当前入口交付合成业务语料 |
| label_path | 可选l1–l4路径，提供时必须匹配registry |
| metadata | 可选有界字符串属性，最多16项，键128字符/值512字符 |

Milvus另存namespace、dataset_version、model_signature、split、content_hash、FP32 vector及JSON metadata；metadata含source/variant、record_hash、原文件hash、batch_id、corpus_digest、registry_version和attributes。全签名保存在collection description，不存连接/客户端/秘密。record_hash覆盖完整CorpusRecord，batch_id绑定文件及scope；改变标签/内容/签名须新版本，先导入验证再切指针。

dev报告doc Recall@1/3及候选Recall@1/2，每类分母单列；错例保留query ID与候选原分数。未知/否定不纳入有标签召回分母；召回成功不证明最终接管、槽位或业务回答正确。dev供未来阈值校准，regression不能当未见test。

## M06故障数据

`mcp-faults.json`为独立`mcp-faults-v1`配置，只引用business.json合成订单，不修改M02样本。七项配置覆盖延迟、模拟500/限流、缺字段、矛盾、注入文字及超大体积；通过可信服务启动配置加载，不成为模型工具参数。默认正常模式不加载故障；feature验收入口显式使用此配置。

## M09检索对照

`m09_corpus.jsonl`为12条独立reference（每类4条），沿用CorpusRecord；`m09_dev.json`为18条开发查询，`m09_test.json`为24条冻结查询。全部合成、同一作者构造，SKU/订单/券编号均非企业数据。查询query_id/text/category/gold/source_group/variant_group必须独立于索引和另一split，gold=null表示无单一支持意图；运行时检查重复内容/组泄漏。只有reference入Milvus。

dev含14条单类+4条无关/多诉求；test含18条单类（各6条）+6条无关/多诉求。category覆盖semantic/exact/negation/typo/oov/unrelated/multiple，不是原文完整语料或500+独立评测。样本在首轮dev查询前固定；选参只用dev，test错例完整保留、不回流本轮索引。后续改变样本/分词/模型或已依据test修改参数时必须另版本和新冻结组。

## M13 FastText 合成语料

`m13_fasttext.json`共80条人工构造并逐条核对的合成句：40 train、20 dev、20 test；优惠未享受、券不可用、订单活动查询、unknown、multiple五类各8/4/4。来源均manual-synthetic-m13-v1，不是PDF完整语料或企业数据。sample_id/source_group/variant_group/split/label/text/synthetic/reviewed必需；同来源/改写组不能跨split，规范化重复句不能重复出现，未知label与缺类split拒收。

语义组由本轮标注者声明，程序能检查组及文本重叠，不能自动证明所有自然语言近义句独立。全部句子同一作者、数量很小，heldout只指未用于本轮训练/选参；不声称真实未见分布或可信泛化。M02/M09文件不修改，本组不进入Milvus。`m13_dictionary.txt`只提供固定业务分词，训练/推理共用，其hash与Jieba基础词典hash写manifest；改变预处理必须新训练/评测版本。

## M17 冻结评测数据

m17_cases.json与m17_manifest.json在首轮运行前固定。case_id/source_group/variant_group/synthetic/split/scenario/live_sample/turns必需；每消息有turn_id、text、intent/label、逻辑event_id、确认实体、expectation和核验依据。expectation区分正常查询、缺槽位、无权对象、未知、多诉求及同事件补充。标签由显式语义证据核验，金额/券状态/活动等预期从business.json读取，模型输出不参与gold。

test未进入原M02/M09索引、M13训练或dev选参，按组/会话及规范化文本审计；编号替换后也检查与既有公开样本重复。regression保留已排错的固定输入，永不称未见。组由同一作者声明，审计不能证明全部近义模板独立或供应商预训练未见。scenario只是命名输入情境，不能据名称数量宣称完成15+业务流程/500+规模。

M17另报五类总体accuracy/macro-F1、三支持类指标和分类计数；空类/空分母返回null及原因。实体值与来源分别核验，歧义多诉求不伪设一个确认槽位。工具参数同时报告实际调用准确率与gold允许场景的行为准确率，漏调用计后者失败。消息完成率与整会话完成率分开；Recall@K只含实际检索。离线语义适配器复用核验词汇，因此只证明编排/指标/硬门禁，不是独立模型质量。首轮test运行不等于调参；若以后依test改生产代码/参数，应登记污染并另留新的冻结组。

## M18 反馈来源与独立发布输入

m18_demo.json登记三条确定性证据审核的合成采集来源及一条物流拒绝来源；原运行结果可能成功、澄清或转接，不能作gold。每路审核仍需记录方法、审核者、理由和修订。三条release只作发布内容验证，不能导出到语料或FastText train；原M17固定回归不改。活动编号引用business.json已存在的000053，第一次发布评测前修正了未存在的演示编号；来源候选原件保留.local/MySQL。

新反馈按编号中立文本及source/variant组与所有历史公开数据隔离。语义独立性仍依靠同一作者审查，三条发布输入不证明企业泛化。学习新规则只允许审核后的有限字面短语，不允许把模型生成的代码或正则作为运行工件。
