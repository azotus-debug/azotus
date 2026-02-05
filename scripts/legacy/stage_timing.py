"""
Stage Timing Instrumentation

Analyzes stage_timeline data from jobs to calculate p50/p90 stage durations.
The infrastructure already exists in omega_db.update_job_via_track() which records started_at
and ended_at for each stage transition.

This module provides:
- extract_stage_durations(): Parse job meta to get durations per stage
- compute_percentiles(): Calculate p50/p90 for a list of durations
- generate_timing_report(): Full report across all jobs
"""

import json
import statistics
from datetime import datetime
from typing import Optional
import omega_db


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        return None


def extract_stage_durations(job: dict) -> dict[str, float]:
    """
    Extract stage durations (in seconds) from a job's stage_timeline.
    
    Returns dict: {stage_name: duration_seconds}
    """
    meta = job.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    
    timeline = meta.get("stage_timeline") or []
    if not isinstance(timeline, list):
        return {}
    
    durations = {}
    for entry in timeline:
        if not isinstance(entry, dict):
            continue
        stage = entry.get("stage")
        started_at = _parse_iso(entry.get("started_at"))
        ended_at = _parse_iso(entry.get("ended_at"))
        
        if not stage or not started_at:
            continue
        
        # If ended_at is missing, stage is still in progress
        if ended_at:
            duration = (ended_at - started_at).total_seconds()
            if duration >= 0:
                durations[stage] = duration
    
    return durations


def compute_percentiles(values: list[float]) -> dict[str, float]:
    """
    Compute p50 (median) and p90 for a list of values.
    
    Returns: {"p50": float, "p90": float, "min": float, "max": float, "count": int}
    """
    if not values:
        return {"p50": 0, "p90": 0, "min": 0, "max": 0, "count": 0}
    
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    # p50 = median
    p50 = statistics.median(sorted_vals)
    
    # p90 = 90th percentile
    p90_idx = int(n * 0.9)
    p90 = sorted_vals[min(p90_idx, n - 1)]
    
    return {
        "p50": round(p50, 2),
        "p90": round(p90, 2),
        "min": round(min(sorted_vals), 2),
        "max": round(max(sorted_vals), 2),
        "count": n
    }


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = seconds / 60
        return f"{mins:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.2f}h"


def generate_timing_report(limit: int = 500) -> dict:
    """
    Generate a timing report across all completed jobs.
    
    Returns:
    {
        "generated_at": "ISO timestamp",
        "jobs_analyzed": int,
        "stages": {
            "STAGE_NAME": {
                "p50": float,
                "p90": float,
                "min": float,
                "max": float,
                "count": int,
                "p50_formatted": str,
                "p90_formatted": str
            },
            ...
        }
    }
    """
    omega_db.ensure_schema()
    jobs = omega_db.get_all_jobs_via_tracks()[:limit]
    
    # Aggregate durations by stage
    stage_durations: dict[str, list[float]] = {}
    
    for job in jobs:
        durations = extract_stage_durations(job)
        for stage, duration in durations.items():
            if stage not in stage_durations:
                stage_durations[stage] = []
            stage_durations[stage].append(duration)
    
    # Compute percentiles for each stage
    stages_report = {}
    for stage, durations in sorted(stage_durations.items()):
        stats = compute_percentiles(durations)
        stats["p50_formatted"] = _format_duration(stats["p50"])
        stats["p90_formatted"] = _format_duration(stats["p90"])
        stages_report[stage] = stats
    
    return {
        "generated_at": datetime.now().isoformat(),
        "jobs_analyzed": len(jobs),
        "stages": stages_report
    }


def print_timing_report():
    """Print a nicely formatted timing report to stdout."""
    report = generate_timing_report()
    
    print(f"\n{'='*60}")
    print(f"STAGE TIMING REPORT")
    print(f"Generated: {report['generated_at']}")
    print(f"Jobs Analyzed: {report['jobs_analyzed']}")
    print(f"{'='*60}\n")
    
    if not report["stages"]:
        print("No stage timing data available.")
        return
    
    # Table header
    print(f"{'Stage':<30} {'Count':>6} {'p50':>10} {'p90':>10} {'Min':>10} {'Max':>10}")
    print("-" * 80)
    
    for stage, stats in report["stages"].items():
        print(f"{stage:<30} {stats['count']:>6} {stats['p50_formatted']:>10} {stats['p90_formatted']:>10} {_format_duration(stats['min']):>10} {_format_duration(stats['max']):>10}")
    
    print()


if __name__ == "__main__":
    print_timing_report()
