# pdfk vs. a knowledge graph — one pinout lookup

Question: *What is the NRST pin number on the PT (LQFP-48) package for the MSPM0G1518?*
Ground truth: **pin 4** (Table 6-2 Pin Attributes, p.18; Figure 6-5 package diagram, p.14).

Two fresh agents, same prompt, same model, no shared context. One was restricted to the
`pdfk` skill, the other to [graphify](https://github.com/admSla99/graphify), an LLM-built
knowledge graph over the same PDF (74 nodes / 118 edges, built for 142k output tokens).

| | pdfk | graphify |
|---|---:|---:|
| Answer | **pin 4** (correct, two sources) | not found |
| Tool calls | 4 | 18 |
| Wall clock | 27 s | 136 s |
| Final-turn context | 58,057 | 89,475 |

## Why the graph missed it

The graph holds architectural concepts — peripherals, clock tree, power domains — extracted
from the datasheet's prose. Pinout tables were never turned into nodes: `NRST` appears only as
three characters inside one node's free-text description, and no node or edge carries a package
or pin number. The graph's vocabulary has no `lqfp` or `package` token at all.

That is the structural point, not a tuning problem. A concept graph answers *how does X relate
to Y*; a pin lookup is a row in a grid, and rows are what get dropped when a document is
summarised into entities. Docpacks keep the grid (`tables/*.csv`, `pdfk table`), so the fact
survives.

The graphify agent deserves credit for reporting the absence instead of inventing a pin number —
but proving the negative cost it 4.5x the tool calls of finding the answer.

## The 58,057 is not what the lookup cost

Parsing the pdfk agent's transcript, per API call (input / cache-creation / cache-read / output):

| Call | in | cache-w | cache-r | out |
|---|---:|---:|---:|---:|
| 1 | 8,941 | 36,872 | 0 | 74 |
| 2 | 1,393 | 47,414 | 0 | 199 |
| 3 | 2 | 2,785 | 47,414 | 288 |
| 4 | 2 | 6,916 | 50,199 | 940 |
| **Σ** | **10,338** | **93,987** | **97,613** | **1,501** |

`58,057` is call 4 alone (2 + 6,916 + 50,199 + 940) — the final request's context size, not the
run. Cumulative is ~203k, of which ~94% is the system prompt, tool schemas (167k chars) and skill
listing being cached and re-read. Every subagent pays that regardless of task, so it is the wrong
number for comparing retrieval strategies. **Uncached input + output (11,839) is the honest one.**

## What the retrieval actually read

| Step | Bytes |
|---|---:|
| `Skill` load | 26 |
| `pdfk status` | 293 |
| `pdfk search "NRST" --doc mspm0g1518 --kind table` | 1,782 |
| `Read …02-6-pin-configuration-and-functions.md offset=1 limit=100` | **13,103** |

The Read is 87% of the content and most of it was wasted: the hit was at line 81, the header row
at 82, the answer at 84. The agent read from line 1 of a 156-line file because the skill said
"a window of 40-120 lines" without saying *where* to put it.

## Fixes

| Strategy | Bytes |
|---|---:|
| `Read offset=1 limit=100` (what happened) | 13,103 |
| `Read offset=76 limit=15` (centred) | 1,260 |
| `pdfk table mspm0g1518 t0008` (CSV, header included) | 1,926 |

1. **Centre the Read window** on the hit line (`offset = line - 5`, `limit = 20-40`) — rules in
   `SKILL.md` and `doc-researcher.md` now say this, with the cost of getting it wrong.
2. **Print the table id in the hit line.** `search` now emits `…md:81 [t0008]`, so a tabular
   question goes `search` → `pdfk table` with no Read and no guessed window. Requires
   `pdfk rebuild <doc>` for docpacks built before this change.

`--full 3` was considered and rejected: it expands *paragraph* blocks, and a table hit has no
paragraph to expand. Measured identical to the plain search (1,808 bytes), so it fixes nothing here.

## Reproducing

```
pdfk build mspm0g1518.pdf --id mspm0g1518 --kind datasheet --figures
pdfk search "NRST" --doc mspm0g1518 --kind table     # hit carries [t0008]
pdfk table mspm0g1518 t0008                          # CSV, PT PIN column → 4
```
