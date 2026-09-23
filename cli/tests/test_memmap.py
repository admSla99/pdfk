"""Memory-map extraction (sentences, SDK define tables, ST boundary tables) and register linking."""

from conftest import heading, table, text

from pdfk.memmap import _instance_names, extract_memmap, link_registers


def names(mm):
    return {e["name"]: e for e in mm}


def test_single_base_sentence_with_sdk_define():
    mm = names(extract_memmap([
        heading("2.5.7. List of Registers", 112, 3),
        text("The DMA registers start at a base address of 0x50000000 (defined as DMA_BASE in SDK).", 112),
    ]))
    assert mm["DMA"]["base"] == "0x50000000"
    assert (mm["DMA"]["page"], mm["DMA"]["section"], mm["DMA"]["source"]) == (112, "2.5.7", "text")


def test_multiple_instances_sentence_respectively():
    mm = names(extract_memmap([text(
        "The PLL_SYS and PLL_USB registers start at base addresses of 0x40028000 and 0x4002c000 "
        "respectively (defined as PLL_SYS_BASE and PLL_USB_BASE in SDK).", 235)]))
    assert mm["PLL_SYS"]["base"] == "0x40028000"
    assert mm["PLL_USB"]["base"] == "0x4002c000"


def test_sdk_define_table_without_header():
    mm = names(extract_memmap([table([["XIP_BASE", "0x10000000"], ["XIP_CTRL_BASE", "0x14000000"],
                                      ["XIP_SRAM_END", "0x15004000"]], 25)]))
    assert set(mm) == {"XIP", "XIP_CTRL"}  # *_END is not a base address


def test_st_boundary_table_with_spaced_hex_and_reserved_rows():
    grid = [["Boundary address", "Peripheral", "Bus", "Register map"],
            ["0x4002 1000 - 0x4002 13FF", "RCC", "AHB1", "Section 7.4.30"],
            ["0x4002 1400 - 0x4002 1FFF", "Reserved", "", ""],
            ["0x4800 0000 - 0x4800 03FF", "GPIOA", "AHB2", "Section 9.4.12"]]
    mm = names(extract_memmap([heading("2.2.2 Memory map and register boundary addresses", 81, 3), table(grid, 81)]))
    assert set(mm) == {"RCC", "GPIOA"}
    assert (mm["RCC"]["base"], mm["RCC"]["end"], mm["RCC"]["bus"]) == ("0x40021000", "0x400213ff", "AHB1")


def test_text_sentence_preferred_over_table_and_conflicts_recorded():
    mm = names(extract_memmap([
        table([["DMA_BASE", "0x50000000"]], 26),
        text("The DMA registers start at a base address of 0x50000000 (defined as DMA_BASE in SDK).", 112),
        text("The UART0 registers start at a base address of 0x40034000.", 430),
        text("The UART0 registers start at a base address of 0x40099000.", 431),
    ]))
    assert mm["DMA"]["source"] == "text" and mm["DMA"]["page"] == 112
    assert mm["UART0"]["conflicts"] == [{"base": "0x40099000", "page": 431}]


def test_instance_names():
    known = {"PLL_SYS", "PLL_USB", "UART0", "UART1", "GPIOA", "GPIOB", "IO_BANK0", "I2C0", "PLLX"}
    assert _instance_names("PLL", known) == ["PLL_SYS", "PLL_USB"]
    assert _instance_names("UART", known) == ["UART0", "UART1"]
    assert _instance_names("GPIOx", known) == ["GPIOA", "GPIOB"]
    assert _instance_names("IO_BANK0", known) == ["IO_BANK0"]
    assert _instance_names("SPI", known) == []


def test_link_registers_absolute_address_bases_and_section_fallback():
    mm = [
        {"name": "IO_BANK0", "base": "0x40014000", "page": 245, "section": "2.19.6.1", "source": "text"},
        {"name": "PLL_SYS", "base": "0x40028000", "page": 235, "section": "2.18.4", "source": "text"},
        {"name": "PLL_USB", "base": "0x4002c000", "page": 235, "section": "2.18.4", "source": "text"},
        {"name": "PPB", "base": "0xe0000000", "page": 77, "section": "2.4.8", "source": "text"},
    ]
    regs = [
        {"name": "GPIO5_CTRL", "peripheral": "IO_BANK0", "offset": "0x02c", "section": "2.19.6.1"},
        {"name": "CS", "peripheral": "PLL", "offset": "0x0", "section": "2.18.4"},
        {"name": "CPUID", "peripheral": "M0PLUS", "offset": "0xed00", "section": "2.4.8"},  # name differs: section
        {"name": "NOBASE", "peripheral": "XYZ", "offset": "0x10", "section": "9.9"},
        {"name": "FIXED", "peripheral": "IO_BANK0", "offset": "0x0", "address": "0x40014000", "section": "2.19.6.1"},
    ]
    assert link_registers(regs, mm) == 4
    gpio, cs, cpuid, nobase, fixed = regs
    assert gpio["address"] == "0x4001402c"
    assert cs["bases"] == {"PLL_SYS": "0x40028000", "PLL_USB": "0x4002c000"} and "address" not in cs
    assert cpuid["address"] == "0xe000ed00"
    assert "bases" not in nobase
    assert fixed["address"] == "0x40014000"


def test_sentence_in_the_registers_section_beats_a_name_match_elsewhere():
    """XIP_BASE (0x10000000) is the XIP memory window; the XIP *registers* sit at XIP_CTRL_BASE."""
    mm = [
        {"name": "XIP", "base": "0x10000000", "page": 25, "section": "2.2.2", "source": "table"},
        {"name": "XIP_CTRL", "base": "0x14000000", "page": 126, "section": "2.6.3.6", "source": "text"},
    ]
    regs = [{"name": "CTRL", "peripheral": "XIP", "offset": "0x00", "section": "2.6.3.6"}]
    link_registers(regs, mm)
    assert regs[0]["address"] == "0x14000000"
