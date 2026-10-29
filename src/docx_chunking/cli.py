from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from . import __version__
from .checks import corpus_coverage, validate
from .chunker import ChunkConfig, chunk_documents
from .structure import HEADING_SOURCES, Table, parse
from .tokens import CharCounter, HuggingFaceCounter
from . import tables as T


CORPUS_DIR = "documents"
CHUNKS_DIR = "chunks"
OUT_NAME = "chunks.jsonl"

STDOUT = "-"


def find_project(start=None) -> Path | None:
    start = Path(start or Path.cwd()).resolve()
    for d in (start, *start.parents):
        if (d / CORPUS_DIR).is_dir():
            return d
    return None


def project_root(paths) -> Path:
    for raw in paths:
        p = Path(raw).resolve()
        base = p if p.is_dir() else p.parent
        if base.name == CORPUS_DIR:
            return base.parent
        found = find_project(base)
        if found is not None:
            return found
    return find_project() or Path.cwd()


def default_out(paths) -> Path:
    return project_root(paths) / CHUNKS_DIR / OUT_NAME


def collect(paths, recursive: bool = False) -> list[Path]:
    if not paths:
        root = find_project()
        if root is None:
            raise SystemExit(
                f"не нашёл каталог {CORPUS_DIR}/ ни здесь, ни выше по дереву "
                f"— создайте его в корне проекта и положите туда .docx, "
                f"либо укажите путь аргументом")
        paths = [root / CORPUS_DIR]

    out: list[Path] = []
    locks: list[Path] = []
    legacy: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            pat = p.rglob if recursive else p.glob
            found = sorted(pat("*.docx"))
            out += [f for f in found if not f.name.startswith("~$")]
            locks += [f for f in found if f.name.startswith("~$")]
            legacy += sorted(f for f in pat("*.doc")
                             if not f.name.startswith("~$"))
        else:
            out.append(p)
    missing = [p for p in out if not p.exists()]
    if missing:
        raise SystemExit(f"нет файла: {missing[0]}")
    if locks:
        print(f"пропущено блокировок Word: {len(locks)}", file=sys.stderr)
    if legacy:
        print(f"НЕ РАЗОБРАНО .doc: {len(legacy)} — пакет работает только "
              f"с .docx, сконвертируйте эти файлы:", file=sys.stderr)
        for f in legacy[:5]:
            print(f"  {f.name}", file=sys.stderr)
        if len(legacy) > 5:
            print(f"  ... и ещё {len(legacy) - 5}", file=sys.stderr)
    if not out:
        where = ", ".join(str(p) for p in paths)
        raise SystemExit(f"не найдено ни одного .docx в {where}")
    return out


def make_counter(args):
    if args.tokenizer:
        return HuggingFaceCounter(args.tokenizer)
    return CharCounter(args.chars_per_token)


def load_docs(args, files):
    docs = []
    for p in files:
        docs.append(parse(p, code_pattern=args.code_pattern,
                          headings=args.headings))
    return docs


def cmd_sections(args) -> int:
    files = collect(args.paths, args.recursive)
    counter = make_counter(args)
    for p in files:
        doc = parse(p, code_pattern=args.code_pattern,
                    headings=args.headings)
        m = doc.meta
        print(f"\n=== {m.file}"
              + (f"  [{m.code}]" if m.code else "")
              + f"  структура: {m.headings}"
              + f"  разделов {len(doc.sections)}, таблиц {m.n_tables}"
              + (f", строк оглавления {m.n_toc}" if m.n_toc else ""))
        if doc.is_empty:
            print("  нет разделов: в документе нет извлекаемого текста "
                  "(возможно, это форма из одной картинки)")
        for s in doc.sections:
            n_tbl = sum(1 for b in s.blocks if isinstance(b, Table))
            mark = "" if s.blocks else "  (пусто)"
            print(f"  {'  ' * (s.level - 1)}{s.num} {s.title}"
                  f"  [{counter.count(s.text)} ток."
                  + (f", таблиц {n_tbl}" if n_tbl else "") + "]" + mark)
    return 0


def cmd_check(args) -> int:
    files = collect(args.paths, args.recursive)
    covs = corpus_coverage(files)
    total = sum(c.total for c in covs)
    seen = sum(c.seen for c in covs)
    bad = [c for c in covs if not c.complete]
    print(f"файлов: {len(files)} | текстовых узлов: {seen} из {total}"
          + (f" — ТЕРЯЕМ {total - seen} в {len(bad)} файлах"
             if bad else " (потерь нет)"))
    for c in bad:
        print(f"  {Path(c.file).name[:70]}: -{c.lost}")
    return 1 if bad else 0


def cmd_tables(args) -> int:
    for p in collect(args.paths, args.recursive):
        print(f"\n=== {p.name}")
        for i, tbl in enumerate(T.iter_tables(p), 1):
            md = T.to_markdown(tbl)
            if md:
                print(f"\n--- таблица {i}\n{md}")
    return 0


def cmd_chunk(args) -> int:
    files = collect(args.paths, args.recursive)
    config = ChunkConfig(target_tokens=args.target, hard_limit=args.hard,
                         merge_below=args.merge_below, overlap=args.overlap,
                         head_template=args.head_template,
                         split_wide_records=args.split_wide_records)
    counter = make_counter(args)

    docs = load_docs(args, files)
    empty = [d.meta.file for d in docs if d.is_empty]
    by_src: dict[str, int] = {}
    for d in docs:
        by_src[d.meta.headings] = by_src.get(d.meta.headings, 0) + 1
    print(f"файлов: {len(files)}"
          f" | разделов: {sum(len(d.sections) for d in docs)}"
          f" | таблиц: {sum(d.meta.n_tables for d in docs)}"
          f" | структура: {', '.join(f'{k} {v}' for k, v in sorted(by_src.items()))}"
          + (f" | без текста: {len(empty)}" if empty else ""),
          file=sys.stderr)
    for f in empty:
        print(f"  без извлекаемого текста: {f}", file=sys.stderr)

    chunks = chunk_documents(docs, counter=counter, config=config)
    if not chunks:
        raise SystemExit("ни одного чанка: в документах нет размеченных разделов")

    sizes = [c.n_tokens for c in chunks]
    kinds: dict[str, int] = {}
    for c in chunks:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    print(f"чанков: {len(chunks)}"
          f" | медиана {statistics.median(sizes):.0f} ток."
          f" | максимум {max(sizes)}"
          f" | {', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}"
          f" | с расширенным бюджетом: {sum(c.wide_row for c in chunks)}",
          file=sys.stderr)

    problems = validate(docs, chunks, config)
    if problems:
        print("ПРОБЛЕМЫ ЧАНКИНГА:", file=sys.stderr)
        for p in problems:
            print("  -", p, file=sys.stderr)
    else:
        print("  контроли пройдены", file=sys.stderr)

    write_chunks(chunks, args.out or default_out(args.paths), args.format)
    return 1 if problems else 0


def write_chunks(chunks, out, fmt: str) -> None:
    rows = [c.to_dict() for c in chunks]
    if out == STDOUT:
        if fmt != "jsonl":
            raise SystemExit(f"формат {fmt} в stdout не пишется, нужен -o ФАЙЛ")
        sys.stdout.write("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                 for r in rows))
        return

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shown = out
    try:
        shown = out.relative_to(Path.cwd())
    except ValueError:
        pass
    if fmt == "jsonl":
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                               for r in rows), encoding="utf-8")
        print(f"записано: {shown} — чанков {len(chunks)}", file=sys.stderr)
        return

    if fmt == "csv":
        import csv
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    elif fmt == "parquet":
        try:
            import pandas as pd
        except ImportError as e:
            raise SystemExit('для parquet нужны pandas и pyarrow: '
                             'pip install "docx-chunking[parquet]"') from e
        pd.DataFrame(rows).to_parquet(out, index=False)
    print(f"записано: {shown} — чанков {len(chunks)}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="docx-chunking",
        description="Разбор .docx в разделы и нарезка на чанки для поиска.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("paths", nargs="*",
                       help=f"файлы .docx или каталоги с ними "
                            f"(по умолчанию {CORPUS_DIR}/ в корне проекта)")
        p.add_argument("-r", "--recursive", action="store_true",
                       help="искать в подкаталогах")
        p.add_argument("--headings", default="auto", choices=HEADING_SOURCES,
                       help="откуда брать структуру: auto (нумерация, иначе "
                            "стили Word, иначе весь документ одним разделом), "
                            "numbering, styles, flat")
        p.add_argument("--code-pattern", default=None,
                       help="регулярное выражение кода документа; "
                            "первая группа попадает в метаданные чанков")

    def tok(p):
        p.add_argument("--tokenizer", default=None,
                       help="токенизатор HuggingFace для точного счёта "
                            "токенов, например BAAI/bge-m3")
        p.add_argument("--chars-per-token", type=float, default=3.0,
                       help="знаков на токен для оценки без токенизатора "
                            "(по умолчанию 3.0)")

    p = sub.add_parser("chunk", help="нарезать корпус на чанки")
    common(p)
    tok(p)
    p.add_argument("-o", "--out", default=None,
                   help=f"файл результата (по умолчанию "
                        f"{CHUNKS_DIR}/{OUT_NAME} в корне проекта — там, где "
                        f"лежит {CORPUS_DIR}/); {STDOUT} — печатать в stdout")
    p.add_argument("-f", "--format", default="jsonl",
                   choices=["jsonl", "csv", "parquet"])
    p.add_argument("--target", type=int, default=600, help="бюджет чанка")
    p.add_argument("--hard", type=int, default=1000,
                   help="предел, выше которого чанк считается проблемой")
    p.add_argument("--merge-below", type=int, default=60,
                   help="склеивать разделы короче этого")
    p.add_argument("--overlap", type=int, default=80,
                   help="перехлёст при нарезке сплошного текста")
    p.add_argument("--split-wide-records", action="store_true",
                   help="резать запись таблицы, не влезающую в бюджет, по "
                        "границам пар «колонка: значение» вместо расширения "
                        "бюджета; нужно моделям с коротким окном")
    p.add_argument("--head-template",
                   default=ChunkConfig.head_template,
                   help="шапка чанка; поля doc_title, doc_code, file, "
                        "section_num, section_title, section_path")
    p.set_defaults(func=cmd_chunk)

    p = sub.add_parser("sections", help="показать дерево разделов")
    common(p)
    tok(p)
    p.set_defaults(func=cmd_sections)

    p = sub.add_parser("check", help="проверить полноту разбора")
    common(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("tables", help="показать таблицы в markdown")
    common(p)
    p.set_defaults(func=cmd_tables)

    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
