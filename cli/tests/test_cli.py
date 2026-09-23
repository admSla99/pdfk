"""Query commands end to end on a hand-built docpack (stdlib only, no docling)."""

import json

import pytest

from pdfk.cli import main
from pdfk.paths import DocEntry, save_doc_entry, save_json, write_jsonl
from pdfk.search import build_index


@pytest.fixture
def root(tmp_path):
    root = tmp_path / ".pdfk"
    pack = root / "rp2040"
    (pack / "sections").mkdir(parents=True)
    (pack / "tables").mkdir()
    save_doc_entry(root, DocEntry(id="rp2040", title="RP2040 Datasheet", pages=642,
                                  counts={"registers": 2, "memmap": 2, "tables": 1}))
    write_jsonl(pack / "registers.jsonl", [
        {"doc": "rp2040", "name": "UARTIBRD", "peripheral": "UART", "offset": "0x024", "section": "4.2.8",
         "page": 432, "file": "25-4_2-uart.md", "line": 547, "verify": "ok",
         "bases": {"UART0": "0x40034000", "UART1": "0x40038000"},
         "fields": [{"bits": "15:0", "name": "BAUD_DIVINT", "access": "RW", "reset": "0x0000", "desc": "Integer divisor"}]},
        {"doc": "rp2040", "name": "GPIO5_CTRL", "peripheral": "IO_BANK0", "offset": "0x02c", "section": "2.19.6.1",
         "page": 248, "file": "17-gpio.md", "line": 167, "verify": "ok", "address": "0x4001402c", "fields": []},
    ])
    write_jsonl(pack / "memmap.jsonl", [
        {"doc": "rp2040", "name": "UART0", "base": "0x40034000", "page": 430, "section": "4.2.8", "source": "text", "verify": "ok"},
        {"doc": "rp2040", "name": "UART1", "base": "0x40038000", "page": 430, "section": "4.2.8", "source": "text", "verify": "ok"},
    ])
    (pack / "tables" / "t0001.csv").write_text("Bits,Name\n31,Res.\n30,Res.\n", encoding="utf-8")
    save_json(pack / "tables" / "t0001.json", {"id": "t0001", "page": 281, "caption": "RCC_CR bit map", "rows": 3, "cols": 2,
                                               "cells": [{"r": 0, "c": 0, "rs": 1, "cs": 1, "text": "Bits", "hdr": True},
                                                         {"r": 1, "c": 1, "rs": 2, "cs": 1, "text": "Res."}]})
    (pack / "sections" / "25-4_2-uart.md").write_text(
        "---\ndoc: rp2040\n---\n\n## 4.2.8. List of Registers <!-- p.430 -->\n\nThe UART0 and UART1 registers …\n",
        encoding="utf-8")
    save_json(pack / "sections.json", [{"sec": "4.2.8", "title": "List of Registers", "level": 2, "page": 430,
                                         "file": "25-4_2-uart.md", "line": 5}])
    build_index(pack / "search.sqlite", "rp2040", [
        {"file": "25-4_2-uart.md", "line": 7, "page": 430, "section": "4.2.8", "kind": "text",
         "text": "The UART0 and UART1 registers start at base addresses of 0x40034000 and 0x40038000"}])
    return root


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


def test_reg_prints_absolute_addresses_of_every_instance(root, capsys):
    code, out = run(capsys, "reg", "UARTIBRD", "--root", str(root))
    assert code == 0
    assert "addr UART0 0x40034024, UART1 0x40038024" in out
    assert "[rp2040 §4.2.8 p.432" in out
    assert "BAUD_DIVINT" in out


def test_reg_single_instance_address(root, capsys):
    _, out = run(capsys, "reg", "GPIO5_CTRL", "--root", str(root))
    assert "addr 0x4001402c" in out


def test_map_lists_and_filters(root, capsys):
    _, out = run(capsys, "map", "--root", str(root))
    assert out.index("UART0") < out.index("UART1")
    _, out = run(capsys, "map", "uart1", "--root", str(root))
    assert "UART1" in out and "UART0" not in out and "[rp2040 §4.2.8 p.430]" in out
    code, out = run(capsys, "map", "NOPE", "--root", str(root))
    assert code == 1


def test_table_cells_shows_spans_and_csv_warns(root, capsys):
    _, out = run(capsys, "table", "rp2040", "t0001", "--cells", "--root", str(root))
    assert "  1   1       2       1 Res." in out
    _, out = run(capsys, "table", "rp2040", "t0001", "--root", str(root))
    assert out.startswith("# merged cells")


def test_search_and_section(root, capsys):
    _, out = run(capsys, "search", "UART1 registers", "--root", str(root))
    assert "rp2040 §4.2.8 p.430 sections/25-4_2-uart.md:7" in out
    _, out = run(capsys, "section", "rp2040", "4.2.8", "--root", str(root))
    assert out.startswith("[rp2040 §4.2.8 p.430")


def test_reg_json(root, capsys):
    _, out = run(capsys, "reg", "UARTIBRD", "--json", "--root", str(root))
    assert json.loads(out)[0]["bases"]["UART1"] == "0x40038000"
