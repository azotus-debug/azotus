import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Optional
import multiprocessing
from multiprocessing import Pool, cpu_count

# Force fork start method so multiprocessing works when launched from stdin wrappers
try:
    multiprocessing.set_start_method("fork", force=True)
except RuntimeError:
    pass

from PIL import Image, ImageDraw, ImageFilter, ImageFont, Image


PROFILES = {
    "AppleTV_IS": {
        "font_path": "/Library/Fonts/SF-Pro-Display-Regular.otf",
        "font_size": 48,  # Industry standard for 1080p (reduced from 54)
        "font_variant": "Regular",  # Current optimal weight
        "text_color": (255, 255, 255),
        "box_opacity": 0.65,
        "box_color": (0, 0, 0),
        "padding_x": 30,
        "padding_y": 15,
        "radius": 20,
        "y_offset": 100,  # Moved lower on screen
        "box_shadow": True,
        "shadow_offset": (0, 4),
        "shadow_blur": 8,
        "shadow_opacity": 0.3,
        "anti_alias": True,
    },
}


def _pick_ffprobe(ffmpeg_bin: str) -> str:
    if "ffmpeg" in ffmpeg_bin:
        candidate = ffmpeg_bin.replace("ffmpeg", "ffprobe")
        if shutil.which(candidate):
            return candidate
    return shutil.which("ffprobe") or "ffprobe"


def _run_ffprobe(ffprobe_bin: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            [ffprobe_bin, *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"ffprobe failed: {exc.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def _probe_video_info(video_path: Path) -> tuple[float, Fraction]:
    import config

    ffmpeg_bin = config.FFMPEG_BIN
    ffprobe_bin = _pick_ffprobe(ffmpeg_bin)

    duration_raw = _run_ffprobe(
        ffprobe_bin,
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
    )
    try:
        duration = float(duration_raw)
    except ValueError:
        print(f"Unable to parse duration from ffprobe: '{duration_raw}'")
        sys.exit(1)

    fps_raw = _run_ffprobe(
        ffprobe_bin,
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
    )
    try:
        fps = Fraction(fps_raw)
    except ValueError:
        print(f"Unable to parse r_frame_rate from ffprobe: '{fps_raw}'")
        sys.exit(1)

    return duration, fps


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        print(f"Font not found at {font_path}, falling back to default.")
        return ImageFont.load_default()


def _render_frame(
    width: int,
    height: int,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    profile: dict,
    position: str = "bottom",
) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not lines:
        return img

    draw = ImageDraw.Draw(img, "RGBA")
    bboxes = [font.getbbox(line) for line in lines]
    widths = [bbox[2] - bbox[0] for bbox in bboxes]
    heights = [bbox[3] - bbox[1] for bbox in bboxes]

    line_spacing = 15  # Increased for better readability (was 8)
    text_width = max(widths)
    text_height = sum(heights) + line_spacing * (len(lines) - 1)

    padding_x = profile["padding_x"]
    padding_y = profile["padding_y"]
    box_width = text_width + padding_x * 2
    box_height = text_height + padding_y * 2

    box_x = (width - box_width) / 2
    
    # Position logic
    if position == "top":
        # Top positioning (offset from top edge)
        box_y = profile["y_offset"]
    else:
        # Bottom positioning (offset from bottom edge)
        box_y = height - profile["y_offset"] - box_height

    box_alpha = int(255 * profile["box_opacity"])
    box_color = (*profile["box_color"], box_alpha)
    
    # Render box shadow if enabled
    if profile.get("box_shadow", False):
        shadow_offset = profile.get("shadow_offset", (0, 4))
        shadow_blur = profile.get("shadow_blur", 8)
        shadow_opacity = profile.get("shadow_opacity", 0.3)
        shadow_alpha = int(255 * shadow_opacity)
        shadow_color = (0, 0, 0, shadow_alpha)
        
        # Create shadow layer with blur
        shadow_x = box_x + shadow_offset[0]
        shadow_y = box_y + shadow_offset[1]
        
        draw.rounded_rectangle(
            [shadow_x, shadow_y, shadow_x + box_width, shadow_y + box_height],
            radius=profile["radius"],
            fill=shadow_color,
        )
    
    # Render main box
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_width, box_y + box_height],
        radius=profile["radius"],
        fill=box_color,
    )

    text_y = box_y + padding_y - 5  # Visual correction: move text up slightly
    for line, h, w in zip(lines, heights, widths):
        text_x = box_x + (box_width - w) / 2
        draw.text(
            (text_x, text_y),
            line,
            font=font,
            fill=(*profile["text_color"], 255),
            stroke_width=1,
            stroke_fill=(*profile["text_color"], 128),
        )
        text_y += h + line_spacing

    return img


# Helper function for parallel frame rendering
def _render_frame_worker(args):
    """Worker function for parallel frame rendering. Must be at module level for multiprocessing."""
    frame_idx, width, height, processed_events, font_path, font_size, profile_cfg, tmpdir_path = args
    
    # Find active events for this frame
    active = [
        ev for ev in processed_events
        if ev["start_frame"] <= frame_idx < ev["end_frame"]
    ]
    if active:
        active_lines = active[0]["lines"]
        # Use position from the active event, default to bottom
        position = active[0].get("position", "bottom")
    else:
        active_lines = []
        position = "bottom"
        
    key = tuple(active_lines + [position]) if active_lines else ("__blank__",)
    
    # Load font (each worker needs its own font object)
    font = _load_font(font_path, font_size)
    
    # Render frame
    frame_image = _render_frame(width, height, active_lines, font, profile_cfg, position)
    
    # Save frame
    frame_path = tmpdir_path / f"{frame_idx:06d}.png"
    try:
        frame_image.save(frame_path)
    except Exception as e:
        # Fallback: Force init and retry
        try:
            Image.init()
            frame_image.save(frame_path)
        except Exception as e2:
            print(f"Worker failed to save PNG: {e} -> {e2}")
            raise e2
    
    return frame_idx, key


import omega_db

# ... (imports)

def render_overlay(
    video_path: str,
    subs_json_path: str,
    output_path: str,
    profile_name: str = "AppleTV_IS",
    stem: str = None, # Added stem for DB updates
    skip_encoding: bool = False,
) -> Optional[Path]:
    """
    Renders subtitles as a transparent overlay.
    If skip_encoding=True, returns the Path to the directory containing PNG frames.
    Otherwise, encodes to ProRes 4444 MOV and returns the output_path.
    """
    import config
    # ...
    
    profile_cfg = PROFILES.get(profile_name)
    if not profile_cfg:
        print(f"Unknown profile '{profile_name}'. Available: {', '.join(PROFILES.keys())}")
        sys.exit(1)

    video_path = Path(video_path)
    subs_json_path = Path(subs_json_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(subs_json_path, "r", encoding="utf-8") as f:
            subs_data = json.load(f)
    except OSError as exc:
        print(f"Failed to read subtitles JSON: {exc}")
        sys.exit(1)

    events = subs_data.get("events", [])
    width = int(subs_data.get("video_width", 1920))
    height = int(subs_data.get("video_height", 1080))

    if not events:
        print("No events found in subtitle JSON.")
        sys.exit(1)

    duration, fps = _probe_video_info(video_path)
    total_frames = int(duration * fps.numerator / fps.denominator)
    if total_frames <= 0:
        print("Computed total_frames is zero; aborting.")
        sys.exit(1)

    processed_events = []
    for event in events:
        start_frame = int(event["start"] * fps.numerator / fps.denominator)
        end_frame = int(event["end"] * fps.numerator / fps.denominator)
        if end_frame <= start_frame:
            end_frame = start_frame + 1
        processed_events.append({
            "start_frame": start_frame,
            "end_frame": end_frame,
            "lines": event.get("lines", []),
            "position": event.get("position", "bottom"),
        })

    font = _load_font(profile_cfg["font_path"], profile_cfg["font_size"])
    ffmpeg_bin = config.FFMPEG_BIN
    
    duration, fps = _probe_video_info(video_path)
    total_frames = int(duration * fps.numerator / fps.denominator)
    
    if stem:
        omega_db.update_job_via_track(stem, status="Rendering Overlay Frames", progress=80.0)

    # ... (setup tempdir)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Render frames in parallel
        env_workers = os.environ.get("OMEGA_OVERLAY_WORKERS")
        if env_workers:
            try:
                num_workers = max(1, int(env_workers))
            except ValueError:
                num_workers = max(1, min(cpu_count() - 1, 4))
        else:
            num_workers = max(1, min(cpu_count() - 1, 4))  # Leave one core free, cap for stability
        print(f"   Using {num_workers} parallel workers...")
        
        if stem:
            omega_db.update_job_via_track(stem, status=f"Rendering frames ({num_workers} workers)", progress=80.0)
        
        rendered_frames: list[Optional[tuple[str, ...]]] = [None] * total_frames
        with Pool(processes=num_workers) as pool:
            # Process frames in chunks for progress updates
            try:
                chunk_size = int(os.environ.get("OMEGA_OVERLAY_CHUNK_SIZE", "100") or 100)
            except ValueError:
                chunk_size = 100
            chunk_size = max(10, min(chunk_size, 1000))
            for start in range(0, total_frames, chunk_size):
                end = min(total_frames, start + chunk_size)
                chunk = [
                    (
                        frame_idx,
                        width,
                        height,
                        processed_events,
                        profile_cfg["font_path"],
                        profile_cfg["font_size"],
                        profile_cfg,
                        tmpdir_path,
                    )
                    for frame_idx in range(start, end)
                ]
                results = pool.map(_render_frame_worker, chunk)
                
                for frame_idx, key in results:
                    rendered_frames[frame_idx] = key
                
                # Progress update
                if stem:
                    prog = 80.0 + (end / total_frames) * 10.0
                    omega_db.update_job_via_track(stem, progress=min(90.0, prog))
        
        # Deduplicate identical frames (link instead of copy)
        print(f"   Deduplicating frames...")
        key_to_frame = {}
        for frame_idx in range(total_frames):
            key = rendered_frames[frame_idx] or ("__blank__",)
            frame_path = tmpdir_path / f"{frame_idx:06d}.png"
            
            if key in key_to_frame and key != ("__blank__",):
                # Link to existing frame
                source_path = key_to_frame[key]
                if frame_path.exists():
                    frame_path.unlink()
                try:
                    os.link(source_path, frame_path)
                except OSError:
                    shutil.copy(source_path, frame_path)
            else:
                key_to_frame[key] = frame_path

        if skip_encoding:
            # Move tmpdir content to a persistent location or just return it?
            # Since tmpdir is a context manager, it will be deleted.
            # We must move it out or change how tmpdir is created.
            # Easier: Create a persistent directory in OUTBOX/temp_overlays/{stem}
            
            # Actually, let's just copy the needed frames to a new persistent dir
            # Or better, change the logic above to not use TemporaryDirectory if skip_encoding is True.
            # But that requires refactoring the 'with' block.
            
            # Simple approach: Copy to a persistent temp location
            persistent_dir = Path(output_path).parent / f"temp_frames_{stem}"
            if persistent_dir.exists():
                shutil.rmtree(persistent_dir)
            shutil.copytree(tmpdir_path, persistent_dir)
            print(f"   Frames saved to: {persistent_dir}")
            return persistent_dir

        framerate_str = f"{fps.numerator}/{fps.denominator}"
        pattern = str(tmpdir_path / "%06d.png")
        cmd = [
            ffmpeg_bin,
            "-y",
            "-framerate",
            framerate_str,
            "-i",
            pattern,
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
            "-r",
            framerate_str,
            "-t",
            str(duration),
            str(output_path),
        ]
        
        if stem:
            omega_db.update_job_via_track(stem, status="Encoding Overlay Video", progress=90.0)

        try:
            # Fix: Redirect stdin to DEVNULL to prevent process suspension (SIGTTIN)
            subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
        except subprocess.CalledProcessError as exc:
            print(f"ffmpeg failed: {exc}")
            if stem: omega_db.update_job_via_track(stem, status="Error: Overlay Encoding Failed", progress=0)
            sys.exit(1)

    print(f"Overlay render complete: {output_path}")
    return Path(output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Render subtitle overlay as ProRes 4444 MOV with alpha."
    )
    parser.add_argument("video_path", help="Path to the source video")
    parser.add_argument("subs_json_path", help="Path to normalized subtitles JSON")
    parser.add_argument("output_path", help="Where to write the overlay MOV")
    parser.add_argument(
        "profile",
        nargs="?",
        default="AppleTV_IS",
        help="Styling profile key (default: AppleTV_IS)",
    )

    args = parser.parse_args()
    render_overlay(
        args.video_path,
        args.subs_json_path,
        args.output_path,
        args.profile,
    )
