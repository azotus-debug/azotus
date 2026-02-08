#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent


def _run(cmd: List[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _tail(text: str, lines: int = 25) -> str:
    raw = (text or "").strip().splitlines()
    if not raw:
        return ""
    return "\n".join(raw[-lines:])


def _build_audit_command(args: argparse.Namespace) -> List[str]:
    cmd = ["python3", "scripts/pipeline_audit.py", "--pretty"]
    if args.audit_stale_sync_minutes is not None:
        cmd.extend(["--stale-sync-minutes", str(args.audit_stale_sync_minutes)])
    if args.audit_max_orphans is not None:
        cmd.extend(["--max-orphans", str(args.audit_max_orphans)])
    if args.audit_max_stale_syncs is not None:
        cmd.extend(["--max-stale-syncs", str(args.audit_max_stale_syncs)])
    if args.audit_max_stalled_jobs is not None:
        cmd.extend(["--max-stalled-jobs", str(args.audit_max_stalled_jobs)])
    if args.audit_max_dead_jobs is not None:
        cmd.extend(["--max-dead-jobs", str(args.audit_max_dead_jobs)])
    if args.audit_breach_level:
        cmd.extend(["--breach-level", args.audit_breach_level])
    if args.audit_notify:
        cmd.append("--notify")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Omega rollout quality gate checks.")
    parser.add_argument(
        "--fail-on-audit-warning",
        action="store_true",
        help="Treat pipeline audit warnings (exit 1) as gate failures.",
    )
    parser.add_argument("--skip-tests", action="store_true", help="Skip unit tests.")
    parser.add_argument("--skip-audit", action="store_true", help="Skip pipeline audit.")
    parser.add_argument("--audit-notify", action="store_true", help="Send audit notifications.")
    parser.add_argument("--audit-stale-sync-minutes", type=int, default=None)
    parser.add_argument("--audit-max-orphans", type=int, default=None)
    parser.add_argument("--audit-max-stale-syncs", type=int, default=None)
    parser.add_argument("--audit-max-stalled-jobs", type=int, default=None)
    parser.add_argument("--audit-max-dead-jobs", type=int, default=None)
    parser.add_argument("--audit-breach-level", choices=["warning", "critical"], default=None)
    args = parser.parse_args()

    started = time.time()
    steps = []
    gate_failed = False

    compile_targets = [
        "cloud_sync_service.py",
        "omega_cloud_worker.py",
        "omega_db.py",
        "omega_manager.py",
        "state_machine.py",
        "transition_service.py",
        "watchdog_supervisor.py",
        "workers/finalizer.py",
        "scripts/pipeline_audit.py",
        "scripts/failure_injection.py",
        "scripts/rollout_gate.py",
    ]
    compile_cmd = ["python3", "-m", "py_compile", *compile_targets]
    compile_proc = _run(compile_cmd, cwd=ROOT_DIR)
    compile_status = "ok" if compile_proc.returncode == 0 else "failed"
    steps.append(
        {
            "name": "compile",
            "status": compile_status,
            "code": compile_proc.returncode,
            "cmd": " ".join(compile_cmd),
            "output_tail": _tail((compile_proc.stdout or "") + "\n" + (compile_proc.stderr or "")),
        }
    )
    if compile_proc.returncode != 0:
        gate_failed = True

    if not args.skip_tests:
        tests_cmd = ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
        tests_proc = _run(tests_cmd, cwd=ROOT_DIR)
        tests_status = "ok" if tests_proc.returncode == 0 else "failed"
        steps.append(
            {
                "name": "unit_tests",
                "status": tests_status,
                "code": tests_proc.returncode,
                "cmd": " ".join(tests_cmd),
                "output_tail": _tail((tests_proc.stdout or "") + "\n" + (tests_proc.stderr or "")),
            }
        )
        if tests_proc.returncode != 0:
            gate_failed = True
    else:
        steps.append({"name": "unit_tests", "status": "skipped", "code": None, "cmd": None, "output_tail": ""})

    failure_injection_cmd = ["python3", "scripts/failure_injection.py"]
    fi_proc = _run(failure_injection_cmd, cwd=ROOT_DIR)
    fi_status = "ok" if fi_proc.returncode == 0 else "failed"
    steps.append(
        {
            "name": "failure_injection",
            "status": fi_status,
            "code": fi_proc.returncode,
            "cmd": " ".join(failure_injection_cmd),
            "output_tail": _tail((fi_proc.stdout or "") + "\n" + (fi_proc.stderr or "")),
        }
    )
    if fi_proc.returncode != 0:
        gate_failed = True

    if not args.skip_audit:
        audit_cmd = _build_audit_command(args)
        audit_proc = _run(audit_cmd, cwd=ROOT_DIR)
        if audit_proc.returncode == 0:
            audit_status = "ok"
        elif audit_proc.returncode == 1 and not args.fail_on_audit_warning:
            audit_status = "warning"
        else:
            audit_status = "failed"
        steps.append(
            {
                "name": "pipeline_audit",
                "status": audit_status,
                "code": audit_proc.returncode,
                "cmd": " ".join(audit_cmd),
                "output_tail": _tail((audit_proc.stdout or "") + "\n" + (audit_proc.stderr or "")),
            }
        )
        if audit_status == "failed":
            gate_failed = True
    else:
        steps.append({"name": "pipeline_audit", "status": "skipped", "code": None, "cmd": None, "output_tail": ""})

    payload = {
        "status": "failed" if gate_failed else "ok",
        "duration_seconds": round(time.time() - started, 2),
        "root_dir": str(ROOT_DIR),
        "steps": steps,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
