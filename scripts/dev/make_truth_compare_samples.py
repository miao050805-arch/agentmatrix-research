"""Generate deterministic truth-compare sample packages for local testing.

Creates three submission packages plus one library-truth directory so every
verdict branch of scripts/run_truth_compare.py can be reproduced without any
external data:

  sample_passed/         factor values identical to the library truth
                         -> standard_truth.status=passed, decision=accept
  sample_failed/         25% of points shifted by +0.251
                         -> standard_truth.status=failed, decision=reject
  sample_not_comparable/ factor alpha999 has no library truth file
                         -> standard_truth.status=not_comparable, decision=reject

Usage:
  python scripts/dev/make_truth_compare_samples.py --out-dir /tmp/truth_samples
  python scripts/run_truth_compare.py --factor-family wq101 --factor-name alpha1 \
      --submission-dir /tmp/truth_samples/sample_passed \
      --library-truth-dir /tmp/truth_samples/library_truth
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

DATES = ["2024-01-02", "2024-01-03", "2024-01-04"]
CODES = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]


def _truth_value(day_index: int, code_index: int) -> float:
    return round(0.1 * (day_index + 1) - 0.05 * (code_index + 1) + 0.001 * day_index * code_index, 6)


def _rows() -> list[list[object]]:
    return [
        [date, code, _truth_value(day_index, code_index)]
        for day_index, date in enumerate(DATES)
        for code_index, code in enumerate(CODES)
    ]


def _write_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "symbol", "factor_value"])
        writer.writerows(rows)


def _write_manifest(path: Path, *, factor_family: str, factor_name: str, package_name: str) -> None:
    manifest = {
        "task_type": "truth_compare",
        "submitter": "make_truth_compare_samples",
        "factor_family": factor_family,
        "factor_name": factor_name,
        "package_name": package_name,
        "data_source": "quant_api",
        "requires_quant_api": False,
        "notes": ["deterministic local sample; no external data required"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_samples(out_dir: Path) -> dict[str, object]:
    random.seed(7)
    truth_rows = _rows()

    # library truth, wide layout on purpose (date/code/alpha1) to exercise the
    # executor's layout auto-detection.
    library_truth_dir = out_dir / "library_truth"
    library_truth_dir.mkdir(parents=True, exist_ok=True)
    wide_path = library_truth_dir / "alpha1.csv"
    with wide_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "code", "alpha1"])
        writer.writerows(truth_rows)

    # 1) passed: identical to truth, long layout
    passed_dir = out_dir / "sample_passed"
    _write_csv(passed_dir / "factor_values.csv", truth_rows)
    _write_manifest(passed_dir / "manifest.json", factor_family="wq101", factor_name="alpha1", package_name="sample_passed")

    # 2) failed: every fourth point shifted by +0.251 (25% mismatch, max_err 0.251)
    failed_rows = [list(row) for row in truth_rows]
    for index in range(0, len(failed_rows), 4):
        failed_rows[index][2] = round(float(failed_rows[index][2]) + 0.251, 6)
    failed_dir = out_dir / "sample_failed"
    _write_csv(failed_dir / "factor_values.csv", failed_rows)
    _write_manifest(failed_dir / "manifest.json", factor_family="wq101", factor_name="alpha1", package_name="sample_failed")

    # 3) not_comparable: alpha999 has no library truth file at all
    unknown_dir = out_dir / "sample_not_comparable"
    _write_csv(unknown_dir / "factor_values.csv", truth_rows)
    _write_manifest(unknown_dir / "manifest.json", factor_family="wq101", factor_name="alpha999", package_name="sample_not_comparable")

    summary = {
        "out_dir": str(out_dir),
        "library_truth": str(wide_path),
        "packages": {
            "sample_passed": {"factor_name": "alpha1", "expected": "passed / accept"},
            "sample_failed": {"factor_name": "alpha1", "expected": "failed / reject (exact_match_ratio=0.75, max_abs_error=0.251)"},
            "sample_not_comparable": {"factor_name": "alpha999", "expected": "not_comparable / reject (no_library_truth)"},
        },
    }
    (out_dir / "README.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic truth-compare sample packages.")
    parser.add_argument("--out-dir", type=Path, default=Path("runtime/truth_compare_samples"))
    args = parser.parse_args()
    summary = build_samples(args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
