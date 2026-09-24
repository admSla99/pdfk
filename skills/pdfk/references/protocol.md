# Query protocol in detail

## Reading the tool output

`pdfk search` line format:

```
<doc> §<section> p.<page> sections/<file>.md:<line> [<tid>]  <snippet>
```

- `§<section>` is the heading number the hit sits under (or a title fragment when unnumbered).
- `p.<page>` is the printed PDF page (1-based, matches the PDF viewer page).
- `<file>:<line>` is where the block starts; `Read` it with `offset = <line> - 5`, `limit = 20-40`.
  Reading from `offset=1` for a hit further down the file costs ~10x the bytes for the same answer.
- `[<tid>]` appears on table and figure hits only: pass it to `pdfk table <doc> <tid>` (CSV with the
  header row) or `pdfk figure <doc> <fid>`, which beats any Read window for tabular data.

`pdfk section <doc> <sec>` prints the section body (default 80 lines) with the same citation header.

## Choosing the search

| Need | Command |
|---|---|
| exact identifier (`RCC_CR`, `GPIO0_CTRL`) | `pdfk reg NAME` first, then `pdfk search "NAME"` |
| base or absolute address of a peripheral / register | `pdfk map NAME`, or the `addr` in `pdfk reg NAME` |
| a table with merged cells (bit maps, pin tables) | `pdfk table <doc> <tid> --cells` |
| a concept ("clock security system") | `pdfk search "clock security system"` |
| something in a table (pin mapping, memory map) | `pdfk search "…" --kind table`, then `pdfk table <doc> <tid>` |
| within one chapter | `--section 7` (prefix match on heading numbers) |
| all headings of a chapter | `pdfk toc <doc> 7 --depth 3` |
| a block diagram, clock tree, timing diagram | `pdfk search "…" --kind figure`, then `pdfk figure <doc> <fid>` |
| which document to trust | `pdfk status` shows each doc's kind: `manual`, `datasheet`, `errata`, `appnote` |

Search terms are AND-ed; each term must appear in the same block. Use fewer, more specific terms.
Underscored identifiers match as phrases (`RCC_CR` also matches "RCC CR"). Use `*` for prefixes.

## Figures

`pdfk figure <doc> <fid>` prints the caption, the text labels found inside the drawing and the PNG path.
Caption and labels answer most questions ("which blocks feed clk_sys?"). Opening the PNG with `Read` costs
image tokens: do it only when the structure of the drawing itself is the answer, and cite the figure:
`[rp2040 §2.18.1 p.229 Figure 35]`.

## Several documents

- Same register name in two docpacks: `pdfk reg NAME` prints both, each with its doc id. Pick by `--doc` and say which.
- Documents disagree (datasheet vs. manual, or an erratum overrides the manual): report both with citations and
  state which one wins. An erratum wins over the manual it corrects.

## Citing

Write citations inline after the fact they support:

> HSE ready is signalled by HSERDY, bit 17 of RCC_CR `[rm0440 §7.4.1 p.281]`.

When a value comes from `pdfk reg`, cite the register line's citation. When you combined two sections,
cite both. If the docpack has `verify: mismatch` for a register, mention it and re-read the section.

## Answer shape (from doc-researcher back to the main agent)

```
Answer: <one paragraph or a short list>
Facts:
- <fact> [doc §sec p.N]
Uncertain / not found:
- <what could not be confirmed>
```

Keep it under ~300 tokens. Do not paste raw manual text back; summarize and cite.
