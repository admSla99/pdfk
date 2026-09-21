# Registers (`registers.jsonl`, `pdfk reg`)

One JSON line per register found by the vendor profile:

```json
{"doc":"rm0440","name":"RCC_CR","peripheral":"RCC","section":"7.4.1","page":281,
 "file":"07-7-reset-and-clock-control-rcc.md","line":412,
 "offset":"0x00","reset":"0x00000063",
 "fields":[{"bits":"27","name":"PLLRDY","desc":"Main PLL clock ready flag ..."}],
 "confidence":"good","verify":"ok"}
```

- `confidence`: `good` = offset and fields found; `partial` = something missing (read the section).
- `verify`: `ok` = name, offset, reset and field names were all found in the PDF text layer of that page;
  `mismatch` = at least one was not (`verify_missing` lists them); `unchecked` = no page.
- Bit ranges are `hi:lo` (`31:28`) or a single bit (`27`).

`pdfk reg NAME` prints the register with all fields and a citation. `pdfk reg PERIPH` (e.g. `RCC`) lists
candidates. `--json` gives the raw record for programmatic use (e.g. generating defines).

When generating code from these values, put the citation into a comment:

```c
#define RCC_CR_PLLRDY_Pos  27U   /* rm0440 §7.4.1 p.281 */
```

If an SVD file or vendor header exists in the SDK, generate numbers from it and use the docpack for
semantics. Cross-check a sample of values between the two; report any disagreement.
