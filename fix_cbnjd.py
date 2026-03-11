import sys
import os
import logging
logging.basicConfig(level=logging.INFO)

sys.path.append(os.getcwd())
from workers.finalizer.main import finalize
from pathlib import Path

stem = "cbnjd031026cc-20260310T223421594998Z"
approved_path = Path(f"/Users/haukurhauksson/Azotus/3_TRANSLATED_DONE/{stem}_APPROVED.json")

print(f"Running finalized on: {approved_path}")
srt_path, norm_path = finalize(approved_path, target_language="is")
print(f"Generated SRT: {srt_path}")
