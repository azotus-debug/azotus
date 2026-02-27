"""
Run the FULL finalizer (all passes enabled) on the last Iceland translation.
Produces a new SRT for comparison.
"""
import os
import json
import sys

# Force full finalizer — disable simplified mode
os.environ["OMEGA_SIMPLIFIED_FINALIZER"] = "0"

from workers import finalizer

def main():
    job_id = "i2608_iceland-20260219T220753150282Z"
    json_path = f"/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE/{job_id}_APPROVED.json"
    
    print(f"Loading raw translation: {json_path}")
    with open(json_path, "r") as f:
        job_data = json.load(f)
    
    segments = job_data.get("segments", [])
    target_lang = job_data.get("target_language", "is")
    
    print(f"Loaded {len(segments)} segments, target: {target_lang}")
    print(f"SIMPLIFIED_FINALIZER = {finalizer.SIMPLIFIED_FINALIZER}")
    print(f"Running FULL finalizer with all passes enabled...\n")
    
    # Run the full finalizer
    finalized = finalizer.normalize_segments_for_review(
        segments,
        target_language=target_lang,
    )
    
    print(f"\nFinalizer complete: {len(segments)} → {len(finalized)} segments")
    
    # Save as SRT
    out_path = f"/Users/haukurhauksson/Azotus/4_DELIVERY/SRT/DONE_{job_id}_FULL_FINALIZER.srt"
    with open(out_path, "w", encoding="utf-8") as f:
        for i, event in enumerate(finalized):
            start = finalizer.format_timestamp(event["start"])
            end = finalizer.format_timestamp(event["end"])
            text = event.get("text", "")
            f.write(f"{i+1}\n{start} --> {end}\n{text}\n\n")
    
    print(f"Saved: {out_path}")

if __name__ == "__main__":
    main()
