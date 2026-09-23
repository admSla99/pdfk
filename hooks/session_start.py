"""SessionStart hook: announce available docpacks (1 line each) and put bin/ on PATH via CLAUDE_ENV_FILE."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    plugin_root = Path(__file__).resolve().parent.parent
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = None
    for base in [project, *project.parents]:
        if (base / ".pdfk" / "manifest.json").is_file():
            root = base / ".pdfk"
            break

    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        bin_dir = (plugin_root / "bin").as_posix()
        try:
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f'export PATH="{bin_dir}:$PATH"\n')
                f.write(f'export CLAUDE_PLUGIN_ROOT="{plugin_root.as_posix()}"\n')
        except OSError:
            pass

    if root is None:
        return 0
    try:
        man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    lines = []
    for d in man.get("docs", {}).values():
        c = d.get("counts", {})
        figs = f", {c['figures']} figures" if c.get("figures") else ""
        lines.append(f"{d['id']} [{d.get('kind', 'manual')}]: \"{d.get('title', '')}\" {d.get('pages', 0)}p, "
                     f"{c.get('registers', 0)} registers, {c.get('tables', 0)} tables{figs}")
    if not lines:
        return 0
    ctx = ("pdfk docpacks available in .pdfk/ (" + "; ".join(lines) + "). For any question about these documents use the "
           "pdfk skill (pdfk reg / map / search / section) and cite [doc §sec p.N]. `pdfk search` shows the full "
           "text of its top hits and `pdfk reg` values marked ok are verified against the PDF: answer from them "
           "without re-reading. Never Read the PDFs.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
