#!/usr/bin/env python3
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: str


def _scenario_db_error_classifier() -> None:
    from cloud_sync_service import _is_db_error

    try:
        try:
            raise TimeoutError("Connection reset by peer")
        except Exception as inner:
            raise RuntimeError("wrapper exception") from inner
    except Exception as outer:
        if not _is_db_error(outer):
            raise AssertionError("Expected DB error classifier to detect nested connection error")


def _scenario_trace_fallback() -> None:
    from cloud_sync_service import _extract_job_context

    job_id = "demo_stem-20260206T120000000000Z"

    fallback = _extract_job_context(job_id, payload={})
    if fallback.get("trace_id") != job_id:
        raise AssertionError("Expected trace_id fallback to job_id when payload has no trace context")

    payload_trace = {
        "trace_id": "trace-from-payload",
        "meta": {"target_language": "is"},
    }
    explicit = _extract_job_context(job_id, payload=payload_trace)
    if explicit.get("trace_id") != "trace-from-payload":
        raise AssertionError("Expected payload trace_id to override fallback")


def _scenario_audit_thresholds() -> None:
    from scripts import pipeline_audit

    base_report = {
        "critical": [],
        "warnings": [],
        "checks": {
            "orphan_deliveries": {"orphans": []},
            "sync_states": {"stale": []},
            "stalled_jobs": {"stalled": []},
            "dead_jobs": {"count": 1},
        },
    }

    # Disabled thresholds should not create a threshold breach.
    report_disabled = json.loads(json.dumps(base_report))
    pipeline_audit._apply_thresholds(
        report_disabled,
        thresholds={
            "max_orphans": None,
            "max_stale_syncs": None,
            "max_stalled_jobs": None,
            "max_dead_jobs": None,
        },
        breach_level="warning",
    )
    disabled_breaches = (((report_disabled.get("checks") or {}).get("thresholds") or {}).get("breaches") or [])
    if disabled_breaches:
        raise AssertionError("Threshold breaches should be empty when all thresholds are disabled")

    # Explicit threshold should breach when dead jobs exceed limit.
    report_enabled = json.loads(json.dumps(base_report))
    pipeline_audit._apply_thresholds(
        report_enabled,
        thresholds={
            "max_orphans": 0,
            "max_stale_syncs": 0,
            "max_stalled_jobs": 0,
            "max_dead_jobs": 0,
        },
        breach_level="warning",
    )
    enabled_breaches = (((report_enabled.get("checks") or {}).get("thresholds") or {}).get("breaches") or [])
    if not enabled_breaches:
        raise AssertionError("Expected explicit max_dead_jobs threshold to breach when dead jobs exist")


def _scenario_state_machine_guardrails() -> None:
    from state_machine import InvalidTransitionError, record_transition

    valid = record_transition(job_id="job-pass", from_stage="FINALIZED", to_stage="BURNING")
    if not valid.get("valid"):
        raise AssertionError("Expected FINALIZED -> BURNING to be valid")

    try:
        record_transition(job_id="job-fail", from_stage="QUEUED", to_stage="FINALIZED")
    except InvalidTransitionError:
        return
    raise AssertionError("Expected QUEUED -> FINALIZED to raise InvalidTransitionError")


def _scenario_subtitle_linebreak_preserved() -> None:
    from workers.finalizer import _strip_metadata_tags

    input_text = "First line\\n<APPLAUSE>\\nSecond line"
    output = _strip_metadata_tags(input_text)
    if "\\n" not in output:
        raise AssertionError("Expected metadata cleaning to preserve subtitle line breaks")
    if "APPLAUSE" in output:
        raise AssertionError("Expected metadata tags to be removed from subtitle text")


def _run(name: str, fn: Callable[[], None]) -> ScenarioResult:
    try:
        fn()
        return ScenarioResult(name=name, passed=True, details="ok")
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}\\n{traceback.format_exc(limit=2)}"
        return ScenarioResult(name=name, passed=False, details=detail)


def main() -> int:
    scenarios: List[tuple[str, Callable[[], None]]] = [
        ("db_error_classifier", _scenario_db_error_classifier),
        ("trace_fallback", _scenario_trace_fallback),
        ("audit_thresholds", _scenario_audit_thresholds),
        ("state_machine_guardrails", _scenario_state_machine_guardrails),
        ("subtitle_linebreak_preserved", _scenario_subtitle_linebreak_preserved),
    ]

    results = [_run(name, fn) for name, fn in scenarios]
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    payload = {
        "status": "ok" if failed == 0 else "failed",
        "passed": passed,
        "failed": failed,
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "details": r.details,
            }
            for r in results
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
