"""Post-processing: DoclingDocument JSON -> docpack (sections/*.md with page anchors, INDEX.md,
tables/, registers.jsonl, search.sqlite). Needs docling-core (installed with docling)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from pdfk import memmap as memmapmod
from pdfk import registers as regmod
from pdfk import search as searchmod
from pdfk.paths import slugify, write_jsonl, save_json

SEC_RE = re.compile(r"^\s*(?:(?:chapter|section|appendix)\s+)?([A-Z]?\d+(?:\.\d+)*)\.?\s+(\S.*)$", re.I)
TOC_PLACEHOLDER = "<!-- printed table of contents omitted; see INDEX.md -->"
FIG_IN_TEXT_RE = re.compile(r"(?:Figure|Fig\.)\s+(\d+)\.\s+\S.*", re.S)
FIG_REFERENCE_RE = re.compile(r"\b(in|see|to|and|by|from|of|on)\s*$", re.I)
MIN_FIG_PT = (60.0, 40.0)  # width, height in PDF points; matches the PNG size filter at convert time
TOC_TITLE_RE = re.compile(r"^(?:table\s+of\s+contents|contents|list\s+of\s+(?:tables|figures|examples))$", re.I)
LEADER_RE = re.compile(r"(?:\.\s?){6,}")
SPLIT_ID_RE = re.compile(r"(?<=[A-Za-z0-9]) _(?=[A-Za-z0-9])")  # docling artifact: "PLL _SYS" -> "PLL_SYS"
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
    cells: list[dict] | None = None  # tables with merged cells: exact cell geometry


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
            grid.append([fix_text((c.text or "").replace("\n", " ").strip()) for c in row])
            for c in row:
                if c.row_span > 1 or c.col_span > 1:
                    spans = True
        cells = None
        if spans:
            cells = [
                {"r": c.start_row_offset_idx, "c": c.start_col_offset_idx, "rs": c.row_span, "cs": c.col_span,
                 "text": fix_text(" ".join((c.text or "").split())),
                 **({"hdr": True} if c.column_header or c.row_header else {})}
                for c in sorted(data.table_cells, key=lambda c: (c.start_row_offset_idx, c.start_col_offset_idx))
            ]
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
        return Block("table", fix_text(md), page, table_id=tid, grid=grid, spans=spans, title=caption, cells=cells)

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
                htext = fix_text(item.text.strip())
                sec, title = _parse_heading(htext)
                blocks.append(Block("heading", htext, page_of(item), level=level, sec=sec, title=title))
                walk(item)
            elif isinstance(item, GroupItem):
                if item.label in (GroupLabel.LIST, GroupLabel.ORDERED_LIST, GroupLabel.INLINE):
                    md = fix_text(ser(item))
                    if md:
                        blocks.append(Block("list", md, page_of(item)))
                else:
                    walk(item)
            elif isinstance(item, TableItem):
                blocks.append(add_table(item, page_of(item)))
            elif isinstance(item, PictureItem):
                if item.prov:
                    bb = item.prov[0].bbox
                    if abs(bb.r - bb.l) < MIN_FIG_PT[0] or abs(bb.t - bb.b) < MIN_FIG_PT[1]:
                        continue  # note / warning icons, logos
                cap = item.caption_text(doc).strip()
                fig_count[0] += 1
                try:  # same id as the PNG written at convert time: index in doc.pictures
                    fid = f"f{int(item.self_ref.rsplit('/', 1)[-1]):04d}"
                except ValueError:
                    fid = f"f{fig_count[0]:04d}"
                # text inside the drawing (block names, signal labels) makes figures searchable
                labels: list[str] = []
                for ch in item.children:
                    sub = ch.resolve(doc)
                    t = getattr(sub, "text", "")
                    if t and ch.cref not in caption_refs:
                        labels.append(fix_text(" ".join(t.split())))
                label_text = ", ".join(dict.fromkeys(labels))[:600]
                page = page_of(item)
                blocks.append(Block("picture", "", page, title=cap, table_id=fid, grid=[[label_text]]))
            elif isinstance(item, TextItem):
                md = fix_text(ser(item))
                if not md:
                    continue
                kind = "code" if item.label == DocItemLabel.CODE else "text"
                blocks.append(Block(kind, md, page_of(item)))
            else:
                walk(item) if hasattr(item, "children") else None

    walk(doc.body)
    _adopt_margin_captions(blocks)
    return blocks, tables


def fix_text(text: str) -> str:
    """Repair identifiers that the PDF text layer splits at underscores ("PLL _SYS", "PLL _CS_LOCK_BITS")."""
    return SPLIT_ID_RE.sub("_", text) if " _" in text else text


def _is_leader_block(b: Block) -> bool:
    """Table-of-contents style content: most lines/cells carry dot leaders ("1.2 Summary . . . . 10")."""
    if b.kind == "table":
        cells = [c for row in (b.grid or []) for c in row if c.strip()]
    elif b.kind in ("text", "list"):
        cells = [line for line in b.text.splitlines() if line.strip()]
    else:
        return False
    if not cells:
        return False
    return sum(1 for c in cells if LEADER_RE.search(c)) / len(cells) >= 0.4


def drop_toc(blocks: list[Block], tables: list[dict]) -> int:
    """Remove the printed table of contents / list of tables / list of figures. They duplicate INDEX.md,
    and every entry matched searches for its own title ("Crystal Oscillator … 217"), pushing the real
    section out of the top hits. Returns the number of blocks removed."""
    out: list[Block] = []
    in_toc = False
    toc_level = 0
    removed_tables: set[str] = set()
    removed = 0
    for b in blocks:
        if b.kind == "heading":
            # only unnumbered front-matter headings; "2.19.6 List of Registers" is real content
            if not b.sec and TOC_TITLE_RE.match(b.title.strip()):
                in_toc, toc_level = True, b.level
                out.append(b)
                out.append(Block("text", TOC_PLACEHOLDER, b.page))
                continue
            if in_toc and (b.sec or b.level <= toc_level):
                in_toc = False
        if in_toc or _is_leader_block(b):
            removed += 1
            if b.kind == "table":
                removed_tables.add(b.table_id)
            continue
        out.append(b)
    blocks[:] = out
    tables[:] = [t for t in tables if t["id"] not in removed_tables]
    return removed


def _adopt_margin_captions(blocks: list[Block]) -> None:
    """Manuals that print 'Figure 35. …' in the page margin leave pictures without a caption, and the
    layout model often glues that caption to the end of an unrelated paragraph or moves it to the
    previous page. Give each caption-less picture the nearest unclaimed 'Figure N. …' text."""
    claimed: set[str] = set()
    for i, b in enumerate(blocks):
        if b.kind != "picture" or b.title:
            continue
        best: tuple[int, int, str, str] | None = None  # (not-at-start, distance, figure number, caption)
        for j in range(max(0, i - 15), min(len(blocks), i + 16)):
            c = blocks[j]
            if c.kind not in ("text", "list") or c.page not in (b.page - 1, b.page, b.page + 1):
                continue
            for m in FIG_IN_TEXT_RE.finditer(c.text):
                before = c.text[max(0, m.start() - 14): m.start()]
                if FIG_REFERENCE_RE.search(before):
                    continue  # "… as shown in Figure 35. The PLL …" is a reference, not a caption
                cand = (int(m.start() > 0), abs(j - i), m.group(1), " ".join(m.group(0).split())[:220])
                if cand[2] not in claimed and (best is None or cand[:2] < best[:2]):
                    best = cand
        if best is not None:
            claimed.add(best[2])
            b.title = best[3]


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


def render_part(part: Part, doc_id: str, source: str, figure_files: set[str] | None = None) -> tuple[str, list[dict], list[dict]]:
    """Return markdown text, heading records and search records (with 1-based line numbers)."""
    figure_files = figure_files or set()
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
            merged = f" (merged cells: pdfk table {doc_id} {b.table_id} --cells)" if b.spans else ""
            lines.append(f"<!-- table {b.table_id} p.{b.page}{merged} -->")
        if b.kind == "picture":
            labels = (b.grid or [[""]])[0][0]
            has_png = b.table_id in figure_files
            b.text = (f"<!-- figure {b.table_id} p.{b.page}: {b.title or 'no caption'}"
                      + (f" | file: figures/{b.table_id}.png" if has_png else "")
                      + (f" | labels: {labels}" if labels else "") + " -->")
        line_no = len(lines) + 1
        lines.extend(b.text.splitlines())
        lines.append("")
        if b.kind == "table":
            text_for_index = _table_index_text(b)
        elif b.kind == "picture":
            text_for_index = f"{b.title}\n{(b.grid or [['']])[0][0]}".strip() or "figure"
        else:
            text_for_index = b.text
        kind = "figure" if b.kind == "picture" else b.kind
        if b.text == TOC_PLACEHOLDER:
            continue
        records.append({"file": part.name + ".md", "line": line_no, "page": b.page, "section": sec_path, "kind": kind, "text": text_for_index})
    return "\n".join(lines).rstrip() + "\n", headings, records


def _one_line(s: str) -> str:
    return " ".join((s or "").split())


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
    try:
        from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
    except ImportError as e:
        raise SystemExit(
            "pdfk build/rebuild need docling, which this Python does not have (query commands work without it). "
            "Install the full CLI: uv tool install \"git+https://github.com/admSla99/pdfk.git#subdirectory=cli\", "
            "or point PDFK_PYTHON at an interpreter that has docling."
        ) from e

    from pdfk.convert import load_doc, save_doc_gz
    from pdfk.paths import DOC_JSON_GZ, doc_json_path

    src_json = doc_json_path(pack_dir)
    if src_json is None:
        raise SystemExit(f"{pack_dir}: no {DOC_JSON_GZ} (run `pdfk build <pdf>` to convert the document)")
    doc = load_doc(src_json)
    if src_json.suffix != ".gz":  # migrate legacy uncompressed packs (46 MB -> a few MB)
        save_doc_gz(doc, pack_dir / DOC_JSON_GZ)
        src_json.unlink()
    serializer = MarkdownDocSerializer(
        doc=doc,
        params=MarkdownParams(escape_underscores=False, escape_html=False, image_placeholder="<!-- image -->"),
    )
    blocks, tables = extract_blocks(doc, serializer)
    toc_removed = drop_toc(blocks, tables)
    level_strategy = normalize_levels(blocks)
    parts = split_parts(blocks)

    sec_dir = pack_dir / "sections"
    if sec_dir.exists():
        for old in sec_dir.glob("*.md"):
            old.unlink()
    sec_dir.mkdir(parents=True, exist_ok=True)
    all_headings: list[dict] = []
    all_records: list[dict] = []
    fig_dir = pack_dir / "figures"
    figure_files = {f.stem for f in fig_dir.glob("f*.png")} if fig_dir.is_dir() else set()
    for p in parts:
        md, headings, records = render_part(p, doc_id, source, figure_files)
        (sec_dir / (p.name + ".md")).write_text(md, encoding="utf-8")
        all_headings.extend(headings)
        all_records.extend(records)

    # tables
    tdir = pack_dir / "tables"
    tdir.mkdir(exist_ok=True)
    for old in list(tdir.glob("t*.csv")) + list(tdir.glob("t*.json")):
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
            if b.cells:  # the CSV repeats merged values; the exact geometry lives next to it
                save_json(tdir / f"{t['id']}.json", {"id": t["id"], "page": t["page"], "caption": t["caption"],
                                                     "rows": t["rows"], "cols": t["cols"], "cells": b.cells})

    # figures index (captions + labels are always indexed; PNG files exist only after `build --figures`)
    pictures = [b for b in blocks if b.kind == "picture"]
    n_fig_files = 0
    if pictures:
        fig_dir.mkdir(exist_ok=True)
        cur_sec = ""
        sec_of_fig: dict[str, str] = {}
        for b in blocks:
            if b.kind == "heading" and (b.sec or not cur_sec):
                cur_sec = b.sec or b.title[:40]
            elif b.kind == "picture":
                sec_of_fig[b.table_id] = cur_sec
        with (fig_dir / "index.tsv").open("w", encoding="utf-8", newline="") as f:
            f.write("id\tpage\tsection\tfile\tcaption\tlabels\n")
            for b in pictures:
                has = b.table_id in figure_files
                n_fig_files += has
                labels = (b.grid or [[""]])[0][0]
                f.write(f"{b.table_id}\t{b.page}\t{sec_of_fig.get(b.table_id, '')}\t"
                        f"{'figures/' + b.table_id + '.png' if has else ''}\t{_one_line(b.title)}\t{_one_line(labels[:300])}\n")

    # registers + memory map
    regs = regmod.extract_registers(blocks, doc_id=doc_id, profile=profile, records=all_records)
    mm = memmapmod.extract_memmap(blocks)
    for e in mm:
        e["doc"] = doc_id
    linked = memmapmod.link_registers(regs, mm)
    write_jsonl(pack_dir / "registers.jsonl", regs)
    write_jsonl(pack_dir / "memmap.jsonl", mm)

    # headings map + index
    title = doc.name or doc_id
    first_title = next((b.title for b in blocks if b.kind == "heading"), "")
    stem = Path(source).stem if source else ""
    if first_title and len(first_title) < 80 and (not title or title.endswith(".pdf") or title in (doc_id, stem)):
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
        "memmap": len(mm),
        "registers_linked": linked,
        "toc_blocks_dropped": toc_removed,
        "figures": len(pictures),
        "figure_files": n_fig_files,
        "blocks": len(blocks),
        "levels": level_strategy,
    }
