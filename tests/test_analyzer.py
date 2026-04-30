from __future__ import annotations

import argparse
import json
from pathlib import Path

from test_gap_radar.analyzer import analyze, parse_lcov, score_file
from test_gap_radar.cli import main


def test_score_prioritizes_low_coverage_critical_untested_file(tmp_path: Path) -> None:
    source = tmp_path / "src" / "billing" / "proration.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export function prorate() { if (true) return 1 }\n", encoding="utf-8")

    risk = score_file(
        root=tmp_path,
        relative_path="src/billing/proration.ts",
        changed_times=9,
        bugfix_commits=3,
        coverage=22,
        owner="@payments",
        test_paths=set(),
        flaky_refs={},
    )

    assert risk.path == "src/billing/proration.ts"
    assert risk.score > 100
    assert "Changed 9 times in the selected window" in risk.reasons
    assert "3 bug-fix commits mention this file" in risk.reasons
    assert "Current coverage: 22%" in risk.reasons
    assert "Critical path keyword detected" in risk.reasons
    assert "No matching test file found" in risk.reasons


def test_parse_lcov_maps_files_to_line_coverage(tmp_path: Path) -> None:
    lcov = tmp_path / "lcov.info"
    lcov.write_text(
        "\n".join(
            [
                "TN:",
                f"SF:{tmp_path}/src/app.ts",
                "LF:10",
                "LH:4",
                "end_of_record",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_lcov(tmp_path, lcov) == {"src/app.ts": 40.0}


def test_analyze_uses_coverage_json_and_test_presence(tmp_path: Path) -> None:
    source = tmp_path / "src" / "auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("def login(user):\n    if user:\n        return True\n", encoding="utf-8")
    tests = tmp_path / "tests" / "test_auth.py"
    tests.parent.mkdir()
    tests.write_text("def test_login():\n    assert True\n", encoding="utf-8")
    (tmp_path / "coverage.json").write_text(
        json.dumps({"files": {"src/auth.py": {"summary": {"percent_covered": 50}}}}),
        encoding="utf-8",
    )

    risks = analyze(argparse.Namespace(root=tmp_path, since="60 days ago", coverage=None, limit=5))

    assert risks[0].path == "src/auth.py"
    assert risks[0].coverage == 50
    assert risks[0].has_tests is True


def test_cli_prints_highest_value_missing_test(tmp_path: Path, capsys) -> None:
    source = tmp_path / "src" / "billing.ts"
    source.parent.mkdir()
    source.write_text("export const bill = () => 1\n", encoding="utf-8")

    exit_code = main(["analyze", "--root", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Highest-value missing test:" in output
    assert "src/billing.ts" in output
