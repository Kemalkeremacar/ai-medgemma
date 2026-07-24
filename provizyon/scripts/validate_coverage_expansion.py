"""Impact analysis: new coverage-expansion rules vs existing 2.36M results.

Reads rule_engine_results.csv and recalculates how many NO_RULE provisions
would flip to PASS with the newly added lab codes.

Usage:
    python -m scripts.validate_coverage_expansion
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent

RESULTS_CSV = (
    _PROJECT_ROOT
    / "data"
    / "handoffs"
    / "bulk_historical_prefill_3_2m_task"
    / "outputs"
    / "rule_engine_results.csv"
)

NEW_CODES = {
    "900130", "901620", "901500", "900200", "900580",
    "901940", "902210", "903670", "903130", "900900",
}


def main() -> None:
    if not RESULTS_CSV.exists():
        print(f"ERROR: {RESULTS_CSV} not found")
        sys.exit(1)

    total = 0
    no_rule_total = 0
    would_flip = 0
    flip_by_code: Counter[str] = Counter()

    with open(RESULTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if row["overall_decision"] != "NO_RULE":
                continue
            no_rule_total += 1

            no_rule_codes = set(
                c.strip()
                for c in row.get("no_rule_codes", "").split(";")
                if c.strip()
            )
            covered = no_rule_codes & NEW_CODES
            if covered:
                remaining_no_rule = no_rule_codes - NEW_CODES
                if not remaining_no_rule:
                    would_flip += 1
                    for c in covered:
                        flip_by_code[c] += 1

    print(f"Total rows: {total:,}")
    print(f"NO_RULE rows: {no_rule_total:,}")
    print(f"Would flip to PASS with new rules: {would_flip:,}")
    print(f"Remaining NO_RULE: {no_rule_total - would_flip:,}")
    print(f"\nNew coverage: {(1 - (no_rule_total - would_flip) / total) * 100:.2f}%")
    print(f"Old coverage: {(1 - no_rule_total / total) * 100:.2f}%")
    print(f"\nFlip by code:")
    for code, count in flip_by_code.most_common():
        print(f"  {code}: {count:,}")


if __name__ == "__main__":
    main()
