from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .structure import Document, Section
from .tokens import TokenCounter, default_counter

__all__ = ["ChunkConfig", "Chunk", "chunk_document", "chunk_documents",
           "record_pairs", "split_record"]


BLOCK_SEP = "\n\n"


RECORD_SEP = "\n"


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 600
    hard_limit: int = 1000
    merge_below: int = 60
    overlap: int = 80
    head_template: str = "{doc_title}\n{section_num} {section_path}"
    split_wide_records: bool = False

    def __post_init__(self):
        if self.target_tokens < 1:
            raise ValueError("target_tokens должен быть положительным")
        if self.hard_limit < self.target_tokens:
            raise ValueError("hard_limit не может быть меньше target_tokens")


@dataclass
class Chunk:
    chunk_id: str
    file: str
    doc_code: str
    doc_title: str
    section_num: str
    section_all: str
    section_title: str
    section_path: str
    kind: str
    part: int
    wide_row: bool
    head: str
    body: str
    n_tokens: int = 0
    n_chars: int = 0

    @property
    def text(self) -> str:
        return f"{self.head}{BLOCK_SEP}{self.body}" if self.head else self.body

    def to_dict(self) -> dict:
        d = asdict(self)
        d["text"] = self.text
        return d


@dataclass
class _Unit:
    kind: str
    text: str
    n: int
    wide: bool = False


def units_of(sec: Section) -> list[tuple[str, str | list[str]]]:
    units: list[tuple[str, str | list[str]]] = []
    buf: list[str] = []
    for b in sec.blocks:
        if isinstance(b, str):
            buf.append(b)
        else:
            if buf:
                units.append(("text", "\n".join(buf)))
                buf = []


            units.append(("table", [re.sub(r"\s*\n\s*", " ", r) for r in b.records]))
    if buf:
        units.append(("text", "\n".join(buf)))
    return units


def parent(num: str) -> str:
    return num.rsplit(".", 1)[0] if "." in num else ""


def group_sections(secs: list[Section], count, config: ChunkConfig
                   ) -> list[list[Section]]:
    groups: list[list[Section]] = []
    sizes: dict[int, int] = {}
    for s in secs:
        sizes[id(s)] = count(s.text) if s.blocks else 0
        if groups:
            g = groups[-1]
            total = sum(sizes[id(x)] for x in g)
            kin = (parent(s.num) == parent(g[-1].num)
                   or s.num.startswith(g[-1].num + "."))
            if (kin and total + sizes[id(s)] <= config.target_tokens
                    and (sizes[id(s)] < config.merge_below
                         or total < config.merge_below)):
                g.append(s)
                continue
        groups.append([s])
    return groups


PAIR_SEP = "; "


PAIR_START = re.compile(r"[^;:]{1,80}: \S")


def record_pairs(rec: str, sep: str = PAIR_SEP) -> list[str]:
    out: list[str] = []
    for p in rec.split(sep):
        if out and not PAIR_START.match(p):
            out[-1] += sep + p
        else:
            out.append(p)
    return out


def _split_by_pairs(rec: str, budget: int, count, sep: str) -> list[str]:
    pairs = record_pairs(rec, sep)
    if len(pairs) < 3:
        return [rec]
    key = pairs[0]
    key_cost = count(key + sep)
    if key_cost >= budget:
        return [rec]

    out: list[str] = []
    cur: list[str] = []
    n = 0
    for p in pairs[1:]:
        k = count(p + sep)
        if cur and key_cost + n + k > budget:
            out.append(sep.join([key] + cur))
            cur, n = [], 0
        cur.append(p)
        n += k
    if cur:
        out.append(sep.join([key] + cur))
    return out or [rec]


def _split_by_sentences(part: str, budget: int, count, split_text,
                        sep: str) -> list[str]:
    pairs = record_pairs(part, sep)
    i = max(range(len(pairs)), key=lambda k: count(pairs[k]))
    others = [p for k, p in enumerate(pairs) if k != i]
    col, _, value = pairs[i].partition(": ")
    if not value:
        col, value = "", pairs[i]
    head = sep.join(others + ([f"{col}:"] if col else []))


    room = budget - count(head + " ") - 1
    if room < max(20, budget // 4):
        return [part]
    return [f"{head} {piece}".strip() if head else piece
            for piece in split_text(value, room)]


def split_record(rec: str, budget: int, count, split_text=None,
                 sep: str = PAIR_SEP) -> list[str]:
    parts = _split_by_pairs(rec, budget, count, sep)
    if split_text is None:
        return parts
    out: list[str] = []
    for p in parts:
        if count(p) <= budget:
            out.append(p)
        else:
            out += _split_by_sentences(p, budget, count, split_text, sep)
    return out


def split_table(recs: list[str], budget: int, count,
                split_wide: bool = False, split_text=None
                ) -> tuple[list[str], int]:
    if split_wide:
        recs = [part for r in recs
                for part in (split_record(r, budget, count, split_text)
                             if count(r) > budget else [r])]
    sizes = [count(r) for r in recs]
    local = max([budget] + sizes)
    sep_cost = count(RECORD_SEP)
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for r, k in zip(recs, sizes):
        if cur and n + sep_cost + k > local:
            out.append(RECORD_SEP.join(cur))
            cur, n = [], 0
        cur.append(r)
        n += k + (sep_cost if len(cur) > 1 else 0)
    if cur:
        out.append(RECORD_SEP.join(cur))
    return out, local


def pack(units: list[_Unit], budget: int, sep_cost: int = 0
         ) -> list[list[_Unit]]:
    out: list[list[_Unit]] = []
    cur: list[_Unit] = []
    n = 0
    for u in units:
        if cur and n + sep_cost + u.n > budget:
            out.append(cur)
            cur, n = [], 0
        cur.append(u)
        n += u.n + (sep_cost if len(cur) > 1 else 0)
    if cur:
        out.append(cur)
    return out


def chunk_document(doc: Document, *, counter: TokenCounter | None = None,
                   config: ChunkConfig | None = None) -> list[Chunk]:
    config = config or ChunkConfig()
    counter = counter or default_counter()
    count = counter.count
    meta = doc.meta
    res: list[Chunk] = []

    for g in group_sections(doc.sections, count, config):
        first = g[0]
        head = config.head_template.format(
            doc_title=meta.title, doc_code=meta.code, file=meta.file,
            section_num=first.num, section_title=first.title,
            section_path=first.path).strip() if config.head_template else ""


        budget = max(1, config.target_tokens
                     - (count(head + BLOCK_SEP) if head else 0))
        sep_cost = count(BLOCK_SEP)

        flat: list[_Unit] = []
        for s in g:
            for kind, payload in units_of(s):
                if kind == "table":
                    parts, local = split_table(payload, budget, count,
                                               config.split_wide_records,
                                               counter.split)
                    for part in parts:
                        flat.append(_Unit("table", part, count(part),
                                          local > budget))
                else:
                    k = count(payload)
                    parts = ([payload] if k <= budget
                             else counter.split(payload, budget, config.overlap))
                    for part in parts:
                        flat.append(_Unit("text", part, count(part)))
        if not flat:
            continue

        for i, bundle in enumerate(pack(flat, budget, sep_cost)):
            kinds = {u.kind for u in bundle}
            body = BLOCK_SEP.join(u.text for u in bundle)
            chunk = Chunk(
                chunk_id=f"{meta.code or meta.file}#{first.num}-{i}",
                file=meta.file,
                doc_code=meta.code,
                doc_title=meta.title,
                section_num=first.num,
                section_all=",".join(x.num for x in g),
                section_title=first.title,
                section_path=first.path,
                kind=("table" if kinds == {"table"}
                      else "text" if kinds == {"text"} else "mixed"),
                part=i,
                wide_row=any(u.wide for u in bundle),
                head=head,
                body=body,
            )
            chunk.n_tokens = count(chunk.text)
            chunk.n_chars = len(chunk.text)
            res.append(chunk)
    return res


def chunk_documents(docs, *, counter: TokenCounter | None = None,
                    config: ChunkConfig | None = None) -> list[Chunk]:
    counter = counter or default_counter()
    config = config or ChunkConfig()
    out: list[Chunk] = []
    for doc in docs:
        out += chunk_document(doc, counter=counter, config=config)
    return out
