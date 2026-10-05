"""旧P00显式重建合成集合，只允许新p00_名字并拒绝覆盖现有数据。

脚本顶层即执行解析与连接，不应作为业务库导入；写后读回36行并查BM25。
这是重建协议证据，不能把四维合成数据或静态编号当业务Embedding质量。
"""

import argparse
import json
import time

from milvus_schema import schema_and_indexes
from settings import BASE, milvus_connect

p = argparse.ArgumentParser()
p.add_argument("--collection", default="p00_rebuilt_" + str(int(time.time())))
args = p.parse_args()
assert args.collection.startswith("p00_") and args.collection.replace("_", "").isalnum()
c = milvus_connect()
assert not c.has_collection(args.collection), "Refusing to overwrite an existing collection"
schema, indexes = schema_and_indexes(c)
rows = [
    r
    for r in json.loads((BASE / "client/dataset.json").read_text(encoding="utf-8"))
    if r["id"] != 999
]
c.create_collection(
    args.collection, schema=schema, index_params=indexes, consistency_level="Strong", num_shards=1
)
for start in range(0, len(rows), 16):
    c.insert(args.collection, rows[start : start + 16])
c.flush(args.collection)
c.load_collection(args.collection)
result = c.query(
    args.collection, filter="id >= 1", output_fields=["id"], limit=100, consistency_level="Strong"
)
assert len(result) == len(rows) == 36
search = c.search(args.collection, data=["物流运单查询"], anns_field="sparse", limit=3)
assert search[0]
(BASE / "reports/milvus-rebuild.json").write_text(
    json.dumps(
        {
            "status": "PASS",
            "collection": args.collection,
            "rows": len(result),
            "bm25_search": search,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print("MILVUS_REBUILD PASS", args.collection, len(result), "rows")
c.release_collection(args.collection)
c.close()
