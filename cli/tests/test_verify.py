"""Text-layer verification, exercised on normalised page text (no PDF needed)."""

from pdfk.verify import check_memmap_entry, check_register, hex_variants, norm

PAGE = norm("""
2.18.3. Programming
42 if ((pll->cs & PLL_CS_LOCK_BITS) && (refdiv == (pll->cs & PLL_CS_REFDIV_BITS)))
PLL: CS Register
Offset: 0x0
Bits Description Type Reset
31 LOCK: PLL is locked RO 0x0
30:9 Reserved. - -
5:0 REFDIV: Divides the PLL input reference clock. Behaviour is undefined for div=0.
PLL output will be unpredictable during refdiv changes, wait for lock=1 before using it. RW 0x01
""")


def reg(**kw):
    base = {"name": "CS", "offset": "0x0", "fields": [
        {"bits": "31", "name": "LOCK", "reset": "0x0"},
        {"bits": "30:9", "name": "Reserved", "reset": "-"},
        {"bits": "5:0", "name": "REFDIV", "reset": "0x01"}]}
    base.update(kw)
    return base


def test_matching_register_passes_even_when_code_mentions_the_field_first():
    assert check_register(reg(), PAGE) == []


def test_wrong_offset_and_wrong_field_reset_are_reported():
    r = reg(offset="0x8")
    r["fields"][2]["reset"] = "0x3f"
    assert check_register(r, PAGE) == ["offset", "field_reset:REFDIV=0x3f"]


def test_missing_field_name_is_reported():
    r = reg()
    r["fields"].append({"bits": "8", "name": "BYPASS", "reset": "0x0"})
    assert check_register(r, PAGE) == ["field:BYPASS"]


def test_derived_family_member_skips_name_and_offset():
    assert check_register(reg(name="GPIO7_CTRL", offset="0x03c", derived=True), PAGE) == []


def test_hex_variants_cover_padding_and_spacing():
    assert {"0x63", "0x00000063", "0x063"} <= hex_variants("0x0000 0063")


def test_memmap_entry_check():
    text = norm("The DMA registers start at a base address of 0x50000000 (defined as DMA_BASE in SDK)")
    assert check_memmap_entry({"base": "0x50000000"}, text) == []
    assert check_memmap_entry({"base": "0x50100000"}, text) == ["base"]
