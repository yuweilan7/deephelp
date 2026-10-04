# 显式合成业务样例

`data/business.json` 为原三类业务事实，`business-fixtures-v2.json` 为22情境事实，`m09_corpus.jsonl` 为原演示检索语料。M19迁移保持所有文件字节、编号、标签和split不变。

服务实现仅有一份，位于[工具包demo](../modules/deephelp-tools/src/deephelp_tools/demo/)。应用通过正式SDK/stdio协议调用它，必须显式选择 `--mcp-module deephelp_tools.demo.mcp_server`；Hybrid同时提供实际发布语料路径。数据合成与协议真实分别说明，不冒充MySQL可维护业务记录。

tools wheel打包这些相同字节，安装后的独立demo服务使用包资源；不另复制根Python实现。数据库模式在M20实现，连接失败不会静默回退JSON。
