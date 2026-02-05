import os
import argparse
import subprocess
import time
from pathlib import Path

import config

def run_test_burn(video_in: Path, ass_in: Path, video_out: Path):
    """
    Runs a 5-minute test burn of the Classic (RuvBox) look using the software encoder (libx264).
    This is used to isolate if hardware acceleration or specific filter parameters were causing crashes.
    """
    if not video_in.exists():
        print(f"❌ Input video not found: {video_in}")
        return
    if not ass_in.exists():
        print(f"❌ ASS file not found: {ass_in}")
        return

    # Create delivery dir if missing
    video_out.parent.mkdir(parents=True, exist_ok=True)

    print(f"🚀 Starting test burn (5 minutes) for Job I2605...")
    print(f"📹 Source: {video_in}")
    print(f"📝 Subtitles: {ass_in}")
    print(f"💿 Output: {video_out}")

    # FFmpeg command:
    # -t 300: limit to 5 minutes
    # -c:v libx264: software encoder for stability
    # -vf ass=...: the core styling filter
    # -preset faster: for quick testing
    # -pix_fmt yuv420p: standard compatibility
    
    # Note: Using absolute path for ass filter and escaping it for FFmpeg
    escaped_ass = str(ass_in).replace(":", "\\:").replace("/", "\\/")
    
    cmd = [
        "ffmpeg", "-y",
        "-t", "300",
        "-i", str(video_in),
        "-vf", f"ass='{ass_in}'",
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "23",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(video_out)
    ]

    print(f"🛠️ Executing: {' '.join(cmd)}")
    
    start_time = time.time()
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()
        
        duration = time.time() - start_time
        
        if process.returncode == 0:
            print(f"✅ Test burn completed successfully in {duration:.2f}s!")
            print(f"📂 File saved to: {video_out}")
        else:
            print(f"❌ FFmpeg failed with exit code {process.returncode}")
            print(f"--- STDERR ---\n{stderr}")
            
    except Exception as e:
        print(f"💥 Python error during execution: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a 5-minute Classic style burn test.")
    parser.add_argument("--video", required=True, help="Path to input video")
    parser.add_argument("--ass", required=True, help="Path to ASS subtitle file")
    parser.add_argument(
        "--out",
        default=str(config.VIDEO_DIR / "classic_burn_test.mp4"),
        help="Output video path",
    )
    args = parser.parse_args()

    run_test_burn(Path(args.video), Path(args.ass), Path(args.out))
