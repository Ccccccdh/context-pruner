"""通用工具：token 计数（优先 tiktoken，缺省时用近似估算）。"""

from __future__ import annotations

from context_pruner.types import estimate_tokens

_ENCODER = None
_TOKENIZER_NAME = None


def count_tokens(text: str) -> int:
    """统一 token 计数口径。"""
    global _ENCODER
    global _TOKENIZER_NAME
    if _ENCODER is None:
        try:
            import tiktoken

            _ENCODER = tiktoken.get_encoding("cl100k_base")
            _TOKENIZER_NAME = "tiktoken:cl100k_base"
        except Exception:
            _ENCODER = False
            _TOKENIZER_NAME = "estimate_tokens:v1"
    if _ENCODER:
        return len(_ENCODER.encode(text))
    return estimate_tokens(text)


def tokenizer_name() -> str:
    if _TOKENIZER_NAME is None:
        count_tokens("")
    return str(_TOKENIZER_NAME)
