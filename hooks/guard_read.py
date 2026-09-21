"""PreToolUse(Read) guard: block reading PDFs and huge docpack files; point to pdfk instead."""

import json
import os
import sys

MAX_BYTES = 2_000_000


def deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if payload.get("tool_name") != "Read":
        return 0
    inp = payload.get("tool_input") or {}
    path = str(inp.get("file_path") or "")
    if not path:
        return 0
    low = path.lower().replace("\\", "/")
    if low.endswith(".pdf"):
        return deny("pdfk: do not read PDFs directly (token cost). Use `pdfk search \"<terms>\"`, `pdfk reg <NAME>` or "
                    "`pdfk section <doc> <sec>`; if no docpack exists, run /pdfk:pdfk-build <pdf> first.")
    if "/.pdfk/" in low:
        if low.endswith("docling.json") or low.endswith("search.sqlite"):
            return deny("pdfk: this is an internal docpack file. Use pdfk search / section / table instead.")
        if low.endswith(".md") and "/sections/" in low and not inp.get("limit"):
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size > 60_000:
                return deny(f"pdfk: section file is {size // 1000} kB. Read it with offset/limit at the line from "
                            "`pdfk search`, or use `pdfk section <doc> <sec>`.")
    try:
        if os.path.getsize(path) > MAX_BYTES and not inp.get("limit"):
            return deny("pdfk: file larger than 2 MB; read with offset/limit or search it instead.")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
