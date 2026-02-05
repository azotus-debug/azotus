#!/usr/bin/env python3
"""
Style Lab: ultra-fast loop to tweak Apple TV+ subtitle styling.

What it does:
- Re-generates the ASS from your SRT (using the same engine as publisher.py).
- Cuts a tiny clip for fast playback (stream copy, no re-encode).
- Plays the clip with subs via ffplay so you can see changes instantly.
- Optionally saves a tiny encoded sample for sharing.
"""

import argparse
import os
import re
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Reuse the same helpers and style definition as the publisher
sys.path.insert(0, str(Path(__file__).parent))
try:
    from publisher import (
        BASE_DIR,
        get_ffmpeg_binary,
        srt_to_ass,
        find_video_file,
    )
    from finalize import json_to_srt
    from subs_render_overlay import render_overlay
except ImportError:
    print("❌ Could not import publisher.py helpers. Make sure it exists and is not running.")
    sys.exit(1)


def get_ffplay_binary():
    candidates = [
        "/opt/homebrew/bin/ffplay",
        "/usr/local/bin/ffplay",
        "ffplay",
    ]
    for path in candidates:
        if shutil.which(path):
            return path
    return "ffplay"


FFMPEG_BIN = get_ffmpeg_binary()
FFPLAY_BIN = get_ffplay_binary()


def escape_ass_path(path: Path) -> str:
    escaped = str(path).replace(":", "\\:").replace("'", "'")
    if os.name == "nt":
        escaped = escaped.replace("\\", "/")
    return escaped


def first_timestamp_seconds(srt_path: Path):
    """
    Grab the first start timestamp in seconds to jump straight to the action.
    """
    time_re = re.compile(r"(\d+):(\d+):(\d+),(\d+)\s*-->")
    with open(srt_path, "r", encoding="utf-8") as f:
        for line in f:
            m = time_re.search(line)
            if m:
                h, mnt, s, ms = map(int, m.groups())
                return h * 3600 + mnt * 60 + s + ms / 1000.0
    return None


def run_cmd(cmd, verbose=False):
    if verbose:
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)
    else:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def style_lab(
    srt_file: Path,
    start: Optional[float],
    duration: float,
    no_clip: bool,
    encode_sample: bool,
    keep_ass: bool,
    verbose: bool,
    overlay_mode: bool,
):
    if not srt_file.exists():
        print(f"❌ SRT not found: {srt_file}")
        return 1

    stem = srt_file.stem
    video_path = find_video_file(stem)
    if not video_path:
        print(f"❌ Matching video not found for stem '{stem}'.")
        print("   Searched in 2_READY_FOR_CLOUD/processed and 1_INBOX (with/without DONE_).")
        return 1

    print(f"🎯 Using SRT:   {srt_file}")
    print(f"🎥 Using video: {video_path}")

    ass_path = BASE_DIR / f"style_preview_{stem}.ass"
    if ass_path.exists():
        ass_path.unlink()

    print("📝 Regenerating ASS with current Apple TV+ style...")
    
    # FIX: Shift SRT timestamps if we are cutting a clip
    temp_srt_path = srt_file
    if start and start > 0:
        print(f"   Shifting SRT timestamps by -{start:.2f}s to match clip...")
        temp_srt_path = BASE_DIR / f"temp_shifted_{stem}.srt"
        with open(srt_file, 'r', encoding='utf-8') as f_in, open(temp_srt_path, 'w', encoding='utf-8') as f_out:
            import re
            time_pattern = re.compile(r"(\d+):(\d+):(\d+),(\d+)")
            
            def shift_match(m):
                h, mnt, s, ms = map(int, m.groups())
                total_seconds = h * 3600 + mnt * 60 + s + ms / 1000.0
                new_total = total_seconds - start
                if new_total < 0: return "00:00:00,000" # Clamp to 0
                
                nh = int(new_total // 3600)
                nmnt = int((new_total % 3600) // 60)
                ns = int(new_total % 60)
                nms = int((new_total * 1000) % 1000)
                return f"{nh:02d}:{nmnt:02d}:{ns:02d},{nms:03d}"

            for line in f_in:
                if "-->" in line:
                    f_out.write(time_pattern.sub(shift_match, line))
                else:
                    f_out.write(line)
                    
    srt_to_ass(temp_srt_path, ass_path, style_name="RuvBox")
    
    # Cleanup temp SRT if created
    if temp_srt_path != srt_file:
        # We might want to keep it for debugging, but usually delete
        # temp_srt_path.unlink() 
        pass

    auto_start = first_timestamp_seconds(srt_file)
    if start is None:
        start = max(0, (auto_start or 0) - 1)

    play_target = video_path
    clip_path = None
    if not no_clip:
        clip_path = BASE_DIR / f"style_clip_{stem}.mp4"
        if clip_path.exists():
            clip_path.unlink()
        print(f"✂️  Cutting {duration}s clip starting at {start:.2f}s (stream copy)...")
        clip_cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(video_path),
            "-c",
            "copy",
            str(clip_path),
        ]
        run_cmd(clip_cmd, verbose=verbose)
        play_target = clip_path

    ass_filter = f"ass='{escape_ass_path(ass_path)}'"
    if not overlay_mode:
        print("👀 Previewing with ffplay (auto-exits after clip)...")
        preview_cmd = [
            FFPLAY_BIN,
            "-autoexit",
            "-hide_banner",
            "-loglevel",
            "error" if not verbose else "info",
        ]
        if no_clip:
            preview_cmd += ["-ss", str(start), "-t", str(duration)]
        preview_cmd += ["-vf", ass_filter, str(play_target)]
        run_cmd(preview_cmd, verbose=verbose)

    if overlay_mode:
        normalized_json = srt_file.with_name(f"{stem}_normalized.json")
        alt_normalized = BASE_DIR / "4_FINAL_OUTPUT" / f"{stem}_normalized.json"
        if not normalized_json.exists() and alt_normalized.exists():
            normalized_json = alt_normalized
        if not normalized_json.exists():
            source_json = BASE_DIR / "3_TRANSLATED_DONE" / f"{stem}_ICELANDIC.json"
            if source_json.exists():
                json_to_srt(source_json)
            else:
                print(f"❌ No normalized JSON found for overlay preview (looked for {source_json}).")
                return 1
        if not normalized_json.exists():
            print(f"❌ Normalized JSON still missing: {normalized_json}")
            return 1

        overlay_path = BASE_DIR / f"style_overlay_{stem}.mov"
        if overlay_path.exists():
            overlay_path.unlink()
        print("🖼️ Rendering overlay for preview clip...")
        
        # Slice and shift JSON to match the clip
        with open(normalized_json, 'r', encoding='utf-8') as f:
            full_data = json.load(f)
            
        sliced_data = full_data.copy()
        sliced_events = []
        clip_start = start if start else 0.0
        clip_end = clip_start + duration
        
        for ev in full_data.get("events", []):
            # Check overlap
            if ev["end"] > clip_start and ev["start"] < clip_end:
                new_ev = ev.copy()
                # Shift timestamps relative to clip start
                new_ev["start"] = max(0.0, ev["start"] - clip_start)
                new_ev["end"] = min(duration, ev["end"] - clip_start)
                sliced_events.append(new_ev)
                
        sliced_data["events"] = sliced_events
        
        # Save temp sliced JSON
        sliced_json_path = BASE_DIR / f"style_sliced_{stem}.json"
        with open(sliced_json_path, 'w', encoding='utf-8') as f:
            json.dump(sliced_data, f, indent=2)
            
        render_overlay(str(play_target), str(sliced_json_path), str(overlay_path), "AppleTV_IS")
        
        # Cleanup sliced JSON
        sliced_json_path.unlink(missing_ok=True)

        # Composite to temp file for preview (ffplay doesn't handle multiple inputs well)
        preview_comp_path = BASE_DIR / f"style_preview_comp_{stem}.mp4"
        if preview_comp_path.exists():
            preview_comp_path.unlink()
            
        print("   Compositing for preview...")
        comp_cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(play_target),
            "-i", str(overlay_path),
            "-filter_complex", "overlay=0:0:shortest=1",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "25",
            "-c:a", "copy",
            str(preview_comp_path)
        ]
        run_cmd(comp_cmd, verbose=verbose)

        print("👀 Previewing overlay with ffplay (auto-exits after clip)...")
        overlay_preview_cmd = [
            FFPLAY_BIN,
            "-autoexit",
            "-hide_banner",
            "-loglevel",
            "error" if not verbose else "info",
            str(preview_comp_path),
        ]
        run_cmd(overlay_preview_cmd, verbose=verbose)
        
        # Cleanup preview comp
        preview_comp_path.unlink(missing_ok=True)
        # overlay_path.unlink(missing_ok=True)  <-- Moved to end

    if encode_sample:
        sample_path = BASE_DIR / f"style_sample_{stem}.mp4"
        if sample_path.exists():
            sample_path.unlink()
        print("💾 Saving tiny encoded sample (ultrafast/CRF 28, muted)...")
        
        if overlay_mode and overlay_path.exists():
             encode_cmd = [
                FFMPEG_BIN,
                "-y",
                "-i", str(play_target),
                "-i", str(overlay_path),
                "-filter_complex", "overlay=0:0:shortest=1",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-an",
                str(sample_path),
            ]
        else:
            encode_cmd = [
                FFMPEG_BIN,
                "-y",
                "-i",
                str(play_target),
                "-vf",
                ass_filter,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-an",
                str(sample_path),
            ]
            
        run_cmd(encode_cmd, verbose=verbose)
        print(f"   Sample: {sample_path}")

    if not keep_ass and ass_path.exists():
        ass_path.unlink()
    if clip_path and clip_path.exists():
        clip_path.unlink()
        
    if overlay_mode and overlay_path.exists():
        overlay_path.unlink()

    print("✅ Done. Tweak ASS_HEADER in publisher.py and re-run for instant feedback.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Rapid Apple TV+ subtitle styling loop (preview without full burn).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("srt_file", nargs="?", help="Path to SRT (defaults to first in 4_FINAL_OUTPUT).")
    parser.add_argument("--start", type=float, help="Start time in seconds. Defaults to 1s before first cue.")
    parser.add_argument("--duration", type=float, default=8.0, help="Clip duration for preview.")
    parser.add_argument("--no-clip", action="store_true", help="Preview directly from full video (no temp clip).")
    parser.add_argument("--encode-sample", action="store_true", help="Also write a tiny encoded sample with subs.")
    parser.add_argument("--keep-ass", action="store_true", help="Keep the generated ASS file.")
    parser.add_argument("--verbose", action="store_true", help="Show ffmpeg/ffplay output.")
    parser.add_argument("--overlay", action="store_true", help="Also render/preview ProRes overlay on the clip.")

    args = parser.parse_args()

    if args.srt_file:
        srt_path = Path(args.srt_file)
    else:
        srt_dir = BASE_DIR / "4_FINAL_OUTPUT"
        srt_files = sorted(srt_dir.glob("*.srt"))
        if not srt_files:
            print("❌ No SRT files found in 4_FINAL_OUTPUT/. Provide an SRT path.")
            return 1
        srt_path = srt_files[0]
        print(f"📋 Auto-selected SRT: {srt_path.name}")

    return style_lab(
        srt_path,
        start=args.start,
        duration=args.duration,
        no_clip=args.no_clip,
        encode_sample=args.encode_sample,
        keep_ass=args.keep_ass,
        verbose=args.verbose,
        overlay_mode=args.overlay,
    )


if __name__ == "__main__":
    sys.exit(main())
