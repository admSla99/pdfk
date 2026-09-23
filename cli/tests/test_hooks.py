"""Plugin hooks run as separate processes with JSON on stdin, exactly as Claude Code invokes them."""

import json
import os
import subprocess
import sys

import pytest


def run_hook(plugin_dir, name, payload, project):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    env.pop("CLAUDE_ENV_FILE", None)
    p = subprocess.run([sys.executable, str(plugin_dir / "hooks" / name)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


@pytest.fixture
def project(tmp_path):
    pack = tmp_path / ".pdfk" / "rp2040"
    pack.mkdir(parents=True)
    (tmp_path / ".pdfk" / "manifest.json").write_text(json.dumps({"version": 1, "docs": {"rp2040": {
        "id": "rp2040", "kind": "manual", "title": "RP2040 Datasheet", "pages": 642,
        "counts": {"registers": 1, "tables": 3, "figures": 2}}}}), encoding="utf-8")
    (pack / "registers.jsonl").write_text(json.dumps({"name": "GPIO0_CTRL", "peripheral": "IO_BANK0", "offset": "0x004",
                                                      "section": "2.19.6.1", "page": 248}) + "\n", encoding="utf-8")
    return tmp_path


def test_guard_blocks_pdf_and_internal_files_allows_code(plugin_dir, project):
    out = run_hook(plugin_dir, "guard_read.py", {"tool_name": "Read", "tool_input": {"file_path": "C:/x/rm0440.PDF"}}, project)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    out = run_hook(plugin_dir, "guard_read.py",
                   {"tool_name": "Read", "tool_input": {"file_path": str(project / ".pdfk/rp2040/docling.json.gz")}}, project)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert run_hook(plugin_dir, "guard_read.py", {"tool_name": "Read", "tool_input": {"file_path": "src/main.c"}}, project) is None


def test_session_start_announces_docpacks(plugin_dir, project):
    out = run_hook(plugin_dir, "session_start.py", {}, project)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert 'rp2040 [manual]: "RP2040 Datasheet" 642p' in ctx and "2 figures" in ctx


def test_prompt_hint_only_for_known_registers(plugin_dir, project):
    out = run_hook(plugin_dir, "prompt_hint.py", {"prompt": "how do I set FUNCSEL in GPIO0_CTRL?"}, project)
    assert "GPIO0_CTRL (IO_BANK0 offset 0x004) is documented in [rp2040 §2.19.6.1 p.248]" in \
        out["hookSpecificOutput"]["additionalContext"]
    assert run_hook(plugin_dir, "prompt_hint.py", {"prompt": "refactor the build script"}, project) is None
    assert run_hook(plugin_dir, "prompt_hint.py", {"prompt": "what about RCC_CR?"}, project) is None
