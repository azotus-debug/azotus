import sys
import shutil
import subprocess
from pathlib import Path

# Add current dir to path to find modules
sys.path.append(str(Path.cwd()))

import config
from workers import finalizer

# 1. DEFINE PATHS
job_stem = "timessquarechurch_20251012-20251230T005251568171Z"
input_video = Path(f"2_VAULT/Videos/TimesSquareChurch_20251012.mp4")
approved_json = Path(f"3_TRANSLATED_DONE/{job_stem}_APPROVED.json")
output_dir = Path("4_DELIVERY/VIDEO")
output_video = output_dir / f"{job_stem}_BURNED.mp4"

def run_burn():
    print(f"🔥 MANUAL BURN STARTED: {job_stem}")
    
    # 2. RUN FINALIZER (Fix Orphans, Generate SRT)
    print(">> Running Finalizer (Orphan Fix)...")
    try:
        finalizer.finalize(approved_json, target_language="is")
    except Exception as e:
        print(f"ERROR in finalizer: {e}")
        # Continue if SRT exists? No, critical.
        return

    srt_path = config.SRT_DIR / f"{job_stem}.srt"
    if not srt_path.exists():
        print(f"ERROR: SRT not found at {srt_path}")
        return

    print(f"✅ SRT Generated: {srt_path}")

    # 3. RUN FFMPEG (VideoToolbox)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # FFmpeg Subtitles Filter Path escaping
    # If path has spaces or special chars, it can be tricky.
    # We use a relative path logic or simplified absolute path.
    # "subtitles='path/to/file.srt'"
    
    # Absolute path is safest but escape colons for Windows? Mac is fine.
    srt_abs = srt_path.resolve()
    # Escape single quotes and colons if needed? 
    # Usually: subtitles='path'
    
    cmd = [
        "ffmpeg",
        "-y", # Overwrite
        "-i", str(input_video),
        "-vf", f"subtitles='{srt_abs}'",
        "-c:v", "h264_videotoolbox", # HW ACCEL
        "-b:v", "6000k", # High Bitrate
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_video)
    ]
    
    print(f">> Executing Burn: {' '.join(cmd)}")
    
    # Run synchronously to see output
    try:
        subprocess.run(cmd, check=True)
        print(f"✅ BURN COMPLETE: {output_video}")
    except subprocess.CalledProcessError as e:
        print(f"❌ BURN FAILED: {e}")

if __name__ == "__main__":
    run_burn()
