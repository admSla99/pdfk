# Registers and memory map (`registers.jsonl`, `memmap.jsonl`, `pdfk reg`, `pdfk map`)

One JSON line per register found by the vendor profile:

```json
{"doc":"rp2040","name":"UARTIBRD","peripheral":"UART","section":"4.2.8","page":432,"page_end":432,
 "file":"25-4_2-uart.md","line":547,"offset":"0x024",
 "bases":{"UART0":"0x40034000","UART1":"0x40038000"},
 "fields":[{"bits":"15:0","name":"BAUD_DIVINT","access":"RW","reset":"0x0000","desc":"The integer baud rate divisor."}],
 "confidence":"good","verify":"ok"}
```

- `address`: absolute address when the peripheral has one instance (`GPIO5_CTRL` → `0x4001402c`).
  `bases`: instance → base address when it has several (`UART0`, `UART1`; `PLL_SYS`, `PLL_USB`).
  Absolute address = base + `offset`. `pdfk reg` prints both forms.
- `confidence`: `good` = offset and fields found; `partial` = something missing (read the section).
- `verify`: `ok` = name, offset, reset, every field name and every non-trivial field reset value were found in
  the PDF text layer of the pages the register spans (`page`…`page_end`); `mismatch` = at least one was not
  (`verify_missing` lists them, e.g. `field_reset:SPEED=0x2`); `unchecked` = no page.
- `derived: true`: member of a family printed once ("GPIO0_CTRL, …, GPIO29_CTRL"); its offset was computed
  from the family's stride. Name and offset of derived members are not checked against the text.
- Bit ranges are `hi:lo` (`31:28`) or a single bit (`27`). A field without a name is the whole register value.

`memmap.jsonl` holds peripheral base addresses, taken from "registers start at a base address of …" sentences,
SDK define tables (`IO_BANK0_BASE | 0x40014000`) and memory-map tables ("Boundary address | Peripheral | Bus").
Each entry is verified against the text layer the same way. `pdfk map` lists them; `pdfk map UART1` looks one up.

`pdfk reg NAME` prints the register with all fields and a citation. `pdfk reg PERIPH` (e.g. `RCC`) lists
candidates. `--json` gives the raw record for programmatic use (e.g. generating defines).

When generating code from these values, put the citation into a comment:

```c
#define UART1_UARTIBRD_ADDR  0x40038024U   /* rp2040 §4.2.8 p.432 */
#define RCC_CR_PLLRDY_Pos    27U           /* rm0440 §7.4.1 p.281 */
```

If an SVD file or vendor header exists in the SDK, generate numbers from it and use the docpack for
semantics. Cross-check a sample of values between the two; report any disagreement.
