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
pdfk reg RCC_CR                         # fields, offset, reset, absolute address, citation
pdfk map RCC                            # base address from the memory map
pdfk search "PLL ready" --doc rm0440    # ranked, cited hits; the top 3 in full (--full N)
pdfk section rm0440 7.4.1
pdfk table rm0440 t0421 --cells         # exact merged-cell geometry
```

In Claude Code: `/pdfk:pdfk-build <pdf>`, `/pdfk:pdfk-status`; the `pdfk` skill is picked up automatically
for hardware questions, and `doc-researcher` handles multi-step lookups in its own context.

## Layout of a docpack

```
.pdfk/<id>/
  INDEX.md          TOC: section files, headings (2 levels), pages
  sections/*.md     markdown with <!-- p.N --> anchors, front-matter, table ids
  tables/index.tsv  + tNNNN.csv (grid cells, merged values repeated) + tNNNN.json (exact spans, merged tables only)
  figures/index.tsv + fNNNN.png (PNG only with --figures; captions and in-drawing labels always indexed)
  registers.jsonl   name, offset, reset, fields[], address / bases, page..page_end, section, verify
  memmap.jsonl      peripheral base addresses (name, base, end, bus, page, section, verify)
  sections.json     heading → file:line map (used by `pdfk section` / `toc`)
  search.sqlite     FTS5 index (regenerable: pdfk rebuild)
  docling.json.gz   lossless DoclingDocument, gzip (~5 MB per 1000 pages); input of `pdfk rebuild`
  QA.md             confidence grades, low-confidence pages, register and memory-map verify results
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

Every register is cross-checked against the PDF text layer of all pages it spans (`verify`): name, offset,
reset value, every field name and every non-trivial field reset value. Base addresses in the memory map are
checked the same way. Mismatches are listed in `QA.md`; `build --strict` fails on any.

The printed table of contents, list of tables and list of figures are dropped (INDEX.md replaces them), so
searches hit the section itself instead of its TOC entry. SDK code listings rank below prose and tables.

## Tests

```bash
cd cli && python -m pytest          # stdlib + pytest only; no docling, no PDF
```

The suite covers the register parser on ST and RP2040 layouts (families, page-spanning bit tables, margin
captions, shifted descriptions), memory-map extraction and linking, TOC removal, text-layer verification,
search ranking, the query commands, the hooks and the eval grader.

## Evaluation

`eval/` compares pdfk against the setup it replaces (the whole PDF as one docling markdown, answered with grep):

```bash
python eval/make_baseline.py <project>/.pdfk/rp2040 <baseline-dir>     # one markdown + CLAUDE.md
python eval/run_eval.py run --mode pdfk     --project <project>
python eval/run_eval.py run --mode baseline --project <baseline-dir>
python eval/run_eval.py report                                        # eval/results/<doc>/summary.md
```

41 questions on the RP2040 datasheet (registers, memory map, procedures, tables, errata, questions the document
cannot answer) are graded automatically. Runs exclude the evaluator's user-level plugins and MCP servers.
Latest results (Sonnet, one run per question, [eval/results/rp2040/summary.md](eval/results/rp2040/summary.md)):

| | pdfk 0.2.0 | docling markdown + grep |
|---|---:|---:|
| Correct | 41/41 | 41/41 |
| Answerable questions with a page citation | 39/39 | 0/39 |
| Cost per question | $0.035 | $0.047 |
| Uncached input tokens per question | 3,180 | 6,947 |
| Total tokens per question (incl. cache reads) | 78,496 | 62,782 |
| Turns per question | 5.4 | 3.9 |

What this says:

- On a born-digital datasheet, markdown + grep is already accurate: docling writes each paragraph and table row
  on one line, so a grep hit is a whole fact. pdfk does not beat it on correctness here.
- pdfk adds what the baseline cannot give: a page citation for every fact (the flat markdown has no page
  numbers), numbers checked against the PDF text layer, absolute register addresses, and the errata overlay.
- pdfk is 26 % cheaper per question because its tool output is compact (half the uncached input). It sends
  more tokens in total because it takes more turns, and every turn re-reads the cached context; cache reads
  are billed at a tenth of the input price.
- Limits: one document, one run per question (the same question can take 3 or 7 turns between runs), one model.

## Notes

- Conversion on CPU takes about 2 s per page (first run also downloads ~500 MB of models). The build prints a
  progress line with ETA every 20 s (`--progress-every SEC`). Use a CUDA machine or a CI runner for 1000+ page
  manuals; commit the resulting docpack.
- `pip install docling` may backtrack forever on Windows; `python -m uv pip install --python .venv/Scripts/python.exe docling` resolves in seconds.
- Page numbers in docpacks are real PDF page numbers, also when built with `--pages`.
