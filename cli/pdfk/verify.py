"""Cross-check extracted numbers against the PDF text layer (pypdfium2).

The checks are pure functions over normalised page text (`check_register`, `check_memmap_entry`), so they
are unit-testable without a PDF; `verify_registers` only supplies the text of the right pages."""

from __future__ import annotations

import re
from pathlib import Path

MAX_SPAN_PAGES = 12  # a register's bit table rarely runs longer; guards against a bad page_end
FIELD_RESET_WINDOW = 1500  # normalised chars after a field name in which its reset value must appear
TRIVIAL_RESETS = {"-", "", "0", "1", "x", "n/a", "na", "undefined", "u"}


def norm(s: str) -> str:
    return re.sub(r"[\s_]+", "", s).lower()


def hex_variants(h: str) -> set[str]:
    h = norm(h)
    if not h.startswith("0x"):
        return {h}
    digits = h[2:].lstrip("0") or "0"
    return {h, "0x" + digits} | {"0x" + digits.zfill(n) for n in (2, 3, 4, 8)}


def _has(text: str, value: str) -> bool:
    return any(v in text for v in hex_variants(value))


def check_register(r: dict, text: str) -> list[str]:
    """Return what could not be confirmed in `text` (normalised text of the register's pages)."""
    missing: list[str] = []
    derived = bool(r.get("derived"))  # expanded family members ("GPIO7_CTRL") are not printed by name
    if not derived and norm(r["name"]) not in text:
        missing.append("name")
    if not derived and r.get("offset") and not _has(text, r["offset"]):
        missing.append("offset")
    if r.get("reset") and not _has(text, r["reset"]):
        missing.append("reset")
    for f in r.get("fields", []):
        n = f.get("name") or ""
        if not n or n == "Reserved":
            continue
        key = norm(n)
        if key not in text:
            missing.append("field:" + n)
            continue
        rv = (f.get("reset") or "").strip()
        if rv.lower() in TRIVIAL_RESETS:
            continue
        # the field is defined as "NAME: description … TYPE RESET"; its name also appears in SDK code
        # listings and in other fields' descriptions, so try the definition first, then every occurrence
        defs = [m.start() for m in re.finditer(re.escape(key + ":"), text)]
        spots = defs or [m.start() for m in re.finditer(re.escape(key), text)]
        want = (lambda w: _has(w, rv)) if rv.lower().startswith("0x") else (lambda w: norm(rv) in w)
        if not any(want(text[pos: pos + FIELD_RESET_WINDOW]) for pos in spots[:40]):
            missing.append(f"field_reset:{n}={rv}")
    return missing


def check_memmap_entry(e: dict, text: str) -> list[str]:
    missing = []
    if not _has(text, e["base"]):
        missing.append("base")
    if e.get("end") and not _has(text, e["end"]):
        missing.append("end")
    return missing


class _PdfText:
    def __init__(self, pdf: Path):
        try:
            import pypdfium2 as pdfium
        except ImportError as e:
            raise SystemExit("pypdfium2 not importable (it is installed together with docling)") from e
        self.doc = pdfium.PdfDocument(str(pdf))
        self.cache: dict[int, str] = {}

    def page(self, pno: int) -> str:
        if pno not in self.cache:
            idx = pno - 1
            self.cache[pno] = norm(self.doc[idx].get_textpage().get_text_range()) if 0 <= idx < len(self.doc) else ""
        return self.cache[pno]

    def span(self, first: int, last: int) -> str:
        last = min(max(last, first) + 1, first + MAX_SPAN_PAGES)  # +1: tables often end at the top of the next page
        return "".join(self.page(p) for p in range(first, last + 1))

    def close(self) -> None:
        self.doc.close()


def verify_registers(pdf: Path, regs: list[dict], page_offset: int = 0, memmap: list[dict] | None = None) -> dict:
    """Annotate registers (and memory-map entries) in place with verify / verify_missing.
    page_offset is kept for API compatibility: docling page numbers are real PDF pages even with --pages."""
    src = _PdfText(pdf)
    summary = {"ok": 0, "mismatch": 0, "unchecked": 0, "mismatches": [],
               "field_resets_checked": 0, "memmap_ok": 0, "memmap_mismatch": 0, "memmap_mismatches": []}
    for r in regs:
        pno = int(r.get("page") or 0)
        if not pno:
            r["verify"] = "unchecked"
            summary["unchecked"] += 1
            continue
        text = src.span(pno + page_offset, int(r.get("page_end") or pno) + page_offset)
        summary["field_resets_checked"] += sum(
            1 for f in r.get("fields", []) if (f.get("reset") or "").strip().lower() not in TRIVIAL_RESETS)
        missing = check_register(r, text)
        if missing:
            r["verify"] = "mismatch"
            r["verify_missing"] = missing[:10]
            summary["mismatch"] += 1
            summary["mismatches"].append({"name": r["name"], "page": pno, "missing": missing[:6]})
        else:
            r["verify"] = "ok"
            r.pop("verify_missing", None)
            summary["ok"] += 1
    for e in memmap or []:
        missing = check_memmap_entry(e, src.span(e["page"], e["page"]))
        if missing:
            e["verify"] = "mismatch"
            summary["memmap_mismatch"] += 1
            summary["memmap_mismatches"].append({"name": e["name"], "page": e["page"], "missing": missing})
        else:
            e["verify"] = "ok"
            summary["memmap_ok"] += 1
    src.close()
    return summary
