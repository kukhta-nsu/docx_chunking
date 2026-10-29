from __future__ import annotations

import math
import re
from typing import Protocol, runtime_checkable

__all__ = ["TokenCounter", "CharCounter", "HuggingFaceCounter",
           "default_counter", "sentences", "pack_pieces"]


@runtime_checkable
class TokenCounter(Protocol):
    def count(self, text: str) -> int:
        ...

    def split(self, text: str, budget: int, overlap: int = 0) -> list[str]:
        ...


SENTENCE = re.compile(r"(?<=[.!?…:;])\s+|\n+")


def sentences(text: str) -> list[str]:
    out, last = [], 0
    for m in SENTENCE.finditer(text):
        out.append(text[last:m.end()])
        last = m.end()
    tail = text[last:]
    if tail:
        out.append(tail)
    return out or [text]


def pack_pieces(pieces: list[str], budget: int, count, overlap: int = 0) -> list[str]:
    overlap = min(overlap, budget // 4)
    parts: list[list[str]] = []
    cur: list[str] = []
    n = 0
    for piece in pieces:
        k = count(piece)
        if cur and n + k > budget:
            parts.append(cur)
            cur, n = [], 0
            if overlap:
                for prev in reversed(parts[-1]):
                    if n + count(prev) > overlap:
                        break
                    cur.insert(0, prev)
                    n += count(prev)
        cur.append(piece)
        n += k
    if cur:
        parts.append(cur)
    return ["".join(p).strip() for p in parts if "".join(p).strip()]


class CharCounter:
    def __init__(self, chars_per_token: float = 3.0):
        if chars_per_token <= 0:
            raise ValueError("chars_per_token должен быть положительным")
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        return max(1, math.ceil(len(text) / self.chars_per_token)) if text else 0

    def split(self, text: str, budget: int, overlap: int = 0) -> list[str]:
        return pack_pieces(sentences(text), budget, self.count, overlap)

    def __repr__(self) -> str:
        return f"CharCounter(chars_per_token={self.chars_per_token})"


class HuggingFaceCounter:
    def __init__(self, model: str = "BAAI/bge-m3", *, tokenizer=None):
        self.model = model
        self._tok = tokenizer
        self._splitters: dict[tuple[int, int], object] = {}

    @property
    def tokenizer(self):
        if self._tok is None:
            try:
                from tokenizers import Tokenizer
            except ImportError as e:
                raise ImportError(
                    "для точного счёта токенов нужен пакет tokenizers: "
                    'pip install "docx-chunking[tokenizers]" '
                    "(или возьмите CharCounter)"
                ) from e
            self._tok = Tokenizer.from_pretrained(self.model)
        return self._tok

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def split(self, text: str, budget: int, overlap: int = 0) -> list[str]:
        sp = self._splitter(budget, overlap)
        if sp is None:
            return pack_pieces(sentences(text), budget, self.count, overlap)
        return [c for c in sp.chunks(text) if c.strip()]

    def _splitter(self, budget: int, overlap: int):
        key = (budget, overlap)
        if key not in self._splitters:
            try:
                from semantic_text_splitter import TextSplitter
            except ImportError:
                self._splitters[key] = None
            else:
                self._splitters[key] = TextSplitter.from_huggingface_tokenizer(
                    self.tokenizer, budget,
                    overlap=min(overlap, budget // 4))
        return self._splitters[key]

    def __repr__(self) -> str:
        return f"HuggingFaceCounter(model={self.model!r})"


def default_counter() -> TokenCounter:
    return CharCounter()
