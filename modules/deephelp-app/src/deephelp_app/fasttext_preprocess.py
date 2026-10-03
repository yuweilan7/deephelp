"""One versioned CPU tokenizer shared by training, audit and inference."""

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

from deephelp_app.text_entity import width_normalize

DICTIONARY = Path(__file__).parent / "sample_data/m13_dictionary.txt"
PREPROCESS_VERSION = "m13-width-space-jieba-hmm-off-v1"


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FastTextPreprocessor:
    def __init__(self) -> None:
        import jieba  # type: ignore[import-untyped]

        jieba.setLogLevel(40)
        self.tokenizer = jieba.Tokenizer()
        self.tokenizer.load_userdict(str(DICTIONARY))
        self.spec = {
            "version": PREPROCESS_VERSION,
            "jieba": importlib.metadata.version("jieba"),
            "dictionary_sha256": hashlib.sha256(DICTIONARY.read_bytes()).hexdigest(),
            "base_dictionary_sha256": hashlib.sha256(
                Path(jieba.__file__).with_name("dict.txt").read_bytes()
            ).hexdigest(),
            "hmm": False,
            "token_pattern": r"[^\W_]+(?:[-_][^\W_]+)*",
            "max_chars": 2000,
        }
        self.signature = digest(self.spec)

    def normalize(self, text: str) -> str:
        return " ".join(width_normalize(text).lower().split())

    def tokens(self, text: str) -> tuple[str, ...]:
        normalized = self.normalize(text)
        # Never let user-supplied FastText label syntax enter the training label channel.
        normalized = normalized.replace("__label__", " ")
        return tuple(
            part
            for word in self.tokenizer.cut(normalized, HMM=False)
            for part in re.findall(str(self.spec["token_pattern"]), word)
        )

    def prepare(self, text: str) -> str:
        return " ".join(self.tokens(text))
