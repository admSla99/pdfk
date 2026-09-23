# Eval report

Questions: `rp2040.tsv` · model: `sonnet` · isolated (no user plugins, hooks or MCP servers)

| Metric | pdfk | baseline |
|---|---:|---:|
| Correct | 41/41 | 41/41 |
| With page citation | 39/41 | 0/41 |
| Tokens per question, mean | 78,496 | 62,782 |
| Tokens per question, median | 79,488 | 57,449 |
| Uncached input tokens, mean | 3,180 | 6,947 |
| Output tokens, mean | 770 | 801 |
| Cost per question (USD) | 0.035 | 0.047 |
| Cost total (USD) | 1.45 | 1.92 |
| Turns, mean | 5.4 | 3.9 |
| Wall time per question (s) | 11 | 10 |
| Runs with a denied tool call | 2 | 0 |

## By category

| Category | pdfk correct | baseline correct |
|---|---:|---:|
| errata | 2/2 | 2/2 |
| memmap | 7/7 | 7/7 |
| negative | 3/3 | 3/3 |
| procedure | 9/9 | 9/9 |
| register | 15/15 | 15/15 |
| table | 5/5 | 5/5 |

## Wrong or incomplete answers

**pdfk**: none

**baseline**: none

