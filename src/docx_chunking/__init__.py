from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"docx-chunking нужен Python 3.10 или новее, а запущен "
        f"{sys.version_info.major}.{sys.version_info.minor}. "
        f"На macOS системный python3 — 3.9; возьмите свежий "
        f"(brew install python, или conda, или python.org)")

from . import tables
from .checks import Coverage, corpus_coverage, coverage, validate
from .chunker import Chunk, ChunkConfig, chunk_document, chunk_documents
from .structure import Document, DocumentMeta, Section, Table, parse
from .tokens import CharCounter, HuggingFaceCounter, TokenCounter

__version__ = "0.1.0"

__all__ = [
    "parse", "parse_dir", "Document", "DocumentMeta", "Section", "Table",
    "chunk_document", "chunk_documents", "chunk_file", "Chunk", "ChunkConfig",
    "TokenCounter", "CharCounter", "HuggingFaceCounter",
    "coverage", "corpus_coverage", "validate", "Coverage",
    "tables", "__version__",
]


def parse_dir(directory, pattern: str = "*.docx", *,
              code_pattern=None, recursive: bool = False,
              headings: str = "auto") -> list[Document]:
    d = Path(directory)
    files = sorted(d.rglob(pattern) if recursive else d.glob(pattern))
    return [parse(p, code_pattern=code_pattern, headings=headings)
            for p in files if not p.name.startswith("~$")]


def chunk_file(path, *, counter=None, config: ChunkConfig | None = None,
               code_pattern=None, headings: str = "auto") -> list[Chunk]:
    return chunk_document(parse(path, code_pattern=code_pattern,
                                headings=headings),
                          counter=counter, config=config)
