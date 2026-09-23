"""Register extraction on the layouts seen in real manuals (block streams as docling produces them)."""

from conftest import heading, table, text

from pdfk.registers import _match_name, extract_registers

BITS_HDR = ["Bits", "Description", "Type", "Reset"]


def by_name(regs):
    return {r["name"]: r for r in regs}


# --------------------------------------------------------------------------- ST reference manual layout


def st_rcc_blocks():
    return [
        heading("7 Reset and clock control (RCC)", 273, 1),
        heading("7.4 RCC registers", 281, 2),
        heading("7.4.1 Clock control register (RCC_CR)", 281, 3),
        text("Address offset: 0x00", 281),
        text("Reset value: 0x0000 0063", 281),
        # the 2x16 bit map is skipped: fields come from the paragraphs below
        table([["31", "30", "29", "28"], ["Res.", "Res.", "Res.", "Res."]], 281),
        text("Bits 31:28 Reserved, must be kept at reset value.", 281),
        text("Bit 27 PLLRDY: Main PLL clock ready flag\nSet by hardware to indicate that the main PLL is locked.", 282),
        text("Bit 26 PLLON: Main PLL enable\nSet and cleared by software.", 282),
        heading("7.4.2 Internal clock sources calibration register (RCC_ICSCR)", 283, 3),
        text("Address offset: 0x04", 283),
        text("Reset value: 0x40XX 0000 where X is undefined.", 283),
        text("Bits 31:24 HSITRIM[6:0]: HSI16 clock trimming", 283),
    ]


def test_st_register_name_offset_reset_and_text_fields():
    regs = by_name(extract_registers(st_rcc_blocks(), doc_id="rm0440"))
    cr = regs["RCC_CR"]
    assert cr["peripheral"] == "RCC"
    assert cr["section"] == "7.4.1"
    assert (cr["offset"], cr["reset"]) == ("0x00", "0x00000063")
    fields = {f["bits"]: f["name"] for f in cr["fields"]}
    assert fields == {"31:28": "Reserved", "27": "PLLRDY", "26": "PLLON"}
    assert cr["confidence"] == "good"
    assert cr["page"] == 281 and cr["page_end"] == 282


def test_st_reset_with_undefined_digits_and_indexed_field_name():
    icscr = by_name(extract_registers(st_rcc_blocks(), doc_id="rm0440"))["RCC_ICSCR"]
    assert icscr["reset"] == "0x40xx0000"
    assert icscr["fields"][0]["name"] == "HSITRIM[6:0]"


# --------------------------------------------------------------------------- RP2040 layout


def test_rp2040_register_family_is_expanded_with_offsets():
    blocks = [
        heading("2.19.6.1. IO - User Bank", 245, 4),
        heading("IO_BANK0: GPIO0_STATUS, GPIO1_STATUS, …, GPIO28_STATUS, GPIO29_STATUS Registers", 248, 5),
        text("Offsets", 248),  # the layout model splits the label from its value
        text(": 0x000, 0x008, …, 0x0e0, 0x0e8", 248),
        table([BITS_HDR, ["31:27", "Reserved.", "-", "-"],
               ["26", "IRQTOPROC: interrupt to processors, after override is applied", "RO", "0x0"]], 248),
    ]
    regs = by_name(extract_registers(blocks, doc_id="rp2040"))
    assert len(regs) == 30
    assert regs["GPIO0_STATUS"]["offset"] == "0x000"
    assert regs["GPIO7_STATUS"]["offset"] == "0x038"
    assert regs["GPIO29_STATUS"]["offset"] == "0x0e8"
    assert regs["GPIO7_STATUS"]["derived"] is True  # not printed by name in the PDF
    assert "derived" not in regs["GPIO28_STATUS"]
    assert regs["GPIO7_STATUS"]["family"] == "GPIO0_STATUS…GPIO29_STATUS"
    assert regs["GPIO7_STATUS"]["peripheral"] == "IO_BANK0"
    f = regs["GPIO7_STATUS"]["fields"][1]
    assert (f["bits"], f["name"], f["access"], f["reset"]) == ("26", "IRQTOPROC", "RO", "0x0")


def test_bit_table_split_over_two_pages_is_merged():
    blocks = [
        heading("IO_BANK0: INTR0 Register", 250, 5),
        text("Offset: 0x0f0", 250),
        table([BITS_HDR, ["31", "GPIO7_EDGE_HIGH", "WC", "0x0"], ["30", "GPIO7_EDGE_LOW", "WC", "0x0"]], 250, "t0001"),
        table([BITS_HDR, ["29", "GPIO7_LEVEL_HIGH", "RO", "0x0"], ["28", "GPIO7_LEVEL_LOW", "RO", "0x0"]], 251, "t0002"),
    ]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    assert [f["bits"] for f in r["fields"]] == ["31", "30", "29", "28"]
    assert r["fields"][2]["name"] == "GPIO7_LEVEL_HIGH"  # a bare identifier in the description column
    assert r["page_end"] == 251


def test_margin_caption_read_before_previous_tables_continuation():
    """Docling reads the next register's margin caption ("Table 291. …") before the rest of the previous
    register's bit table. The continuation rows must stay with the previous register."""
    blocks = [
        heading("IO_BANK0: PROC0_INTE0 Register", 254, 5),
        text("Offset: 0x100", 254),
        table([BITS_HDR, ["31", "GPIO7_EDGE_HIGH", "RW", "0x0"], ["2", "GPIO0_EDGE_LOW", "RW", "0x0"]], 254, "t0001"),
        text("Table 291. PROC0_INTE1 Register", 255),
        table([BITS_HDR, ["1", "GPIO0_LEVEL_HIGH", "RW", "0x0"], ["0", "GPIO0_LEVEL_LOW", "RW", "0x0"]], 255, "t0002"),
        heading("IO_BANK0: PROC0_INTE1 Register", 255, 5),
        text("Offset: 0x104", 255),
        table([BITS_HDR, ["31", "GPIO15_EDGE_HIGH", "RW", "0x0"]], 255, "t0003"),
    ]
    regs = by_name(extract_registers(blocks, doc_id="rp2040"))
    assert [f["bits"] for f in regs["PROC0_INTE0"]["fields"]] == ["31", "2", "1", "0"]
    assert regs["PROC0_INTE0"]["page_end"] == 255
    assert regs["PROC0_INTE1"]["offset"] == "0x104"
    assert [f["name"] for f in regs["PROC0_INTE1"]["fields"]] == ["GPIO15_EDGE_HIGH"]
    assert regs["PROC0_INTE1"]["peripheral"] == "IO_BANK0"


def test_repeated_caption_on_next_page_does_not_split_the_register():
    blocks = [
        heading("DMA: CH0_CTRL_TRIG Register", 112, 5),
        text("Offset: 0x00c", 112),
        table([BITS_HDR, ["31", "AHB_ERROR: Logical OR of the READ_ERROR and WRITE_ERROR flags.", "RO", "0x0"]], 112),
        text("Table 125. CH0_CTRL_TRIG Register", 113),
        table([BITS_HDR, ["3:2", "DATA_SIZE: Set the size of each bus transfer", "RW", "0x0"]], 113, "t0002"),
    ]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    assert [f["name"] for f in r["fields"]] == ["AHB_ERROR", "DATA_SIZE"]


def test_single_unnamed_field_takes_the_register_name():
    blocks = [
        heading("SYSCFG: PROC0_NMI_MASK Register", 308, 5),
        text("Offset: 0x00", 308),
        table([BITS_HDR, ["31:0", "Set a bit high to enable NMI from that IRQ", "RW", "0x00000000"]], 308),
    ]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    assert r["fields"][0]["name"] == "PROC0_NMI_MASK"


def test_unnumbered_label_headings_do_not_end_a_register():
    blocks = [
        heading("2.18.4. List of Registers", 235, 4),
        heading("PLL: CS Register", 235, 5),
        text("Offset: 0x0", 235),
        heading("Description", 235, 5),  # label-like heading between the offset and the bit table
        text("Control and Status", 235),
        table([BITS_HDR, ["31", "LOCK: PLL is locked", "RO", "0x0"]], 235),
    ]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    assert r["name"] == "CS" and r["offset"] == "0x0" and r["fields"][0]["name"] == "LOCK"
    assert r["section"] == "2.18.4"


def test_match_name_rejects_ordinary_captions():
    assert _match_name("Table 12. Clock sources") is None
    assert _match_name("Figure 3. Register access timing") is None
    assert _match_name("Table 12. TIMER_CTRL Register")[1] == ["TIMER_CTRL"]


def test_offset_promoted_to_heading_is_still_read():
    blocks = [
        heading("4.3.17. List of Registers", 465, 3),
        text("I2C: IC_CON Register", 466),
        text("Table 453. IC_CON Register", 467),
        heading("Offset : 0x00", 467, 4),
        heading("Description", 467, 4),
        table([BITS_HDR, ["0", "MASTER_MODE: This bit controls whether the master is enabled.", "RW", "0x1"]], 467),
    ]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    assert (r["name"], r["offset"], r["peripheral"]) == ("IC_CON", "0x00", "I2C")


def test_description_shifted_into_the_next_row_keeps_field_names():
    grid = [BITS_HDR,
            ["11:8", "CLKDIV_RESTART : Restart a state machine's clock divider after the divisors", "SC", "0x0"],
            ["7:4", "(SMx_CLKDIV) have been changed on-the-fly. SM_RESTART : Write 1 to clear internal SM state.", "SC", "0x0"],
            ["3:0", "scratch registers are not affected. SM_ENABLE : Enable/disable each state machine.", "RW", "0x0"]]
    blocks = [heading("PIO: CTRL Register", 372, 5), text("Offset: 0x000", 372), table(grid, 373)]
    (r,) = extract_registers(blocks, doc_id="rp2040")
    names = [f["name"] for f in r["fields"]]
    assert names == ["CLKDIV_RESTART", "SM_RESTART", "SM_ENABLE"]
    assert r["fields"][0]["desc"].endswith("(SMx_CLKDIV) have been changed on-the-fly.")
    assert r["fields"][1]["desc"].startswith("Write 1")
