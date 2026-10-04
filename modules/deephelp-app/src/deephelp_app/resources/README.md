# 运行资源

只包含runtime策略、FastText词典、SOP/Prompt/schema和增量SQL；从deephelp_app.resources或importlib.resources读取。运行包不包含demo事实、冻结集、fake或探针。

冻结输入及版本口径见[数据集说明](../../../../../datasets/README.md)，样例事实见[demo](../../../../../demo/README.md)，离线入口见[工具包](../../../../deephelp-tools/README.md)。这些资源没有默认运行消费者；Hybrid显式提供已发布语料路径，MCP显式选择已安装的服务组件。

runtime-v2绑定运行包代码及资源的实际字节；tooling_digest由独立工具包计算。旧清单/报告保持不变，代码变化仍须精确内容评测和新清单；本次结构交付不发布新版本。
