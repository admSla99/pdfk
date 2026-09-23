#!/usr/bin/env python3
"""End-to-end eval: run each question through `claude -p` and grade the answer automatically.

Modes
  pdfk      the project has .pdfk/ docpacks and the pdfk plugin is loaded (--plugin-dir)
  baseline  the project has one docling markdown file and a CLAUDE.md telling the agent to grep it
            (build it with eval/make_baseline.py); no plugin

    python eval/run_eval.py run --mode pdfk     --project ../poc-full          --out eval/results/rp2040
    python eval/run_eval.py run --mode baseline --project ../poc-full-baseline --out eval/results/rp2040
    python eval/run_eval.py report --out eval/results/rp2040

Grading (questions/*.tsv, column `must`): groups separated by ` ; ` must all appear in the answer;
alternatives inside a group are separated by `|`. Hex values match with any zero padding, bit ranges
"4:0" also match "[4:0]", "4-0", "4..0", "4 to 0". `NOT_FOUND` expects the answer to say the documentation
does not contain it. Citation: pdfk answers must carry [doc … p.N]; baseline answers any page reference.
Token and cost figures come from the `claude -p --output-format json` result. By default each run excludes
user-level settings, plugins and MCP servers (`--setting-sources project,local --strict-mcp-config`), so the
numbers describe a clean team install rather than the evaluator's machine."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # plugin repo
NOT_FOUND_RE = re.compile(
    r"not (?:found|present|contain|in the|documented|available|exist|cover|include|defined|part of|mention)"
    r"|no (?:such|mention|entry|register|hits?|information|match|results?|documentation)"
    r"|(?:does|do|is|are)n[’']?t (?:exist|appear|contain|cover|include|have|mention|document|in|part)"
    r"|(?:does|do|is|are) not (?:exist|appear|contain|cover|include|have|mention|document|in|part)"
    r"|(?:can(?:no|')t|could(?:n'| no)t|unable to) (?:find|locate|answer)|nothing (?:about|on|for)"
    r"|isn[’']?t (?:in|part|present|documented)|outside the scope",
    re.I,
)
CITE_RE = {"pdfk": re.compile(r"\[[^\]]*\bp\.\s?\d+", re.I), "baseline": re.compile(r"\b(?:p\.|pages?)\s?\d+", re.I)}


# --------------------------------------------------------------------------- grading


def _norm_answer(a: str) -> str:
    a = a.replace("`", "").replace("*", "").replace(" ", " ")
    a = re.sub(r"(?<=[0-9a-fA-F])[_ ](?=[0-9a-fA-F]{4}\b)", "", a)  # 0x4003_8024 / 0x4003 8024
    return a.lower()


def _alt_regex(alt: str) -> re.Pattern:
    alt = alt.strip()
    if re.fullmatch(r"0x[0-9a-fA-F]+", alt):
        digits = alt[2:].lower().lstrip("0") or "0"
        return re.compile(r"0x0*" + re.escape(digits) + r"(?![0-9a-f])")
    m = re.fullmatch(r"(\d+):(\d+)", alt)
    if m:
        hi, lo = m.groups()
        return re.compile(rf"(?<!\d){hi}\s*(?::|-|–|\.\.|to)\s*{lo}(?!\d)")
    if re.fullmatch(r"[\d.]+", alt):
        return re.compile(rf"(?<![\d.]){re.escape(alt)}(?![\d])")
    return re.compile(re.escape(alt.lower()))


def grade(must: str, answer: str, mode: str) -> dict:
    a = _norm_answer(answer)
    cited = bool(CITE_RE[mode].search(answer))
    if must.strip() == "NOT_FOUND":
        # a refusal must not invent a value either
        ok = bool(NOT_FOUND_RE.search(answer)) and not re.search(r"reset value (?:is|=|:)\s*0x", a)
        return {"correct": ok, "missing": [] if ok else ["NOT_FOUND"], "cited": cited}
    missing = []
    for group in [g for g in must.split(";") if g.strip()]:
        alts = [x for x in group.split("|") if x.strip()]
        if not any(_alt_regex(x).search(a) for x in alts):
            missing.append(group.strip())
    return {"correct": not missing, "missing": missing, "cited": cited}


# --------------------------------------------------------------------------- running


def load_questions(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _usage(d: dict) -> dict:
    """Token totals over all models used in the session (main agent, sub-agents, helper models)."""
    tot = {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0}
    mu = d.get("modelUsage") or {}
    if mu:
        for m in mu.values():
            tot["input"] += m.get("inputTokens", 0)
            tot["cache_create"] += m.get("cacheCreationInputTokens", 0)
            tot["cache_read"] += m.get("cacheReadInputTokens", 0)
            tot["output"] += m.get("outputTokens", 0)
    else:
        u = d.get("usage") or {}
        tot = {"input": u.get("input_tokens", 0), "cache_create": u.get("cache_creation_input_tokens", 0),
               "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}
    tot["total"] = sum(tot.values())
    return tot


def run_one(q: dict, a: argparse.Namespace, out_dir: Path) -> dict:
    raw = out_dir / f"{q['id']}.json"
    if raw.is_file() and not a.force:
        d = json.loads(raw.read_text(encoding="utf-8"))
    else:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        cmd = [a.claude, "-p", q["question"], "--model", a.model, "--max-turns", str(a.max_turns),
               "--output-format", "json"]
        if a.isolate:
            # measure pdfk, not the evaluator's personal setup: no user-level plugins, hooks or MCP servers
            cmd += ["--setting-sources", "project,local", "--strict-mcp-config"]
        if a.mode == "pdfk":
            plugin = Path(a.plugin_dir).resolve()
            cmd += ["--plugin-dir", str(plugin), "--allowedTools",
                    "Bash(pdfk:*)", "Bash(pdfk *)", f"Bash({(plugin / 'bin' / 'pdfk').as_posix()}:*)", "Read", "Grep", "Glob", "Task", "Skill"]
            env["PATH"] = str(plugin / "bin") + os.pathsep + env.get("PATH", "")
            if a.pdfk_python:
                env["PDFK_PYTHON"] = a.pdfk_python
        else:
            cmd += ["--allowedTools", "Read", "Grep", "Glob"]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=a.project, env=env, capture_output=True, text=True, encoding="utf-8",
                           stdin=subprocess.DEVNULL, timeout=a.timeout)
        try:
            d = json.loads(p.stdout)
        except ValueError:
            d = {"result": "", "is_error": True, "error": (p.stderr or p.stdout)[-2000:]}
        d["_wall_s"] = round(time.time() - t0, 1)
        raw.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    answer = d.get("result") or ""
    g = grade(q["must"], answer, a.mode)
    u = _usage(d)
    return {"id": q["id"], "category": q["category"], "mode": a.mode, "correct": int(g["correct"]),
            "cited": int(g["cited"]), "missing": " ; ".join(g["missing"]), "turns": d.get("num_turns", 0),
            "cost_usd": round(d.get("total_cost_usd", 0.0), 4), "tokens_total": u["total"],
            "tokens_input_uncached": u["input"] + u["cache_create"], "tokens_cache_read": u["cache_read"],
            "tokens_output": u["output"], "wall_s": d.get("_wall_s", 0), "error": int(bool(d.get("is_error"))),
            "denied": ",".join(sorted({x.get("tool_name", "?") for x in d.get("permission_denials") or []})),
            "answer": " ".join(answer.split())[:1500]}


def cmd_run(a: argparse.Namespace) -> int:
    qs = load_questions(Path(a.questions))
    if a.ids:
        want = set(a.ids.split(","))
        qs = [q for q in qs if q["id"] in want]
    out_dir = Path(a.out) / a.mode
    out_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which(a.claude):
        raise SystemExit(f"`{a.claude}` not found on PATH")
    rows: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_one, q, a, out_dir): q for q in qs}
        for fut in cf.as_completed(futs):
            r = fut.result()
            rows.append(r)
            mark = "ok " if r["correct"] else "BAD"
            denied = f" DENIED:{r['denied']}" if r["denied"] else ""
            print(f"{mark} {r['id']:<4} {a.mode:<8} turns={r['turns']:<3} ${r['cost_usd']:<6} "
                  f"tokens={r['tokens_total']:>8,} {r['missing']}{denied}", flush=True)
    order = {q["id"]: i for i, q in enumerate(load_questions(Path(a.questions)))}
    merged = {r["id"]: r for r in _read_rows(out_dir.parent / f"results-{a.mode}.csv")}
    merged.update({r["id"]: r for r in rows})
    all_rows = sorted(merged.values(), key=lambda r: order.get(r["id"], 999))
    _write_rows(out_dir.parent / f"results-{a.mode}.csv", all_rows)
    print(_summary_line(a.mode, all_rows))
    return 0


def cmd_regrade(a: argparse.Namespace) -> int:
    """Re-grade stored raw results (after changing questions.tsv) without calling claude again."""
    qs = {q["id"]: q for q in load_questions(Path(a.questions))}
    for mode in ("pdfk", "baseline"):
        path = Path(a.out) / f"results-{mode}.csv"
        rows = _read_rows(path)
        if not rows:
            continue
        for r in rows:
            d = json.loads((Path(a.out) / mode / f"{r['id']}.json").read_text(encoding="utf-8"))
            g = grade(qs[r["id"]]["must"], d.get("result") or "", mode)
            r.update(correct=int(g["correct"]), cited=int(g["cited"]), missing=" ; ".join(g["missing"]))
        _write_rows(path, rows)
        print(_summary_line(mode, rows))
    return 0


# --------------------------------------------------------------------------- reporting

FIELDS = ["id", "category", "mode", "correct", "cited", "missing", "turns", "cost_usd", "tokens_total",
          "tokens_input_uncached", "tokens_cache_read", "tokens_output", "wall_s", "error", "denied", "answer"]


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _num(r: dict, k: str) -> float:
    try:
        return float(r[k])
    except (KeyError, ValueError):
        return 0.0


def _stats(rows: list[dict]) -> dict:
    n = len(rows) or 1
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0  # noqa: E731
    return {"n": len(rows), "correct": sum(int(_num(r, "correct")) for r in rows),
            "cited": sum(int(_num(r, "cited")) for r in rows),
            "cost": sum(_num(r, "cost_usd") for r in rows), "cost_avg": sum(_num(r, "cost_usd") for r in rows) / n,
            "tok_avg": sum(_num(r, "tokens_total") for r in rows) / n,
            "tok_med": med([_num(r, "tokens_total") for r in rows]),
            "uncached_avg": sum(_num(r, "tokens_input_uncached") for r in rows) / n,
            "out_avg": sum(_num(r, "tokens_output") for r in rows) / n,
            "turns_avg": sum(_num(r, "turns") for r in rows) / n, "wall_avg": sum(_num(r, "wall_s") for r in rows) / n}


def _summary_line(mode: str, rows: list[dict]) -> str:
    s = _stats(rows)
    return (f"{mode}: {s['correct']}/{s['n']} correct, {s['cited']}/{s['n']} with page citation, "
            f"avg {s['tok_avg']:,.0f} tokens, ${s['cost_avg']:.3f}/question, {s['turns_avg']:.1f} turns")


def cmd_report(a: argparse.Namespace) -> int:
    out = Path(a.out)
    # every stored run: results-pdfk.csv, results-baseline.csv and archived ones such as results-pdfk-v1.csv
    names = sorted((f.stem[len("results-"):] for f in out.glob("results-*.csv")),
                   key=lambda m: (m != "pdfk", m == "baseline", m))
    modes = {m: _read_rows(out / f"results-{m}.csv") for m in names}
    modes = {m: r for m, r in modes.items() if r}
    if not modes:
        raise SystemExit(f"no results in {out}")
    lines = ["# Eval report", "",
             f"Questions: `{Path(a.questions).name}` · model: `{a.model}` · "
             f"{'isolated (no user plugins, hooks or MCP servers)' if a.isolate else 'user settings included'}", "",
             "| Metric | " + " | ".join(modes) + " |", "|---|" + "---:|" * len(modes)]
    st = {m: _stats(r) for m, r in modes.items()}
    rows = [("Correct", lambda s: f"{s['correct']}/{s['n']}"),
            ("With page citation", lambda s: f"{s['cited']}/{s['n']}"),
            ("Tokens per question, mean", lambda s: f"{s['tok_avg']:,.0f}"),
            ("Tokens per question, median", lambda s: f"{s['tok_med']:,.0f}"),
            ("Uncached input tokens, mean", lambda s: f"{s['uncached_avg']:,.0f}"),
            ("Output tokens, mean", lambda s: f"{s['out_avg']:,.0f}"),
            ("Cost per question (USD)", lambda s: f"{s['cost_avg']:.3f}"),
            ("Cost total (USD)", lambda s: f"{s['cost']:.2f}"),
            ("Turns, mean", lambda s: f"{s['turns_avg']:.1f}"),
            ("Wall time per question (s)", lambda s: f"{s['wall_avg']:.0f}")]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(st[m]) for m in modes) + " |")
    denied = {m: sum(1 for r in rs if r.get("denied")) for m, rs in modes.items()}
    lines.append("| Runs with a denied tool call | " + " | ".join(str(denied[m]) for m in modes) + " |")
    lines += ["", "## By category", "", "| Category | " + " | ".join(f"{m} correct" for m in modes) + " |",
              "|---|" + "---:|" * len(modes)]
    cats = sorted({r["category"] for rs in modes.values() for r in rs})
    for c in cats:
        cells = []
        for m, rs in modes.items():
            sub = [r for r in rs if r["category"] == c]
            cells.append(f"{sum(int(_num(r, 'correct')) for r in sub)}/{len(sub)}")
        lines.append(f"| {c} | " + " | ".join(cells) + " |")
    lines += ["", "## Wrong or incomplete answers", ""]
    for m, rs in modes.items():
        bad = [r for r in rs if not int(_num(r, "correct"))]
        lines.append(f"**{m}**: " + (", ".join(f"{r['id']} (missing {r['missing']})" for r in bad) or "none"))
        lines.append("")
    text = "\n".join(lines)
    (out / "summary.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("cmd", choices=["run", "regrade", "report"])
    p.add_argument("--questions", default=str(ROOT / "eval" / "questions" / "rp2040.tsv"))
    p.add_argument("--mode", choices=["pdfk", "baseline"], default="pdfk")
    p.add_argument("--project", help="working directory for claude (docpack project or baseline dir)")
    p.add_argument("--plugin-dir", default=str(ROOT))
    p.add_argument("--pdfk-python", default=os.environ.get("PDFK_PYTHON"), help="interpreter for the bin/pdfk launcher")
    p.add_argument("--claude", default="claude")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--max-turns", type=int, default=15)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--no-isolate", dest="isolate", action="store_false",
                   help="keep user-level settings, plugins and MCP servers (default: excluded)")
    p.add_argument("--ids", help="comma-separated question ids")
    p.add_argument("--force", action="store_true", help="re-run questions that already have a stored result")
    p.add_argument("--out", default=str(ROOT / "eval" / "results" / "rp2040"))
    a = p.parse_args(argv)
    if a.cmd == "run":
        if not a.project:
            p.error("run needs --project")
        return cmd_run(a)
    if a.cmd == "regrade":
        return cmd_regrade(a)
    return cmd_report(a)


if __name__ == "__main__":
    raise SystemExit(main())
