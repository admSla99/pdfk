"""pdfk command line. Query commands are stdlib-only; build/rebuild/verify need docling."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from pdfk import __version__
from pdfk.paths import (
    DOC_JSON_GZ,
    DOC_KINDS,
    DocEntry,
    doc_id_from_pdf,
    doc_json_path,
    find_root,
    list_docs,
    load_json,
    read_jsonl,
    require_root,
    save_doc_entry,
    write_jsonl,
)


# ----------------------------------------------------------------------------- helpers


def _pick_doc(root: Path, doc: str | None) -> list[str]:
    docs = list_docs(root)
    if doc:
        if doc not in docs:
            raise SystemExit(f"unknown doc '{doc}'. Available: {', '.join(docs) or '(none)'}")
        return [doc]
    return list(docs)


def _write_qa(pack: Path, entry: DocEntry, verify_summary: dict | None) -> None:
    lines = [f"# QA — {entry.id}", "", f"Built {entry.built} with docling {entry.docling_version}; {entry.pages} pages in {entry.convert_seconds}s.",
             f"Confidence: mean={entry.grades.get('mean', '?')} low={entry.grades.get('low', '?')}.", ""]
    if entry.low_pages:
        lines += [f"## Pages with low confidence ({len(entry.low_pages)})", ", ".join(map(str, entry.low_pages)), ""]
    if verify_summary:
        lines += ["## Register verification (PDF text layer)",
                  f"ok={verify_summary['ok']} mismatch={verify_summary['mismatch']} unchecked={verify_summary['unchecked']}", ""]
        for m in verify_summary["mismatches"][:200]:
            lines.append(f"- {m['name']} p.{m['page']}: missing {', '.join(m['missing'])}")
        lines.append("")
    (pack / "QA.md").write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------- commands


def cmd_build(a) -> int:
    from pdfk.convert import convert_pdf, sha256_of
    from pdfk.docpack import build_pack

    pdf = Path(a.pdf).resolve()
    if not pdf.is_file():
        raise SystemExit(f"not a file: {pdf}")
    root = find_root(a.root) or (Path.cwd() / ".pdfk")
    root.mkdir(parents=True, exist_ok=True)
    doc_id = a.id or doc_id_from_pdf(pdf)
    pack = root / doc_id
    pack.mkdir(parents=True, exist_ok=True)
    sha = sha256_of(pdf)
    page_range = None
    if a.pages:
        lo, _, hi = a.pages.partition("-")
        page_range = (int(lo), int(hi or lo))

    existing = list_docs(root).get(doc_id)
    entry = existing or DocEntry(id=doc_id)
    same = existing and existing.pdf_sha256 == sha and existing.page_range == (list(page_range) if page_range else None) and doc_json_path(pack) is not None
    if same and a.figures and not any((pack / "figures").glob("f*.png")):
        same = False  # figures were requested but never exported: needs a reconversion
    if same and not a.force:
        print(f"{doc_id}: stored document is up to date (same PDF hash); running post-processing only. Use --force to reconvert.")
    else:
        print(f"{doc_id}: converting {pdf.name}{' pages ' + a.pages if a.pages else ''} … (this can take a while)", flush=True)
        stats = convert_pdf(
            pdf, pack / DOC_JSON_GZ, label=doc_id, page_range=page_range, ocr=a.ocr, figures=a.figures,
            threads=a.threads, device=a.device, artifacts_path=a.artifacts_path, table_mode=a.table_mode,
            progress_every_s=a.progress_every,
        )
        entry = DocEntry(
            id=doc_id, kind=(existing.kind if existing and not a.kind else (a.kind or "manual")),
            source=str(pdf), pdf_sha256=sha, pages=stats.pages,
            page_range=list(page_range) if page_range else None, profile=a.profile,
            docling_version=stats.docling_version, built=dt.datetime.now().isoformat(timespec="seconds"),
            convert_seconds=stats.seconds, grades={"mean": stats.mean_grade, "low": stats.low_grade},
            low_pages=stats.low_pages,
        )
        print(f"{doc_id}: converted {stats.pages} pages in {stats.seconds}s; grades mean={stats.mean_grade} low={stats.low_grade}", flush=True)

    entry.profile = a.profile
    if a.kind:
        entry.kind = a.kind
    counts = build_pack(pack, doc_id, str(pdf), profile=a.profile)
    entry.title = a.title or counts.pop("title")
    counts.pop("title", None)
    entry.counts = counts
    vsum = None
    if not a.no_verify:
        regs = read_jsonl(pack / "registers.jsonl")
        if regs:
            from pdfk.verify import verify_registers
            vsum = verify_registers(pdf, regs, page_offset=0)
            write_jsonl(pack / "registers.jsonl", regs)
            entry.verify = {k: vsum[k] for k in ("ok", "mismatch", "unchecked")}
    save_doc_entry(root, entry)
    _write_qa(pack, entry, vsum)
    print(_status_line(entry))
    if a.strict and (entry.low_pages or (vsum and vsum["mismatch"])):
        print("strict: low-confidence pages or register mismatches present, see QA.md", file=sys.stderr)
        return 2
    return 0


def cmd_rebuild(a) -> int:
    from pdfk.docpack import build_pack

    root = require_root(a.root)
    for doc_id in _pick_doc(root, a.doc):
        pack = root / doc_id
        entry = list_docs(root)[doc_id]
        if a.profile:
            entry.profile = a.profile
        if a.kind:
            entry.kind = a.kind
        counts = build_pack(pack, doc_id, entry.source, profile=entry.profile)
        auto_title = counts.pop("title")
        if a.title:
            entry.title = a.title
        elif not entry.title:
            entry.title = auto_title
        entry.counts = counts
        vsum = None
        src = Path(entry.source)
        if not a.no_verify and src.is_file():
            regs = read_jsonl(pack / "registers.jsonl")
            if regs:
                from pdfk.verify import verify_registers
                pr = entry.page_range
                vsum = verify_registers(src, regs, page_offset=0)
                write_jsonl(pack / "registers.jsonl", regs)
                entry.verify = {k: vsum[k] for k in ("ok", "mismatch", "unchecked")}
        save_doc_entry(root, entry)
        _write_qa(pack, entry, vsum)
        print(_status_line(entry))
    return 0


def _status_line(e: DocEntry) -> str:
    c = e.counts or {}
    v = e.verify or {}
    vs = f" verify ok={v.get('ok', 0)} mismatch={v.get('mismatch', 0)}" if v else ""
    pr = f" (pages {e.page_range[0]}-{e.page_range[1]} of PDF)" if e.page_range else ""
    figs = f", {c.get('figures', 0)} figures ({c.get('figure_files', 0)} png)" if c.get("figures") else ""
    return (f"{e.id} [{e.kind}]: \"{e.title}\" {e.pages}p{pr}, {c.get('sections', 0)} section files, {c.get('headings', 0)} headings, "
            f"{c.get('tables', 0)} tables{figs}, {c.get('registers', 0)} registers; confidence mean={e.grades.get('mean', '?')} "
            f"low={e.grades.get('low', '?')} low_pages={len(e.low_pages)};{vs}")


def cmd_status(a) -> int:
    root = find_root(a.root)
    if root is None:
        print("no docpacks (.pdfk not found)")
        return 0
    docs = list_docs(root)
    if not docs:
        print(f"no docpacks in {root}")
        return 0
    print(f"docpacks in {root}:")
    for e in docs.values():
        print("  " + _status_line(e))
    return 0


def cmd_search(a) -> int:
    from pdfk.search import format_hit, query

    root = require_root(a.root)
    hits = []
    for doc_id in _pick_doc(root, a.doc):
        hits += query(root / doc_id / "search.sqlite", a.query, kind=a.kind, section=a.section, limit=a.n)
    hits.sort(key=lambda h: h["rank"])
    top = hits[: a.n]
    # several docpacks: bm25 scores are per index, so make sure each document's best hit is shown
    for doc_id in {h["doc"] for h in hits} - {h["doc"] for h in top}:
        top.append(next(h for h in hits if h["doc"] == doc_id))
    hits = top
    if a.json:
        print(json.dumps(hits, ensure_ascii=False))
        return 0
    if not hits:
        print(f"no hits for: {a.query}")
        return 1
    for h in hits:
        print(format_hit(h))
    return 0


def _find_reg(root: Path, docs: list[str], name: str) -> tuple[list[dict], list[dict]]:
    exact, partial = [], []
    name_u = name.upper()
    for doc_id in docs:
        for r in read_jsonl(root / doc_id / "registers.jsonl"):
            n = r["name"].upper()
            if n == name_u:
                exact.append(r)
            elif name_u in n or r.get("peripheral", "").upper() == name_u:
                partial.append(r)
    return exact, partial


def _print_reg(r: dict, full: bool) -> None:
    cite = f"[{r['doc']} §{r.get('section') or '?'} p.{r.get('page')} sections/{r.get('file')}:{r.get('line')}]"
    bits = f" offset {r['offset']}" if r.get("offset") else ""
    bits += f" reset {r['reset']}" if r.get("reset") else ""
    bits += f" addr {r['address']}" if r.get("address") else ""
    ver = r.get("verify", r.get("confidence", ""))
    print(f"{r['name']}  {r.get('peripheral', '')}{bits}  {cite}  {ver}")
    if r.get("verify_missing"):
        print(f"  ! unverified against PDF text: {', '.join(r['verify_missing'])}")
    if full:
        for f in r.get("fields", []):
            acc = f" {f['access']}" if f.get("access") else ""
            rst = f" reset={f['reset']}" if f.get("reset") else ""
            print(f"  {f['bits']:>6}  {f.get('name') or '-':<18}{acc}{rst}  {f.get('desc', '')[:160]}")


def _errata_hits(root: Path, name: str, limit: int = 3) -> list[dict]:
    """Mentions of a register in docpacks of kind `errata` (shown under `pdfk reg`)."""
    from pdfk.search import query

    hits = []
    for doc_id, e in list_docs(root).items():
        if e.kind == "errata":
            hits += query(root / doc_id / "search.sqlite", name, limit=limit)
    return sorted(hits, key=lambda h: h["rank"])[:limit]


def cmd_reg(a) -> int:
    from pdfk.search import format_hit

    root = require_root(a.root)
    docs = _pick_doc(root, a.doc)
    exact, partial = _find_reg(root, docs, a.name)
    if a.json:
        print(json.dumps(exact or partial, ensure_ascii=False))
        return 0
    if exact:
        for r in exact:
            _print_reg(r, full=True)
        errata = _errata_hits(root, a.name)
        if errata:
            print(f"! errata mention {a.name} — read before relying on this register:")
            for h in errata:
                print("  " + format_hit(h))
        return 0
    if partial:
        print(f"no exact match for {a.name}; {len(partial)} candidates:")
        for r in partial[: a.n]:
            _print_reg(r, full=False)
        return 0
    print(f"register not found: {a.name} (try `pdfk search \"{a.name}\"`)")
    return 1


def cmd_section(a) -> int:
    root = require_root(a.root)
    pack = root / a.doc if a.doc in list_docs(root) else None
    if pack is None:
        raise SystemExit(f"unknown doc '{a.doc}'")
    heads = load_json(pack / "sections.json", [])
    sec = a.sec.lstrip("§")
    idx = next((i for i, h in enumerate(heads) if h["sec"] == sec), None)
    if idx is None:
        idx = next((i for i, h in enumerate(heads) if sec.lower() in h["title"].lower()), None)
    if idx is None:
        print(f"section {a.sec} not found (see INDEX.md or `pdfk toc {a.doc}`)")
        return 1
    h = heads[idx]
    end = None
    for nxt in heads[idx + 1 :]:
        if nxt["file"] != h["file"]:
            break
        if nxt["level"] <= h["level"]:
            end = nxt["line"] - 1
            break
    lines = (pack / "sections" / h["file"]).read_text(encoding="utf-8").splitlines()
    start = h["line"] - 1 + a.offset
    stop = min(end if end else len(lines), start + a.lines)
    print(f"[{a.doc} §{h['sec'] or h['title']} p.{h['page']} sections/{h['file']}:{h['line']}]")
    for i in range(start, stop):
        print(lines[i])
    if end and stop < end:
        print(f"… ({end - stop} more lines; use --offset {a.offset + a.lines})")
    return 0


def cmd_toc(a) -> int:
    root = require_root(a.root)
    pack = root / a.doc
    heads = load_json(pack / "sections.json", [])
    sec = (a.sec or "").lstrip("§")
    base_level = None
    shown = 0
    for h in heads:
        if sec:
            if h["sec"] == sec:
                base_level = h["level"]
            elif base_level is None or not (h["sec"].startswith(sec + ".") ):
                if base_level is not None and h["level"] <= base_level:
                    break
                continue
        if base_level is not None and h["level"] - base_level >= a.depth:
            continue
        if base_level is None and h["level"] > a.depth:
            continue
        label = f"§{h['sec']} {h['title']}" if h["sec"] else h["title"]
        print(f"{'  ' * (h['level'] - 1)}{label}  p.{h['page']}  sections/{h['file']}:{h['line']}")
        shown += 1
    if not shown:
        print("nothing found")
        return 1
    return 0


def cmd_table(a) -> int:
    root = require_root(a.root)
    pack = root / a.doc
    if a.tid == "list":
        print((pack / "tables" / "index.tsv").read_text(encoding="utf-8")[: a.lines * 100])
        return 0
    p = pack / "tables" / f"{a.tid}.csv"
    if not p.is_file():
        print(f"table {a.tid} not found")
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


TAB = chr(9)


def cmd_figure(a) -> int:
    root = require_root(a.root)
    pack = root / a.doc
    idx = pack / "figures" / "index.tsv"
    if not idx.is_file():
        print(f"{a.doc}: no figures indexed")
        return 1
    rows = [(l.split(TAB) + [""] * 6)[:6] for l in idx.read_text(encoding="utf-8").splitlines()[1:]]
    if a.fid == "list":
        for fid, page, sec, file, cap, _labels in rows[: a.lines]:
            print(f"{fid} p.{page} §{sec}  {cap or '(no caption)'}{'  [png]' if file else ''}")
        if len(rows) > a.lines:
            print(f"… {len(rows) - a.lines} more (use --lines)")
        return 0
    row = next((r for r in rows if r[0] == a.fid), None)
    if row is None:
        print(f"figure {a.fid} not found (try `pdfk figure {a.doc} list` or `pdfk search \"…\" --kind figure`)")
        return 1
    fid, page, sec, file, cap, labels = row
    print(f"[{a.doc} §{sec} p.{page}] {cap or '(no caption)'}")
    if labels:
        print(f"labels: {labels}")
    if file:
        print(f"image: {(pack / file).as_posix()}  (open with Read only if the caption and labels are not enough)")
    else:
        print("image: not exported (rebuild with `pdfk build <pdf> --figures`)")
    return 0


def cmd_verify(a) -> int:
    from pdfk.verify import verify_registers

    root = require_root(a.root)
    for doc_id in _pick_doc(root, a.doc):
        entry = list_docs(root)[doc_id]
        src = Path(a.pdf) if a.pdf else Path(entry.source)
        if not src.is_file():
            raise SystemExit(f"source PDF not found: {src} (pass --pdf)")
        pack = root / doc_id
        regs = read_jsonl(pack / "registers.jsonl")
        pr = entry.page_range
        vsum = verify_registers(src, regs, page_offset=0)
        write_jsonl(pack / "registers.jsonl", regs)
        entry.verify = {k: vsum[k] for k in ("ok", "mismatch", "unchecked")}
        save_doc_entry(root, entry)
        _write_qa(pack, entry, vsum)
        print(f"{doc_id}: verify ok={vsum['ok']} mismatch={vsum['mismatch']} unchecked={vsum['unchecked']} (details in QA.md)")
    return 0


# ----------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdfk", description="Docpacks from large PDFs for token-cheap, citeable agent queries.")
    p.add_argument("--version", action="version", version=f"pdfk {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--root", help="path to .pdfk (default: walk up from cwd / $PDFK_ROOT)")

    s = sub.add_parser("build", help="convert a PDF into a docpack (needs docling)")
    s.add_argument("pdf")
    s.add_argument("--id", help="doc id (default derived from file name, e.g. rm0440)")
    s.add_argument("--kind", choices=DOC_KINDS, help="document kind (default manual); `errata` docs are cross-referenced by `pdfk reg`")
    s.add_argument("--title", help="override the detected document title")
    s.add_argument("--profile", default="generic", help="register extraction profile (generic|st)")
    s.add_argument("--pages", help="page range a-b for PoC runs")
    s.add_argument("--ocr", action="store_true", help="enable OCR (scanned PDFs only)")
    s.add_argument("--figures", action="store_true", help="export figures as PNG (figures/fNNNN.png); captions and labels are indexed either way")
    s.add_argument("--progress-every", type=float, default=20.0, metavar="SEC", help="seconds between progress lines (default 20)")
    s.add_argument("--threads", type=int, default=8)
    s.add_argument("--device", default="auto", help="auto|cpu|cuda|mps")
    s.add_argument("--table-mode", default="accurate", choices=["accurate", "fast"])
    s.add_argument("--artifacts-path", help="prefetched docling models dir")
    s.add_argument("--force", action="store_true", help="reconvert even if the PDF hash is unchanged")
    s.add_argument("--no-verify", action="store_true")
    s.add_argument("--strict", action="store_true", help="exit 2 on low-confidence pages or register mismatches")
    common(s)
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("rebuild", help="re-run post-processing from docling.json")
    s.add_argument("doc", nargs="?")
    s.add_argument("--profile")
    s.add_argument("--kind", choices=DOC_KINDS)
    s.add_argument("--title")
    s.add_argument("--no-verify", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_rebuild)

    s = sub.add_parser("status", help="list docpacks")
    common(s)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("search", help="full-text search (compact, cited hits)")
    s.add_argument("query")
    s.add_argument("--doc")
    s.add_argument("--kind", choices=["heading", "text", "list", "table", "code", "figure"], help="restrict block kind")
    s.add_argument("--section", help="restrict to section number prefix, e.g. 7.4")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--json", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("reg", help="register lookup by name or peripheral")
    s.add_argument("name")
    s.add_argument("--doc")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--json", action="store_true")
    common(s)
    s.set_defaults(fn=cmd_reg)

    s = sub.add_parser("section", help="print a section by number (e.g. 7.4.1) or title fragment")
    s.add_argument("doc")
    s.add_argument("sec")
    s.add_argument("--lines", type=int, default=80)
    s.add_argument("--offset", type=int, default=0)
    common(s)
    s.set_defaults(fn=cmd_section)

    s = sub.add_parser("toc", help="headings under a section")
    s.add_argument("doc")
    s.add_argument("sec", nargs="?")
    s.add_argument("--depth", type=int, default=2)
    common(s)
    s.set_defaults(fn=cmd_toc)

    s = sub.add_parser("table", help="print a table as CSV (or `list`)")
    s.add_argument("doc")
    s.add_argument("tid")
    s.add_argument("--lines", type=int, default=60)
    common(s)
    s.set_defaults(fn=cmd_table)

    s = sub.add_parser("figure", help="figure caption, labels and PNG path (or `list`)")
    s.add_argument("doc")
    s.add_argument("fid")
    s.add_argument("--lines", type=int, default=40)
    common(s)
    s.set_defaults(fn=cmd_figure)

    s = sub.add_parser("verify", help="cross-check registers against the PDF text layer")
    s.add_argument("doc", nargs="?")
    s.add_argument("--pdf")
    common(s)
    s.set_defaults(fn=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    return args.fn(args)
