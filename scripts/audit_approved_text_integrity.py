#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import omega_db
from workers.text_sanitizer import detect_likely_t_escape_corruption

TERMINAL_STAGES = {"COMPLETED", "COMPLETE", "DELIVERED", "FAILED", "DEAD"}


def iter_approved_files(root: Path) -> Iterable[Path]:
    return sorted(root.glob("*_APPROVED.json"))


def load_segments(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        segments = payload.get("segments", [])
        return segments if isinstance(segments, list) else []
    return payload if isinstance(payload, list) else []


def infer_language(segments, fallback: str) -> str:
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        lang = seg.get("target_language") or seg.get("language") or seg.get("lang")
        if isinstance(lang, str) and lang.strip():
            return lang.strip().lower()
    return fallback


def extract_job_id(path: Path) -> str:
    return path.name.replace("_APPROVED.json", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit approved subtitle payloads for likely tab-escape corruption.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path("3_TRANSLATED_DONE"),
        help="Directory containing *_APPROVED.json files.",
    )
    parser.add_argument(
        "--default-language",
        default="is",
        help="Fallback language code when payload has no language hint (default: is).",
    )
    parser.add_argument(
        "--min-letters",
        type=int,
        default=3000,
        help="Minimum alphabetic letters before flagging.",
    )
    parser.add_argument(
        "--min-t-ratio",
        type=float,
        default=0.01,
        help="Minimum lowercase t ratio for enabled languages.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional path to write a JSON report.",
    )
    parser.add_argument(
        "--mark-db",
        action="store_true",
        help="Mark suspicious jobs in DB meta with qa_text_integrity_suspicious=true.",
    )
    args = parser.parse_args()

    root = args.dir
    if not root.exists():
        print(f"Directory not found: {root}")
        return 2

    suspicious = []
    checked = 0
    for approved_path in iter_approved_files(root):
        checked += 1
        try:
            segments = load_segments(approved_path)
        except Exception as exc:
            print(f"[ERROR] {approved_path.name}: {exc}")
            continue
        language = infer_language(segments, fallback=args.default_language)
        job_id = extract_job_id(approved_path)
        job = omega_db.get_job_via_track(job_id) or {}
        stage = str(job.get("stage") or "")
        status = str(job.get("status") or "")
        report = detect_likely_t_escape_corruption(
            segments,
            target_language=language,
            min_letters=args.min_letters,
            min_t_ratio=args.min_t_ratio,
        )
        if report.get("suspicious"):
            text_stats = report.get("text", {})
            suspicious.append(
                {
                    "job_id": job_id,
                    "path": str(approved_path),
                    "target_language": language,
                    "stage": stage,
                    "status": status,
                    "is_terminal": stage.upper() in TERMINAL_STAGES,
                    "t_ratio": float(text_stats.get("t_ratio") or 0.0),
                    "letters": int(text_stats.get("letter_count") or 0),
                    "t_count": int(text_stats.get("t_count") or 0),
                    "reason": str(report.get("reason") or ""),
                    "integrity": report,
                }
            )

    terminal_count = sum(1 for row in suspicious if row.get("is_terminal"))
    active_count = len(suspicious) - terminal_count
    print(f"Checked {checked} approved payload(s).")
    if not suspicious:
        print("No suspicious payloads detected.")
        if args.report_path:
            args.report_path.parent.mkdir(parents=True, exist_ok=True)
            args.report_path.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now().isoformat(),
                        "checked": checked,
                        "suspicious_total": 0,
                        "suspicious_active": 0,
                        "suspicious_terminal": 0,
                        "jobs": [],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(f"Wrote report: {args.report_path}")
        return 0

    print(f"Suspicious payloads: {len(suspicious)}")
    print(f"  active/non-terminal: {active_count}")
    print(f"  terminal: {terminal_count}")
    for row in suspicious:
        print(
            f"- {Path(row['path']).name} [{row['target_language']}] stage={row['stage'] or '<none>'} "
            f"t_ratio={row['t_ratio']:.6f} "
            f"letters={row['letters']} "
            f"t={row['t_count']} "
            f"reason={row['reason']}"
        )

    report_payload = {
        "generated_at": datetime.now().isoformat(),
        "checked": checked,
        "suspicious_total": len(suspicious),
        "suspicious_active": active_count,
        "suspicious_terminal": terminal_count,
        "jobs": suspicious,
    }
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(
            json.dumps(report_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote report: {args.report_path}")

    if args.mark_db:
        flagged = 0
        failed = 0
        flagged_at = datetime.now().isoformat()
        for row in suspicious:
            try:
                omega_db.update_job_via_track(
                    row["job_id"],
                    meta={
                        "qa_text_integrity_suspicious": True,
                        "qa_text_integrity_flagged_at": flagged_at,
                        "qa_text_integrity_report": {
                            "reason": row["reason"],
                            "t_ratio": row["t_ratio"],
                            "letters": row["letters"],
                            "target_language": row["target_language"],
                            "approved_path": row["path"],
                        },
                    },
                )
                flagged += 1
            except Exception as exc:
                failed += 1
                print(f"[WARN] Failed to mark {row['job_id']}: {exc}")
        print(f"DB marking complete: flagged={flagged} failed={failed}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
