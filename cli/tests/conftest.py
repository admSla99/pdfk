"""Shared helpers. Tests run without docling or a PDF: every module under test is stdlib-only at import
time, and blocks are built by hand in the shape docpack.extract_blocks produces."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CLI_DIR = Path(__file__).resolve().parents[1]
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from pdfk.docpack import Block, _parse_heading  # noqa: E402

PLUGIN_DIR = CLI_DIR.parent


def heading(text: str, page: int, level: int = 1) -> Block:
    sec, title = _parse_heading(text)
    return Block("heading", text, page, level=level, sec=sec, title=title)


def text(t: str, page: int) -> Block:
    return Block("text", t, page)


def table(grid: list[list[str]], page: int, tid: str = "t0001", title: str = "") -> Block:
    md = "\n".join("| " + " | ".join(r) + " |" for r in grid)
    return Block("table", md, page, table_id=tid, grid=grid, title=title)


@pytest.fixture
def plugin_dir() -> Path:
    return PLUGIN_DIR
