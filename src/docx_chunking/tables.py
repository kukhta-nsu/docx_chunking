from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

__all__ = [
    "W", "q", "to_grid", "split_header", "to_records", "to_markdown",
    "nested", "iter_tables", "cell_text",
]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


EMPTY_VALUES = ("-", "–", "—")


def q(tag: str) -> str:
    return "{%s}%s" % (W, tag)


def cell_text(tc: ET.Element) -> str:
    parts = []
    for p in tc.findall(q("p")):
        t = "".join(n.text or "" for n in p.iter(q("t"))).strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def _props(tc: ET.Element) -> tuple[int, str | None]:
    pr = tc.find(q("tcPr"))
    span, vmerge = 1, None
    if pr is not None:
        g = pr.find(q("gridSpan"))
        if g is not None:
            span = int(g.get(q("val"), 1))
        v = pr.find(q("vMerge"))
        if v is not None:
            vmerge = v.get(q("val")) or "continue"
    return span, vmerge


def _is_header_row(tr: ET.Element) -> bool:
    pr = tr.find(q("trPr"))
    return pr is not None and pr.find(q("tblHeader")) is not None


def to_grid(tbl: ET.Element) -> tuple[list[list[str]], list[list[dict]]]:
    grid: list[list[str]] = []
    meta: list[list[dict]] = []
    for tr in tbl.findall(q("tr")):
        row: list[str] = []
        mrow: list[dict] = []
        col = 0
        is_head = _is_header_row(tr)
        for tc in tr.findall(q("tc")):
            span, vmerge = _props(tc)
            text = cell_text(tc)
            inherited = vmerge == "continue"
            if inherited:
                text = grid[-1][col] if grid and col < len(grid[-1]) else ""
            for _ in range(span):
                row.append(text)
                mrow.append({"inherited": inherited,
                             "spanned": span > 1,
                             "header": is_head})
                col += 1
        grid.append(row)
        meta.append(mrow)

    width = max((len(r) for r in grid), default=0)
    for r, m in zip(grid, meta):
        while len(r) < width:
            r.append("")
            m.append({"inherited": False, "spanned": False, "header": False})
    return grid, meta


def split_header(grid: list[list[str]], meta: list[list[dict]],
                 join: str = " / ") -> tuple[list[str], list[list[str]]]:
    n = sum(1 for m in meta if m and m[0]["header"])
    if n == 0:
        n = 1
        for i in range(1, min(3, len(grid))):
            prev, cur = meta[i - 1], meta[i]
            if all(cur[j]["inherited"] or prev[j]["spanned"]
                   for j in range(len(cur))):
                n = i + 1
            else:
                break
    if len(grid) > 1:
        n = min(n, len(grid) - 1)

    head_rows = grid[:n]
    cols = []
    for j in range(len(grid[0])):
        seen, parts = set(), []
        for r in head_rows:
            v = r[j].strip()
            if v and v not in seen:
                seen.add(v)
                parts.append(v)
        cols.append(join.join(parts))
    return cols, grid[n:]


def to_records(tbl: ET.Element, context: str = "", *,
               empty_values: tuple[str, ...] = EMPTY_VALUES) -> list[str]:
    grid, meta = to_grid(tbl)
    if not grid:
        return []
    cols, rows = split_header(grid, meta)

    out = []
    for r in rows:
        pairs = []
        for c, v in zip(cols, r):
            v, c = v.strip(), c.strip()
            if not v or v in empty_values:
                continue
            pairs.append(f"{c}: {v}" if c else v)
        if not pairs:
            continue
        rec = "; ".join(pairs)
        out.append(f"{context}. {rec}" if context else rec)
    return out


def to_markdown(tbl: ET.Element) -> str:
    grid, meta = to_grid(tbl)
    if not grid:
        return ""
    cols, rows = split_header(grid, meta)
    esc = lambda s: s.replace("\n", " ").replace("|", "\\|").strip()
    lines = ["| " + " | ".join(esc(c) for c in cols) + " |",
             "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(lines)


def nested(tbl: ET.Element):
    for tr in tbl.findall(q("tr")):
        for tc in tr.findall(q("tc")):
            for inner in tc.findall(q("tbl")):
                yield inner
                yield from nested(inner)


def iter_tables(path) -> list[ET.Element]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(q("body"))
    return list(body.iter(q("tbl")))
