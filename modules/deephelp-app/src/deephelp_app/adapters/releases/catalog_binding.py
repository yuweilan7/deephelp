"""完整发布的目录闭包：配置选择也是需要验收的运行语义。

旧发布无此字段，继续使用原三叶子配置。六叶子发布显式说明旧FastText
只保留用于恢复，不能拿其三标签结论接管新目录；主意图仍走原600与结构化兜底。
本模块只验证字节与关联，不读冻结测试、不调用模型、不改变active。
"""

from typing import Any

from deephelp_app.adapters.releases.runtime_assets import read_data
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.cascade import CascadePolicy
from deephelp_app.application.corpus import read_corpus
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.retrieval import HybridScope


def catalog_binding(data: dict[str, Any], catalog: str) -> dict[str, Any] | None:
    """拒绝目录/语料/检索权重混配；消费文件须由原资产files完整绑定。"""
    path = data.get("catalog_binding")
    if path is None:
        if catalog != "complaints-v1":
            raise ConfigurationError("Expanded release requires an explicit catalog binding")
        return None
    binding = read_data(resolve_artifact(path))
    required = {str(resolve_artifact(path))}
    if (
        binding.get("format") != "m42-catalog-binding-v1"
        or binding.get("catalog_version") != catalog
        or catalog != "complaints-v3"
        or binding.get("classifier_mode") != "not_configured"
        or binding.get("classifier_reason")
        != "retained_three_leaf_model_not_calibrated_for_six_leaf"
    ):
        raise ConfigurationError("Unsupported or mismatched catalog/classifier binding")
    for key in ("corpus", "cascade_policy", "dev", "test"):
        required.add(str(resolve_artifact(binding[key])))
    if not required.issubset({str(resolve_artifact(p)) for p in data["files"]}):
        raise ConfigurationError("Catalog consumed artifacts have no immutable fingerprints")
    pointer = read_data(resolve_artifact(data["dense_pointer"]))
    scope = HybridScope.model_validate(pointer["active"])
    corpus = read_corpus(resolve_artifact(binding["corpus"]))
    corpus.require_valid()
    policy = CascadePolicy.load(resolve_artifact(binding["cascade_policy"]))
    if (
        scope.registry_version != catalog
        or corpus.index_digest != pointer["corpus_digest"]
        or policy.corpus_digest != corpus.index_digest
        or policy.scope_fingerprint != scope.fingerprint
        or policy.dense_weight != pointer["dense_weight"]
        or binding.get("dev_digest") != pointer["selection"]["dev_digest"]
        or binding.get("test_digest") != pointer["selection"]["test_digest"]
    ):
        raise ConfigurationError("Catalog corpus/scope/selection/cascade binding changed")
    return binding
