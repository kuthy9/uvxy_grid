"""Tests for scripts/audit_resilience.py — read-only resilience audit."""
import json
from pathlib import Path
import sys

# Make scripts/ importable as a package-less module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import audit_resilience as A


def test_check_result_dataclass_fields():
    """A check result carries enough info for both the table and the JSON output."""
    r = A.CheckResult(
        code="X",
        name="dummy",
        status="OK",
        observed="x=1",
        expected="x>=0",
        suggested_action="",
    )
    assert r.code == "X"
    assert r.status == "OK"
    d = r.to_dict()
    assert d["code"] == "X"
    assert d["status"] == "OK"


def test_overall_exit_code_logic():
    results = [
        A.CheckResult("A", "n", "OK", "", "", ""),
        A.CheckResult("B", "n", "OK", "", "", ""),
    ]
    assert A.overall_exit_code(results) == 0

    results.append(A.CheckResult("C", "n", "WARN", "", "", ""))
    assert A.overall_exit_code(results) == 1

    results.append(A.CheckResult("D", "n", "FAIL", "", "", ""))
    assert A.overall_exit_code(results) == 2


def test_render_table_contains_codes_and_statuses(capsys):
    results = [
        A.CheckResult("A", "host", "OK", "ok", "manual", ""),
        A.CheckResult("B", "compose", "FAIL", "missing", "restart:always", "fix it"),
    ]
    A.render_table(results)
    captured = capsys.readouterr().out
    assert "A" in captured and "host" in captured and "OK" in captured
    assert "B" in captured and "compose" in captured and "FAIL" in captured
    assert "fix it" in captured


def test_write_json_creates_file(tmp_path):
    results = [A.CheckResult("A", "n", "OK", "", "", "")]
    out = tmp_path / "audit.json"
    A.write_json(results, out)
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["overall"]["exit_code"] == 0
    assert loaded["checks"][0]["code"] == "A"
