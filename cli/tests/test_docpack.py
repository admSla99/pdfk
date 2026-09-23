"""Post-processing steps that shape the docpack: identifier repair, TOC removal, heading levels,
figure captions and file splitting."""

from conftest import heading, table, text

from pdfk.docpack import (
    TOC_PLACEHOLDER,
    Block,
    _adopt_margin_captions,
    drop_toc,
    fix_text,
    normalize_levels,
    render_part,
    split_parts,
)


def test_fix_text_rejoins_identifiers_split_at_underscores():
    assert fix_text("The PLL _SYS and PLL _USB registers") == "The PLL_SYS and PLL_USB registers"
    assert fix_text("(pll->cs & PLL _CS_LOCK_BITS)") == "(pll->cs & PLL_CS_LOCK_BITS)"
    assert fix_text("a _b stays? no: x _1") == "a_b stays? no: x_1"
    assert fix_text("nothing to do") == "nothing to do"


def test_drop_toc_removes_printed_contents_but_keeps_list_of_registers():
    toc_grid = [["1.1. Why is the chip called RP2040?. . . . . . . . . . . . . 9"],
                ["2.1. Bus Fabric . . . . . . . . . . . . . . . . . . 15"]]
    blocks = [
        heading("Table of contents", 3, 1),
        table(toc_grid, 3, "t0001"),
        table([["2.9.2. Digital Core Supply (DVDD). . . . . . . . . . . . 152"]], 4, "t0002"),
        heading("Chapter 1. Introduction", 9, 1),
        text("RP2040 is a low-cost, high-performance microcontroller.", 9),
        heading("2.19.6. List of Registers", 245, 3),
        table([["Offset", "Name", "Info"], ["0x000", "GPIO0_STATUS", "GPIO status"]], 245, "t0003"),
    ]
    tables = [{"id": "t0001"}, {"id": "t0002"}, {"id": "t0003"}]
    assert drop_toc(blocks, tables) == 2
    assert [t["id"] for t in tables] == ["t0003"]
    kinds = [(b.kind, b.text[:25]) for b in blocks]
    assert ("text", TOC_PLACEHOLDER[:25]) in kinds
    assert any(b.kind == "table" and b.table_id == "t0003" for b in blocks)


def test_leader_heuristic_catches_an_unlabelled_list_of_tables():
    blocks = [text("Table 1. Pin functions . . . . . . . . . . . 12\nTable 2. Clocks . . . . . . . . . . . 30", 8),
              text("Plain paragraph with a single ellipsis... and no leaders.", 9)]
    tables = []
    assert drop_toc(blocks, tables) == 1
    assert blocks[0].text.startswith("Plain paragraph")


def test_normalize_levels_from_numbering_nests_unnumbered_headings():
    blocks = [heading("2.18. PLL", 229), heading("2.18.4. List of Registers", 235),
              heading("PLL: CS Register", 235), heading("Description", 235), heading("2.19. GPIO", 237)]
    assert normalize_levels(blocks) == "numbering"
    assert [b.level for b in blocks] == [1, 2, 3, 3, 1]


def test_margin_caption_is_adopted_but_references_are_not():
    pic = Block("picture", "", 229, table_id="f0006", grid=[["FREF, VCO"]])
    blocks = [
        text("See the block diagram in Figure 35. The PLL contains a VCO.", 229),  # reference, not a caption
        pic,
        text("Offset : 0x1c Table 273. COUNT Register Figure 35. PLL overview.", 228),  # glued caption
    ]
    _adopt_margin_captions(blocks)
    assert pic.title == "Figure 35. PLL overview."


def test_split_parts_names_files_after_numbered_sections():
    filler = "x" * 20_000
    blocks = [heading("1. Introduction", 9, 1), text(filler, 9),
              heading("2. System Description", 15, 1), text(filler, 15),
              heading("2.1. Bus Fabric", 15, 2), text(filler * 4, 16),
              heading("2.2. Address Map", 25, 2), text(filler * 4, 25)]
    normalize_levels(blocks)
    parts = split_parts(blocks)
    names = [p.name for p in parts]
    assert names[0].startswith("00-1-introduction")
    assert any("2_1-bus-fabric" in n for n in names)
    assert any("2_2-address-map" in n for n in names)


def test_render_part_emits_page_anchors_and_merged_cell_pointer():
    b_tab = table([["Bits", "Name"], ["31:28", "Res."]], 282, "t0421")
    b_tab.spans = True
    blocks = [heading("7.4.1 Clock control register (RCC_CR)", 281, 3), text("Address offset: 0x00", 281), b_tab]
    normalize_levels(blocks)
    parts = split_parts(blocks)
    md, heads, records = render_part(parts[-1], "rm0440", "rm0440.pdf")
    assert "<!-- p.281 -->" in md and "<!-- p.282 -->" in md
    assert "pdfk table rm0440 t0421 --cells" in md
    assert heads[0]["sec"] == "7.4.1"
    assert {r["kind"] for r in records} == {"heading", "text", "table"}
