#!/usr/bin/env python3
"""Build the baseline workspace for the eval: the whole document as ONE docling markdown file, with a
CLAUDE.md that tells the agent to grep it. This is the "docling markdown + grep" setup that pdfk replaces.

    python eval/make_baseline.py <project>/.pdfk/<doc> <out-dir>

Needs docling-core (installed with docling)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))

from pdfk.convert import load_doc  # noqa: E402
from pdfk.paths import doc_json_path  # noqa: E402

CLAUDE_MD = """# Documentation

The {title} ({pages} pages) was converted to Markdown with docling and is stored in `{name}`.
Answer hardware questions only from that file. Search it with Grep and read only the relevant parts
(Read with offset/limit); the file is too large to read whole. Cite the section you used.
If the file does not contain the answer, say so.
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    pack, out = Path(sys.argv[1]), Path(sys.argv[2])
    src = doc_json_path(pack)
    if src is None:
        raise SystemExit(f"no docling.json.gz in {pack}")
    doc = load_doc(src)
    out.mkdir(parents=True, exist_ok=True)
    name = f"{pack.name}.md"
    md = doc.export_to_markdown()
    (out / name).write_text(md, encoding="utf-8")
    (out / "CLAUDE.md").write_text(CLAUDE_MD.format(title=pack.name.upper() + " document", pages=len(doc.pages),
                                                    name=name), encoding="utf-8")
    print(f"{out / name}: {len(md):,} chars (~{len(md) // 4:,} tokens) from {len(doc.pages)} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
