import os
import sys
from pathlib import Path
from normalization import get_canonical_stem
import config

def audit_files():
    """
    Scans the configured directories, applies normalization,
    and prints a report of Original -> Canonical mappings.
    """
    print("--- Canonicalization Audit Report ---")
    print(f"{'ORIGINAL':<50} | {'CANONICAL':<30}")
    print("-" * 85)

    # Gather all file stems from key directories
    # We look at ingest, skeleton, and active working dirs
    dirs_to_scan = [
        config.INBOX_DIR,
        config.VAULT_DATA,
        config.EDITOR_DIR,
        config.TRANSLATED_DONE_DIR,
        config.DELIVERY_DIR
    ]
    
    seen_stems = set()
    
    for directory in dirs_to_scan:
        if not directory.exists():
            continue
            
        for filepath in directory.glob("*"):
            if filepath.name.startswith('.'): continue
            if filepath.is_dir(): continue
            
            stem = filepath.stem
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            
            canonical = get_canonical_stem(stem)
            
            # Highlight changes
            if stem != canonical:
                print(f"{stem:<50} | {canonical:<30}")
            else:
                # Optional: Don't print if no change to keep noise down?
                # Or print grayed out. Let's print everything for now to be sure.
                print(f"{stem:<50} | {canonical:<30} (No Change)")

if __name__ == "__main__":
    audit_files()
