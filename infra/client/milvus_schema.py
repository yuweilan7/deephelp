"""P00小型四维合成检索schema，与业务1024维签名空间分开。

text产生BM25 sparse，dense用FLAT/COSINE，tenant作过滤；调用者负责创建/关闭客户端。
"""

from pymilvus import DataType, Function, FunctionType, MilvusClient


def schema_and_indexes(client):
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field(
        "text",
        DataType.VARCHAR,
        max_length=1024,
        enable_analyzer=True,
        analyzer_params={"type": "chinese"},
    )
    schema.add_field("tenant", DataType.VARCHAR, max_length=20)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=4)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(
        Function(
            name="text_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse"],
        )
    )
    indexes = client.prepare_index_params()
    indexes.add_index(field_name="dense", index_type="FLAT", metric_type="COSINE")
    indexes.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    return schema, indexes
