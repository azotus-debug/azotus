import config
from pathlib import Path

job_id = "cbnjd010126cc_hdmpeg2-20260105T211434455897Z"
original_stem = "CBNJD010126CC_HDMPEG2"

print(f"PROXIES_DIR: {config.PROXIES_DIR}")

p1 = config.PROXIES_DIR / f"{job_id}_PROXY.mp4"
print(f"Checking P1: {p1}")
print(f"Exists? {p1.exists()}")

p2 = config.PROXIES_DIR / f"{original_stem}_PROXY.mp4"
print(f"Checking P2: {p2}")
print(f"Exists? {p2.exists()}")
