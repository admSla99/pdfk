"""Memory map: peripheral base addresses, and linking registers to absolute addresses.

Sources recognised (stdlib only, works on the block stream of docpack.extract_blocks):
  A) sentence:  "The DMA registers start at a base address of 0x50000000 (defined as DMA_BASE in SDK)"
                "The PLL_SYS and PLL_USB registers start at base addresses of 0x40028000 and 0x4002c000
                 respectively (defined as PLL_SYS_BASE and PLL_USB_BASE in SDK)"
  B) two-column tables of SDK defines:  | IO_BANK0_BASE | 0x40014000 |
  C) memory-map tables with a header naming an address column and a peripheral column
     (ST: "Boundary address | Peripheral | Bus | Register map", rows "0x4002 1000 - 0x4002 13FF | RCC | AHB1 | …")
"""

from __future__ import annotations

import re

HEX = r"0x[0-9A-Fa-f]{1,8}(?:[ _][0-9A-Fa-f]{4})?"
HEX_RE = re.compile(HEX)
ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

SENTENCE_RE = re.compile(
    r"(?:The\s+)?(?P<desc>[\w\-+/ ]{1,80}?)\s+registers?\s+(?:start|starts|are located|is located|begin)\s+at\s+"
    r"(?:a\s+)?base\s+address(?:es)?\s+of\s+"
    r"(?P<addrs>" + HEX + r"(?:\s*(?:,|and)\s*" + HEX + r")*)"
    r"(?:\s+respectively)?"
    r"(?:\s*\(defined\s+as\s+(?P<defs>[A-Z][A-Z0-9_]*(?:\s*(?:,|and)\s*[A-Z][A-Z0-9_]*)*)\s+in\s+SDK\))?",
    re.S,
)
ADDR_HEADERS = ("boundary address", "base address", "start address", "address range", "address", "base")
NAME_HEADERS = ("peripheral", "peripherals", "name", "module", "block", "instance")
RESERVED = {"reserved", "-", ""}


def norm_hex(s: str) -> str:
    return "0x" + s.strip().lower().replace(" ", "").replace("_", "")[2:].zfill(8)


def _split_ids(s: str) -> list[str]:
    return [p for p in re.split(r"\s*(?:,|\band\b)\s*", s.strip()) if p]


def _entry(name, base, b, sec, source, end=None, bus=None, desc=None):
    e = {"name": name, "base": norm_hex(base), "page": b.page, "section": sec, "source": source}
    if end:
        e["end"] = norm_hex(end)
    if bus:
        e["bus"] = bus
    if desc:
        e["desc"] = desc
    return e


def _from_sentence(text: str, b, sec: str) -> list[dict]:
    out = []
    for m in SENTENCE_RE.finditer(text):
        addrs = HEX_RE.findall(m.group("addrs"))
        defs = _split_ids(m.group("defs") or "")
        desc = " ".join(m.group("desc").split())
        if defs and len(defs) == len(addrs):
            names = [d[:-5] if d.endswith("_BASE") else d for d in defs]
        else:
            cand = [c for c in _split_ids(desc.replace(" and ", ",")) if ID_RE.match(c)]
            if len(cand) == len(addrs):
                names = cand
            elif len(addrs) == 1 and ID_RE.match(desc.split()[-1]):
                names = [desc.split()[-1]]
            else:
                continue
        for n, a in zip(names, addrs):
            out.append(_entry(n, a, b, sec, "text", desc=desc))
    return out


def _from_table(grid: list[list[str]], b, sec: str) -> list[dict]:
    if not grid:
        return []
    out = []
    # B) | NAME_BASE | 0x… |  (no header row)
    if all(len(r) >= 2 for r in grid):
        rows = [(r[0].strip(), r[1].strip()) for r in grid]
        defs = [(n, a) for n, a in rows if n.endswith("_BASE") and ID_RE.match(n) and HEX_RE.fullmatch(a)]
        if defs and len(defs) >= len(rows) // 2:
            return [_entry(n[:-5], a, b, sec, "table") for n, a in defs]
    # C) header row with an address column and a name column
    for hi, row in enumerate(grid[:2]):
        cells = [c.strip().lower() for c in row]
        a_col = next((i for i, c in enumerate(cells) if any(c.startswith(h) for h in ADDR_HEADERS)), None)
        n_col = next((i for i, c in enumerate(cells) if c in NAME_HEADERS), None)
        if a_col is None or n_col is None or a_col == n_col:
            continue
        bus_col = next((i for i, c in enumerate(cells) if c == "bus"), None)
        for r in grid[hi + 1:]:
            if max(a_col, n_col) >= len(r):
                continue
            addrs = HEX_RE.findall(r[a_col])
            name = r[n_col].strip()
            if not addrs or name.lower() in RESERVED:
                continue
            bus = r[bus_col].strip() if bus_col is not None and bus_col < len(r) else None
            # "GPIOA" or "TIM2 / TIM3": one entry per id in the cell
            for n in [p for p in re.split(r"\s*[/,]\s*|\s+", name) if ID_RE.match(p)] or []:
                out.append(_entry(n, addrs[0], b, sec, "table", end=addrs[1] if len(addrs) > 1 else None, bus=bus))
        if out:
            return out
    return []


def extract_memmap(blocks) -> list[dict]:
    """Return one entry per peripheral/instance name (first occurrence wins, later ones must agree)."""
    found: dict[str, dict] = {}
    sec = ""
    for b in blocks:
        if b.kind == "heading":
            if b.sec:
                sec = b.sec
            continue
        if b.kind in ("text", "list"):
            entries = _from_sentence(b.text, b, sec)
        elif b.kind == "table":
            entries = _from_table(b.grid or [], b, sec)
        else:
            continue
        for e in entries:
            prev = found.get(e["name"])
            if prev is None:
                found[e["name"]] = e
            elif prev["base"] != e["base"]:
                prev.setdefault("conflicts", []).append({"base": e["base"], "page": e["page"]})
            elif e["source"] == "text" and prev["source"] != "text":
                # the "registers start at" sentence names the register-list section; prefer it
                e["also"] = [{"page": prev["page"], "source": prev["source"]}]
                found[e["name"]] = e
    return sorted(found.values(), key=lambda e: e["base"])


# --------------------------------------------------------------------------- linking


def _instance_names(periph: str, names: set[str]) -> list[str]:
    """Memory-map names that are instances of a register peripheral:
    PLL -> PLL_SYS, PLL_USB;  UART -> UART0, UART1;  GPIOx -> GPIOA, GPIOB, …"""
    if periph in names:
        return [periph]
    stem = periph[:-1] if periph.endswith("x") and len(periph) > 2 else periph
    pat = re.compile(r"^" + re.escape(stem.upper()) + r"(?:\d{1,2}|[A-K]|_[A-Z0-9]+)$")
    return sorted(n for n in names if pat.match(n))


def link_registers(regs: list[dict], memmap: list[dict]) -> int:
    """Add `bases` (instance -> base) and, for a single instance, `address` (absolute) to each register.
    A base-address sentence in the register's own section wins over a name match elsewhere in the map.
    Returns the number of linked registers."""
    by_name = {e["name"]: e for e in memmap}
    names = set(by_name)
    sentence_by_sec: dict[str, list[dict]] = {}
    for e in memmap:
        if e["source"] == "text" and e["section"]:
            sentence_by_sec.setdefault(e["section"], []).append(e)
    secs = sorted(sentence_by_sec, key=len, reverse=True)  # longest (most specific) section first
    linked = 0
    for r in regs:
        periph = r.get("peripheral", "")
        sec = r.get("section", "")
        # 1) "The XIP registers start at a base address of 0x14000000 (defined as XIP_CTRL_BASE)" in the
        #    register's own section is authoritative: the bare name "XIP" is also the 0x10000000 memory window
        entries: list[dict] = []
        for s in secs:
            if sec == s or sec.startswith(s + "."):
                es = sentence_by_sec[s]
                own = set(_instance_names(periph, {e["name"] for e in es}))
                entries = [e for e in es if e["name"] in own] or es
                break
        # 2) otherwise the peripheral name (and its numbered / suffixed instances) anywhere in the map
        if not entries:
            entries = [by_name[n] for n in _instance_names(periph, names)]
        if not entries or not r.get("offset"):
            continue
        try:
            off = int(r["offset"], 16)
        except ValueError:
            continue
        r["bases"] = {e["name"]: e["base"] for e in entries}
        if len(entries) == 1:
            # an absolute address printed by the manual itself wins over the computed one
            r.setdefault("address", f"0x{int(entries[0]['base'], 16) + off:08x}")
        linked += 1
    return linked
