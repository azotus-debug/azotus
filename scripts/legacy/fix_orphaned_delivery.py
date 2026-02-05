#!/usr/bin/env python3
"""
Fix orphaned deliveries - jobs that completed but aren't in the database.
This happens when cloud jobs finish but the local DB isn't updated properly.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import omega_db
import config

def find_orphaned_deliveries():
    """Find delivered videos that don't have database records."""
    delivery_dir = Path(config.DELIVERY_DIR) / "VIDEO"
    orphans = []

    if not delivery_dir.exists():
        print(f"Delivery directory not found: {delivery_dir}")
        return orphans

    for video_file in delivery_dir.glob("*_SUBBED.mp4"):
        # Extract job_id from filename (remove _SUBBED.mp4)
        job_id = video_file.stem.replace("_SUBBED", "")

        # Check if job exists
        job = omega_db.get_job_via_track(job_id)
        if not job:
            print(f"❌ ORPHAN: {job_id} - file exists but no DB record")
            orphans.append({
                'job_id': job_id,
                'video_path': video_file,
                'delivered_at': datetime.fromtimestamp(video_file.stat().st_mtime)
            })
        elif job.get('stage') != 'COMPLETED':
            print(f"⚠️  INCOMPLETE DB: {job_id} - file delivered but DB shows stage={job.get('stage')}")
            orphans.append({
                'job_id': job_id,
                'video_path': video_file,
                'delivered_at': datetime.fromtimestamp(video_file.stat().st_mtime),
                'existing_job': job
            })

    return orphans

def create_program_for_orphan(orphan):
    """Create a Program and Track for an orphaned delivery."""
    job_id = orphan['job_id']
    video_path = orphan['video_path']

    # Extract original filename from job_id (format: name-TIMESTAMP)
    parts = job_id.rsplit('-', 1)
    if len(parts) == 2:
        title = parts[0].replace('_', ' ').replace('-', ' ').title()
    else:
        title = job_id

    print(f"\n🔧 Creating Program for: {title}")

    # Check if program already exists
    programs = omega_db.get_all_programs()
    for prog in programs:
        if prog.get('title') == title or job_id in str(prog.get('original_filename', '')):
            print(f"   Program already exists: {prog['id']}")
            return prog['id']

    # Create new program
    program_id = omega_db.create_program(
        title=title,
        original_filename=f"{job_id}.mp4",
        video_path=str(video_path),
        client="Unknown",  # Can't determine from orphaned file
        meta={
            'imported_from_orphan': True,
            'orphan_discovery_date': datetime.now().isoformat(),
            'original_job_id': job_id
        }
    )

    print(f"   ✅ Created Program: {program_id}")

    # Create a track (assume Icelandic subtitle since that's the default)
    track_id = omega_db.create_track(
        program_id=program_id,
        type='subtitle',
        language_code='is',
        language_name='Icelandic',
        stage='COMPLETED',
        status='Delivered (imported from orphan)',
        job_id=job_id,
        meta={
            'imported_from_orphan': True,
            'delivery_date': orphan['delivered_at'].isoformat()
        }
    )

    # Update track with progress and output_path
    omega_db.update_track(
        track_id,
        progress=100.0,
        output_path=str(video_path)
    )

    print(f"   ✅ Created Track: {track_id}")

    # Create or update the job record
    if 'existing_job' in orphan:
        # Update existing incomplete job
        omega_db.update_job_via_track(
            job_id,
            stage='COMPLETED',
            status='Done (corrected from orphan)',
            progress=100.0,
            meta={
                'program_id': program_id,
                'track_id': track_id,
                'corrected_from_orphan': True
            }
        )
        print(f"   ✅ Updated existing job to COMPLETED")
    else:
        # Create new job record
        omega_db.update_job_via_track(
            job_id,
            stage='COMPLETED',
            status='Done (imported from orphan)',
            progress=100.0,
            target_language='is',
            meta={
                'program_id': program_id,
                'track_id': track_id,
                'imported_from_orphan': True
            }
        )
        print(f"   ✅ Created job record")

    return program_id

def main():
    print("=" * 60)
    print("ORPHANED DELIVERY FIXER")
    print("=" * 60)
    print()

    orphans = find_orphaned_deliveries()

    if not orphans:
        print("✅ No orphaned deliveries found!")
        return

    print(f"\nFound {len(orphans)} orphaned delivery(ies)")
    print()

    if len(sys.argv) > 1 and sys.argv[1] == "--fix":
        print("🔧 Fixing orphaned deliveries...")
        for orphan in orphans:
            try:
                create_program_for_orphan(orphan)
            except Exception as e:
                print(f"   ❌ Error: {e}")
                import traceback
                traceback.print_exc()

        print("\n✅ Done! Refresh your frontend to see the imported programs.")
    else:
        print("Run with --fix to create database records for these orphans")
        print(f"  python {sys.argv[0]} --fix")

if __name__ == "__main__":
    main()
