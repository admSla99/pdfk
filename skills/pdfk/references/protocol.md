# Query protocol in detail

## Reading the tool output

`pdfk search` line format:

```
<doc> §<section> p.<page> sections/<file>.md:<line>  <snippet>
```

- `§<section>` is the heading number the hit sits under (or a title fragment when unnumbered).
- `p.<page>` is the printed PDF page (1-based, matches the PDF viewer page).
- `<file>:<line>` is where the block starts; open with `Read` at that offset.

`pdfk section <doc> <sec>` prints the section body (default 80 lines) with the same citation header.

## Choosing the search

| Need | Command |
|---|---|
| exact identifier (`RCC_CR`, `GPIO0_CTRL`) | `pdfk reg NAME` first, then `pdfk search "NAME"` |
| a concept ("clock security system") | `pdfk search "clock security system"` |
| something in a table (pin mapping, memory map) | `pdfk search "…" --kind table`, then `pdfk table <doc> <tid>` |
| within one chapter | `--section 7` (prefix match on heading numbers) |
| all headings of a chapter | `pdfk toc <doc> 7 --depth 3` |

Search terms are AND-ed; each term must appear in the same block. Use fewer, more specific terms.
Underscored identifiers match as phrases (`RCC_CR` also matches "RCC CR"). Use `*` for prefixes.

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
