import os
import sys
import uuid
from pathlib import Path
from datetime import datetime
import re

# Add root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import omega_db
from normalization import get_canonical_stem

def get_client(stem: str) -> str:
    """Detect client from stem using config patterns."""
    stem_lower = stem.lower()
    for pattern, client_name in config.CLIENT_PATTERNS.items():
        if pattern in stem_lower:
            return client_name
    return "unknown"

def get_language(stem: str) -> str:
    """Simple heuristic for language code."""
    # This should match the regex used in normalization roughly
    match = re.search(r'_(is|en|es|nl|pt|fr|de|it|ru)(_|$)', stem, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return "en" # Default? Or 'is'?

def backfill():
    print("--- Starting Database Backfill ---")
    
    # Ensure tables exist
    omega_db.ensure_schema()
    
    conn = omega_db._connect()
    c = conn.cursor()
    
    dirs_to_scan = [
        config.INBOX_DIR,
        config.VAULT_DATA,
        config.EDITOR_DIR,
        config.TRANSLATED_DONE_DIR,
        config.DELIVERY_DIR
    ]

    count_programs = 0
    count_tracks = 0

    seen_stems = set()

    for directory in dirs_to_scan:
        if not directory.exists():
            continue
            
        for filepath in directory.glob("*"):
            if filepath.name.startswith('.'): continue
            if filepath.is_dir(): continue
            
            original_stem = filepath.stem
            
            # Reduce noise from multiple files of same batch
            if original_stem in seen_stems:
                continue
            seen_stems.add(original_stem)

            canonical = get_canonical_stem(original_stem)
            client = get_client(canonical)
            lang = get_language(original_stem)
            
            # PROGAM IDENTITY
            # Key = client_id | canonical_stem (or just canonical_stem if unique enough?)
            # Spec says: client_id | canonical_stem
            # Let's generate a UUID based on that or use the string itself
            program_key = f"{client}|{canonical}"
            program_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, program_key))
            
            # Upsert Program
            c.execute("SELECT id FROM programs WHERE id=?", (program_id,))
            if not c.fetchone():
                now = datetime.now()
                c.execute("""
                    INSERT INTO programs (id, title, original_filename, client, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (program_id, canonical, original_stem, client, now, now))
                count_programs += 1
                print(f"[NEW PROGRAM] {canonical}")

            # Upsert Track (Implied by file existence)
            # Track Key = UNIQUE(program_id, language_code, type='subtitle')
            track_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{program_id}|{lang}|subtitle"))
            
            c.execute("SELECT id FROM tracks WHERE id=?", (track_id,))
            if not c.fetchone():
                now = datetime.now()
                # Infer stage/status from directory?
                # Simplify for now: Just exist
                c.execute("""
                    INSERT INTO tracks (id, program_id, language_code, type, stage, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'subtitle', 'QUEUED', 'Backfilled', ?, ?)
                """, (track_id, program_id, lang, now, now))
                count_tracks += 1
                # print(f"[NEW TRACK]   -> {lang}")

    conn.commit()
    conn.close()
    
    print(f"--- Backfill Complete ---")
    print(f"Programs Created: {count_programs}")
    print(f"Tracks Created:   {count_tracks}")

if __name__ == "__main__":
    backfill()
