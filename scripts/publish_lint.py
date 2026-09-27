#!/usr/bin/env python3
"""Fail-closed lint for private data in public HTML files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Pattern, Sequence


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: Pattern[str]


RULES: tuple[Rule, ...] = (
    Rule(
        "R1_PATH",
        re.compile(r"(?:/Users/[^\s<>\"']+|~/[^\s<>\"']+|/private/[^\s<>\"']+)"),
    ),
    Rule(
        "R2_TOKEN",
        re.compile(
            r"(?:\btoken\b|\bapi(?:_|-)?key\b|\bsecret\b|\bbearer\b|"
            r"\bauthorization\s*:\s*\S+|(?<![\w])\.env(?:\b|/))",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "R3_VAULT_DIR",
        re.compile(
            r"(?:raw/inbox|wiki/(?:source|concept|entity|incident)-|handoffs/|06-SYSTEM/)",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    column: int
    rule_id: str
    context: str


def context_for(line: str, start: int, end: int, radius: int = 40) -> str:
    left = max(0, start - radius)
    right = min(len(line), end + radius)
    prefix = "…" if left else ""
    suffix = "…" if right < len(line) else ""
    return f"{prefix}{line[left:right]}{suffix}".replace("\t", " ")


def scan(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                findings.append(
                    Finding(
                        path=path,
                        line_number=line_number,
                        column=match.start() + 1,
                        rule_id=rule.rule_id,
                        context=context_for(line, match.start(), match.end()),
                    )
                )
    return findings


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Block private paths, credentials, and Vault internals in public files."
    )
    parser.add_argument("paths", nargs="+", type=Path, help="Files to scan")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    all_findings: list[Finding] = []
    errors: list[str] = []

    for path in args.paths:
        try:
            all_findings.extend(scan(path))
        except (OSError, UnicodeError) as exc:
            errors.append(f"ERROR: {path}: {exc}")

    for finding in all_findings:
        print(
            f"{finding.path}:{finding.line_number}:{finding.column} "
            f"[{finding.rule_id}] {finding.context}"
        )

    for error in errors:
        print(error, file=sys.stderr)

    if errors:
        print(f"FAIL: {len(errors)} file error(s)", file=sys.stderr)
        return 2
    if all_findings:
        print(f"FAIL: {len(all_findings)} finding(s)")
        return 1

    for path in args.paths:
        print(f"PASS: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
