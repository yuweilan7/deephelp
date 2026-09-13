# M13｜FastText 本机训练、量化、拒识与兜底接入

**阶段：** D 执行与治理

**前置：** M02, M09, M12

**来源：** 原 PDF 文件页 41–52、53–54、71。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式（v2）

默认 MODE=PLAN_MODULE。实际读取 AGENTS、CONTRACTS、PROJECT_STATE、当前相关代码/测试与直接前置 handoff，按[通用规划 Prompt](../PLAN_MODULE_PROMPT.md)先选择 DIRECT / DECOMPOSE / PROBE_FIRST / VERIFY_EXISTING，再给最小充分设计和实施任务；不强制拆分。明确要求实施时按本轮授权执行，不继续生成下一层规划。

原图按模块页码读取 `.local/references/deephelp-original.pdf` 或本轮 PDF 附件；GitHub 访问不包含被忽略的本地文件。已核对的原图不必每个 S 重读，旧解压稿仅为历史来源。保留现有 MySQL、uv workspace 和已记录的云 Milvus 实验，不重建 P00。一个 M 默认一个主会话顺序实施，相关测试随每个任务交付；跨会话从实际文件与最新合法 HEAD 接续。

## 本模块目标
复刻 FastText 的轻量意图兜底链路，重点是数据划分、预处理一致性、未知类和版本化发布，不是训练出一条漂亮收敛曲线。训练默认在个人电脑 CPU/Linux 环境，RTX4060 不是必须条件。

## 训练设计
使用 M02 注册表和已核验训练数据，保留来源及 split。原文 8:1:1 可作为起点，但按会话/语义改写组分割，不能把同模板改写随机散到三个集合。若某类样本不足，明确无法做可信泛化评估；可以完成训练流程，却不能宣传分类能力。

训练与在线推理共用同一 normalization/tokenization 函数、Jieba/词典/配置版本，覆盖全半角、空白、业务词、未知词；预处理变更必须重训或明确兼容。导出 __label__ 及 code 映射，不依赖字符串拆分猜层级。

限定 bucket、dim、wordNgrams、epoch、线程数等探索范围与内存预算；先做小样本计量再训练，原文 800MB→4.3MB 不是本项目保证。可比较量化前后大小/准确率/延迟，不为压到某个数字牺牲未知类安全。避免安装整套 PyTorch/CUDA 只训练 FastText。

## 推理与决策
Python 直接调用经当前环境验证的 FastText binding；原文 JNI 是 Java 适配层，在本项目无需复刻 JNI。返回 topK、raw probability、模型/预处理版本以及 unknown/need_review。FastText 概率不是已校准正确率，阈值与top1/top2间隔在 dev 集评估，低把握可进入已有LLM/人工兜底。

专门处理 p52 的截图矛盾：final_intent_code、topK、confidence_kind、is_actionable、need_review 必须一致；选择非top1需要确定性策略与理由。禁止出现最终低概率code却 needReview=false 的无解释结果。

## 接入和验收
M12 的 FallbackPort 只增加一个适配器，不另建一套识别服务。比较关闭FastText、启用FastText、量化版本三种模式的正确率/覆盖率/错误接管率、调用成本与实际速度。小模型没有优势可以保持默认关闭，但模块流程仍可验收为完成。

测试包含未知标签、越界code、模型损坏、预处理签名不匹配、空输入、OOV、只含编号、低置信误接管、训练集泄漏检查。云端只上传经审核的小模型和manifest，保留当前/上一版本，超过预算就仅本机推理。

交付训练脚本及实际支持的seed/config、数据审计、模型manifest、量化对照、推理Port、真实评测报告和回滚指针。训练集准确率与heldout结果必须分栏，合成语料标签必须显著标明。

## 接口与分期边界（v2复核）

先验证当前 Windows/Python 的 FastText binding；不支持时以已授权 Linux/WSL 小探针为先，不偷偷改主workspace环境。只设置实际绑定支持的随机种子/参数，记录线程数与模型hash，不虚构多线程训练逐字节可复现。训练—推理共用预处理，数据不足只能验流程，不能称泛化质量通过；默认关闭的模型也应保留可验证的独立训练/推理结果。

## 本轮交付

按通用规划 Prompt 自适应决定任务粒度；可只给一份实施任务，需要拆分时才给最少必要的 S 和依赖。已有成果先验收/补差异，不重复建设。保留本模块全部关键失败案例和适用的分层验收。路径：`docs/MODULES/M13.md`、`handoffs/M13.md`、`reports/M13/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。
