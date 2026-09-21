"""Post-processing: DoclingDocument JSON -> docpack (sections/*.md with page anchors, INDEX.md,
tables/, registers.jsonl, search.sqlite). Needs docling-core (installed with docling)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from pdfk import registers as regmod
from pdfk import search as searchmod
from pdfk.paths import slugify, write_jsonl, save_json

SEC_RE = re.compile(r"^\s*(?:(?:chapter|section|appendix)\s+)?([A-Z]?\d+(?:\.\d+)*)\.?\s+(\S.*)$", re.I)
MAX_FILE_BYTES = 150_000
MIN_FILE_BYTES = 12_000


@dataclass
class Block:
    kind: str  # heading | text | list | table | picture | code
    text: str
    page: int
    level: int = 0
    sec: str = ""
    title: str = ""
    table_id: str = ""
    grid: list[list[str]] | None = None  # tables only
    spans: bool = False


LABEL_TITLES = {"description", "warning", "note", "caution", "registers", "register", "offset", "offsets", "reset", "table"}


@dataclass
class Part:
    """One output markdown file."""

    name: str
    blocks: list[Block] = field(default_factory=list)
    head: Block | None = None
    parent: Block | None = None  # nearest numbered heading above `head` (for naming unnumbered splits)

    @property
    def size(self) -> int:
        return sum(len(b.text) + 2 for b in self.blocks)

    @property
    def pages(self) -> tuple[int, int]:
        ps = [b.page for b in self.blocks if b.page]
        return (min(ps), max(ps)) if ps else (0, 0)


# --------------------------------------------------------------------------- extraction


def _parse_heading(text: str) -> tuple[str, str]:
    m = SEC_RE.match(text.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return "", text.strip()


def extract_blocks(doc, serializer) -> tuple[list[Block], list[dict]]:
    """Walk the body tree in reading order and produce blocks + table records."""
    from docling_core.types.doc import (
        ContentLayer,
        DocItemLabel,
        GroupItem,
        GroupLabel,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )

    blocks: list[Block] = []
    tables: list[dict] = []
    last_page = [0]
    fig_count = [0]
    caption_refs: set[str] = set()
    for t in list(doc.tables) + list(doc.pictures):
        for c in t.captions:
            caption_refs.add(c.cref)

    def page_of(item) -> int:
        prov = getattr(item, "prov", None)
        if prov:
            last_page[0] = prov[0].page_no
            return prov[0].page_no
        if isinstance(item, GroupItem):
            for ch in item.children:
                p = page_of(ch.resolve(doc))
                if p:
                    return p
        return last_page[0]

    def ser(item) -> str:
        return serializer.serialize(item=item).text.strip()

    def add_table(item: TableItem, page: int) -> Block:
        tid = f"t{len(tables) + 1:04d}"
        grid: list[list[str]] = []
        spans = False
        data = item.data
        for row in data.grid:
            grid.append([(c.text or "").replace("\n", " ").strip() for c in row])
            for c in row:
                if c.row_span > 1 or c.col_span > 1:
                    spans = True
        caption = item.caption_text(doc).strip()
        md = ser(item)
        tables.append(
            {
                "id": tid,
                "page": page,
                "rows": data.num_rows,
                "cols": data.num_cols,
                "spans": spans,
                "caption": caption,
            }
        )
        return Block("table", md, page, table_id=tid, grid=grid, spans=spans, title=caption)

    def walk(node):
        for ref in node.children:
            item = ref.resolve(doc)
            layer = getattr(item, "content_layer", ContentLayer.BODY)
            if layer != ContentLayer.BODY:
                continue
            if ref.cref in caption_refs:
                continue
            if isinstance(item, (SectionHeaderItem, TitleItem)):
                level = item.level if isinstance(item, SectionHeaderItem) else 1
                sec, title = _parse_heading(item.text)
                blocks.append(Block("heading", item.text.strip(), page_of(item), level=level, sec=sec, title=title))
                walk(item)
            elif isinstance(item, GroupItem):
                if item.label in (GroupLabel.LIST, GroupLabel.ORDERED_LIST, GroupLabel.INLINE):
                    md = ser(item)
                    if md:
                        blocks.append(Block("list", md, page_of(item)))
                else:
                    walk(item)
            elif isinstance(item, TableItem):
                blocks.append(add_table(item, page_of(item)))
            elif isinstance(item, PictureItem):
                cap = item.caption_text(doc).strip()
                fig_count[0] += 1
                fid = f"f{fig_count[0]:04d}"
                blocks.append(Block("picture", f"<!-- figure {fid}: {cap or 'no caption'} -->", page_of(item), title=cap))
            elif isinstance(item, TextItem):
                md = ser(item)
                if not md:
                    continue
                kind = "code" if item.label == DocItemLabel.CODE else "text"
                blocks.append(Block(kind, md, page_of(item)))
            else:
                walk(item) if hasattr(item, "children") else None

    walk(doc.body)
    return blocks, tables


# --------------------------------------------------------------------------- splitting


def normalize_levels(blocks: list[Block]) -> str:
    """Derive heading levels from section numbering when the document is numbered
    (vendor manuals always are). Unnumbered headings ("Description", "WARNING",
    register names) nest one level below the last numbered heading, so they never
    split chapters. Returns the strategy used."""
    numbered = [b for b in blocks if b.kind == "heading" and b.sec]
    if len(numbered) < 3:
        return "docling"
    min_depth = min(b.sec.count(".") + 1 for b in numbered)
    last_level = 1
    for b in blocks:
        if b.kind != "heading":
            continue
        if b.sec:
            b.level = min(6, b.sec.count(".") + 1 - min_depth + 1)
            last_level = b.level
        else:
            b.level = min(6, last_level + 1)
    return "numbering"


def split_parts(blocks: list[Block]) -> list[Part]:
    levels = [b.level for b in blocks if b.kind == "heading"]
    if not levels:
        return [Part("00-document", blocks)]
    top = min(levels)

    parts: list[Part] = []
    cur = Part("00-front-matter")
    for b in blocks:
        if b.kind == "heading" and b.level == top:
            parts.append(cur)
            cur = Part("", head=b)
        cur.blocks.append(b)
    parts.append(cur)

    def split_deeper(p: Part, level: int) -> list[Part]:
        """Recursively split an oversized part at the next heading level present
        (numbered headings preferred; label-like headings such as "Description" never split)."""
        if p.size <= MAX_FILE_BYTES:
            return [p]
        cands = [b for b in p.blocks if b.kind == "heading" and b.level > level and b.title.strip().lower() not in LABEL_TITLES]
        numbered = [b.level for b in cands if b.sec]
        sub_levels = numbered or [b.level for b in cands]
        if not sub_levels:
            return [p]
        sub = min(sub_levels)
        pieces: list[Part] = []
        acc = Part("", head=p.head, parent=p.parent)
        last_numbered = p.head if p.head and p.head.sec else p.parent
        for b in p.blocks:
            if b.kind == "heading" and b.level == sub and b in cands and acc.blocks and acc.size > MIN_FILE_BYTES:
                pieces.append(acc)
                acc = Part("", head=b, parent=last_numbered)
            if b.kind == "heading" and b.sec:
                last_numbered = b
            acc.blocks.append(b)
        pieces.append(acc)
        out: list[Part] = []
        for piece in pieces:
            out.extend(split_deeper(piece, sub))
        return out

    out: list[Part] = []
    for p in parts:
        out.extend(split_deeper(p, top))

    # merge tiny consecutive parts (flat-heading fallback), but never grow a file past 3×MIN
    merged: list[Part] = []
    for p in out:
        if merged and p.head and merged[-1].head and p.size < MIN_FILE_BYTES and merged[-1].size + p.size < 3 * MIN_FILE_BYTES:
            merged[-1].blocks.extend(p.blocks)
        else:
            merged.append(p)
    parts = [p for p in merged if p.blocks]

    for i, p in enumerate(parts):
        if p.head:
            sec = p.head.sec or (p.parent.sec if p.parent and p.parent.sec else "")
            label = (sec.replace(".", "_") + "-" if sec else "") + slugify(p.head.title, 32)
            p.name = f"{i:02d}-{label}"
        elif not p.name:
            p.name = f"{i:02d}-part"
    return parts


# --------------------------------------------------------------------------- rendering


def render_part(part: Part, doc_id: str, source: str) -> tuple[str, list[dict], list[dict]]:
    """Return markdown text, heading records and search records (with 1-based line numbers)."""
    lines: list[str] = []
    headings: list[dict] = []
    records: list[dict] = []
    title = part.head.text if part.head else "Front matter"
    p0, p1 = part.pages
    lines += ["---", f"doc: {doc_id}", f'title: "{title}"', f"pages: {p0}-{p1}", f"source: {source}", "---", ""]
    cur_page = 0
    sec_path = ""
    for b in part.blocks:
        if b.kind == "heading":
            if b.sec or not sec_path:
                sec_path = b.sec or b.title[:40]
            cur_page = b.page
            line_no = len(lines) + 1
            lines.append(f"{'#' * max(1, b.level)} {b.text} <!-- p.{b.page} -->")
            lines.append("")
            headings.append(
                {"sec": b.sec, "title": b.title, "level": b.level, "page": b.page, "file": part.name + ".md", "line": line_no}
            )
            records.append({"file": part.name + ".md", "line": line_no, "page": b.page, "section": sec_path, "kind": "heading", "text": b.text})
            continue
        if b.page and b.page != cur_page:
            cur_page = b.page
            lines.append(f"<!-- p.{b.page} -->")
        if b.kind == "table":
            lines.append(f"<!-- table {b.table_id} p.{b.page}{' (grid: merged cells, see tables/' + b.table_id + '.csv)' if b.spans else ''} -->")
        line_no = len(lines) + 1
        lines.extend(b.text.splitlines())
        lines.append("")
        text_for_index = b.text if b.kind != "table" else _table_index_text(b)
        records.append({"file": part.name + ".md", "line": line_no, "page": b.page, "section": sec_path, "kind": b.kind, "text": text_for_index})
    return "\n".join(lines).rstrip() + "\n", headings, records


def _table_index_text(b: Block) -> str:
    rows = [" | ".join(c for c in r if c) for r in (b.grid or [])]
    return (b.title + "\n" if b.title else "") + "\n".join(rows)


def render_index(doc_id: str, title: str, pages: int, parts: list[Part], headings: list[dict], depth: int = 2) -> str:
    levels = sorted({h["level"] for h in headings})
    keep = set(levels[:depth])
    out = [f"# {title} — INDEX", f"doc: {doc_id} · {pages} pages · {len(parts)} section files",
           f"Query: `pdfk search \"<q>\" --doc {doc_id}` · `pdfk section {doc_id} <sec>` · `pdfk reg <NAME>` · "
           f"`pdfk toc {doc_id} <sec>` for deeper headings. Read files with offset/limit only.", ""]
    by_file: dict[str, list[dict]] = {}
    for h in headings:
        by_file.setdefault(h["file"], []).append(h)
    for p in parts:
        fn = p.name + ".md"
        p0, p1 = p.pages
        head = f"§{p.head.sec} {p.head.title}" if p.head and p.head.sec else (p.head.title if p.head else "Front matter")
        out.append(f"## sections/{fn}  {head}  p.{p0}-{p1}")
        for i, h in enumerate(by_file.get(fn, [])):
            if h["level"] not in keep:
                continue
            if i == 0 and p.head and h["title"] == p.head.title:
                continue  # already in the file header line
            label = f"§{h['sec']} {h['title']}" if h["sec"] else h["title"]
            out.append(f"- {label}  p.{h['page']}")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- driver


def build_pack(pack_dir: Path, doc_id: str, source: str, profile: str = "generic") -> dict:
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    from docling_core.types.doc import DoclingDocument

    doc = DoclingDocument.load_from_json(pack_dir / "docling.json")
    serializer = MarkdownDocSerializer(
        doc=doc,
        params=MarkdownParams(escape_underscores=False, escape_html=False, image_placeholder="<!-- image -->"),
    )
    blocks, tables = extract_blocks(doc, serializer)
    level_strategy = normalize_levels(blocks)
    parts = split_parts(blocks)

    sec_dir = pack_dir / "sections"
    if sec_dir.exists():
        for old in sec_dir.glob("*.md"):
            old.unlink()
    sec_dir.mkdir(parents=True, exist_ok=True)
    all_headings: list[dict] = []
    all_records: list[dict] = []
    for p in parts:
        md, headings, records = render_part(p, doc_id, source)
        (sec_dir / (p.name + ".md")).write_text(md, encoding="utf-8")
        all_headings.extend(headings)
        all_records.extend(records)

    # tables
    tdir = pack_dir / "tables"
    tdir.mkdir(exist_ok=True)
    for old in tdir.glob("t*.csv"):
        old.unlink()
    tb_by_id = {b.table_id: b for b in blocks if b.kind == "table"}
    sec_of_table: dict[str, str] = {}
    cur_sec = ""
    for b in blocks:
        if b.kind == "heading":
            if b.sec or not cur_sec:
                cur_sec = b.sec or b.title[:40]
        elif b.kind == "table":
            sec_of_table[b.table_id] = cur_sec
    with (tdir / "index.tsv").open("w", encoding="utf-8", newline="") as f:
        f.write("id\tpage\tsection\trows\tcols\tspans\tcaption\n")
        for t in tables:
            f.write(f"{t['id']}\t{t['page']}\t{sec_of_table.get(t['id'], '')}\t{t['rows']}\t{t['cols']}\t{int(t['spans'])}\t{t['caption']}\n")
            b = tb_by_id[t["id"]]
            with (tdir / f"{t['id']}.csv").open("w", encoding="utf-8", newline="") as cf:
                csv.writer(cf).writerows(b.grid or [])

    # registers
    regs = regmod.extract_registers(blocks, doc_id=doc_id, profile=profile, records=all_records)
    write_jsonl(pack_dir / "registers.jsonl", regs)

    # headings map + index
    title = doc.name or doc_id
    first_title = next((b.title for b in blocks if b.kind == "heading"), "")
    if first_title and len(first_title) < 80 and (not title or title.endswith(".pdf") or title == doc_id):
        title = first_title
    save_json(pack_dir / "sections.json", all_headings)
    (pack_dir / "INDEX.md").write_text(render_index(doc_id, title, len(doc.pages), parts, all_headings), encoding="utf-8")

    # search index
    searchmod.build_index(pack_dir / "search.sqlite", doc_id, all_records)

    return {
        "title": title,
        "pages": len(doc.pages),
        "sections": len(parts),
        "headings": len(all_headings),
        "tables": len(tables),
        "registers": len(regs),
        "blocks": len(blocks),
        "levels": level_strategy,
    }
