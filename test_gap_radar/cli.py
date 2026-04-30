from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from test_gap_radar.analyzer import FileRisk, analyze


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test-gap-radar",
        description="Map changed code and production-risk signals to missing tests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="rank files where tests would reduce the most risk")
    analyze_parser.add_argument("--root", default=".", help="repository root to analyze")
    analyze_parser.add_argument("--since", default="60 days ago", help="git history window, passed to git --since")
    analyze_parser.add_argument("--coverage", help="coverage.json, coverage-summary.json, or lcov.info path")
    analyze_parser.add_argument("--limit", type=int, default=5, help="number of risky files to print")
    analyze_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "analyze":
        parser.error(f"unknown command: {args.command}")

    risks = analyze(args)
    if args.json:
        print(json.dumps([asdict(risk) for risk in risks], indent=2))
    else:
        print_text(risks)
    return 0


def print_text(risks: list[FileRisk]) -> None:
    if not risks:
        print("No risky missing-test candidates found.")
        return

    highest = risks[0]
    print("Highest-value missing test:")
    print(highest.path)
    print()
    print("Reasons:")
    for reason in highest.reasons:
        print(f"- {reason}")

    if len(risks) > 1:
        print()
        print("Other high-value candidates:")
        for risk in risks[1:]:
            print(f"- {risk.path} (score {risk.score})")


if __name__ == "__main__":
    raise SystemExit(main())
