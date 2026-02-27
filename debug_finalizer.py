"""Debug: trace what happens to segments 186-187 through each pass."""
import os
import json

os.environ["OMEGA_SIMPLIFIED_FINALIZER"] = "0"

from workers import finalizer

json_path = "/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE/i2608_iceland-20260219T220753150282Z_APPROVED.json"
with open(json_path, "r") as f:
    job_data = json.load(f)

segments = job_data.get("segments", [])

# Find segments containing "hvar varst" or "þú? Guð"
for i, seg in enumerate(segments):
    text = seg.get("text", "")
    if "hvar varst" in text or "þú?" in text.lower() or "Trúfesti" in text:
        print(f"  RAW seg[{i}] ({seg.get('start',0):.1f}-{seg.get('end',0):.1f}): {text[:70]}")

print("\n--- Running finalizer ---\n")
import logging
logging.basicConfig(level=logging.DEBUG)

finalized = finalizer.normalize_segments_for_review(segments, target_language="is")

print(f"\n--- Post-finalizer ---")
for i, seg in enumerate(finalized):
    text = seg.get("text", "")
    if "hvar varst" in text or "þú?" in text or "Trúfesti" in text or "á. Trú" in text:
        print(f"  FINAL seg[{i}] ({seg.get('start',0):.1f}-{seg.get('end',0):.1f}): {text[:80]}")
