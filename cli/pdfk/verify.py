"""Cross-check extracted register numbers against the PDF text layer (pypdfium2)."""

from __future__ import annotations

import re
from pathlib import Path


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", "", s).lower()


def _hex_variants(h: str) -> set[str]:
    h = _norm(h)
    if not h.startswith("0x"):
        return {h}
    digits = h[2:].lstrip("0") or "0"
    return {h, "0x" + digits, "0x" + digits.zfill(2), "0x" + digits.zfill(4), "0x" + digits.zfill(8)}


def verify_registers(pdf: Path, regs: list[dict], page_offset: int = 0) -> dict:
    """page_offset is 0 for docling output: page_no is the real PDF page even with --pages."""
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise SystemExit("pypdfium2 not importable (it is installed together with docling)") from e

    doc = pdfium.PdfDocument(str(pdf))
    cache: dict[int, str] = {}

    def page_text(pno: int) -> str:
        if pno not in cache:
            idx = pno - 1 + page_offset
            if 0 <= idx < len(doc):
                cache[pno] = _norm(doc[idx].get_textpage().get_text_range())
            else:
                cache[pno] = ""
        return cache[pno]

    summary = {"ok": 0, "mismatch": 0, "unchecked": 0, "mismatches": []}
    for r in regs:
        pno = int(r.get("page") or 0)
        if not pno:
            r["verify"] = "unchecked"
            summary["unchecked"] += 1
            continue
        # a wide register's bit table can run over several pages
        text = "".join(page_text(pno + i) for i in range(4))
        missing = []
        derived = bool(r.get("derived"))
        if not derived and _norm(r["name"]) not in text:
            missing.append("name")
        if not derived and r.get("offset") and not any(v in text for v in _hex_variants(r["offset"])):
            missing.append("offset")
        if r.get("reset") and not any(v in text for v in _hex_variants(r["reset"])):
            missing.append("reset")
        for f in r.get("fields", []):
            n = f.get("name") or ""
            if n and n != "Reserved" and _norm(n) not in text:
                missing.append("field:" + n)
        if missing:
            r["verify"] = "mismatch"
            r["verify_missing"] = missing[:10]
            summary["mismatch"] += 1
            summary["mismatches"].append({"name": r["name"], "page": pno, "missing": missing[:6]})
        else:
            r["verify"] = "ok"
            r.pop("verify_missing", None)
            summary["ok"] += 1
    doc.close()
    return summary
