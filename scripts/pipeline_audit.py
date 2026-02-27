#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
import omega_db
from workers.text_sanitizer import detect_likely_t_escape_corruption


TERMINAL_STAGES = {"COMPLETED", "COMPLETE", "DELIVERED", "FAILED", "DEAD"}


def _env_threshold(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return max(0, int(value))
    except Exception:
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    return None


def _is_hidden(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name.startswith("._")


def _as_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.replace(tzinfo=None)
    return dt


def _find_orphan_deliveries(limit: int = 25) -> Dict[str, Any]:
    results: List[Dict[str, str]] = []
    scanned = 0
    hidden = 0
    delivery_dir = Path(config.DELIVERY_DIR) / "VIDEO"
    if not delivery_dir.exists():
        return {"scanned": 0, "hidden": 0, "orphans": []}

    for video_file in delivery_dir.glob("*_SUBBED.mp4"):
        scanned += 1
        if _is_hidden(video_file):
            hidden += 1
            continue
        stem = video_file.stem.replace("_SUBBED", "")
        if not stem:
            continue
        job = omega_db.get_job_via_track(stem)
        if job:
            continue
        if len(results) < limit:
            results.append({"job_id": stem, "path": str(video_file)})
    return {"scanned": scanned, "hidden": hidden, "orphans": results}


def _find_stale_sync_states(stale_minutes: int, limit: int = 25) -> Dict[str, Any]:
    rows = omega_db.get_pending_syncs("approved_json") or []
    cutoff = datetime.now() - timedelta(minutes=stale_minutes)
    stale: List[Dict[str, str]] = []
    for row in rows:
        updated = _as_naive(_parse_iso(row.get("updated_at")))
        if not updated or updated >= cutoff:
            continue
        if len(stale) < limit:
            stale.append(
                {
                    "job_id": str(row.get("id")),
                    "state": str(row.get("state") or ""),
                    "updated_at": str(row.get("updated_at") or ""),
                }
            )
    return {"total_pending": len(rows), "stale": stale}


def _find_stalled_jobs(limit: int = 25) -> Dict[str, Any]:
    # Conservative defaults tuned for cloud-stage jobs.
    stage_limits = {
        "TRANSLATING_CLOUD_SUBMITTED": int(os.environ.get("OMEGA_AUDIT_MAX_SUBMITTED_MIN", "120")),
        "CLOUD_TRANSLATING": int(os.environ.get("OMEGA_AUDIT_MAX_TRANSLATING_MIN", "180")),
        "CLOUD_REVIEWING": int(os.environ.get("OMEGA_AUDIT_MAX_REVIEWING_MIN", "240")),
        "FINALIZING": int(os.environ.get("OMEGA_AUDIT_MAX_FINALIZING_MIN", "240")),
        "BURNING": int(os.environ.get("OMEGA_AUDIT_MAX_BURNING_MIN", "360")),
    }
    jobs = omega_db.get_all_jobs_via_tracks(stages=list(stage_limits.keys()), limit=1000) or []
    now = datetime.now()
    stale: List[Dict[str, str]] = []

    for job in jobs:
        stage = str(job.get("stage") or "").upper()
        if stage not in stage_limits:
            continue
        updated = _as_naive(_parse_iso(job.get("updated_at")))
        if not updated:
            continue
        max_minutes = stage_limits[stage]
        if (now - updated).total_seconds() < (max_minutes * 60):
            continue
        if len(stale) < limit:
            stale.append(
                {
                    "job_id": str(job.get("file_stem") or ""),
                    "stage": stage,
                    "updated_at": str(job.get("updated_at") or ""),
                    "status": str(job.get("status") or ""),
                }
            )

    return {"checked_jobs": len(jobs), "stalled": stale}


def _find_dead_jobs(limit: int = 25) -> Dict[str, Any]:
    jobs = omega_db.get_all_jobs_via_tracks(stages=["DEAD"], limit=1000) or []
    dead: List[Dict[str, str]] = []
    for job in jobs:
        if len(dead) >= limit:
            break
        stem = str(job.get("file_stem") or "")
        if not stem:
            continue
        dead.append(
            {
                "job_id": stem,
                "status": str(job.get("status") or ""),
                "updated_at": str(job.get("updated_at") or ""),
            }
        )
    return {"count": len(jobs), "samples": dead}


def _find_suspicious_approved_payloads(limit: int = 50) -> Dict[str, Any]:
    scanned = 0
    suspicious: List[Dict[str, Any]] = []
    approved_dir = Path(config.TRANSLATED_DONE_DIR)
    if not approved_dir.exists():
        return {
            "scanned": 0,
            "suspicious_total": 0,
            "suspicious_active": 0,
            "suspicious_terminal": 0,
            "samples": [],
        }

    for approved_path in sorted(approved_dir.glob("*_APPROVED.json")):
        scanned += 1
        if _is_hidden(approved_path):
            continue
        try:
            payload = json.loads(approved_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        if not isinstance(segments, list):
            continue

        job_id = approved_path.name.replace("_APPROVED.json", "")
        job = omega_db.get_job_via_track(job_id) or {}
        target_language = str(job.get("target_language") or "is").strip().lower() or "is"
        integrity = detect_likely_t_escape_corruption(
            segments,
            target_language=target_language,
        )
        if not integrity.get("suspicious"):
            continue

        stage = str(job.get("stage") or "")
        stage_upper = stage.upper()
        row = {
            "job_id": job_id,
            "stage": stage,
            "status": str(job.get("status") or ""),
            "target_language": target_language,
            "path": str(approved_path),
            "t_ratio": round(float((integrity.get("text") or {}).get("t_ratio") or 0.0), 6),
            "letters": int((integrity.get("text") or {}).get("letter_count") or 0),
            "reason": str(integrity.get("reason") or ""),
            "is_active": stage_upper not in TERMINAL_STAGES,
        }
        suspicious.append(row)

    suspicious_active = sum(1 for row in suspicious if row.get("is_active"))
    suspicious_terminal = len(suspicious) - suspicious_active
    return {
        "scanned": scanned,
        "suspicious_total": len(suspicious),
        "suspicious_active": suspicious_active,
        "suspicious_terminal": suspicious_terminal,
        "samples": suspicious[:limit],
    }


def _apply_thresholds(report: Dict[str, Any], thresholds: Dict[str, Optional[int]], breach_level: str) -> None:
    checks = report.get("checks", {})
    orphan_count = len(((checks.get("orphan_deliveries") or {}).get("orphans") or []))
    stale_sync_count = len(((checks.get("sync_states") or {}).get("stale") or []))
    stalled_count = len(((checks.get("stalled_jobs") or {}).get("stalled") or []))
    dead_count = int((checks.get("dead_jobs") or {}).get("count") or 0)
    suspicious_active = int((checks.get("approved_text_integrity") or {}).get("suspicious_active") or 0)
    suspicious_terminal = int((checks.get("approved_text_integrity") or {}).get("suspicious_terminal") or 0)

    breaches = []
    max_orphans = thresholds.get("max_orphans")
    max_stale_syncs = thresholds.get("max_stale_syncs")
    max_stalled_jobs = thresholds.get("max_stalled_jobs")
    max_dead_jobs = thresholds.get("max_dead_jobs")
    max_suspicious_active = thresholds.get("max_suspicious_active")
    max_suspicious_terminal = thresholds.get("max_suspicious_terminal")

    if max_orphans is not None and orphan_count > max_orphans:
        breaches.append(f"orphan_deliveries={orphan_count} > max_orphans={max_orphans}")
    if max_stale_syncs is not None and stale_sync_count > max_stale_syncs:
        breaches.append(f"stale_syncs={stale_sync_count} > max_stale_syncs={max_stale_syncs}")
    if max_stalled_jobs is not None and stalled_count > max_stalled_jobs:
        breaches.append(f"stalled_jobs={stalled_count} > max_stalled_jobs={max_stalled_jobs}")
    if max_dead_jobs is not None and dead_count > max_dead_jobs:
        breaches.append(f"dead_jobs={dead_count} > max_dead_jobs={max_dead_jobs}")
    if max_suspicious_active is not None and suspicious_active > max_suspicious_active:
        breaches.append(
            f"suspicious_active={suspicious_active} > max_suspicious_active={max_suspicious_active}"
        )
    if max_suspicious_terminal is not None and suspicious_terminal > max_suspicious_terminal:
        breaches.append(
            f"suspicious_terminal={suspicious_terminal} > max_suspicious_terminal={max_suspicious_terminal}"
        )

    checks["thresholds"] = {
        **thresholds,
        "breach_level": breach_level,
        "breaches": breaches,
    }
    report["checks"] = checks
    if not breaches:
        return

    msg = f"Threshold breach(es): {', '.join(breaches)}"
    if breach_level == "critical":
        report["critical"].append(msg)
    else:
        report["warnings"].append(msg)


def _notify_if_requested(report: Dict[str, Any]) -> None:
    problems = []
    for message in report.get("critical") or []:
        problems.append({"stem": "system", "description": f"CRITICAL: {message}"})
    for message in report.get("warnings") or []:
        problems.append({"stem": "system", "description": f"WARNING: {message}"})
    if not problems:
        return
    try:
        from notification_manager import NotificationManager

        NotificationManager.notify_system_health(
            problems=problems,
            fixed=[],
            dashboard_url=os.environ.get("OMEGA_DASHBOARD_URL", "http://127.0.0.1:8080"),
        )
    except Exception as exc:
        report["warnings"].append(f"Notification attempt failed: {exc}")


def run_audit(
    stale_sync_minutes: int,
    thresholds: Dict[str, Optional[int]],
    breach_level: str,
    notify: bool = False,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "status": "ok",
        "critical": [],
        "warnings": [],
        "checks": {},
    }

    try:
        omega_db.ensure_schema()
    except Exception as exc:
        report["status"] = "critical"
        report["critical"].append(f"DB schema check failed: {exc}")
        return report

    try:
        orphans = _find_orphan_deliveries()
        report["checks"]["orphan_deliveries"] = orphans
        if orphans["orphans"]:
            report["warnings"].append(f"Found {len(orphans['orphans'])} orphan delivery file(s)")
    except Exception as exc:
        report["critical"].append(f"Delivery orphan check failed: {exc}")

    try:
        sync_states = _find_stale_sync_states(stale_sync_minutes)
        report["checks"]["sync_states"] = sync_states
        if sync_states["stale"]:
            report["warnings"].append(f"Found {len(sync_states['stale'])} stale sync state(s)")
    except Exception as exc:
        report["critical"].append(f"Sync-state check failed: {exc}")

    try:
        stalled = _find_stalled_jobs()
        report["checks"]["stalled_jobs"] = stalled
        if stalled["stalled"]:
            report["warnings"].append(f"Found {len(stalled['stalled'])} stalled job(s)")
    except Exception as exc:
        report["critical"].append(f"Stalled-job check failed: {exc}")

    try:
        dead = _find_dead_jobs()
        report["checks"]["dead_jobs"] = dead
        if dead["count"]:
            report["warnings"].append(f"Found {dead['count']} DEAD job(s)")
    except Exception as exc:
        report["critical"].append(f"Dead-job check failed: {exc}")

    try:
        integrity = _find_suspicious_approved_payloads()
        report["checks"]["approved_text_integrity"] = integrity
        if int(integrity.get("suspicious_active") or 0) > 0:
            report["critical"].append(
                f"Found {integrity['suspicious_active']} suspicious approved payload(s) on non-terminal jobs"
            )
    except Exception as exc:
        report["critical"].append(f"Approved-text integrity check failed: {exc}")

    _apply_thresholds(report, thresholds=thresholds, breach_level=breach_level)

    if report["critical"]:
        report["status"] = "critical"
    elif report["warnings"]:
        report["status"] = "warning"

    if notify:
        _notify_if_requested(report)

    return report


def _exit_code(status: str) -> int:
    if status == "ok":
        return 0
    if status == "warning":
        return 1
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local Omega pipeline invariants.")
    parser.add_argument(
        "--stale-sync-minutes",
        type=int,
        default=int(os.environ.get("OMEGA_AUDIT_SYNC_STALE_MIN", "15")),
        help="Mark pending sync states older than this as stale (default: 15).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument(
        "--max-orphans",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_ORPHANS"),
        help="Maximum allowed orphan delivery files before threshold breach (default: disabled).",
    )
    parser.add_argument(
        "--max-stale-syncs",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_STALE_SYNCS"),
        help="Maximum allowed stale sync states before threshold breach (default: disabled).",
    )
    parser.add_argument(
        "--max-stalled-jobs",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_STALLED_JOBS"),
        help="Maximum allowed stalled jobs before threshold breach (default: disabled).",
    )
    parser.add_argument(
        "--max-dead-jobs",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_DEAD_JOBS"),
        help="Maximum allowed DEAD jobs before threshold breach (default: disabled).",
    )
    parser.add_argument(
        "--max-suspicious-active",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_SUSPICIOUS_ACTIVE"),
        help="Maximum allowed suspicious approved payloads on non-terminal jobs (default: disabled).",
    )
    parser.add_argument(
        "--max-suspicious-terminal",
        type=int,
        default=_env_threshold("OMEGA_AUDIT_MAX_SUSPICIOUS_TERMINAL"),
        help="Maximum allowed suspicious approved payloads on terminal jobs (default: disabled).",
    )
    parser.add_argument(
        "--breach-level",
        choices=["warning", "critical"],
        default=str(os.environ.get("OMEGA_AUDIT_BREACH_LEVEL", "warning")).strip().lower() or "warning",
        help="Classify threshold breaches as warning or critical (default: warning).",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send notification email for warnings/critical findings (if operator email configured).",
    )
    args = parser.parse_args()

    thresholds = {
        "max_orphans": (None if args.max_orphans is None else max(0, int(args.max_orphans))),
        "max_stale_syncs": (None if args.max_stale_syncs is None else max(0, int(args.max_stale_syncs))),
        "max_stalled_jobs": (None if args.max_stalled_jobs is None else max(0, int(args.max_stalled_jobs))),
        "max_dead_jobs": (None if args.max_dead_jobs is None else max(0, int(args.max_dead_jobs))),
        "max_suspicious_active": (
            None if args.max_suspicious_active is None else max(0, int(args.max_suspicious_active))
        ),
        "max_suspicious_terminal": (
            None if args.max_suspicious_terminal is None else max(0, int(args.max_suspicious_terminal))
        ),
    }
    report = run_audit(
        stale_sync_minutes=max(1, int(args.stale_sync_minutes)),
        thresholds=thresholds,
        breach_level=args.breach_level,
        notify=bool(args.notify),
    )
    if args.pretty:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return _exit_code(report["status"])


if __name__ == "__main__":
    sys.exit(main())
