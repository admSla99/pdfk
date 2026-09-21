# pdfk — docpacks for Claude Code

Turns large vendor PDFs (MCU reference manuals, datasheets, errata) into **docpacks**: markdown split by
chapter with page anchors, a table of contents, CSV tables, a register database and a full-text index.
Claude Code then answers from the docpack with citations `[doc §sec p.N]` at a few thousand tokens per
question instead of reading the PDF.

## Install

The CLI depends on `docling`, which pulls torch and the layout/table models (~2 GB) on first use.

```bash
# CLI (full install: build + query)
uv tool install git+<repo-url>#subdirectory=cli
# or from a checkout:
uv tool install ./cli

# Plugin, from this repo as a marketplace
/plugin marketplace add <repo-url>
/plugin install pdfk@pdfk
```

Team members who only **query** docpacks that are already committed in a project repo need none of the
machine learning stack: `pip install --no-deps pdfk` gives working `search`, `reg`, `section`, `toc`,
`table` and `status` (they are stdlib-only). Only `build`, `rebuild` and `verify` import docling.

During plugin development use `claude --plugin-dir ./pdfk` and `/reload-plugins`. If `pdfk` is not on
PATH, the bundled launcher `bin/pdfk` runs the vendored package with `$PDFK_PYTHON` (default `python`).

## Use

```bash
pdfk build docs/rm0440.pdf --id rm0440 --profile generic      # once per document (minutes to an hour on CPU)
pdfk status
pdfk reg RCC_CR
pdfk search "PLL ready" --doc rm0440
pdfk section rm0440 7.4.1
```

In Claude Code: `/pdfk:pdfk-build <pdf>`, `/pdfk:pdfk-status`; the `pdfk` skill is picked up automatically
for hardware questions, and `doc-researcher` handles multi-step lookups in its own context.

## Layout of a docpack

```
.pdfk/<id>/
  INDEX.md          TOC: section files, headings (2 levels), pages
  sections/*.md     markdown with <!-- p.N --> anchors, front-matter, table ids
  tables/index.tsv  + tNNNN.csv (grid cells, merged cells repeated)
  figures/index.tsv + fNNNN.png (PNG only with --figures; captions and in-drawing labels always indexed)
  registers.jsonl   name, offset, reset, fields[], page, section, verify
  sections.json     heading → file:line map (used by `pdfk section` / `toc`)
  search.sqlite     FTS5 index (regenerable: pdfk rebuild)
  docling.json.gz   lossless DoclingDocument, gzip (~5 MB per 1000 pages); input of `pdfk rebuild`
  QA.md             confidence grades, low-confidence pages, verify mismatches
```

Commit everything except `search.sqlite` (regenerable with `pdfk rebuild`). `docling.json.gz` is worth keeping:
teammates can re-run post-processing after a plugin update without the hour-long conversion.

## Several documents per project

```bash
pdfk build docs/rm0440.pdf  --id rm0440                   # kind defaults to manual
pdfk build docs/ds12345.pdf --id ds12345 --kind datasheet --figures
pdfk build docs/es0430.pdf  --id es0430  --kind errata
```

`search` and `reg` cover all docpacks unless `--doc` is given. When an `errata` docpack mentions a register,
`pdfk reg NAME` appends the errata hits under the register.

## Hooks

- **SessionStart** lists the docpacks (id, kind, title, counts) in one line each.
- **UserPromptSubmit** stays silent unless the prompt names a register or peripheral that exists in a docpack;
  then it injects where it is documented (`GPIO0_CTRL (IO_BANK0 offset 0x004) is documented in [rp2040 §2.19.6.1 p.248]`).
- **PreToolUse(Read)** blocks PDFs, `docling.json.gz`, and whole section files above 60 kB without `limit`.

## Register profiles

`--profile generic` (default) recognises the layouts seen so far:

- ST reference manuals: heading `7.4.1 Clock control register (RCC_CR)`, lines `Address offset: 0x00`,
  `Reset value: 0x0000 0063`, fields as paragraphs `Bit 27 PLLRDY: …` / `Bits 31:28 Reserved…`.
- Raspberry Pi RP2040 style: `IO_BANK0: GPIO0_STATUS, GPIO1_STATUS, …, GPIO29_STATUS Registers`,
  `Offsets: 0x000, 0x008, …` (families are expanded, derived members carry `derived: true`), tables with
  `Bits | Description | Type | Reset`, tables spanning pages.
- Plain `NAME Register` / `Table 12. NAME Register` captions.

Every register is cross-checked against the PDF text layer (`verify`); mismatches are listed in `QA.md`.

## Evaluation

`poc/eval/run_eval.sh` runs a question set through `claude -p --plugin-dir` and prints answer, citation and
token usage per question. On the RP2040 GPIO chapter: 5/5 correct with citations, 4-6 turns, ≈ $0.08-0.10 per
question with Sonnet.

## Notes

- Conversion on CPU takes about 2 s per page (first run also downloads ~500 MB of models). The build prints a
  progress line with ETA every 20 s (`--progress-every SEC`). Use a CUDA machine or a CI runner for 1000+ page
  manuals; commit the resulting docpack.
- `pip install docling` may backtrack forever on Windows; `python -m uv pip install --python .venv/Scripts/python.exe docling` resolves in seconds.
- Page numbers in docpacks are real PDF page numbers, also when built with `--pages`.
