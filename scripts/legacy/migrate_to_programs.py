#!/usr/bin/env python3
"""
Migration script: Backfill programs and tracks from existing jobs.

This creates program records for each unique video and track records
for each job, linking them together.
"""

import sys
from pathlib import Path

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import omega_db
import config
from workers import transcriber
from datetime import datetime

def migrate_jobs_to_programs():
    """
    Group existing jobs by original video.
    Create program record for each unique video.
    Create track record for each job.
    """
    print("🔄 Starting migration: jobs → programs + tracks")
    
    jobs = omega_db.get_all_jobs_via_tracks()
    print(f"   Found {len(jobs)} jobs to migrate")
    
    # Group by original video path (vault_path)
    programs_map = {}
    
    for job in jobs:
        meta = job.get('meta', {}) or {}
        if isinstance(meta, str):
            import json
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        
        vault_path = meta.get('vault_path')
        original_stem = meta.get('original_stem', job['file_stem'].split('-')[0])
        
        if not vault_path:
            # Try to construct from original filename
            original_filename = meta.get('original_filename')
            if original_filename:
                vault_path = str(config.VAULT_VIDEOS / original_filename)
            else:
                vault_path = f"unknown-{job['file_stem']}"
        
        if vault_path not in programs_map:
            programs_map[vault_path] = {
                'title': original_stem,
                'original_filename': meta.get('original_filename'),
                'video_path': vault_path,
                'client': job.get('client', 'unknown'),
                'due_date': job.get('due_date'),
                'default_style': job.get('subtitle_style', 'Classic'),
                'jobs': []
            }
        
        programs_map[vault_path]['jobs'].append(job)
    
    print(f"   Grouped into {len(programs_map)} programs")
    
    # Create program + track records
    created_programs = 0
    created_tracks = 0
    
    for vault_path, data in programs_map.items():
        # Check if program already exists
        existing = omega_db.get_program_by_video(vault_path)
        if existing:
            program_id = existing['id']
            print(f"   ⏭️ Program exists: {data['title']}")
        else:
            # Get thumbnail if exists
            stem = Path(data['original_filename']).stem if data['original_filename'] else data['title']
            thumbnail_path = config.VAULT_DIR / "Thumbnails" / f"{stem}.jpg"
            
            # Get duration from audio if exists
            audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
            duration = None
            if audio_path.exists():
                try:
                    duration = transcriber.get_audio_duration(audio_path)
                except:
                    pass
            
            program_id = omega_db.create_program(
                title=data['title'],
                original_filename=data['original_filename'],
                video_path=vault_path,
                thumbnail_path=str(thumbnail_path) if thumbnail_path.exists() else None,
                duration_seconds=duration,
                client=data['client'],
                due_date=data['due_date'],
                default_style=data['default_style'],
                meta={'migrated_at': datetime.now().isoformat()}
            )
            created_programs += 1
            print(f"   ✅ Created program: {data['title']} ({program_id[:8]}...)")
        
        # Create tracks for each job
        for job in data['jobs']:
            # Check if track already exists for this job
            existing_track = omega_db.get_track_by_job(job['file_stem'])
            if existing_track:
                print(f"      ⏭️ Track exists for job: {job['file_stem'][:30]}...")
                continue
            
            target_lang = job.get('target_language', 'is')
            
            track_id = omega_db.create_track(
                program_id=program_id,
                type='subtitle',
                language_code=target_lang,
                stage=job.get('stage', 'QUEUED'),
                status=job.get('status', 'Pending'),
                job_id=job['file_stem'],
                meta={
                    'migrated_from_job': True,
                    'original_job_stage': job.get('stage'),
                    'original_progress': job.get('progress'),
                }
            )
            
            # Update track progress
            omega_db.update_track(track_id, progress=job.get('progress', 0.0))
            
            created_tracks += 1
            print(f"      ✅ Created track: {target_lang} ({track_id[:8]}...)")
    
    print(f"\n✅ Migration complete!")
    print(f"   Programs created: {created_programs}")
    print(f"   Tracks created: {created_tracks}")


if __name__ == "__main__":
    migrate_jobs_to_programs()
