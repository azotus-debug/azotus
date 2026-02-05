import sys
import os
import argparse
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

from workers.publisher import publish

def redo_burn(video_in: Path, srt_in: Path, subtitle_style: str, delivery_profile: str):
    """
    Renders a full job using the given subtitle style.
    Uses the updated publisher.py which now defaults to stable software encoding for this style.
    """
    if not video_in.exists():
        print(f"❌ Input video not found: {video_in}")
        return
    if not srt_in.exists():
        print(f"❌ SRT file not found: {srt_in}")
        return

    print(f"🔥 Redoing full burn with style={subtitle_style} profile={delivery_profile}...")
    try:
        # publish handles the logic of choosing ASS/Apple and encoding stability
        result_path = publish(
            video_path=video_in,
            srt_path=srt_in,
            subtitle_style=subtitle_style,
            delivery_profile=delivery_profile,
        )
        print(f"✅ Full burn completed successfully!")
        print(f"📂 Result: {result_path}")
    except Exception as e:
        print(f"❌ Burn failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redo a full burn with a specific style/profile.")
    parser.add_argument("--video", required=True, help="Path to input video")
    parser.add_argument("--srt", required=True, help="Path to SRT file")
    parser.add_argument("--style", default="Classic", help="Subtitle style (default: Classic)")
    parser.add_argument("--profile", default="universal", help="Delivery profile (default: universal)")
    args = parser.parse_args()

    redo_burn(Path(args.video), Path(args.srt), args.style, args.profile)
