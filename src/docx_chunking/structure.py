from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from . import tables as T

__all__ = ["Table", "Section", "DocumentMeta", "Document", "parse",
           "heading_candidate", "accepts", "pick_headings", "body_blocks",
           "para_text", "is_toc", "style_levels", "para_style_level",
           "HEADING_SOURCES"]

q = T.q

NUM = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.*?)\s*$")
LEADER = re.compile(r"[.…]{3,}\s*\.*\s*\d+\s*$")
WORD = re.compile(r"[A-Za-zА-Яа-яЁё]{3}")


MAX_HEADING_LEN = 120

HEADING_SOURCES = ("auto", "numbering", "styles", "flat")

STYLE_HEADING = re.compile(r"^(?:heading|заголовок)\s*([1-9])$", re.I)
STYLE_TITLE = ("title", "название", "наименование")


@dataclass
class Table:
    records: list[str]

    def __len__(self) -> int:
        return len(self.records)


Block = str | Table


@dataclass
class Section:
    num: str
    title: str
    path: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        out: list[str] = []
        for b in self.blocks:
            out.extend([b] if isinstance(b, str) else b.records)
        return "\n".join(out)

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def level(self) -> int:
        return self.num.count(".") + 1


@dataclass
class DocumentMeta:
    file: str
    title: str
    code: str = ""
    n_tables: int = 0
    n_toc: int = 0
    headings: str = ""
    front: list[Block] = field(default_factory=list)
    path: str = ""


@dataclass
class Document:
    meta: DocumentMeta
    sections: list[Section] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.sections

    def __iter__(self):
        return iter(self.sections)


def para_text(p: ET.Element) -> str:
    return "".join(n.text or "" for n in p.iter(q("t"))).strip()


def is_toc(p: ET.Element, text: str) -> bool:
    if "PAGEREF" in "".join(n.text or "" for n in p.iter(q("instrText"))):
        return True
    return bool(LEADER.search(text))


def heading_candidate(text: str) -> tuple[tuple[int, ...], str] | None:
    m = NUM.match(text)
    if not m or len(text) > MAX_HEADING_LEN:
        return None
    num, title = m.group(1), m.group(2)
    title = re.sub(r"\s*\d+$", "", title) if len(title) > 8 else title
    title = title.strip()
    if not title or title[-1] in ";," or not WORD.search(title):
        return None
    if title.endswith(".") and len(title.split()) > 6:
        return None
    return tuple(int(x) for x in num.split(".")), title


def accepts(cur: tuple[int, ...], new: tuple[int, ...]) -> bool:
    if len(new) > len(cur) + 1:
        return False
    k = 0
    while k < min(len(cur), len(new)) and cur[k] == new[k]:
        k += 1
    if k == len(new):
        return False
    if k == len(cur):
        return True
    return new[k] > cur[k]


def pick_headings(cands: list[tuple[int, tuple[int, ...]]]) -> set[int]:
    n = len(cands)
    if not n:
        return set()
    best, prev = [1] * n, [-1] * n
    for j in range(n):
        for i in range(j):
            if best[i] + 1 > best[j] and accepts(cands[i][1], cands[j][1]):
                best[j], prev[j] = best[i] + 1, i
    j = max(range(n), key=lambda k: best[k])
    chain = set()
    while j != -1:
        chain.add(cands[j][0])
        j = prev[j]
    return chain


def body_blocks(el: ET.Element):
    for child in el:
        if child.tag == q("sdt"):
            content = child.find(q("sdtContent"))
            if content is not None:
                yield from body_blocks(content)
        else:
            yield child


def document_body(path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("word/document.xml")).find(q("body"))


def heading_level(name: str) -> int:
    name = (name or "").strip().lower().replace("\u00a0", " ")
    m = STYLE_HEADING.match(name)
    if m:
        return int(m.group(1))
    return 1 if name in STYLE_TITLE else 0


def style_levels(path) -> dict[str, int]:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/styles.xml")
    except (KeyError, zipfile.BadZipFile):
        return {}
    out: dict[str, int] = {}
    for st in ET.fromstring(xml).findall(q("style")):
        sid = st.get(q("styleId")) or ""
        el = st.find(q("name"))
        lvl = heading_level(sid) or heading_level(
            el.get(q("val")) if el is not None else "")
        if sid and lvl:
            out[sid] = lvl
    return out


def para_style_level(p: ET.Element, levels: dict[str, int]) -> int:
    pr = p.find(q("pPr"))
    if pr is None:
        return 0
    ol = pr.find(q("outlineLvl"))
    if ol is not None:
        try:
            v = int(ol.get(q("val"), "9"))
        except ValueError:
            v = 9
        if 0 <= v <= 8:
            return v + 1
    st = pr.find(q("pStyle"))
    if st is not None:
        return levels.get(st.get(q("val"), ""), 0)
    return 0


def numbering_heads(blocks) -> tuple[dict, int]:
    cands: list[tuple[int, tuple[int, ...], str]] = []
    n_toc = 0
    for idx, el in enumerate(blocks):
        if el.tag != q("p"):
            continue
        text = para_text(el)
        if not text:
            continue
        if is_toc(el, text):
            n_toc += 1
            continue
        cand = heading_candidate(text)
        if cand:
            cands.append((idx, cand[0], cand[1]))
    chain = pick_headings([(c[0], c[1]) for c in cands])
    return {c[0]: (c[1], c[2]) for c in cands if c[0] in chain}, n_toc


def style_heads(blocks, levels: dict[str, int]) -> dict:
    heads: dict[int, tuple[tuple[int, ...], str]] = {}
    counters: list[int] = []
    for idx, el in enumerate(blocks):
        if el.tag != q("p"):
            continue
        text = para_text(el)
        if not text or is_toc(el, text) or len(text) > MAX_HEADING_LEN:
            continue
        lvl = para_style_level(el, levels)
        if not lvl:
            continue
        while len(counters) < lvl - 1:
            counters.append(1)
        counters = counters[:lvl]
        if len(counters) < lvl:
            counters.append(1)
        else:
            counters[-1] += 1
        heads[idx] = (tuple(counters), text)
    return heads


def parse(path, *, code_pattern: str | re.Pattern | None = None,
          headings: str = "auto") -> Document:
    name = os.path.basename(str(path))
    if name.lower().endswith(".doc"):
        raise ValueError(
            f"{name}: .doc — бинарный формат до 2007 года, zip-пакета в нём "
            f"нет и структуру взять негде. Сконвертируйте файл в .docx "
            f"заранее: этот пакет работает только с .docx")
    try:
        body = document_body(path)
    except zipfile.BadZipFile as e:
        raise ValueError(f"{name}: не похоже на .docx — внутри нет "
                         f"zip-пакета OOXML") from e
    if body is None:
        raise ValueError(f"{name}: в word/document.xml нет элемента w:body")
    blocks = list(body_blocks(body))

    if headings not in HEADING_SOURCES:
        raise ValueError(f"headings: {headings!r}, а допустимы "
                         f"{', '.join(HEADING_SOURCES)}")

    num_heads, n_toc = numbering_heads(blocks)
    if headings == "numbering":
        heads, source = num_heads, "numbering"
    elif headings == "styles":
        heads, source = style_heads(blocks, style_levels(path)), "styles"
    elif headings == "flat":
        heads, source = {}, "flat"
    elif len(num_heads) >= 2:
        heads, source = num_heads, "numbering"
    else:
        by_style = style_heads(blocks, style_levels(path))
        heads, source = (by_style, "styles") if by_style else ({}, "flat")


    front: list[Block] = []
    sections: list[Section] = []
    titles: dict[tuple[int, ...], str] = {}
    cur: Section | None = None
    n_tbl = 0
    for idx, el in enumerate(blocks):
        tag = el.tag.split("}")[1]
        if tag == "p":
            text = para_text(el)
            if not text or is_toc(el, text):
                continue
            if idx in heads:
                num, title = heads[idx]
                titles[num] = title
                parts = [titles.get(num[:i + 1]) for i in range(len(num))]
                cur = Section(num=".".join(map(str, num)), title=title,
                              path=" / ".join(p for p in parts if p))
                sections.append(cur)
            else:
                (cur.blocks if cur else front).append(text)
        elif tag == "tbl":
            for t_el in (el, *T.nested(el)):
                n_tbl += 1
                recs = T.to_records(t_el)
                if recs:
                    (cur.blocks if cur else front).append(Table(recs))

    if source == "flat" and front:
        sections = [Section(num="1", title="", path="", blocks=front)]
        front = []

    code = ""
    if code_pattern is not None:
        rx = (code_pattern if isinstance(code_pattern, re.Pattern)
              else re.compile(code_pattern, re.I))
        front_text = " ".join(b for b in front[:40] if isinstance(b, str))
        m = rx.search(name) or rx.search(front_text)
        if m:
            code = re.sub(r"\s+", " ", m.group(1) if m.groups() else m.group(0))

    meta = DocumentMeta(file=name, code=code,
                        title=re.sub(r"\.docx?$", "", name, flags=re.I),
                        n_tables=n_tbl, n_toc=n_toc, front=front,
                        headings=source, path=str(path))
    return Document(meta=meta, sections=sections)
