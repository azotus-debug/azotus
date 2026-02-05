#!/usr/bin/env python3
"""
migrate_folders.py - Migrate from flat to project-based folder structure

This script migrates existing files from:
  - 2_VAULT/Videos/{file}
  - 2_VAULT/Data/{file}
  
To the new project-based structure:
  - 2_VAULT/{YYYY-MM}/{stem}/source/{video}
  - 2_VAULT/{YYYY-MM}/{stem}/data/{data_files}
  - 2_VAULT/{YYYY-MM}/{stem}/subtitles/{srt,ass}

Usage:
    python migrate_folders.py --dry-run     # Preview only
    python migrate_folders.py               # Execute migration
"""

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Import from config
import sys
sys.path.insert(0, str(Path(__file__).parent))
import config

# Patterns to extract stem from data filenames
DATA_SUFFIXES = [
    "_SKELETON_DONE.json",
    "_SKELETON.json",
    "_APPROVED.json",
    "_TRANSLATED.json",
    "_CUTS.json",
    ".ass",
    ".srt",
]


def extract_stem(filename: str) -> str:
    """Extract the canonical stem from a filename."""
    name = filename
    
    # Remove known suffixes
    for suffix in DATA_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    
    # Remove backup extensions like .bak_1234567890.json
    if ".bak_" in name:
        name = re.sub(r"\.bak_\d+\.json$", "", name)
    
    # Remove common extensions
    name = re.sub(r"\.(json|ass|srt|mp4|mov|mkv|avi|mxf|mpg|webm)$", "", name, flags=re.IGNORECASE)
    
    return name


def get_file_month(filepath: Path) -> str:
    """Get the month folder name based on file modification time."""
    try:
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%Y-%m")
    except Exception:
        return datetime.now().strftime("%Y-%m")


def categorize_file(filename: str) -> str:
    """Determine which subfolder a file belongs to."""
    lower = filename.lower()
    
    if lower.endswith((".mp4", ".mov", ".mkv", ".avi", ".mxf", ".mpg", ".webm")):
        return "source"
    elif lower.endswith((".ass", ".srt")):
        return "subtitles"
    else:
        return "data"


def scan_vault() -> dict:
    """
    Scan VAULT_DATA and VAULT_VIDEOS, group files by stem.
    
    Returns:
        Dict[stem, {"video": Path or None, "data": [Path, ...], "month": str}]
    """
    programs = defaultdict(lambda: {"video": None, "data": [], "month": None})
    
    # Scan videos
    if config.VAULT_VIDEOS.exists():
        for f in config.VAULT_VIDEOS.iterdir():
            if f.is_file() and not f.name.startswith("."):
                stem = extract_stem(f.name)
                programs[stem]["video"] = f
                programs[stem]["month"] = get_file_month(f)
    
    # Scan data
    if config.VAULT_DATA.exists():
        for f in config.VAULT_DATA.iterdir():
            if f.is_file() and not f.name.startswith("."):
                stem = extract_stem(f.name)
                programs[stem]["data"].append(f)
                # Update month if not set from video
                if not programs[stem]["month"]:
                    programs[stem]["month"] = get_file_month(f)
    
    return dict(programs)


def migrate(dry_run: bool = True):
    """
    Execute the migration.
    
    Args:
        dry_run: If True, only print what would happen
    """
    print(f"\n{'=' * 60}")
    print(f"  VAULT FOLDER MIGRATION {'(DRY RUN)' if dry_run else '(LIVE)'}")
    print(f"{'=' * 60}\n")
    
    programs = scan_vault()
    
    print(f"Found {len(programs)} programs to migrate\n")
    
    # Track stats
    moved = 0
    skipped = 0
    errors = []
    
    for stem, info in sorted(programs.items()):
        month = info["month"] or datetime.now().strftime("%Y-%m")
        project_dir = config.VAULT_DIR / month / stem
        
        print(f"\n📁 {stem}")
        print(f"   └─ Target: {project_dir.relative_to(config.VAULT_DIR)}/")
        
        # List of moves: (source, dest)
        moves = []
        
        # Video
        if info["video"]:
            src = info["video"]
            cat = categorize_file(src.name)
            dest = project_dir / cat / src.name
            moves.append((src, dest))
        
        # Data files
        for src in info["data"]:
            cat = categorize_file(src.name)
            dest = project_dir / cat / src.name
            moves.append((src, dest))
        
        # Execute moves
        for src, dest in moves:
            try:
                # Check if already exists
                if dest.exists():
                    print(f"      ⏭️  {src.name} (already exists)")
                    skipped += 1
                    continue
                
                print(f"      📄 {src.name} → {dest.parent.name}/")
                
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                
                moved += 1
                
            except Exception as e:
                errors.append((str(src), str(e)))
                print(f"      ❌ {src.name}: {e}")
    
    # Summary
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Programs:  {len(programs)}")
    print(f"  Moved:     {moved}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {len(errors)}")
    
    if dry_run:
        print(f"\n  ℹ️  This was a DRY RUN. No files were moved.")
        print(f"  ℹ️  Run without --dry-run to execute migration.")
    else:
        print(f"\n  ✅ Migration complete!")
    
    if errors:
        print(f"\n  Errors:")
        for path, err in errors:
            print(f"    - {path}: {err}")
    
    return {"programs": len(programs), "moved": moved, "skipped": skipped, "errors": errors}


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate VAULT to project folders")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't move files")
    args = parser.parse_args()
    
    migrate(dry_run=args.dry_run)
