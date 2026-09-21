"""Register extraction from the block stream.

Generic profile recognises three naming layouts seen in vendor manuals:
  A) ST:      "7.4.1 Clock control register (RCC_CR)"  + text "Bits 31:28 Reserved" / "Bit 27 PLLRDY: ..."
  B) RP2040:  "IO_BANK0: GPIO0_STATUS, GPIO1_STATUS, …, GPIO29_STATUS Registers" + "Offsets: 0x000, 0x008, …"
              + table with header Bits | Description | Type | Reset
  C) plain:   "GPIO0_CTRL Register" / "Table 12. TIMER_CTRL Register"
"""

from __future__ import annotations

import re

ID = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*"
ID_US = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+"  # must contain an underscore
ELLIPSIS = r"(?:…|\.\.\.|�)"

NAME_PAREN_RE = re.compile(r"\((" + ID_US + r")\)\s*$")
NAME_PERIPH_LIST_RE = re.compile(r"^(" + ID + r")\s*:\s*(.+?)(?:\s+[Rr]egisters?)?\s*$", re.S)
NAME_LEAD_RE = re.compile(r"^(?:Table\s+\d+\.\s*)?(" + ID_US + r")(?:\s*[:\-–]\s*|\s+)[Rr]egisters?\b")
NAME_CAPTION_LIST_RE = re.compile(r"^Table\s+\d+\.\s*(.+?)\s+[Rr]egisters?\s*$", re.S)
LIST_ITEM_RE = re.compile(r"^(?:" + ID + r"|" + ELLIPSIS + r")$")
OFFSET_RE = re.compile(r"(?:Address\s+)?[Oo]ffsets?\s*:?\s*((?:0x[0-9A-Fa-f_]+(?:\s*,\s*(?:0x[0-9A-Fa-f_]+|" + ELLIPSIS + r"))*))")
RESET_RE = re.compile(r"Reset(?:\s+value)?\s*:?\s*(0x[0-9A-Fa-fXx]{1,8}(?:\s?[0-9A-Fa-fXx]{4})?|[01xX]{4,}(?:\s[01xX]{4})*)")
ADDRESS_RE = re.compile(r"(?:Base\s+)?[Aa]ddress\s*:?\s*(0x[0-9A-Fa-f_]+)")
BIT_TEXT_RE = re.compile(r"^\s*Bits?\s+(\d+(?:\s*[:\-–]\s*\d+)?)\s+(.*)$", re.S)
BITS_CELL_RE = re.compile(r"^\[?\s*(\d+)(?:\s*[:\-–]\s*(\d+))?\s*\]?$")
FIELD_LEAD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:\[\d+(?::\d+)?\])?)\s*[:\-–]\s*(.*)$", re.S)
NUMBERED_RE = re.compile(r"^(.*?)(\d+)([A-Z0-9_]*)$")
BITS_HEADERS = ("bits", "bit", "bit no.", "bit number", "bit(s)", "bit field")


def _norm_hex(s: str) -> str:
    return s.strip().replace(" ", "").replace("_", "").lower()


def _norm_bits(s: str) -> str:
    s = s.strip().strip("[]")
    s = re.sub(r"\s*[\-–]\s*", ":", s)
    return re.sub(r"\s*:\s*", ":", s)


def _split_list(s: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s*,\s*|\s+and\s+", s)]
    return [p for p in parts if p]


def _expand_names(items: list[str]) -> tuple[list[str], list[bool]]:
    """Expand 'GPIO0_STATUS, GPIO1_STATUS, …, GPIO29_STATUS' into all members. Returns (names, derived flags)."""
    has_ellipsis = any(re.fullmatch(ELLIPSIS, p) for p in items)
    names = [p for p in items if re.fullmatch(ID, p)]
    if not has_ellipsis or len(names) < 2:
        return names, [False] * len(names)
    m0, m1 = NUMBERED_RE.match(names[0]), NUMBERED_RE.match(names[-1])
    if not (m0 and m1 and m0.group(1) == m1.group(1) and m0.group(3) == m1.group(3)):
        return names, [False] * len(names)
    lo, hi = int(m0.group(2)), int(m1.group(2))
    if hi <= lo or hi - lo > 512:
        return names, [False] * len(names)
    listed = set(names)
    out, derived = [], []
    for i in range(lo, hi + 1):
        n = f"{m0.group(1)}{i}{m0.group(3)}"
        out.append(n)
        derived.append(n not in listed)
    return out, derived


def _expand_offsets(items: list[str], count: int) -> list[str]:
    hexes = [_norm_hex(p) for p in items if p.lower().startswith("0x")]
    has_ellipsis = any(re.fullmatch(ELLIPSIS, p) for p in items)
    if not has_ellipsis or len(hexes) < 2 or count <= len(hexes):
        return hexes
    try:
        a, b = int(hexes[0], 16), int(hexes[1], 16)
    except ValueError:
        return hexes
    stride = b - a
    if stride <= 0:
        return hexes
    width = max(len(hexes[0]) - 2, 3)
    return [f"0x{a + i * stride:0{width}x}" for i in range(count)]


def _header_map(grid: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    for ri, row in enumerate(grid[:3]):
        cells = [c.strip().lower() for c in row]
        if not any(c in BITS_HEADERS for c in cells):
            continue
        roles: dict[str, int] = {}
        for ci, c in enumerate(cells):
            if c in BITS_HEADERS and "bits" not in roles:
                roles["bits"] = ci
            elif c in ("name", "field", "field name", "symbol", "bit name") and "name" not in roles:
                roles["name"] = ci
            elif c.startswith("desc") and "desc" not in roles:
                roles["desc"] = ci
            elif c in ("type", "access", "r/w", "attr", "attributes", "rw") and "access" not in roles:
                roles["access"] = ci
            elif c.startswith("reset") and "reset" not in roles:
                roles["reset"] = ci
        return ri, roles
    return None


def _fields_from_table(grid: list[list[str]]) -> list[dict]:
    hm = _header_map(grid)
    if not hm:
        return []
    hri, roles = hm
    out = []
    for row in grid[hri + 1 :]:
        if roles["bits"] >= len(row):
            continue
        m = BITS_CELL_RE.match(row[roles["bits"]].strip())
        if not m:
            continue
        bits = m.group(1) + (":" + m.group(2) if m.group(2) else "")
        name = row[roles["name"]].strip() if "name" in roles and roles["name"] < len(row) else ""
        desc = row[roles["desc"]].strip() if "desc" in roles and roles["desc"] < len(row) else ""
        if not name and desc:
            fm = FIELD_LEAD_RE.match(desc)
            if fm and fm.group(1).lower() != "reserved":
                name, desc = fm.group(1), fm.group(2).strip()
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", desc.strip().rstrip(".")):
                name, desc = desc.strip().rstrip("."), ""
        if not name and desc.lower().startswith("reserved"):
            name = "Reserved"
        f = {"bits": bits, "name": name, "desc": re.sub(r"\s+", " ", desc)[:400]}
        if "access" in roles and roles["access"] < len(row):
            f["access"] = row[roles["access"]].strip()
        if "reset" in roles and roles["reset"] < len(row):
            f["reset"] = row[roles["reset"]].strip()
        out.append(f)
    return out


def _fields_from_text(text: str) -> list[dict]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        m = BIT_TEXT_RE.match(para.strip())
        if not m:
            continue
        bits = _norm_bits(m.group(1))
        rest = m.group(2).strip()
        name, desc = "", rest
        fm = FIELD_LEAD_RE.match(rest)
        if fm and fm.group(1).lower() != "reserved":
            name, desc = fm.group(1), fm.group(2).strip()
        elif rest.lower().startswith("reserved"):
            name = "Reserved"
        out.append({"bits": bits, "name": name, "desc": re.sub(r"\s+", " ", desc)[:400]})
    return out


def _match_name(text: str) -> tuple[str, list[str], list[bool]] | None:
    """Return (peripheral, names, derived) if the text names a register."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    joined = " ".join(l.strip() for l in text.strip().splitlines()[:4])
    m = NAME_PERIPH_LIST_RE.match(joined)
    if m:
        items = _split_list(m.group(2))
        if items and all(LIST_ITEM_RE.match(p) for p in items):
            names, derived = _expand_names(items)
            if names:
                return m.group(1), names, derived
    m = NAME_CAPTION_LIST_RE.match(joined)
    if m:
        items = _split_list(m.group(1))
        if items and all(LIST_ITEM_RE.match(p) for p in items):
            names, derived = _expand_names(items)
            if names:
                return "", names, derived
    if re.search(r"register", first, re.I):
        m = NAME_PAREN_RE.search(first)
        if m:
            return m.group(1).split("_")[0], [m.group(1)], [False]
    m = NAME_LEAD_RE.match(first)
    if m:
        return "", [m.group(1)], [False]
    return None


LABEL_WORDS = {"offset", "offsets", "address offset", "reset", "reset value", "address", "base address"}


def _bit_lo(bits: str) -> int:
    try:
        return int(bits.split(":")[-1])
    except ValueError:
        return -1


def _bit_hi(bits: str) -> int:
    try:
        return int(bits.split(":")[0])
    except ValueError:
        return -1


def _continues(prev: list[dict], new: list[dict]) -> bool:
    """True if `new` rows continue `prev` downwards (prev ends at bit N>0, new starts below N)."""
    if not prev or not new:
        return False
    lo_prev = _bit_lo(prev[-1]["bits"])
    hi_new = _bit_hi(new[0]["bits"])
    return lo_prev > 0 and 0 <= hi_new < lo_prev and hi_new < 31


def extract_registers(blocks, *, doc_id: str, profile: str = "generic", records: list[dict] | None = None) -> list[dict]:
    loc = {i: r for i, r in enumerate(records or [])}
    regs: list[dict] = []
    cur: dict | None = None  # a "family": shared fields, possibly several names
    cur_level = 99
    sec_path = ""
    pending_label = ""  # "Offsets" split from ": 0x000, …" into two text blocks by the layout model
    periph_ctx = ""  # last peripheral seen in "PERIPH: NAME Register" form; reset at numbered headings
    last_family: dict | None = None  # previous register family (its `fields` list is shared by its records)

    def close():
        nonlocal cur, last_family
        if not cur:
            return
        last_family = cur
        if cur.get("offsets") or cur.get("fields"):
            names, derived, offsets = cur.pop("names"), cur.pop("derived"), cur.pop("offsets")
            family = f"{names[0]}…{names[-1]}" if len(names) > 1 else ""
            if not cur["peripheral"]:
                cur["peripheral"] = periph_ctx or names[0].split("_")[0]
            real = [f for f in cur["fields"] if f.get("name") != "Reserved"]
            if len(real) == 1 and not real[0].get("name") and len(names) == 1:
                real[0]["name"] = names[0]  # single-field register: field is named after the register
            for i, n in enumerate(names):
                r = dict(cur)
                r["name"] = n
                if offsets:
                    r["offset"] = offsets[i] if i < len(offsets) else offsets[0]
                if family:
                    r["family"] = family
                if derived[i]:
                    r["derived"] = True
                r["confidence"] = "good" if r.get("offset") and r.get("fields") else "partial"
                regs.append(r)
        cur = None

    def start(pe: str, names: list[str], derived: list[bool], b, rec: dict, title: str, level: int):
        nonlocal cur, cur_level, periph_ctx
        if pe:
            periph_ctx = pe
        if cur and cur["names"] == names:
            # same register named again (table caption, or a repeated caption when the bit table
            # continues on the next page): keep the current record, refine peripheral
            if pe and pe != cur["peripheral"]:
                cur["peripheral"] = pe
            return
        close()
        cur = {"doc": doc_id, "peripheral": pe, "names": names, "derived": derived, "offsets": [],
               "section": sec_path, "page": b.page,
               "file": rec.get("file", ""), "line": rec.get("line", 0), "title": title[:120], "fields": []}
        cur_level = level

    for i, b in enumerate(blocks):
        rec = loc.get(i, {})
        if b.kind == "heading":
            if b.sec:
                sec_path = b.sec
                periph_ctx = ""
                pm = re.search(r"\(([A-Z][A-Z0-9_]{1,15})\)\s*$", b.text)  # ST: "7 Reset and clock control (RCC)"
                if pm:
                    periph_ctx = pm.group(1)
            elif not sec_path:
                sec_path = b.title[:40]
            nm = _match_name(b.text)
            if nm:
                start(*nm, b, rec, b.title, b.level)
                continue
            # only numbered headings end a register; labels like "Description" / "WARNING" do not
            if cur and b.sec and b.level <= cur_level:
                close()
            continue

        if b.kind in ("text", "list", "code"):
            txt = b.text
            stripped = txt.strip().rstrip(":").strip().lower()
            if stripped in LABEL_WORDS:
                pending_label = txt.strip().rstrip(":")
                continue
            if pending_label:
                txt = pending_label + " " + txt.strip().lstrip(":").strip()
                pending_label = ""
            if len(txt) < 400:
                nm = _match_name(txt)
                if nm:
                    is_caption = txt.lstrip().lower().startswith("table")
                    if is_caption and cur is not None and not cur["fields"]:
                        # margin captions are read before the previous register's bit table;
                        # the heading that follows the table names this register anyway
                        continue
                    start(*nm, b, rec, txt.strip().splitlines()[0], 99)
                    continue
            if cur is None:
                continue
            om = OFFSET_RE.search(txt)
            if om and not cur["offsets"]:
                cur["offsets"] = _expand_offsets(_split_list(om.group(1)), len(cur["names"]))
            rm = RESET_RE.search(txt)
            if rm and not cur.get("reset"):
                v = rm.group(1).strip()
                cur["reset"] = _norm_hex(v) if v.lower().startswith("0x") else re.sub(r"\s+", "", v)
            am = ADDRESS_RE.search(txt)
            if am and not cur.get("address") and "offset" not in txt.lower():
                cur["address"] = _norm_hex(am.group(1))
            fields = _fields_from_text(txt)
            if fields:
                cur["fields"].extend(fields)
            continue

        if b.kind == "table" and cur is not None:
            fields = _fields_from_table(b.grid or [])
            if fields:
                if not cur["fields"] and last_family and _continues(last_family["fields"], fields):
                    # a page break inside the previous register's bit table: the next register's
                    # margin caption was read before the continuation rows
                    last_family["fields"].extend(fields)
                    continue
                seen = {f["bits"] for f in cur["fields"]}
                cur["fields"].extend(f for f in fields if f["bits"] not in seen)  # page-spanning tables
                cur.setdefault("table", b.table_id)
                if not cur["offsets"]:
                    om = OFFSET_RE.search(b.title or "")
                    if om:
                        cur["offsets"] = _expand_offsets(_split_list(om.group(1)), len(cur["names"]))

    close()
    return regs
