# Changelog

## Unreleased

- **Table ids in search hits.** `pdfk search` prints the table or figure id after the line number
  (`sections/02-….md:81 [t0008]`), so a hit in a grid goes straight to `pdfk table <doc> <tid>` — CSV with the
  header row — instead of a guessed `Read` window. A table row without its header is not an answer.
  Existing docpacks need `pdfk rebuild <doc>` for the id to appear.
- **Read windows.** The skill and the `doc-researcher` agent now say to centre a `Read` on the hit line
  (`offset = <line> - 5`, `limit = 20-40`). Reading from `offset=1` for a hit at line 81 cost 10x the bytes
  for the same answer.
- **Eval.** `eval/results/mspm0g1518/graphify-comparison.md` compares a docpack against a knowledge graph of
  the same datasheet on one pinout question, and decomposes the per-call token use of the lookup.

## 0.2.0

Accuracy, memory map, tests and a reproducible eval.

- **Memory map.** Peripheral base addresses are extracted from "registers start at a base address of …"
  sentences, SDK define tables and memory-map tables (ST "Boundary address | Peripheral") into `memmap.jsonl`.
  Registers get their absolute `address` (single instance) or `bases` (UART0/UART1, PLL_SYS/PLL_USB, GPIOA…).
  New command `pdfk map [NAME]`; `pdfk reg` prints absolute addresses. A base-address sentence in the
  register's own section wins over a name match elsewhere (XIP registers are at XIP_CTRL, not the XIP window).
- **Verify.** Checks every page a register spans (`page`…`page_end`, previously a fixed 4 pages) and every
  non-trivial field reset value, preferring the field's own `NAME:` definition over mentions in SDK code.
  Memory-map base addresses are verified too. RP2040: 937/937 registers, 4 342 field resets, 52/52 bases.
- **Search noise.** The printed table of contents / list of tables / list of figures is dropped (INDEX.md
  replaces it; the RP2040 front matter shrank from 129 kB to 3.6 kB). SDK code listings rank below prose and
  tables. Identifiers split by the text layer (`PLL _SYS`) are rejoined. Search snippets no longer insert a
  space after every match.
- **Merged cells.** Tables with row/column spans get `tables/tNNNN.json` with exact cell geometry;
  `pdfk table <doc> <tid> --cells` prints it, and the CSV output says when values are repeated across a span.
- **Register parser fixes.** Offsets that the layout model promotes to a heading ("Offset : 0x00", I2C
  registers) are read; field names shifted into the middle of a cell by TableFormer are recovered (PIO CTRL);
  figure references ("… in Figure 35.") are no longer taken as captions.
- **Tests.** 52 pytest tests (`cd cli && python -m pytest`), stdlib only, no docling or PDF needed.
- **Search output.** The top 3 hits are printed in full (for a heading: the heading and what follows it), so
  most answers need no follow-up read. The skill tells the agent to answer as soon as a hit holds the fact,
  to run independent lookups in one turn, and to call `pdfk` as a plain command.
- **README.** Requirements, install from the git URL (pdfk is not on PyPI), a quick start for a new MCU
  project, the command list and current limits. `build`/`rebuild` without docling now explain how to install
  it instead of failing with a traceback.
- **Eval.** `eval/` runs 41 graded questions through `claude -p` against pdfk and against the plain
  "docling markdown + grep" baseline, isolated from user-level plugins. RP2040, Sonnet: both 41/41 correct;
  pdfk cites a page for 39/39 answerable questions (baseline 0/39) at $0.035 vs $0.047 per question.

## 0.1.0

First release: docling conversion with page anchors, section split, tables, register extraction with
text-layer verify, FTS5 search, figures, errata overlay, multi-document projects, skills, sub-agent and hooks.
