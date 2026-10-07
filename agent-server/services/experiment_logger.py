from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("maple-ai-agent")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG_PATH = _REPO_ROOT / "experiments" / "runs" / "maple_platform.jsonl"
_RUN_JOIN_WINDOW = timedelta(minutes=2)


def experiment_log_path() -> Path:
    return Path(os.getenv("RSNA_QI_MARS_LOG", str(_DEFAULT_LOG_PATH)))


def _read_runs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    runs: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if "records" in item:
                    runs.append(item)
                else:
                    # Backward compatibility for earlier endpoint-level JSONL rows.
                    timestamp = item.get("timestamp")
                    run_id = item.get("run_id") or item.get("record_id")
                    runs.append({
                        "run_id": run_id,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                        "query": item.get("query", ""),
                        "uploaded_types": item.get("uploaded_types", []),
                        "records": [item],
                        "summary": {},
                        "excel_fields": {},
                    })
    except Exception:
        logger.exception("[experiment] failed to read experiment runs")
    return runs


def _write_runs(path: Path, runs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
    tmp_path.replace(path)


def _next_record_id(runs: list[dict[str, Any]]) -> str:
    max_record = 0
    for run in runs:
        for record in run.get("records", []):
            record_id = str(record.get("record_id", ""))
            if record_id.startswith("MARS") and record_id[4:].isdigit():
                max_record = max(max_record, int(record_id[4:]))
    return f"MARS{max_record + 1:06d}"


def _next_run_id(runs: list[dict[str, Any]]) -> str:
    max_run = 0
    for run in runs:
        run_id = str(run.get("run_id", ""))
        if run_id.startswith("RUN") and run_id[3:].isdigit():
            max_run = max(max_run, int(run_id[3:]))
    return f"RUN{max_run + 1:06d}"


def _find_recent_run(runs: list[dict[str, Any]], query: str, now: datetime) -> dict[str, Any] | None:
    if not query:
        return None

    for run in reversed(runs):
        if run.get("query") != query:
            continue
        timestamp = run.get("updated_at") or run.get("created_at")
        if not timestamp:
            continue
        try:
            previous = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
        if now - previous <= _RUN_JOIN_WINDOW:
            return run
        return None
    return None


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    records = run.get("records", [])
    plan_records = [r for r in records if r.get("endpoint") == "plan"]
    interpret_records = [r for r in records if r.get("endpoint") == "interpret"]

    plan_latency = sum(float(r.get("latency_sec") or 0) for r in plan_records)
    interpret_latency = sum(float(r.get("latency_sec") or 0) for r in interpret_records)

    selected_model = None
    final_status = None
    step_count = 0
    for record in plan_records:
        if record.get("selected_model"):
            selected_model = record.get("selected_model")
        if record.get("status"):
            final_status = record.get("status")
        if record.get("step_count") is not None:
            step_count = record.get("step_count")

    interpretation_generated = any(bool(r.get("interpretation_generated")) for r in interpret_records)
    xai_signal_detected = any(bool(r.get("xai_marker_included")) for r in interpret_records)
    image_count = sum(int(r.get("image_count") or 0) for r in interpret_records)

    return {
        "plan_latency_sec": round(plan_latency, 4),
        "interpret_latency_sec": round(interpret_latency, 4),
        "total_latency_sec": round(plan_latency + interpret_latency, 4),
        "final_status": final_status,
        "selected_model": selected_model,
        "step_count": step_count,
        "interpretation_generated": interpretation_generated,
        "xai_signal_detected": xai_signal_detected,
        "image_count": image_count,
    }


def _excel_fields(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "maple_status": summary.get("final_status"),
        "maple_selected_model": summary.get("selected_model"),
        # Manual review fields: leave blank on purpose for the RSNA QI worksheet.
        "maple_correct": "",
        "maple_total_latency_sec": summary.get("total_latency_sec"),
        "maple_quant_included": "",
        "maple_xai_included": "",
    }


def append_experiment_record(record: dict[str, Any]) -> None:
    """Append one PHI-light RSNA QI experiment record under a run-level JSONL row."""
    path = experiment_log_path()
    now = datetime.now(timezone.utc)
    runs = _read_runs(path)
    run = _find_recent_run(runs, str(record.get("query", "")), now)

    if run is None:
        run = {
            "run_id": _next_run_id(runs),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "query": record.get("query", ""),
            "uploaded_types": record.get("uploaded_types", []),
            "records": [],
            "summary": {},
            "excel_fields": {},
        }
        runs.append(run)

    payload = {
        # RSNA QI EXPERIMENT LOGGING: auto-increment key for later endpoint tracing.
        "record_id": _next_record_id(runs),
        "timestamp": now.isoformat(),
        **record,
    }
    run["updated_at"] = now.isoformat()
    if not run.get("uploaded_types") and record.get("uploaded_types"):
        run["uploaded_types"] = record.get("uploaded_types")
    run.setdefault("records", []).append(payload)
    summary = _run_summary(run)
    run["summary"] = summary
    run["excel_fields"] = _excel_fields(summary)

    try:
        _write_runs(path, runs)
    except Exception:
        logger.exception("[experiment] failed to write experiment run")
