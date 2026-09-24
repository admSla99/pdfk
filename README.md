# pdfk — docpacks for Claude Code

Turns large vendor PDFs (MCU reference manuals, datasheets, errata) into **docpacks**: markdown split by
chapter with page anchors, a table of contents, CSV tables, a register database, a memory map and a full-text
index. Claude Code then answers from the docpack with citations `[doc §sec p.N]` instead of reading the PDF.

## Requirements

- Python 3.10 or newer, available as `python` on PATH (the plugin hooks run `python`).
- [uv](https://docs.astral.sh/uv/) for installing the CLI: `pip install uv`, then use `uv` or `python -m uv`.
- For building docpacks: about 2 GB of disk for docling, torch and the layout/table models, downloaded on
  first use. Conversion runs on CPU at about 2 s per page, so a 2 000-page manual takes about an hour.
  A CUDA machine is faster. Only the person who builds a docpack needs this.
- For asking questions: nothing beyond Python. The query commands use the standard library only.

## Install

The repository is both the plugin and its marketplace; the CLI lives in `cli/`.

```bash
# 1. CLI with build support (pulls docling)
uv tool install "git+https://github.com/admSla99/pdfk.git#subdirectory=cli"

# 1b. or query-only, for teammates who use docpacks someone else built (no docling, a few kB)
pip install --no-deps "git+https://github.com/admSla99/pdfk.git#subdirectory=cli"
```

```text
# 2. Plugin, inside Claude Code
/plugin marketplace add https://github.com/admSla99/pdfk.git
/plugin install pdfk@pdfk
```

Check the CLI with `pdfk --version`. Update later with `uv tool upgrade pdfk` and `/plugin marketplace update pdfk`.

`pdfk` is not published on PyPI: always install from the git URL, never `pip install pdfk`.

On Windows `uv` resolves docling in seconds, while `pip install docling` can backtrack for a long time.

## Quick start: a new MCU project

1. Put the PDFs somewhere in the project, for example `docs/`. They do not have to be committed.
2. Build one docpack per document from the project root. It writes to `.pdfk/<id>/` and prints progress with an ETA:

   ```bash
   pdfk build docs/rm0440.pdf  --id rm0440                          # reference manual
   pdfk build docs/ds12288.pdf --id ds12288 --kind datasheet --figures
   pdfk build docs/es0430.pdf  --id es0430  --kind errata
   ```

   For a first check on a big manual, build one chapter with `--pages 273-352` before the whole document.
3. Check the result:
   - `pdfk status` prints one line per docpack.
   - `pdfk toc rm0440 --depth 1` should match the manual's chapters.
   - `.pdfk/rm0440/QA.md` lists low-confidence pages and registers whose numbers did not match the PDF text.
   - Spot-check a register you know with `pdfk reg <NAME>`.
4. Commit `.pdfk/` and add `.pdfk/*/search.sqlite` to `.gitignore`. Teammates then need only the query-only
   install. After a plugin update, `pdfk rebuild` regenerates everything from `docling.json.gz` in seconds.
5. Open Claude Code in the project and ask, for example "Which bits of RCC_CR enable the PLL, and what is its
   reset value?". The plugin announces the docpacks at session start. The `pdfk` skill loads automatically,
   and answers cite `[rm0440 §7.4.1 p.281]`. `/pdfk:pdfk-build <pdf>` runs step 2 from inside Claude Code.

## Commands

```bash
pdfk status
pdfk reg RCC_CR                         # fields, offset, reset, absolute address, citation
pdfk map RCC                            # base address from the memory map
pdfk search "PLL ready" --doc rm0440    # ranked, cited hits; the top 3 in full (--full N)
pdfk section rm0440 7.4.1               # a section by number, with page anchors
pdfk toc rm0440 7 --depth 3             # headings below a chapter
pdfk table rm0440 t0421 --cells         # a table as CSV; --cells gives exact merged-cell spans
pdfk figure rm0440 list                 # figures: caption, labels, PNG path
pdfk rebuild [id]                       # re-run post-processing from docling.json.gz
pdfk verify [id]                        # re-check numbers against the PDF text layer
```

Every command takes `--root <dir>` if you are not inside the project. `pdfk <command> --help` lists all options.

In Claude Code the `pdfk` skill loads automatically for hardware questions. `doc-researcher` handles
multi-step lookups in its own context. The user commands are `/pdfk:pdfk-build <pdf>` and `/pdfk:pdfk-status`.

## Current limits

- **Register layouts.** The parser is verified on the RP2040 datasheet (937 registers, all checked against the
  PDF). The ST reference-manual layout is covered by unit tests only. Build a real STM32 manual and read its
  `QA.md` before relying on `pdfk reg` for it. `pdfk search` and `pdfk section` work on any layout.
- **Scanned PDFs.** Born-digital PDFs only by default. Scanned PDFs need `--ocr`, which is slower and untested here.
- **Formulas.** Equations drawn as images are not converted, for example the XOSC startup-delay formula in the RP2040 datasheet.
- **Tested setup.** Tested on Windows 11 with Python 3.12 and docling 2.127.

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

A project usually has a reference manual, a datasheet and an errata sheet (see the quick start). `search` and
`reg` cover all docpacks unless `--doc` is given. When an `errata` docpack mentions a register, `pdfk reg NAME`
appends the errata hits under the register.

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

A separate single-question comparison against an LLM-built knowledge graph of the same PDF is written up in
[eval/results/mspm0g1518/graphify-comparison.md](eval/results/mspm0g1518/graphify-comparison.md): the graph
summarises a document into concepts and drops the table rows, so a pinout lookup has nothing to hit. It also
decomposes where the tokens of one lookup actually go, which is what motivated printing table ids in search hits.

## Notes

- Conversion on CPU takes about 2 s per page (first run also downloads ~500 MB of models). The build prints a
  progress line with ETA every 20 s (`--progress-every SEC`). Use a CUDA machine or a CI runner for 1000+ page
  manuals; commit the resulting docpack.
- Page numbers in docpacks are real PDF page numbers, also when built with `--pages`.
