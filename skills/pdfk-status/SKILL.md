---
name: pdfk-status
description: Show which docpacks exist in this project and their QA summary. User-invoked: /pdfk:pdfk-status
disable-model-invocation: true
allowed-tools: Bash(pdfk:*) Bash(*/bin/pdfk *) Read
---

!`pdfk status 2>/dev/null || "${CLAUDE_PLUGIN_ROOT}/bin/pdfk" status 2>/dev/null || echo "pdfk not available on PATH"`

Summarize the docpacks above for the user in two or three lines. If there are register mismatches or
low-confidence pages, point to `.pdfk/<id>/QA.md`. Do not open section files.
