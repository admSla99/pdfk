"""The eval grader decides the headline numbers, so its matching rules are tested too."""

import importlib.util

from conftest import PLUGIN_DIR

spec = importlib.util.spec_from_file_location("run_eval", PLUGIN_DIR / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)
grade = run_eval.grade


def test_hex_matches_any_padding_and_separators():
    assert grade("0x004 ; 4:0", "GPIO0_CTRL is at offset **0x4**, FUNCSEL is bits [4:0].", "pdfk")["correct"]
    assert grade("0x40038024", "UART1 UARTIBRD lives at `0x4003_8024` [rp2040 §4.2.8 p.432]", "pdfk")["correct"]
    assert not grade("0x004", "the offset is 0x0040", "pdfk")["correct"]


def test_bit_ranges_accept_common_spellings_but_not_other_numbers():
    for s in ("bits 18:16", "bits 18-16", "bits [18..16]", "bits 18 to 16", "bits 18–16"):
        assert grade("18:16", s, "pdfk")["correct"], s
    assert not grade("18:16", "bits 118:16", "pdfk")["correct"]


def test_numbers_do_not_match_inside_other_numbers():
    assert grade("12|twelve", "There are 12 independent channels.", "pdfk")["correct"]
    assert not grade("12", "Offset 0x120 and 112 bytes", "pdfk")["correct"]
    assert grade("1.10|1.1", "VSEL 0xb selects 1.10 V", "pdfk")["correct"]


def test_missing_groups_are_reported():
    g = grade("DATA_SIZE ; 3:2 ; halfword|half-word", "DATA_SIZE, bits 3:2, byte or word", "pdfk")
    assert g == {"correct": False, "missing": ["halfword|half-word"], "cited": False}


def test_not_found_requires_a_refusal_without_an_invented_value():
    assert grade("NOT_FOUND", "RCC_CR is not in the available docpack; it is an STM32 register.", "pdfk")["correct"]
    assert grade("NOT_FOUND", "I couldn't find ETH_MACCR anywhere in rp2040.md.", "baseline")["correct"]
    assert not grade("NOT_FOUND", "The reset value is 0x00000083.", "pdfk")["correct"]


def test_citation_rules_per_mode():
    assert grade("26", "bit 26 [rp2040 §2.19.6.1 p.248]", "pdfk")["cited"]
    assert not grade("26", "bit 26 (section 2.19.6.1)", "pdfk")["cited"]
    assert grade("26", "bit 26, see page 248", "baseline")["cited"]
