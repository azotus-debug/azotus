#!/usr/bin/env python3
import argparse
import json
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
import omega_db


@dataclass
class Action:
    source: str
    classification: str
    target: Optional[str]
    applied: bool
    note: str


def _is_hidden(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name.startswith("._")


def _stem_from_video(path: Path) -> str:
    name = path.stem
    if name.endswith("_SUBBED"):
        return name[: -len("_SUBBED")]
    return name


def _plan_action(video_file: Path, delivery_dir: Path, archive_dir: Path) -> Optional[Action]:
    stem = _stem_from_video(video_file)
    if not stem:
        return None

    direct_job = omega_db.get_job_via_track(stem)
    if direct_job:
        return None

    # Handle legacy files prefixed with DONE_ that should map to canonical job_id.
    if stem.startswith("DONE_"):
        stripped_stem = stem[5:]
        stripped_job = omega_db.get_job_via_track(stripped_stem)
        if stripped_job:
            canonical = delivery_dir / f"{stripped_stem}_SUBBED.mp4"
            if canonical.exists():
                return Action(
                    source=str(video_file),
                    classification="done_duplicate_archive",
                    target=str(archive_dir / video_file.name),
                    applied=False,
                    note="Canonical file already exists; archive duplicate DONE_ file.",
                )
            return Action(
                source=str(video_file),
                classification="done_rename_to_canonical",
                target=str(canonical),
                applied=False,
                note="Canonical file missing; rename DONE_ file to canonical DB job_id filename.",
            )

    # No DB record found -> keep file, but move out of active delivery scan.
    return Action(
        source=str(video_file),
        classification="no_db_orphan_archive",
        target=str(archive_dir / video_file.name),
        applied=False,
        note="No matching job record found; archive to keep active delivery folder clean.",
    )


def _execute_action(action: Action) -> Action:
    src = Path(action.source)
    dst = Path(action.target or "")
    if not src.exists():
        action.note = f"{action.note} Source missing at execution time."
        action.applied = False
        return action

    if action.target:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            action.note = f"{action.note} Target already exists; skipped."
            action.applied = False
            return action
        shutil.move(str(src), str(dst))
        action.applied = True
        return action

    action.applied = False
    action.note = f"{action.note} No target set; skipped."
    return action


def reconcile(apply: bool, archive_dir: Path, write_manifest: bool) -> dict:
    omega_db.ensure_schema()
    delivery_dir = Path(config.DELIVERY_DIR) / "VIDEO"
    if not delivery_dir.exists():
        return {
            "generated_at": datetime.now().isoformat(),
            "status": "ok",
            "delivery_dir": str(delivery_dir),
            "message": "Delivery folder missing; nothing to do.",
            "actions": [],
            "summary": {"planned": 0, "applied": 0},
        }

    planned: List[Action] = []
    for video_file in sorted(delivery_dir.glob("*_SUBBED.mp4")):
        if _is_hidden(video_file):
            continue
        action = _plan_action(video_file, delivery_dir, archive_dir)
        if action:
            planned.append(action)

    executed: List[Action] = []
    if apply:
        for action in planned:
            executed.append(_execute_action(action))
    else:
        executed = planned

    summary = {
        "planned": len(planned),
        "applied": sum(1 for a in executed if a.applied),
        "by_classification": {},
    }
    for a in executed:
        summary["by_classification"][a.classification] = summary["by_classification"].get(a.classification, 0) + 1

    report = {
        "generated_at": datetime.now().isoformat(),
        "status": "ok",
        "mode": "apply" if apply else "dry-run",
        "delivery_dir": str(delivery_dir),
        "archive_dir": str(archive_dir),
        "summary": summary,
        "actions": [asdict(a) for a in executed],
    }

    if write_manifest and apply:
        archive_dir.mkdir(parents=True, exist_ok=True)
        manifest = archive_dir / "reconcile_manifest.json"
        manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        report["manifest"] = str(manifest)

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile orphan delivery videos by DB lookup (safe dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply filesystem changes (move/rename). Default is dry-run.",
    )
    parser.add_argument(
        "--archive-dir",
        default="",
        help="Archive folder path for orphan files. Default: 4_DELIVERY/VIDEO/ORPHAN_ARCHIVE_YYYYMMDD",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Do not write reconcile_manifest.json in archive dir during --apply.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    default_archive = Path(config.DELIVERY_DIR) / "VIDEO" / f"ORPHAN_ARCHIVE_{datetime.now():%Y%m%d}"
    archive_dir = Path(args.archive_dir).expanduser().resolve() if args.archive_dir else default_archive

    report = reconcile(
        apply=args.apply,
        archive_dir=archive_dir,
        write_manifest=not args.no_manifest,
    )
    if args.pretty:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
