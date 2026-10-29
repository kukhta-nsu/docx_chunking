from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from . import structure as DS
from . import tables as T
from .chunker import Chunk, ChunkConfig, record_pairs
from .tokens import sentences

__all__ = ["Coverage", "coverage", "corpus_coverage", "validate"]

q = T.q


@dataclass
class Coverage:
    file: str
    total: int
    seen: int

    @property
    def lost(self) -> int:
        return self.total - self.seen

    @property
    def complete(self) -> bool:
        return self.lost == 0


def coverage(path) -> Coverage:
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(q("body"))
    total = len(list(body.iter(q("t"))))

    seen = 0
    for el in DS.body_blocks(body):
        tag = el.tag.split("}")[1]
        if tag == "p":
            seen += len(list(el.iter(q("t"))))
        elif tag == "tbl":
            for t_el in (el, *T.nested(el)):
                for tr in t_el.findall(q("tr")):
                    for tc in tr.findall(q("tc")):
                        for pp in tc.findall(q("p")):
                            seen += len(list(pp.iter(q("t"))))
    name = getattr(path, "name", None) or str(path)
    return Coverage(file=str(name), total=total, seen=seen)


def corpus_coverage(paths) -> list[Coverage]:
    return [coverage(p) for p in paths]


def validate(docs, chunks: list[Chunk],
             config: ChunkConfig | None = None) -> list[str]:
    config = config or ChunkConfig()
    docs = list(docs)
    bad: list[str] = []

    over = [c for c in chunks if c.n_tokens > config.hard_limit and not c.wide_row]
    if over:
        bad.append(f"чанков сверх предела {config.hard_limit} без wide_row: "
                   f"{len(over)} — {[c.chunk_id for c in over[:3]]}")

    covered = {(c.file, n) for c in chunks for n in c.section_all.split(",")}
    lost = [(d.meta.file, s.num) for d in docs for s in d.sections
            if s.blocks and (d.meta.file, s.num) not in covered]
    if lost:
        bad.append(f"разделов с содержимым вне чанков: {len(lost)} — {lost[:3]}")

    lines = {l.strip() for c in chunks for l in c.body.split("\n") if l.strip()}
    recs = [re.sub(r"\s*\n\s*", " ", r)
            for d in docs for s in d.sections
            for b in s.blocks if isinstance(b, DS.Table) for r in b.records]
    missing = [r for r in recs if r not in lines]
    if missing and config.split_wide_records:
        seen = {p.strip() for l in lines for p in record_pairs(l)}
        seen |= {s.strip() for l in lines for s in sentences(l) if s.strip()}
        missing = [r for r in missing if not _covered(r, seen)]
    if missing:
        bad.append(f"записей таблиц разорвано: {len(missing)} из {len(recs)}"
                   f" — {[r[:60] for r in missing[:2]]}")

    return bad


def _covered(rec: str, seen: set[str]) -> bool:
    for pair in record_pairs(rec):
        if pair.strip() in seen:
            continue
        if all(s.strip() in seen for s in sentences(pair) if s.strip()):
            continue
        return False
    return True
