#!/usr/bin/env python3
"""
backup_artifacts.py - Daily backup of logs, reports, and job data

Writes to: /Volumes/Extreme SSD/Omega_Backups/{date}/
Run manually or via cron/launchd.

Usage:
    python backup_artifacts.py
    python backup_artifacts.py --dry-run
"""

import os
import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Configuration
DEFAULT_BACKUP_ROOT = Path("/Volumes/Extreme SSD/Omega_Backups")
BASE_DIR = Path(__file__).resolve().parent

# What to back up
BACKUP_SOURCES = [
    ("logs", BASE_DIR / "logs"),
    ("heartbeats", BASE_DIR / "heartbeats"),
    ("production_db", BASE_DIR / "production.db"),
    ("omega_db", BASE_DIR / "omega.db"),
]


def get_dated_backup_dir(root: Path = DEFAULT_BACKUP_ROOT) -> Path:
    """Returns backup directory for today: {root}/{YYYY-MM-DD}/"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    return root / date_str


def check_external_storage(root: Path) -> bool:
    """Checks if external SSD is mounted and writable."""
    try:
        # Check if mount point exists
        if not root.parent.exists():
            print(f"❌ External storage not found: {root.parent}")
            return False
        
        # Check if we can write
        root.mkdir(parents=True, exist_ok=True)
        test_file = root / ".omega_backup_test"
        test_file.write_text("test")
        test_file.unlink()
        return True
    except Exception as e:
        print(f"❌ Cannot write to {root}: {e}")
        return False


def backup_directory(src: Path, dst: Path, dry_run: bool = False) -> int:
    """Recursively copies a directory. Returns file count."""
    if not src.exists():
        return 0
    
    count = 0
    if src.is_file():
        if dry_run:
            print(f"  [DRY] {src} -> {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return 1
    
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            if dry_run:
                print(f"  [DRY] {item} -> {target}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            count += 1
    return count


def export_job_summary(backup_dir: Path, dry_run: bool = False) -> None:
    """Exports a summary of all jobs from the database."""
    try:
        # Import here to avoid circular deps
        sys.path.insert(0, str(BASE_DIR))
        import omega_db
        
        jobs = omega_db.get_all_jobs_via_tracks()
        summary = []
        for job in jobs:
            summary.append({
                "file_stem": job.get("file_stem"),
                "stage": job.get("stage"),
                "status": job.get("status"),
                "progress": job.get("progress"),
                "updated_at": job.get("updated_at"),
            })
        
        if dry_run:
            print(f"  [DRY] Would export {len(summary)} jobs to jobs_summary.json")
        else:
            summary_path = backup_dir / "jobs_summary.json"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"  ✅ Exported {len(summary)} jobs to jobs_summary.json")
    except Exception as e:
        print(f"  ⚠️ Could not export job summary: {e}")


def main():
    parser = argparse.ArgumentParser(description="Backup Omega artifacts to external SSD")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be backed up")
    parser.add_argument("--output", type=str, help="Override backup root directory")
    args = parser.parse_args()
    
    backup_root = Path(args.output) if args.output else DEFAULT_BACKUP_ROOT
    
    print(f"🗄️  Omega Artifact Backup")
    print(f"   Source: {BASE_DIR}")
    print(f"   Target: {backup_root}")
    print()
    
    if not args.dry_run and not check_external_storage(backup_root):
        print("\n❌ Backup aborted: storage not available")
        return 1
    
    backup_dir = get_dated_backup_dir(backup_root)
    if not args.dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
    print(f"   Backup folder: {backup_dir}")
    print()
    
    total_files = 0
    
    # Back up each source
    for name, src in BACKUP_SOURCES:
        if not src.exists():
            print(f"  ⏭️  Skipping {name} (not found)")
            continue
        
        dst = backup_dir / name
        count = backup_directory(src, dst, dry_run=args.dry_run)
        total_files += count
        if not args.dry_run:
            print(f"  ✅ {name}: {count} files")
    
    # Export job summary
    export_job_summary(backup_dir, dry_run=args.dry_run)
    
    print()
    if args.dry_run:
        print(f"[DRY RUN] Would back up {total_files} files")
    else:
        print(f"✅ Backup complete: {total_files} files to {backup_dir}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
