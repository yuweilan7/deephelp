# 回滚与恢复

本轮仅改动 DeepHelp 专属目录、容器、网络和 `deephelp-infra.service`。没有更改现有 SSH、防火墙规则、daemon.json、fstab、分区或云套餐。

## 暂停并保留数据

```bash
sudo systemctl stop deephelp-infra.service
sudo systemctl disable deephelp-infra.service
cd /srv/deephelp-infra
sudo bash scripts/compose.sh stop -t 120
```

仅需要移除项目容器/网络时执行 `sudo bash scripts/compose.sh down`；不要添加 `--volumes`，不要删除 bind 数据目录。镜像继续保留。用户决定彻底销毁数据时再单独确认范围，不使用全局 prune。

## MySQL

`sudo bash scripts/backup-mysql.sh` 在 guard 通过后执行事务一致性 mysqldump，gzip 保存至 `/srv/deephelp-backup`，文件 600。本实验只使用 InnoDB；备份期间不应执行 schema 变更。

`sudo bash scripts/restore-mysql.sh /srv/deephelp-backup/FILE.sql.gz deephelp_restore_NAME` 只允许新建独立恢复库，已有库会报错，不覆盖正式 deephelp。实际备份/恢复/中文查询证据见 `mysql-backup-restore.log`。保留恢复测试库便于复核。

## Milvus

轻量方案为合成 `client/dataset.json` + `client/collection-schema.json` + `client/milvus_schema.py` + `client/rebuild-milvus.py`。从本机、隧道开启时运行 `python client/rebuild-milvus.py --collection p00_rebuilt_NEWNAME`，新集合已有则拒绝覆盖。本轮已实际重建 36 条记录并验证 BM25；见 `milvus-rebuild.json`。

这不是全库备份工具。以后导入真实资料时，需要保存有权保留的原始资料、切分参数、向量/模型版本及元数据，才能重建。若必须复制整个 Standalone 数据目录，需先正常停止 Milvus、确认退出后再冷备完整目录；本轮没有把运行时热拷贝宣称为可靠备份。

## Redis 与本机

Redis 缓存不备份；业务事实、审批、幂等记录、最终状态必须保存在 MySQL。停止本机隧道使用 `client/tunnel.ps1 -Action stop` 或 `bash client/tunnel.sh stop`。复用的原始私钥未被删除或更改，私钥受限副本在用户 `.ssh` 内。

恢复运行：`sudo systemctl enable --now deephelp-infra.service`，然后运行服务器 doctor 和本机 health。重启/恢复后以真实认证查询确认，不凭容器 Up 判断。
