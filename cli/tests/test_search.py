"""SQLite FTS5 index: query escaping and kind-weighted ranking."""

from pdfk.search import build_index, format_hit, fts_query, query


def rec(line, kind, text, section="2.18.4"):
    return {"file": "14-2_18-pll.md", "line": line, "page": 235, "section": section, "kind": kind, "text": text}


def test_fts_query_quotes_terms_and_keeps_prefix():
    assert fts_query('PLL_SYS "lock"') == '"PLL_SYS" "lock"'
    assert fts_query("RCC_AHB2*") == '"RCC_AHB2"*'
    assert fts_query("   ") == ""


def test_code_listings_rank_below_the_definition(tmp_path):
    db = tmp_path / "search.sqlite"
    code = " ".join(["pll->cs & PLL_CS_LOCK_BITS"] * 6)
    build_index(db, "rp2040", [
        rec(144, "code", code),
        rec(162, "heading", "PLL: CS Register"),
        rec(170, "table", "31 | LOCK: PLL is locked | RO | 0x0"),
    ])
    hits = query(db, "LOCK")
    assert hits[0]["kind"] != "code"
    assert hits[-1]["kind"] == "code"


def test_kind_and_section_filters(tmp_path):
    db = tmp_path / "search.sqlite"
    build_index(db, "rp2040", [rec(1, "text", "VCO frequency", "2.18.2"), rec(2, "figure", "VCO, FREF", "2.18.1")])
    assert [h["kind"] for h in query(db, "VCO", kind="figure")] == ["figure"]
    assert [h["section"] for h in query(db, "VCO", section="2.18.2")] == ["2.18.2"]
    assert query(db, "nonexistentterm") == []


def test_snippet_does_not_insert_spaces_after_matches(tmp_path):
    db = tmp_path / "search.sqlite"
    build_index(db, "rp2040", [rec(1, "text", "The PLL_SYS and PLL_USB registers start at base addresses")])
    (hit,) = query(db, "PLL")
    assert "PLL_SYS and PLL_USB" in hit["snippet"]


def test_full_hits_carry_the_block_and_headings_carry_what_follows(tmp_path):
    db = tmp_path / "search.sqlite"
    build_index(db, "rp2040", [
        rec(61, "heading", "2.19.4.1. Bus Keeper Mode", "2.19.4.1"),
        rec(63, "text", "If you set both the GPIO0.PDE and GPIO0.PUE bits simultaneously then you enable bus keeper mode.",
            "2.19.4.1"),
        rec(90, "text", "Unrelated paragraph about keeper plates.", "2.19.5"),
    ])
    hits = query(db, "keeper", full=2)
    head = next(h for h in hits if h["kind"] == "heading")
    assert "PDE and GPIO0.PUE" in head["text"]
    assert "text" not in hits[2]
    capped = query(db, "keeper", full=3, max_chars=40)
    assert all(len(h["text"]) <= 42 for h in capped)


def test_table_hits_carry_the_table_id_so_the_next_call_is_pdfk_table(tmp_path):
    db = tmp_path / "search.sqlite"
    build_index(db, "mspm0g1518", [
        dict(rec(81, "table", "PT PIN | PM PIN | NRST | NRST | RESET"), tid="t0008"),
        rec(20, "text", "The NRST reset pin must be pulled up to VDD."),
    ])
    table_hit = next(h for h in query(db, "NRST") if h["kind"] == "table")
    assert table_hit["tid"] == "t0008"
    assert "[t0008]" in format_hit(table_hit)
    text_hit = next(h for h in query(db, "NRST") if h["kind"] == "text")
    assert text_hit["tid"] == ""
    assert "[" not in format_hit(text_hit).split("  ", 1)[0]
