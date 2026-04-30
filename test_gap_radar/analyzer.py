from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
TEST_MARKERS = ("test", "tests", "__tests__", "spec")
BUG_WORDS = ("bug", "fix", "fixed", "hotfix", "incident", "regression", "defect", "patch")
CRITICAL_WORDS = (
    "auth",
    "billing",
    "checkout",
    "invoice",
    "login",
    "order",
    "payment",
    "payout",
    "proration",
    "revenue",
    "security",
    "subscription",
)
FLAKY_WORDS = ("flaky", "flake", "quarantine", "skip", "xfail", "todo")
COMPLEXITY_PATTERN = re.compile(r"\b(if|for|while|case|catch|except|elif|switch|when)\b|&&|\|\||\?")


@dataclass(slots=True)
class FileRisk:
    path: str
    score: float
    reasons: list[str] = field(default_factory=list)
    change_count: int = 0
    bugfix_commits: int = 0
    coverage: float | None = None
    critical: bool = False
    owner: str | None = None
    complexity: int = 0
    has_tests: bool = False
    flaky_test_refs: int = 0


def analyze(args: argparse.Namespace) -> list[FileRisk]:
    root = Path(args.root).resolve()
    changed = changed_files(root, args.since)
    candidates = sorted(changed) if changed else sorted(source_files(root))
    bugfix = bugfix_counts(root, args.since)
    coverage = coverage_by_file(root, args.coverage)
    owners = owners_by_file(root, candidates)
    tests = set(test_files(root))
    flaky_refs = flaky_test_refs(root)

    risks = [
        score_file(
            root=root,
            relative_path=path,
            change_count=changed.get(path, 0),
            bugfix_commits=bugfix.get(path, 0),
            coverage=coverage.get(path),
            owner=owners.get(path),
            test_paths=tests,
            flaky_refs=flaky_refs,
        )
        for path in candidates
    ]
    risks = [risk for risk in risks if risk.score > 0]
    return sorted(risks, key=lambda risk: (-risk.score, risk.path))[: args.limit]


def score_file(
    root: Path,
    relative_path: str,
    change_count: int,
    bugfix_commits: int,
    coverage: float | None,
    owner: str | None,
    test_paths: set[str],
    flaky_refs: Counter[str],
) -> FileRisk:
    path = root / relative_path
    complexity = complexity_score(path)
    has_tests = has_matching_test(relative_path, test_paths)
    critical = is_critical_path(relative_path)
    flaky_count = flaky_refs.get(Path(relative_path).stem, 0)

    score = 0.0
    reasons: list[str] = []

    if change_count:
        score += min(change_count, 12) * 4
        reasons.append(f"Changed {change_count} time{'s' if change_count != 1 else ''} in the selected window")
    if bugfix_commits:
        score += min(bugfix_commits, 6) * 10
        reasons.append(f"{bugfix_commits} bug-fix commit{'s' if bugfix_commits != 1 else ''} mention this file")
    if coverage is not None:
        coverage_gap = max(0.0, 100.0 - coverage)
        score += coverage_gap / 4
        reasons.append(f"Current coverage: {coverage:.0f}%")
    else:
        score += 18
        reasons.append("No coverage data found")
    if critical:
        score += 20
        reasons.append("Critical path keyword detected")
    if owner:
        score += 4
        reasons.append(f"Owned by {owner}")
    if complexity:
        score += min(complexity, 30) * 0.8
        reasons.append(f"Complexity score: {complexity}")
    if not has_tests:
        score += 25
        reasons.append("No matching test file found")
    if flaky_count:
        score += min(flaky_count, 5) * 3
        reasons.append(f"{flaky_count} flaky/disabled test reference{'s' if flaky_count != 1 else ''}")

    return FileRisk(
        path=relative_path,
        score=round(score, 1),
        reasons=reasons,
        change_count=change_count,
        bugfix_commits=bugfix_commits,
        coverage=coverage,
        critical=critical,
        owner=owner,
        complexity=complexity,
        has_tests=has_tests,
        flaky_test_refs=flaky_count,
    )


def changed_files(root: Path, since: str) -> Counter[str]:
    output = git(root, "log", f"--since={since}", "--name-only", "--pretty=format:")
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        file_path = line.strip()
        if is_source_path(file_path) and not is_test_path(file_path):
            counts[normalize(file_path)] += 1
    return counts


def bugfix_counts(root: Path, since: str) -> Counter[str]:
    output = git(root, "log", f"--since={since}", "--pretty=format:%H%x00%s")
    commit_messages = [line.split("\0", 1) for line in output.splitlines() if "\0" in line]
    counts: Counter[str] = Counter()
    for commit, message in commit_messages:
        if not contains_any(message, BUG_WORDS):
            continue
        files = git(root, "show", "--name-only", "--pretty=format:", "--no-renames", commit)
        for file_path in files.splitlines():
            file_path = file_path.strip()
            if is_source_path(file_path) and not is_test_path(file_path):
                counts[normalize(file_path)] += 1
    return counts


def source_files(root: Path) -> Iterable[str]:
    for path in root.rglob("*"):
        if should_skip(path) or not path.is_file():
            continue
        relative = normalize(path.relative_to(root).as_posix())
        if is_source_path(relative) and not is_test_path(relative):
            yield relative


def test_files(root: Path) -> Iterable[str]:
    for path in root.rglob("*"):
        if should_skip(path) or not path.is_file():
            continue
        relative = normalize(path.relative_to(root).as_posix())
        if is_test_path(relative):
            yield relative


def coverage_by_file(root: Path, explicit_path: str | None) -> dict[str, float]:
    paths = [Path(explicit_path)] if explicit_path else [
        root / "coverage.json",
        root / "coverage" / "coverage-summary.json",
        root / "coverage" / "lcov.info",
        root / "lcov.info",
    ]
    for path in paths:
        path = path if path.is_absolute() else root / path
        if not path.exists():
            continue
        if path.name == "lcov.info":
            return parse_lcov(root, path)
        if path.suffix == ".json":
            return parse_coverage_json(root, path)
    return {}


def parse_coverage_json(root: Path, path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if "files" in data:
        return {
            normalize_file_key(root, file_path): float(metrics.get("summary", {}).get("percent_covered", 0))
            for file_path, metrics in data["files"].items()
        }
    result = {}
    for file_path, metrics in data.items():
        if file_path == "total" or not isinstance(metrics, dict):
            continue
        pct = metrics.get("lines", {}).get("pct")
        if pct is not None:
            result[normalize_file_key(root, file_path)] = float(pct)
    return result


def parse_lcov(root: Path, path: Path) -> dict[str, float]:
    coverage: dict[str, float] = {}
    current_file: str | None = None
    found = hit = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_file = normalize_file_key(root, line[3:])
            found = hit = 0
        elif line.startswith("LF:"):
            found = int(line[3:] or 0)
        elif line.startswith("LH:"):
            hit = int(line[3:] or 0)
        elif line == "end_of_record" and current_file:
            coverage[current_file] = (hit / found * 100) if found else 0.0
            current_file = None
    return coverage


def owners_by_file(root: Path, files: Iterable[str]) -> dict[str, str]:
    codeowners = root / "CODEOWNERS"
    if not codeowners.exists():
        codeowners = root / ".github" / "CODEOWNERS"
    if not codeowners.exists():
        return {}

    rules: list[tuple[str, str]] = []
    for line in codeowners.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            rules.append((parts[0].lstrip("/"), " ".join(parts[1:])))

    owners = {}
    for file_path in files:
        for pattern, owner in rules:
            if codeowner_matches(pattern, file_path):
                owners[file_path] = owner
    return owners


def codeowner_matches(pattern: str, file_path: str) -> bool:
    if pattern.endswith("/"):
        return file_path.startswith(pattern)
    if "*" in pattern:
        return Path(file_path).match(pattern)
    return file_path == pattern or file_path.startswith(f"{pattern}/")


def complexity_score(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    branches = len(COMPLEXITY_PATTERN.findall(text))
    loc = len([line for line in text.splitlines() if line.strip()])
    return branches + loc // 80


def has_matching_test(source_path: str, tests: set[str]) -> bool:
    stem = Path(source_path).stem
    parent_parts = set(Path(source_path).parts[:-1])
    for test_path in tests:
        test = Path(test_path)
        if stem in test.stem:
            return True
        if parent_parts.intersection(test.parts) and stem in test_path:
            return True
    return False


def flaky_test_refs(root: Path) -> Counter[str]:
    refs: Counter[str] = Counter()
    for relative in test_files(root):
        path = root / relative
        text = path.read_text(encoding="utf-8", errors="ignore")
        if contains_any(text, FLAKY_WORDS):
            refs[Path(relative).stem] += 1
    return refs


def is_source_path(path: str) -> bool:
    return Path(path).suffix.lower() in SOURCE_SUFFIXES


def is_test_path(path: str) -> bool:
    lowered = path.lower()
    parts = Path(lowered).parts
    return any(part in TEST_MARKERS for part in parts) or any(
        marker in Path(lowered).stem for marker in ("test", "spec")
    )


def is_critical_path(path: str) -> bool:
    return contains_any(path, CRITICAL_WORDS)


def contains_any(value: str, words: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in words)


def should_skip(path: Path) -> bool:
    return any(part in {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"} for part in path.parts)


def normalize(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def normalize_file_key(root: Path, path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return normalize(candidate.relative_to(root).as_posix())
        except ValueError:
            return normalize(candidate.name)
    return normalize(path)


def git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout
