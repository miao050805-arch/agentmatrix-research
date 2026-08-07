"""Sync truth-compare execution results to the Factor Lab Supabase project.

Reads artifacts/supabase_sync_payload.json produced by
scripts/run_truth_compare.py and upserts:

  1. public.factor_truth_comparisons  (insert, ignore duplicates on run_id)
  2. public.public_dashboard_factors  (merge on factor_id)
  3. public.public_dashboard_tasks    (merge on task_id, only when task_id exists)

Configure credentials with environment variables (loaded from .env.local / .env
at the repo root when present):

  FACTOR_LAB_SUPABASE_URL        e.g. https://<project>.supabase.co
  FACTOR_LAB_SUPABASE_WRITE_KEY  service_role key on a trusted backend/local
                                 machine, or an authenticated user JWT.

This script intentionally contains no secret and is never called from the
GitHub Pages browser frontend. Run it from the trusted machine that executed
the comparison (local agent host or the cloud API server).

Usage:
  python scripts/sync_truth_compare_to_supabase.py --task-id <task_id>
  python scripts/sync_truth_compare_to_supabase.py --all
  python scripts/sync_truth_compare_to_supabase.py payload1.json [payload2.json ...]
  python scripts/sync_truth_compare_to_supabase.py --all --dry-run

Exit codes: 0 = synced (or dry-run), 1 = sync failed, 2 = credentials missing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "runtime" / "factor_lab" / "agent_tasks"


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


def _credentials() -> tuple[str, str]:
    url = (os.environ.get("FACTOR_LAB_SUPABASE_URL") or os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (
        os.environ.get("FACTOR_LAB_SUPABASE_WRITE_KEY")
        or os.environ.get("FACTOR_LAB_SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    ).strip()
    return url, key


def _request_json(
    method: str,
    url: str,
    key: str,
    payload: Any | None = None,
    prefer: str | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code} {detail}") from exc


def _collect_payload_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = [Path(item) for item in args.payloads]
    runtime_root = Path(args.runtime_root)
    if args.task_id:
        paths.append(runtime_root / args.task_id / "artifacts" / "supabase_sync_payload.json")
    if args.all:
        if runtime_root.is_dir():
            paths.extend(sorted(runtime_root.glob("*/artifacts/supabase_sync_payload.json")))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _load_rows(paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {
        "factor_truth_comparisons": [],
        "public_dashboard_factors": [],
        "public_dashboard_tasks": [],
    }
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"sync payload not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "truth_compare_sync_v1":
            raise ValueError(f"{path} is not a truth_compare_sync_v1 payload")
        for table in rows:
            items = payload.get(table) or []
            if not isinstance(items, list):
                raise ValueError(f"{path}: {table} must be a list")
            rows[table].extend(item for item in items if isinstance(item, dict))
    return rows


def sync(rows: dict[str, list[dict[str, Any]]], *, dry_run: bool) -> dict[str, Any]:
    summary = {
        "dry_run": dry_run,
        "planned": {table: len(items) for table, items in rows.items()},
        "synced": {},
    }
    if dry_run:
        summary["rows"] = rows
        return summary

    url, key = _credentials()
    if not url or not key:
        raise PermissionError(
            "Supabase credentials missing: set FACTOR_LAB_SUPABASE_URL and "
            "FACTOR_LAB_SUPABASE_WRITE_KEY (service_role) in the environment or .env.local"
        )

    if rows["factor_truth_comparisons"]:
        _request_json(
            "POST",
            f"{url}/rest/v1/factor_truth_comparisons",
            key,
            rows["factor_truth_comparisons"],
            prefer="return=minimal,resolution=ignore-duplicates",
        )
        summary["synced"]["factor_truth_comparisons"] = len(rows["factor_truth_comparisons"])

    if rows["public_dashboard_factors"]:
        _request_json(
            "POST",
            f"{url}/rest/v1/public_dashboard_factors",
            key,
            rows["public_dashboard_factors"],
            prefer="return=minimal,resolution=merge-duplicates",
        )
        summary["synced"]["public_dashboard_factors"] = len(rows["public_dashboard_factors"])

    if rows["public_dashboard_tasks"]:
        _request_json(
            "POST",
            f"{url}/rest/v1/public_dashboard_tasks",
            key,
            rows["public_dashboard_tasks"],
            prefer="return=minimal,resolution=merge-duplicates",
        )
        summary["synced"]["public_dashboard_tasks"] = len(rows["public_dashboard_tasks"])

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync truth-compare results to the Factor Lab Supabase project.")
    parser.add_argument("payloads", nargs="*", help="Explicit supabase_sync_payload.json paths.")
    parser.add_argument("--task-id", help="Sync the payload of one agent task.")
    parser.add_argument("--all", action="store_true", help="Sync every task payload under the runtime root.")
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT), help="Agent task runtime root.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned rows without calling Supabase.")
    args = parser.parse_args()

    _load_env()
    try:
        paths = _collect_payload_paths(args)
        if not paths:
            print("ERROR: no sync payload found. Pass payload paths, --task-id, or --all.", file=sys.stderr)
            return 1
        rows = _load_rows(paths)
        summary = sync(rows, dry_run=args.dry_run)
    except PermissionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
