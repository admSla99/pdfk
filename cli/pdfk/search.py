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


FULL_CHARS = 450  # ~110 tokens per fully shown hit

KIND_WEIGHTS = {"code": 0.35, "heading": 1.2, "table": 1.1, "figure": 0.9}
KIND_WEIGHT_SQL = "(CASE kind " + " ".join(f"WHEN '{k}' THEN {w}" for k, w in KIND_WEIGHTS.items()) + " ELSE 1.0 END)"


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


def query(db_path: Path, q: str, *, kind: str | None = None, section: str | None = None, limit: int = 10,
          full: int = 0, max_chars: int = FULL_CHARS) -> list[dict]:
    """Ranked hits. The first `full` hits also carry `text`: the whole block (for a heading, the heading
    plus the blocks that follow it), capped at `max_chars`. That usually answers the question without
    a follow-up `pdfk section` / Read, which costs a whole extra agent turn."""
    if not db_path.is_file():
        return []
    con = sqlite3.connect(db_path)
    # bm25 is negative (more negative = better). Weights push SDK code listings, which repeat every
    # identifier many times, below the prose and tables that define them.
    sql = ("SELECT rowid,doc,file,line,page,section,kind,snippet(chunks,6,'','','…',14) AS snip, text, "
           "bm25(chunks) * " + KIND_WEIGHT_SQL + " AS rank FROM chunks WHERE chunks MATCH ?")
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
    out = []
    for i, (rowid, doc, file, line, page, sec, knd, snip, text, rank) in enumerate(rows):
        h = {"doc": doc, "file": file, "line": line, "page": page, "section": sec, "kind": knd,
             "snippet": _one_line(snip), "rank": rank}
        if i < full:
            body = _one_line(text)
            if knd == "heading":  # a heading alone says nothing: add what follows it in the same file
                for (nxt,) in con.execute("SELECT text FROM chunks WHERE rowid > ? AND rowid <= ? AND file = ? "
                                          "ORDER BY rowid", (rowid, rowid + 4, file)):
                    body += " | " + _one_line(nxt)
                    if len(body) >= max_chars:
                        break
            h["text"] = body if len(body) <= max_chars else body[:max_chars].rsplit(" ", 1)[0] + " …"
        out.append(h)
    con.close()
    return out


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def format_hit(h: dict, width: int = 120) -> str:
    sec = f"§{h['section']}" if h["section"] else "§?"
    body = h.get("text") or h["snippet"][:width]
    return f"{h['doc']} {sec} p.{h['page']} sections/{h['file']}:{h['line']}  {body}"
