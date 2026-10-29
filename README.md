# docx-chunking

[![smoke](https://github.com/kukhta-nsu/docx_chunking/actions/workflows/ci.yml/badge.svg)](https://github.com/kukhta-nsu/docx_chunking/actions/workflows/ci.yml)

[Русская версия](README%28ru%29.md)

## What it is

A chunking tool: it parses Word `.docx` documents into a tree of numbered
sections and cuts it into chunks for retrieval. Tables are parsed in full —
merged cells and multi-level headers included.

The output is chunks enriched with metadata, saved as a single file in the
project: chunks/chunks.jsonl

## How to use it

**1. Download the project.**

```bash
git clone https://github.com/kukhta-nsu/docx_chunking
cd docx_chunking
```

**2. Install the tool.** Python 3.10 or newer:

```bash
pip install -e .
```

**3. Put your `.docx` in `documents/`.** The folder is already there, with a
note inside.

**4. Run it.**

```bash
docx-chunking chunk          # documents/*.docx  ->  chunks/chunks.jsonl
```

## What comes out

`chunks/chunks.jsonl` — one JSON line per chunk. This is a real chunk, from a
small document with a two-level table header:

```json
{
  "chunk_id": "handbook.docx#1-0",
  "file": "handbook.docx",
  "doc_code": "",
  "doc_title": "handbook",
  "section_num": "1",
  "section_all": "1,2,5,5.1",
  "section_title": "PURPOSE",
  "section_path": "PURPOSE",
  "kind": "mixed",
  "part": 0,
  "wide_row": false,
  "head": "handbook\n1 PURPOSE",
  "body": "Define how records are kept and who signs them.\n\nAll departments.\n\nNo: 1; Retention / place: Safety office; Retention / period: 3 years\nNo: 2; Retention / place: Archive; Retention / period: permanent",
  "n_tokens": 74,
  "n_chars": 220,
  "text": "handbook\n1 PURPOSE\n\nDefine how records are kept and who si …"
}
```

| Field | Meaning |
|---|---|
| `chunk_id` | `<document code or file name>#<section>-<part>` |
| `file` | The file the chunk was taken from |
| `doc_code` | The document code, when `--code-pattern` is given; empty otherwise |
| `doc_title` | File name without the extension |
| `section_num` | Number of the chunk's first section |
| `section_all` | Every section number in the chunk after merging — `1,2,5,5.1` |
| `section_title` | Title of the first section; empty when the document has no structure |
| `section_path` | The section path from the root: `PROCEDURE / STORAGE`; empty without structure |
| `kind` | `text`, `table` or `mixed`; useful as a search filter |
| `part` | Index of the chunk within its section group |
| `wide_row` | A table record longer than the budget is inside, so the chunk is deliberately larger than usual |
| `head` | The header: document title and section path. Context for the vector and a ready-made citation for the answer |
| `body` | The content itself, without the header |
| `n_tokens` | Size in tokens, by the counter you passed |
| `n_chars` | Size in characters |
| `text` | What goes into the embedding: header, blank line, content |

`-f csv` and `-f parquet` write the same fields. From Python,
`chunk_documents()` returns `Chunk` objects, and `to_dict()` gives the same
flat dictionary.

## How it works

**1. Parsing.** Structure is looked for in three ways, in order; the first one
that works wins:

| Way | When it applies |
|---|---|
| section numbering | there is consistent numbering — the preferred path |
| Word heading styles (`w:pStyle`, `w:outlineLvl`) | there is no numbering; section numbers are synthesized from the style levels |
| the whole document as one section | there is neither; the text is split on sentence boundaries, tables stay whole |

To set it explicitly — `--headings numbering|styles|flat`, or `headings=` in
`parse()`.

**2. Tables.** Merges are resolved: `gridSpan` duplicates the value to the
right, `vMerge:continue` inherits from above, a header of two or three rows is
joined into `Retention / place`.

**3. Chunking.** The unit is a section, not a window over the text: a section
has a number, a title and a path, you can cite it, and it almost always answers
one question in full.

Three rules apply:

| Rule | Why |
|---|---|
| Small kindred sections are merged | A two-line section is useless as a chunk of its own. Neighbours under one parent, and a parent with its child, are merged while the total fits the budget |
| An oversized section is split on sentence boundaries | A requirement cut mid-sentence stops being an answer |
| A table record is never split mid-pair | An offcut loses the link between values and column names |

```python
cfg = dc.ChunkConfig(
    target_tokens=600,          # chunk budget, header included
    hard_limit=1000,            # above this a chunk counts as a problem, except wide_row
    merge_below=60,             # sections shorter than this are merged
    overlap=80,                 # overlap when splitting continuous text
    head_template="{doc_title}\n{section_num} {section_path}",
    split_wide_records=False,   # True: no chunk exceeds the budget
)
chunks = dc.chunk_documents(docs, config=cfg)
```

**4. Checks.** A `.docx` parser does not fail with an error — it silently drops
content, and the result looks fine. So the checks are built into the tool:

* `validate()` runs on every `chunk`: no chunk over the limit without a flag,
  no section with content left out of the chunks, no table record cut in half.
* `coverage()`, in the `check` command, counts the `w:t` nodes in the file and
  the nodes the walk reaches. A gap means the parser did not see part of your
  document — the one failure that is invisible in the result.

## Which files

**`.docx` only.** The pre-2007 binary `.doc` cannot be read without an external
converter; convert such files beforehand, in Word or LibreOffice. `parse()` on
a `.doc` refuses with a clear message, and `chunk` over a folder prints every
`.doc` it found to stderr — rather than quietly leaving the document out of the
index. Word lock files (open files, `~$name.docx`) are skipped.

**Section numbering is desirable but not required.** Without it the heading
styles are used, and without those the whole document becomes one section:
there will be chunks, but `section_title` and `section_path` come out empty,
and there is nothing to cite in an answer. Which way worked for each file is
printed by `sections` and `chunk`.

**Extractable text is required.** There is no image handling and no OCR here: a
`.docx` with a single picture inside gives zero sections and zero chunks. Such
files are named on a line of their own.

**A one-row table yields no records:** its only row is read as a header. Word
uses such tables as frames, and the caption inside is dropped on purpose.

Before trusting a corpus, run `docx-chunking check` over your own folder — it
is the fastest way to find out whether these limits touch your documents.

## License

MIT, see [LICENSE](LICENSE).
