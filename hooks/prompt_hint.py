"""UserPromptSubmit hook: when the prompt names a register or peripheral that exists in a docpack,
inject one precise line (where it is documented + the command to use). Silent otherwise, so ordinary
prompts cost nothing."""

import json
import os
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}(?:_[A-Z0-9]+)*\b")
MAX_HINTS = 5
STOP = {"TODO", "NOTE", "README", "JSON", "HTTP", "HTTPS", "API", "PDF", "USB", "CPU", "GPU", "RAM", "ROM",
        "SDK", "IDE", "MCU", "HAL", "ISR", "IRQ", "DMA", "OK", "ID", "IO", "UI", "OS", "PR", "CI"}


def find_root() -> Path | None:
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    for base in [project, *project.parents]:
        if (base / ".pdfk" / "manifest.json").is_file():
            return base / ".pdfk"
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = str(payload.get("prompt") or "")
    tokens = [t for t in dict.fromkeys(TOKEN_RE.findall(prompt)) if t not in STOP]
    # an underscore or a digit makes a token register-like; plain words (e.g. "WARNING") are ignored
    tokens = [t for t in tokens if "_" in t or any(c.isdigit() for c in t) or len(t) >= 4][:40]
    if not tokens:
        return 0
    root = find_root()
    if root is None:
        return 0
    wanted = set(tokens)
    hints: list[str] = []
    seen: set[tuple[str, str]] = set()
    periph_docs: dict[str, set[str]] = {}
    for pack in sorted(p for p in root.iterdir() if p.is_dir()):
        regs = pack / "registers.jsonl"
        if not regs.is_file():
            continue
        try:
            with regs.open(encoding="utf-8") as f:
                for line in f:
                    # cheap pre-filter before JSON parsing: the file can hold thousands of registers
                    if not any(t in line for t in wanted):
                        continue
                    r = json.loads(line)
                    name, per = r.get("name", ""), r.get("peripheral", "")
                    # short names without underscore ("CS", "CTRL") are too ambiguous to hint on
                    specific = "_" in name or len(name) >= 5
                    if name in wanted and specific and (pack.name, name) not in seen:
                        seen.add((pack.name, name))
                        off = f" offset {r['offset']}" if r.get("offset") else ""
                        hints.append(f"{name} ({per}{off}) is documented in [{pack.name} §{r.get('section', '?')} p.{r.get('page', '?')}]")
                    if per in wanted:
                        periph_docs.setdefault(per, set()).add(pack.name)
        except (OSError, ValueError):
            continue
    for per, docs in periph_docs.items():
        if not any(h.startswith(per + " ") for h in hints):
            hints.append(f"peripheral {per} has registers in docpack {', '.join(sorted(docs))} (`pdfk reg {per}` lists them)")
    if not hints:
        return 0
    more = f" (+{len(hints) - MAX_HINTS} more)" if len(hints) > MAX_HINTS else ""
    ctx = ("pdfk: " + "; ".join(hints[:MAX_HINTS]) + more +
           ". Use `pdfk reg <NAME>` for fields and cite [doc §sec p.N]; do not answer register facts from memory.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
