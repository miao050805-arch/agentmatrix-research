from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_core.factor_lab import FactorLabWorkspaceConfig
from research_core.factor_lab_web import build_factor_library_view

FRONTEND = ROOT / "frontend" / "factor-lab-dashboard"
OUTPUT = ROOT / "pages" / "factor-lab-dashboard"
DETAILS_DIR = OUTPUT / "data" / "factor-details"


FACTOR_FIELDS = {
    "id",
    "factor_name",
    "raw_factor_name",
    "library",
    "raw_library",
    "category",
    "category_inferred",
    "subcategory",
    "required_fields",
    "metadata",
    "formula",
    "description",
    "source_document",
    "source",
    "display_name",
    "declared_factor_id",
    "implementation_status",
    "proof_status",
    "truth_status",
    "overall_status",
    "coverage_ratio",
    "rank_ic_mean",
    "rank_ic_ir",
    "long_short_mean",
    "truth_exact_match_ratio",
    "truth_max_abs_error",
    "data_source",
    "dataset",
    "reuse_recommendation",
}


DETAIL_FIELDS = {
    "factor_id",
    "factor_name",
    "library",
    "data_source",
    "frequency",
    "coverage_ratio",
    "non_null_count",
    "rank_ic_mean",
    "rank_ic_ir",
    "pearson_ic_mean",
    "ic_time_series",
    "group_returns",
    "stratification",
    "dataset",
}


def static_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "artifact"


def scrub_string(value: str) -> str:
    value = re.sub(r"C:\\Users\\[^\"'\\s]+", "[local-path]", value)
    value = re.sub(r"http://115\\.159\\.73\\.134:8765", "[quant-api]", value)
    value = re.sub(r"sk-[A-Za-z0-9_\\-]+", "[redacted-token]", value)
    return value


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return scrub_string(value)
    return value


def public_dataset(dataset: Any) -> dict[str, Any]:
    if not isinstance(dataset, dict):
        return {}
    allowed = {
        "panel_rows",
        "factor_rows",
        "analysis_rows",
        "rows",
        "sample_count",
        "symbols",
        "codes",
        "dates",
        "groups",
        "start_date",
        "end_date",
        "n_dates_requested",
        "n_symbols_requested",
    }
    return {key: scrub(value) for key, value in dataset.items() if key in allowed}


def public_factor(row: dict[str, Any]) -> dict[str, Any]:
    item = {key: scrub(row.get(key)) for key in FACTOR_FIELDS if key in row}
    item["latest_job_id"] = None
    item["latest_checked_at"] = row.get("latest_checked_at")
    item["dataset"] = public_dataset(row.get("dataset"))
    return item


def public_detail(payload: dict[str, Any]) -> dict[str, Any]:
    detail = {key: scrub(payload.get(key)) for key in DETAIL_FIELDS if key in payload}
    detail["dataset"] = public_dataset(payload.get("dataset"))
    return detail


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def copy_frontend() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ["index.html", "app.js", "styles.css", "config.js"]:
        shutil.copy2(FRONTEND / name, OUTPUT / name)
    shutil.copytree(FRONTEND / "assets", OUTPUT / "assets")


def build_library() -> dict[str, Any]:
    workspace = FactorLabWorkspaceConfig()
    payload = build_factor_library_view(workspace)
    factors = [public_factor(row) for row in payload.get("factors", [])]
    return {
        "schema_version": payload.get("schema_version", "factor_lab_view_v1"),
        "generated_at": payload.get("generated_at"),
        "snapshot_mode": "static_public_preview",
        "local_flask": {"connected": False},
        "cloud_registry": {"status": "static_snapshot", "label": "Static GitHub Pages snapshot"},
        "categories": payload.get("categories", {}),
        "libraries": payload.get("libraries", {}),
        "metadata": {
            "warning_count": 0,
            "factor_source_warning_count": 0,
            "artifact_warning_count": 0,
            "factor_source_types": payload.get("metadata", {}).get("factor_source_types", []),
            "redaction": "tokens, local paths, raw frames, artifact paths, and backend URLs removed",
        },
        "errors": [],
        "factors": factors,
    }


def build_details(factors: list[dict[str, Any]]) -> int:
    workspace = FactorLabWorkspaceConfig()
    cache_dir = workspace.runtime_root / "factor_detail_cache"
    count = 0
    for row in factors:
        factor_id = str(row.get("id") or "")
        if not factor_id:
            continue
        source = cache_dir / f"{static_key(factor_id)}.json"
        if not source.is_file():
            continue
        payload = json.loads(source.read_text(encoding="utf-8"))
        write_json(DETAILS_DIR / f"{static_key(factor_id)}.json", public_detail(payload))
        count += 1
    return count


def main() -> None:
    copy_frontend()
    library = build_library()
    write_json(OUTPUT / "data" / "demo-factor-library.json", library)
    detail_count = build_details(library["factors"])
    manifest = {
        "kind": "factor_lab_static_snapshot",
        "factor_count": len(library["factors"]),
        "detail_count": detail_count,
        "redacted": True,
        "excluded": [
            "environment files",
            "raw factor frames",
            "runtime job records",
            "full reports",
            "raw API result dumps",
            "local raw data",
            "dependency folders",
        ],
    }
    write_json(OUTPUT / "snapshot-manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
