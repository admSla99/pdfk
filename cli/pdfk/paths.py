"""Docpack root discovery and manifest IO (stdlib only)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PACK_DIRNAME = ".pdfk"
MANIFEST = "manifest.json"
DOC_JSON_GZ = "docling.json.gz"
DOC_JSON = "docling.json"  # legacy, uncompressed
DOC_KINDS = ("manual", "datasheet", "errata", "appnote", "other")


def find_root(explicit: str | None = None) -> Path | None:
    """Return the `.pdfk` directory: --root, $PDFK_ROOT, or walk up from cwd."""
    if explicit:
        p = Path(explicit)
        return p if p.name == PACK_DIRNAME else p / PACK_DIRNAME
    env = os.environ.get("PDFK_ROOT")
    if env:
        p = Path(env)
        return p if p.name == PACK_DIRNAME else p / PACK_DIRNAME
    for base in [Path.cwd(), *Path.cwd().parents]:
        cand = base / PACK_DIRNAME
        if cand.is_dir():
            return cand
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj and (Path(proj) / PACK_DIRNAME).is_dir():
        return Path(proj) / PACK_DIRNAME
    return None


def require_root(explicit: str | None = None) -> Path:
    root = find_root(explicit)
    if root is None or not root.is_dir():
        raise SystemExit(
            "no .pdfk directory found (run `pdfk build <pdf>` first, or pass --root / set PDFK_ROOT)"
        )
    return root


def load_json(path: Path, default=None):
    if not path.is_file():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:maxlen].rstrip("-") or "section"


def doc_id_from_pdf(pdf: Path) -> str:
    stem = pdf.stem.lower()
    m = re.match(r"([a-z]{1,4}\d{3,5})", stem)  # rm0440, ds12345, um1234
    return m.group(1) if m else slugify(stem, 24)


@dataclass
class DocEntry:
    """Per-document manifest entry (kept as a plain dict on disk)."""

    id: str
    title: str = ""
    kind: str = "manual"  # manual | datasheet | errata | appnote | other
    source: str = ""
    pdf_sha256: str = ""
    pages: int = 0
    page_range: list[int] | None = None
    profile: str = "generic"
    docling_version: str = ""
    built: str = ""
    convert_seconds: float = 0.0
    grades: dict = field(default_factory=dict)
    low_pages: list[int] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "DocEntry":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def doc_json_path(pack: Path) -> Path | None:
    """Path of the stored DoclingDocument (gzip preferred, legacy plain JSON accepted)."""
    for name in (DOC_JSON_GZ, DOC_JSON):
        if (pack / name).is_file():
            return pack / name
    return None


def list_docs(root: Path) -> dict[str, DocEntry]:
    man = load_json(root / MANIFEST, {"version": 1, "docs": {}})
    return {k: DocEntry.from_dict(v) for k, v in man.get("docs", {}).items()}


def save_doc_entry(root: Path, entry: DocEntry) -> None:
    man = load_json(root / MANIFEST, {"version": 1, "docs": {}})
    man.setdefault("docs", {})[entry.id] = entry.to_dict()
    save_json(root / MANIFEST, man)
    save_json(root / entry.id / MANIFEST, entry.to_dict())
