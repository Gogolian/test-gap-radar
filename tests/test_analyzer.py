from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_gap_radar.analyzer import analyze, parse_lcov, score_file
from test_gap_radar.cli import main


class AnalyzerTests(unittest.TestCase):
    def test_score_prioritizes_low_coverage_critical_untested_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "billing" / "proration.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export function prorate() { if (true) return 1 }\n", encoding="utf-8")

            risk = score_file(
                root=root,
                relative_path="src/billing/proration.ts",
                changed_times=9,
                bugfix_commits=3,
                coverage=22,
                owner="@payments",
                test_paths=set(),
                flaky_refs={},
            )

            self.assertEqual(risk.path, "src/billing/proration.ts")
            self.assertGreater(risk.score, 100)
            self.assertIn("Changed 9 times in the selected window", risk.reasons)
            self.assertIn("3 bug-fix commits mention this file", risk.reasons)
            self.assertIn("Current coverage: 22%", risk.reasons)
            self.assertIn("Critical path keyword detected", risk.reasons)
            self.assertIn("No matching test file found", risk.reasons)

    def test_parse_lcov_maps_files_to_line_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lcov = root / "lcov.info"
            lcov.write_text(
                "\n".join(
                    [
                        "TN:",
                        f"SF:{root}/src/app.ts",
                        "LF:10",
                        "LH:4",
                        "end_of_record",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(parse_lcov(root, lcov), {"src/app.ts": 40.0})

    def test_analyze_uses_coverage_json_and_test_presence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "auth.py"
            source.parent.mkdir(parents=True)
            source.write_text("def login(user):\n    if user:\n        return True\n", encoding="utf-8")
            tests = root / "tests" / "test_auth.py"
            tests.parent.mkdir()
            tests.write_text("def test_login():\n    assert True\n", encoding="utf-8")
            (root / "coverage.json").write_text(
                json.dumps({"files": {"src/auth.py": {"summary": {"percent_covered": 50}}}}),
                encoding="utf-8",
            )

            risks = analyze(argparse.Namespace(root=root, since="60 days ago", coverage=None, limit=5))

            self.assertEqual(risks[0].path, "src/auth.py")
            self.assertEqual(risks[0].coverage, 50)
            self.assertIs(risks[0].has_tests, True)

    def test_cli_prints_highest_value_missing_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "billing.ts"
            source.parent.mkdir()
            source.write_text("export const bill = () => 1\n", encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["analyze", "--root", str(root)])

            self.assertEqual(exit_code, 0)
            self.assertIn("Highest-value missing test:", output.getvalue())
            self.assertIn("src/billing.ts", output.getvalue())


if __name__ == "__main__":
    unittest.main()
