"""SQLite FTS5 index over docpack blocks (stdlib only)."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA = (
    "CREATE VIRTUAL TABLE chunks USING fts5("
    "doc UNINDEXED, file UNINDEXED, line UNINDEXED, page UNINDEXED, section UNINDEXED, kind UNINDEXED, text, "
    "tokenize='unicode61 remove_diacritics 2')"
)


def build_index(db_path: Path, doc_id: str, records: list[dict]) -> None:
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO chunks(doc,file,line,page,section,kind,text) VALUES (?,?,?,?,?,?,?)",
        [(doc_id, r["file"], r["line"], r["page"], r["section"], r["kind"], r["text"]) for r in records],
    )
    con.commit()
    con.close()


def fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 query: every term becomes a quoted phrase (AND-ed);
    a trailing * keeps prefix semantics. Underscored identifiers become implicit phrases."""
    terms = []
    for t in q.split():
        prefix = t.endswith("*")
        t = t.rstrip("*").replace('"', "")
        if not t:
            continue
        terms.append(f'"{t}"' + ("*" if prefix else ""))
    return " ".join(terms)


def query(db_path: Path, q: str, *, kind: str | None = None, section: str | None = None, limit: int = 10) -> list[dict]:
    if not db_path.is_file():
        return []
    con = sqlite3.connect(db_path)
    sql = "SELECT doc,file,line,page,section,kind,snippet(chunks,6,'',' ','…',14) AS snip, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ?"
    args: list = [fts_query(q)]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if section:
        sql += " AND (section = ? OR section LIKE ?)"
        args += [section, section + ".%"]
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        con.close()
        raise SystemExit(f"search error: {e}")
    con.close()
    out = []
    for doc, file, line, page, section, kind, snip, rank in rows:
        snip = re.sub(r"\s+", " ", snip).strip()
        out.append({"doc": doc, "file": file, "line": line, "page": page, "section": section, "kind": kind, "snippet": snip, "rank": rank})
    return out


def format_hit(h: dict, width: int = 120) -> str:
    sec = f"§{h['section']}" if h["section"] else "§?"
    snip = h["snippet"][:width]
    return f"{h['doc']} {sec} p.{h['page']} sections/{h['file']}:{h['line']}  {snip}"
