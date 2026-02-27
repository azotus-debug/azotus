from pathlib import Path
from managers.burn_manager import queue_burn_job
import omega_db

def main():
    job_id = "i2608_iceland-20260219T220753150282Z"
    video_path = Path("/Users/haukurhauksson/Azotus/1_VAULT/VIDEOS/i2608.mp4")
    srt_path = Path("/Users/haukurhauksson/Azotus/4_DELIVERY/SRT/DONE_i2608_iceland-20260219T220753150282Z_BBC_FORMAT.srt")
    out_path = Path("/Users/haukurhauksson/Azotus/4_DELIVERY/VIDEO") / f"DONE_i2608_iceland-BBC_FORMAT.mp4"
    
    # Check if we have an explicit burn script or manager
    import os
    import subprocess
    
    print(f"Burning video {video_path.name} with {srt_path.name}")
    print(f"Outputting to {out_path.name}")
    
    # We'll use ffmpeg directly to burn it quickly since the internal paths might be tricky to mock
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_path}':force_style='Fontname=Roboto,Fontsize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=30'",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", # Fast encode block
        "-c:a", "copy",
        "-t", "120",  # Just do the first 2 minutes so you can see it quickly
        str(out_path)
    ]
    
    print("Running ffmpeg directly for a fast 2-minute preview...")
    subprocess.run(cmd, check=True)
    print(f"\nFinished. Preview file saved to {out_path}")

if __name__ == "__main__":
    main()
