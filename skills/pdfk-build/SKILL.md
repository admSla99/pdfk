---
name: pdfk-build
description: Build (or rebuild) a docpack from a PDF with docling. User-invoked: /pdfk:pdfk-build <pdf> [--id ID] [--kind manual|datasheet|errata|appnote] [--pages a-b] [--figures] [--ocr]
disable-model-invocation: true
allowed-tools: Bash(pdfk:*) Bash(*/bin/pdfk *) Bash(python:*) Read
---

# Build a docpack

Current docpacks:

!`pdfk status 2>/dev/null || "${CLAUDE_PLUGIN_ROOT}/bin/pdfk" status 2>/dev/null || echo "pdfk not available on PATH"`

Arguments given: `$ARGUMENTS`

Steps:

1. Run `pdfk build $ARGUMENTS` (if `pdfk` is missing, use `"${CLAUDE_PLUGIN_ROOT}/bin/pdfk" build $ARGUMENTS`;
   if docling is not importable, tell the user to install it in a venv, `pip install "docling"`, and set
   `PDFK_PYTHON` to that interpreter, then retry). Conversion of a 1000-page manual takes tens of minutes on CPU;
   run it in the background. The build prints a progress line every 20 s
   (`rm0440: 120/2100 pages (5%) 1.7 s/page, ETA 56 min`): relay the ETA once, then wait for completion.
   Ask which `--kind` the document is if it is not obvious from its title (errata sheets matter: `pdfk reg`
   cross-references them). Suggest `--figures` for manuals with clock trees and block diagrams.
2. When it finishes, print the one-line summary and read `.pdfk/<id>/QA.md`. Report: pages, time, confidence
   grades, number of low-confidence pages, section/table/register counts, verify ok/mismatch.
3. Sanity check: run `pdfk toc <id> --depth 1` and confirm the chapter structure looks like the manual's table
   of contents. If everything is a single flat level, the PDF has no bookmarks: report it.
4. Suggest `.gitignore` entry `.pdfk/*/search.sqlite` (regenerable with `pdfk rebuild`). Everything else is
   small enough for git: `docling.json.gz` is a few MB per 1000 pages and lets teammates run `pdfk rebuild`
   without reconverting; `figures/*.png` can go to LFS if the repo policy requires it.

Do not read `docling.json.gz` or whole section files. Do not read the PDF.
