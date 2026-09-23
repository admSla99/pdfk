---
name: pdfk
description: Answer questions from large vendor PDFs (MCU reference manuals, datasheets, errata) that were converted into docpacks under .pdfk/. Use whenever the user asks about registers, bit fields, offsets, reset values, memory maps, peripherals, clocks, configuration sequences, pinouts, or anything "in the manual / datasheet / PDF". Never Read the PDF itself; use pdfk search / reg / section and cite pages.
allowed-tools: Bash(pdfk:*) Bash(*/bin/pdfk *) Read Grep Glob
---

# pdfk — querying docpacks

Docpacks live in `.pdfk/<doc-id>/` (see `pdfk status`). A project usually has several: a reference `manual`,
a `datasheet`, `errata`, app notes. Each has `INDEX.md` (table of contents with pages), `sections/*.md`
(markdown with `<!-- p.N -->` page anchors), `tables/`, `figures/`, `registers.jsonl`, `memmap.jsonl`, `search.sqlite`.

Run `pdfk …` as one plain command: no `cd`, no `&&`/`||` fallbacks, no pipes. The plugin puts it on PATH
and compound commands trigger permission prompts. Only if it fails with "command not found", run
`"${CLAUDE_PLUGIN_ROOT}/bin/pdfk"` with the same arguments.

## Rules

1. **Never answer hardware facts from memory.** If a docpack exists for the device, every number
   (address, offset, reset value, bit position, timing) must come from it. No hit → say the document does not contain it.
2. **Cite every fact** as `[DOC §section p.N]`, e.g. `[rm0440 §7.4.1 p.281]`. The citation comes from the
   tool output (`pdfk search`, `pdfk reg`, `pdfk section` print it).
3. **Never Read a PDF or a whole section file.** Read `sections/*.md` only with `offset`/`limit` at the line
   number a search hit gave you (a window of 40-120 lines).
4. **Prefer machine sources for numbers when present** (CMSIS-SVD, vendor headers in the SDK); use the docpack
   for meaning, sequences, constraints. Say which source a number came from.
5. If a register lookup shows `mismatch`, the value was not confirmed against the PDF text layer:
   open the cited section and read the number yourself before using it. Values marked `ok` were already
   checked against the PDF: do not re-read the section just to confirm them.
6. **Answer as soon as a hit contains the fact.** `pdfk search` prints the whole paragraph (or the heading
   plus what follows it) for the top 3 hits; `pdfk reg` prints every field. Run independent lookups
   (e.g. `pdfk reg` and `pdfk search`) in parallel in the same turn. Every extra turn re-sends the whole
   conversation.

## Protocol (cheapest first)

```
pdfk status                                  # which docs exist (already in the session context: skip it)
pdfk reg RCC_CR [--doc rm0440]               # register, bit fields, offset/reset, absolute address, citation
pdfk map [UART1]                             # peripheral base addresses (memory map), verified
pdfk search "PLL ready flag" --doc rm0440    # ranked hits: doc §sec p.N sections/file.md:line  text (top 3 in full)
pdfk search "RCC_AHB2*" --kind table         # prefix, restrict to tables / headings / text
pdfk section rm0440 7.4.1 --lines 80         # print a section by number (paged with --offset)
pdfk toc rm0440 7 --depth 3                  # headings below a chapter
pdfk table rm0440 t0421 [--cells]            # CSV of a table; --cells shows merged-cell spans exactly
pdfk search "clock tree" --kind figure       # figures are indexed by caption and by the labels inside them
pdfk figure rm0440 f0102                     # caption, labels, PNG path (Read the PNG only if really needed)
```

Without `--doc`, `search` and `reg` cover every docpack; hits carry the doc id. Electrical limits and pinout
come from the `datasheet`, register behaviour from the `manual`. `pdfk reg` prints a `! errata mention …`
block when an `errata` docpack names that register: read those hits before relying on the register.

Then, if needed, `Read .pdfk/<doc>/sections/<file>.md` with `offset=<line>` `limit=80`.

For anything that needs more than two searches, delegate to the `doc-researcher` agent with the exact
question and expected output, so raw manual text stays out of the main context.

Details: [references/protocol.md](references/protocol.md) · [references/registers.md](references/registers.md)
