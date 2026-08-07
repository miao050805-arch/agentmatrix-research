"""Factor Lab truth-compare executor (offline, file-contract based).

This script is the execution half of the Factor Lab "数据入口 / truth_compare"
intake path. The Flask backend (backend/factor_lab_api.py) freezes
artifacts/criteria.json at intake time; this executor reads that locked
criteria, compares the submitted factor values against the library standard
truth, and writes the verdict back through the file contract:

  runtime/factor_lab/agent_tasks/<task_id>/
    request.json                          (read)
    artifacts/criteria.json               (read, sha256 verified)
    artifacts/standard_truth_comparison.json  (written)
    artifacts/supabase_sync_payload.json      (written)
    status.json                           (updated: gates G0-G7, standard_truth,
                                           final_decision, truth_execution)

Decision contract (docs/FACTOR_LAB_TWO_ENTRY_BACKEND_FLOW.md):

  accept  <=> standard_truth.status = passed
              AND overlap_ratio >= min_overlap_ratio
              AND exact_match_ratio >= pass_exact_match_ratio
              AND max_abs_error <= tolerance
  no library truth => standard_truth.status = not_comparable
                      AND final_decision.decision = reject

Usage:

  # task mode (after POST /api/agents/factor-lab/intake/truth-compare)
  python scripts/run_truth_compare.py --task-id <task_id> --submission-dir <path>

  # standalone mode (no task files touched, result printed to stdout)
  python scripts/run_truth_compare.py \
      --factor-family wq101 --factor-name alpha1 \
      --submission-dir submissions/example_truth_compare_submission \
      --library-truth-dir path/to/library_truth

Truth resolution order:
  1. <submission-dir>/truth_values.csv | truth_values.parquet
  2. --library-truth-dir/<factor_name>.csv|parquet (or <factor_family>_<factor_name>.*)
  3. $FACTOR_LAB_TRUTH_DIR/<factor_name>.csv|parquet (from env, .env, .env.local)
  4. none found => not_comparable / reject

Exit codes: 0 = executed (any verdict, including reject); 1 = operational error.
This script contains no secrets. Supabase sync is handled separately by
scripts/sync_truth_compare_to_supabase.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from research_core.factor_lab.truth import compare_factor_to_truth

DEFAULT_RUNTIME_ROOT = REPO_ROOT / "runtime" / "factor_lab" / "agent_tasks"

# Mirror of backend/factor_lab_api.py TRUTH_CRITERIA_REGISTRY, used only in
# standalone mode when no locked criteria.json is available. Task mode always
# uses the locked criteria artifact instead.
STANDALONE_CRITERIA_REGISTRY: dict[str, dict[str, Any]] = {
    "alpha101": {
        "tolerance": 1e-8,
        "min_overlap_ratio": 0.9,
        "pass_exact_match_ratio": 0.99,
        "criteria_source": "registry:alpha101_v1",
    },
    "wq101": {
        "tolerance": 1e-8,
        "min_overlap_ratio": 0.9,
        "pass_exact_match_ratio": 0.99,
        "criteria_source": "registry:wq101_v1",
    },
    "gtja191": {
        "tolerance": 1e-8,
        "min_overlap_ratio": 0.9,
        "pass_exact_match_ratio": 0.99,
        "criteria_source": "registry:gtja191_v1",
    },
    "exploratory": {
        "tolerance": 1e-8,
        "min_overlap_ratio": 0.9,
        "pass_exact_match_ratio": 0.99,
        "criteria_source": "registry:exploratory_v1",
    },
}

LONG_VALUE_COLUMNS = ("factor_value", "value")
CODE_COLUMNS = ("symbol", "code")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_env() -> None:
    _load_env_file(REPO_ROOT / ".env.local")
    _load_env_file(REPO_ROOT / ".env")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_panel(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                f"reading parquet requires pyarrow/fastparquet, which is not installed: {path}"
            ) from exc
    raise ValueError(f"unsupported data file extension: {path}")


def _normalize_panel(frame: pd.DataFrame, *, factor_name: str, role: str) -> pd.DataFrame:
    """Accept long (date,symbol,factor_value) or wide (date,code,<factor_name>) layouts."""
    columns = {str(col).strip().lower(): col for col in frame.columns}
    if "date" not in columns:
        raise ValueError(f"{role} file is missing the required 'date' column")

    code_col = next((columns[name] for name in CODE_COLUMNS if name in columns), None)
    if code_col is None:
        raise ValueError(f"{role} file is missing a symbol/code column")

    value_col = next((columns[name] for name in LONG_VALUE_COLUMNS if name in columns), None)
    factor_col = columns.get(factor_name.lower())

    normalized = frame.rename(columns={columns["date"]: "date", code_col: "code"}).copy()
    normalized["date"] = pd.to_datetime(normalized["date"])
    normalized["code"] = normalized["code"].astype(str)

    if value_col is not None:
        # long layout: one factor per file
        normalized = normalized.rename(columns={value_col: factor_name})
    elif factor_col is not None:
        normalized = normalized.rename(columns={factor_col: factor_name})
    else:
        raise ValueError(
            f"{role} file must contain either a long value column {LONG_VALUE_COLUMNS} "
            f"or a wide factor column named '{factor_name}'"
        )

    normalized[factor_name] = pd.to_numeric(normalized[factor_name], errors="coerce")
    return normalized[["date", "code", factor_name]]


def _find_truth_file(
    submission_dir: Path,
    *,
    factor_family: str,
    factor_name: str,
    library_truth_dir: Path | None,
) -> tuple[Path | None, str | None]:
    for name in ("truth_values.csv", "truth_values.parquet"):
        candidate = submission_dir / name
        if candidate.is_file():
            return candidate, "submission_truth_values"
    candidate_names = [
        f"{factor_name}.csv",
        f"{factor_name}.parquet",
        f"{factor_family}_{factor_name}.csv",
        f"{factor_family}_{factor_name}.parquet",
    ]
    for root, label in ((library_truth_dir, "library_truth_dir"),):
        if not root:
            continue
        for name in candidate_names:
            candidate = root / name
            if candidate.is_file():
                return candidate, label
    env_dir = os.environ.get("FACTOR_LAB_TRUTH_DIR")
    if env_dir:
        root = Path(env_dir)
        for name in candidate_names:
            candidate = root / name
            if candidate.is_file():
                return candidate, "env_factor_lab_truth_dir"
    return None, None


def _resolve_standalone_criteria(factor_family: str) -> dict[str, Any]:
    criteria = STANDALONE_CRITERIA_REGISTRY.get(factor_family)
    if criteria:
        return {
            **criteria,
            "schema_version": "factor_intake_criteria_v1",
            "task_type": "truth_compare",
            "factor_family": factor_family,
            "criteria_status": "resolved",
            "truth_required": True,
        }
    return {
        "tolerance": 1e-8,
        "min_overlap_ratio": 0.9,
        "pass_exact_match_ratio": 0.99,
        "criteria_source": f"registry:unknown_factor_family:{factor_family}",
        "schema_version": "factor_intake_criteria_v1",
        "task_type": "truth_compare",
        "factor_family": factor_family,
        "criteria_status": "failed",
        "criteria_error": "unknown_factor_family",
        "truth_required": True,
    }


def _decide(
    *,
    overlap_ratio: float,
    exact_match_ratio: float,
    max_abs_error: float,
    criteria: dict[str, Any],
) -> tuple[str, str | None]:
    """Return (standard_truth_status, reject_reason)."""
    min_overlap = float(criteria.get("min_overlap_ratio", 0.9))
    pass_exact = float(criteria.get("pass_exact_match_ratio", 0.99))
    tolerance = float(criteria.get("tolerance", 1e-8))
    if overlap_ratio < min_overlap:
        return "failed", f"overlap_ratio {overlap_ratio:.4f} < min_overlap_ratio {min_overlap}"
    if exact_match_ratio < pass_exact:
        return "failed", f"exact_match_ratio {exact_match_ratio:.6f} < pass_exact_match_ratio {pass_exact}"
    if max_abs_error > tolerance:
        return "failed", f"max_abs_error {max_abs_error:g} > tolerance {tolerance:g}"
    return "passed", None


def run_truth_compare(
    *,
    task_id: str | None,
    submission_dir: Path,
    factor_family: str,
    factor_name: str,
    library_truth_dir: Path | None,
    runtime_root: Path,
) -> dict[str, Any]:
    _load_env()
    run_id = f"truth-run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    executed_at = _utc_now_iso()

    task_dir: Path | None = None
    status_payload: dict[str, Any] = {}
    criteria: dict[str, Any]
    criteria_source_detail: str

    if task_id:
        task_dir = runtime_root / task_id
        if not task_dir.is_dir():
            raise FileNotFoundError(f"task directory not found: {task_dir}")
        criteria_path = task_dir / "artifacts" / "criteria.json"
        status_path = task_dir / "status.json"
        if not criteria_path.is_file():
            raise FileNotFoundError(f"locked criteria not found: {criteria_path}")
        status_payload = _read_json(status_path) if status_path.is_file() else {}
        expected_sha = str(status_payload.get("criteria_sha256") or "")
        actual_sha = _sha256_file(criteria_path)
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(
                "criteria integrity check failed: "
                f"expected sha256 {expected_sha}, got {actual_sha}. "
                "criteria.json was locked at intake and must not be modified."
            )
        criteria = _read_json(criteria_path)
        criteria_source_detail = f"locked criteria.json (sha256 {actual_sha[:12]}...)"
        request_payload = _read_json(task_dir / "request.json") if (task_dir / "request.json").is_file() else {}
        factor_family = (
            factor_family
            or str(criteria.get("factor_family") or request_payload.get("factor_family") or "").strip().lower()
        )
        factor_name = factor_name or str(request_payload.get("factor_name") or "").strip()
        if not factor_name:
            files = request_payload.get("files") or []
            package = request_payload.get("package") or {}
            factor_name = str(package.get("package_name") or (files[0].get("name", "").split(".")[0] if files else "")).strip()
    else:
        if not factor_family:
            raise ValueError("--factor-family is required in standalone mode")
        if not factor_name:
            raise ValueError("--factor-name is required in standalone mode")
        criteria = _resolve_standalone_criteria(factor_family)
        criteria_source_detail = "standalone registry mirror (no locked criteria)"

    if not factor_name:
        raise ValueError("factor_name could not be resolved from the task request or CLI arguments")

    if not submission_dir.is_dir():
        raise FileNotFoundError(f"submission directory not found: {submission_dir}")
    submission_file = submission_dir / "factor_values.csv"
    if not submission_file.is_file():
        parquet_candidate = submission_dir / "factor_values.parquet"
        if parquet_candidate.is_file():
            submission_file = parquet_candidate
        else:
            raise FileNotFoundError(
                f"submission package must contain factor_values.csv (or factor_values.parquet): {submission_dir}"
            )

    # G2 value schema check + load submission panel
    submission_frame = _normalize_panel(_read_panel(submission_file), factor_name=factor_name, role="submission")
    submission_rows = int(len(submission_frame))
    if submission_rows == 0:
        raise ValueError(f"{submission_file} contains no data rows")

    # G3 data quality
    duplicate_keys = int(submission_frame.duplicated(subset=["date", "code"]).sum())
    null_values = int(submission_frame[factor_name].isna().sum())
    submission_frame = submission_frame.drop_duplicates(subset=["date", "code"], keep="first")
    unique_submission_rows = int(len(submission_frame))

    # G4 library truth lookup
    truth_file, truth_source = _find_truth_file(
        submission_dir,
        factor_family=factor_family,
        factor_name=factor_name,
        library_truth_dir=library_truth_dir,
    )

    tolerance = float(criteria.get("tolerance", 1e-8))
    min_overlap = float(criteria.get("min_overlap_ratio", 0.9))
    pass_exact = float(criteria.get("pass_exact_match_ratio", 0.99))

    metrics: dict[str, Any] = {}
    if truth_file is None:
        truth_status = "not_comparable"
        reject_reason = "no_library_truth"
        decision = "reject"
        metrics = {
            "overlap_ratio": 0.0,
            "exact_match_ratio": None,
            "max_abs_error": None,
            "compared_count": 0,
            "mismatch_count": None,
        }
    else:
        truth_frame = _normalize_panel(_read_panel(truth_file), factor_name=factor_name, role="library truth")
        comparison = compare_factor_to_truth(
            submission_frame,
            truth_frame,
            factor_name=factor_name,
            tolerance=tolerance,
        )
        compared_count = int(comparison["compared_count"])
        overlap_ratio = compared_count / unique_submission_rows if unique_submission_rows else 0.0
        exact_match_ratio = float(comparison["exact_match_ratio"]) if compared_count else 0.0
        max_abs_error = comparison["max_abs_error"]
        max_abs_error = float(max_abs_error) if pd.notna(max_abs_error) else float("inf")
        truth_status, reject_reason = _decide(
            overlap_ratio=overlap_ratio,
            exact_match_ratio=exact_match_ratio,
            max_abs_error=max_abs_error,
            criteria=criteria,
        )
        decision = "accept" if truth_status == "passed" else "reject"
        metrics = {
            "overlap_ratio": round(overlap_ratio, 6),
            "exact_match_ratio": round(exact_match_ratio, 6),
            "max_abs_error": None if max_abs_error == float("inf") else max_abs_error,
            "mean_abs_error": comparison.get("mean_abs_error"),
            "compared_count": compared_count,
            "mismatch_count": comparison.get("mismatch_count"),
            "exact_match_count": comparison.get("exact_match_count"),
            "cross_section_spearman_mean": comparison.get("cross_section_spearman_mean"),
            "cross_section_pearson_mean": comparison.get("cross_section_pearson_mean"),
            "mismatch_samples": comparison.get("mismatches", []),
        }

    standard_truth = {
        "role": "primary_gate",
        "status": truth_status,
        "reason": reject_reason,
        "source": "factor_library_truth",
        "truth_source_detail": truth_source,
        "truth_file": str(truth_file) if truth_file else None,
        "blocking": True,
    }
    final_decision = {
        "decision": decision,
        "decided_by": "scripts/run_truth_compare.py",
        "basis": (
            "accept <=> standard_truth.status=passed AND overlap_ratio >= min_overlap_ratio "
            "AND exact_match_ratio >= pass_exact_match_ratio AND max_abs_error <= tolerance"
        ),
        "decided_at": executed_at,
        "note": "G8 final_approval remains a human-only gate." if decision == "accept" else "rejected by the primary truth gate; human review optional.",
    }

    gates_update = {
        "G0": ("passed", "intake package validated"),
        "G1": ("passed", f"criteria frozen and verified: {criteria_source_detail}"),
        "G2": ("passed", f"schema ok: date/code/{factor_name}, {submission_rows} rows"),
        "G3": (
            "warning" if (duplicate_keys or null_values) else "passed",
            f"duplicates={duplicate_keys}, nulls={null_values} (duplicates dropped, keep first)",
        ),
        "G4": (
            ("passed", f"library truth loaded via {truth_source}: {truth_file}")
            if truth_file
            else ("failed", "no_library_truth: no truth_values file and no library truth found")
        ),
        "G5": (
            ("passed", f"standard truth comparison passed (overlap={metrics['overlap_ratio']}, exact={metrics['exact_match_ratio']}, max_err={metrics['max_abs_error']})")
            if truth_status == "passed"
            else ("failed", f"standard_truth.status={truth_status}; reason={reject_reason}")
        ),
        "G6": ("skipped", "library similarity diagnostic not enabled in this executor"),
        "G7": ("passed", "artifacts/standard_truth_comparison.json written"),
        "G8": (
            ("pending", "human-only final approval")
            if decision == "accept"
            else ("failed", "rejected by primary truth gate")
        ),
    }

    truth_execution = {
        "status": truth_status,
        "decision": decision,
        "reason": reject_reason,
        "overlap_ratio": metrics.get("overlap_ratio"),
        "exact_match_ratio": metrics.get("exact_match_ratio"),
        "max_abs_error": metrics.get("max_abs_error"),
        "compared_count": metrics.get("compared_count"),
        "mismatch_count": metrics.get("mismatch_count"),
        "tolerance": tolerance,
        "min_overlap_ratio": min_overlap,
        "pass_exact_match_ratio": pass_exact,
        "run_id": run_id,
        "artifact": "artifacts/standard_truth_comparison.json",
        "executed_at": executed_at,
    }

    result: dict[str, Any] = {
        "schema_version": "standard_truth_comparison_v1",
        "task_id": task_id,
        "run_id": run_id,
        "task_type": "truth_compare",
        "factor_family": factor_family,
        "factor_name": factor_name,
        "criteria_snapshot": {
            "source": criteria_source_detail,
            "criteria_status": criteria.get("criteria_status"),
            "tolerance": tolerance,
            "min_overlap_ratio": min_overlap,
            "pass_exact_match_ratio": pass_exact,
            "criteria_source": criteria.get("criteria_source"),
        },
        "submission": {
            "dir": str(submission_dir),
            "file": submission_file.name,
            "rows": submission_rows,
            "unique_date_code_rows": unique_submission_rows,
            "duplicate_keys_dropped": duplicate_keys,
            "null_values": null_values,
            "dates": int(submission_frame["date"].nunique()),
            "codes": int(submission_frame["code"].nunique()),
        },
        "metrics": metrics,
        "standard_truth": standard_truth,
        "final_decision": final_decision,
        "truth_execution": truth_execution,
        "generated_at": executed_at,
    }

    sync_payload = {
        "schema_version": "truth_compare_sync_v1",
        "generated_at": executed_at,
        "factor_truth_comparisons": [
            {
                "task_id": task_id,
                "run_id": run_id,
                "factor_family": factor_family,
                "factor_name": factor_name,
                "criteria_source": criteria.get("criteria_source"),
                "status": truth_status,
                "decision": decision,
                "reason": reject_reason,
                "overlap_ratio": metrics.get("overlap_ratio"),
                "exact_match_ratio": metrics.get("exact_match_ratio"),
                "max_abs_error": metrics.get("max_abs_error"),
                "compared_count": metrics.get("compared_count"),
                "mismatch_count": metrics.get("mismatch_count"),
                "executed_at": executed_at,
            }
        ],
        "public_dashboard_factors": [
            {
                "factor_id": f"{factor_family}:{factor_name}",
                "factor_name": factor_name,
                "factor_family": factor_family,
                "truth_status": truth_status,
                "truth_exact_match_ratio": metrics.get("exact_match_ratio"),
                "truth_max_abs_error": metrics.get("max_abs_error"),
                "latest_task_id": task_id,
                "latest_checked_at": executed_at,
                "payload": {"run_id": run_id, "decision": decision, "reason": reject_reason},
            }
        ],
        "public_dashboard_tasks": (
            [
                {
                    "task_id": task_id,
                    "task_type": "truth_compare",
                    "title": f"真值对照 {factor_family}/{factor_name}",
                    "status": "rejected" if decision == "reject" else "waiting_final_approval",
                    "current_gate": "G8",
                    "summary": f"standard_truth={truth_status}; decision={decision}; reason={reject_reason or 'ok'}",
                    "payload": {"truth_execution": truth_execution},
                    "latest_checked_at": executed_at,
                }
            ]
            if task_id
            else []
        ),
    }

    if task_dir is not None:
        artifacts_dir = task_dir / "artifacts"
        _write_json(artifacts_dir / "standard_truth_comparison.json", result)
        _write_json(artifacts_dir / "supabase_sync_payload.json", sync_payload)

        # update status.json through the file contract
        now = _utc_now_iso()
        gates = status_payload.get("gates") if isinstance(status_payload.get("gates"), list) else []
        new_gates = []
        for gate in gates:
            gate_id = str(gate.get("gate") or "")
            if gate_id in gates_update:
                gate_status, note = gates_update[gate_id]
                new_gates.append({**gate, "status": gate_status, "note": note, "updated_at": now})
            else:
                new_gates.append(gate)
        status_payload.update(
            {
                "status": "waiting_final_approval" if decision == "accept" else "rejected",
                "current_gate": "G8",
                "progress": 92 if decision == "accept" else 100,
                "message": (
                    f"真值对照 {truth_status}：{reject_reason or 'all criteria satisfied'}。"
                    + ("等待人工最终确认（G8）。" if decision == "accept" else "主闸门拒绝。")
                ),
                "gates": new_gates,
                "standard_truth": standard_truth,
                "final_decision": final_decision,
                "truth_execution": truth_execution,
                "updated_at": now,
            }
        )
        _write_json(task_dir / "status.json", status_payload)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Factor Lab truth-compare entry offline and write the verdict back to the task files.",
    )
    parser.add_argument("--task-id", help="Agent task id under runtime/factor_lab/agent_tasks. Enables task mode.")
    parser.add_argument("--submission-dir", type=Path, required=True, help="Folder containing factor_values.csv (long or wide layout).")
    parser.add_argument("--library-truth-dir", type=Path, help="Folder with library standard truth files (<factor_name>.csv|parquet).")
    parser.add_argument("--factor-family", default="", help="alpha101 | wq101 | gtja191 | exploratory. Required in standalone mode.")
    parser.add_argument("--factor-name", default="", help="Factor name, e.g. alpha1. Required in standalone mode.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
        help="Agent task runtime root (default: runtime/factor_lab/agent_tasks).",
    )
    args = parser.parse_args()

    try:
        result = run_truth_compare(
            task_id=args.task_id,
            submission_dir=args.submission_dir,
            factor_family=str(args.factor_family or "").strip().lower(),
            factor_name=str(args.factor_name or "").strip(),
            library_truth_dir=args.library_truth_dir,
            runtime_root=args.runtime_root,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
