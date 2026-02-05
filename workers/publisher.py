import os
import time
import shutil
import subprocess
import re
import json
import select
import logging
from pathlib import Path
from datetime import datetime
import config
import omega_db

# Import renderers (assuming they are in root)
# We might need to add root to sys.path if running as module
import sys
sys.path.append(str(config.BASE_DIR))
try:
    from burn_in import burn_subtitles as burn_in_composite
    from subs_render_overlay import render_overlay
except ImportError:
    # Fallback if running from root
    try:
        from burn_in import burn_subtitles as burn_in_composite
        from subs_render_overlay import render_overlay
    except ImportError:
        pass # Will fail later if needed

logger = logging.getLogger("OmegaManager.Publisher")

ASS_HEADER = """[Script Info]
Title: Omega TV Iceland Broadcast Subtitles
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H8A000000,&H8A000000,-1,0,0,0,100,100,0,0,4,28,0,2,20,20,50,1
Style: RuvBox,SF Pro Display,65,&H00FFFFFF,&H000000FF,&H33000000,&H33000000,0,0,0,0,100,100,0,0,3,2,0,2,50,50,65,1
"""

def iso_now():
    return datetime.now().isoformat()

def adjust_ass_time(time_str, delta_ms):
    parts = time_str.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s_parts = parts[2].split('.')
    s = int(s_parts[0])
    cs = int(s_parts[1])
    total_ms = (h * 3600 + m * 60 + s) * 1000 + (cs * 10)
    new_ms = max(0, total_ms + delta_ms)
    
    new_h = new_ms // 3600000
    rem = new_ms % 3600000
    new_m = rem // 60000
    rem = rem % 60000
    new_s = rem // 1000
    new_cs = (rem % 1000) // 10
    return f"{new_h}:{new_m:02d}:{new_s:02d}.{new_cs:02d}"

def build_encoder_args(profile: dict) -> list:
    """
    Build FFmpeg encoder arguments from a delivery profile.
    Supports both hardware (videotoolbox) and software (libx264) encoders.
    """
    encoder = profile.get("encoder", "hevc_videotoolbox")
    args = ["-c:v", encoder]
    
    if "videotoolbox" in encoder:
        # Hardware encoder - uses bitrate mode
        if profile.get("bitrate"):
            args.extend(["-b:v", profile["bitrate"]])
        if profile.get("maxrate"):
            args.extend(["-maxrate", profile["maxrate"]])
        if profile.get("bufsize"):
            args.extend(["-bufsize", profile["bufsize"]])
        # Add QuickTime compatibility tag for HEVC
        if "hevc" in encoder:
            args.extend(["-tag:v", "hvc1"])
    else:
        # Software encoder (libx264) - uses CRF or bitrate
        if profile.get("preset"):
            args.extend(["-preset", profile["preset"]])
        if profile.get("crf"):
            args.extend(["-crf", profile["crf"]])
        elif profile.get("bitrate"):
            args.extend(["-b:v", profile["bitrate"]])
        if profile.get("maxrate"):
            args.extend(["-maxrate", profile["maxrate"]])
        if profile.get("bufsize"):
            args.extend(["-bufsize", profile["bufsize"]])
    
    # Add any extra profile-specific args
    if profile.get("extra_args"):
        args.extend(profile["extra_args"])
        
    return args

def _run_ffmpeg_with_progress(cmd, stem, output_file, video_path):
    """
    Runs FFmpeg with real-time progress tracking updates to DB.
    """
    try:
        # Use Popen for real-time progress parsing
        logger.info(f"   🐢 Encoding with progress tracking...")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, 
            universal_newlines=True,
            bufsize=1,
            stdin=subprocess.DEVNULL  # Critical: Prevent SIGTTOU when backgrounded
        )
        
        # Get total duration for progress calculation
        duration_sec = 0
        try:
            probe_cmd = [
                config.FFPROBE_BIN, 
                "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                str(video_path) # Ensure video_path is string for subprocess
            ]
            res = subprocess.run(probe_cmd, capture_output=True, text=True)
            duration_sec = float(res.stdout.strip())
        except Exception as e:
            logger.warning(f"Could not determine duration for progress: {e}")
            duration_sec = 0

        # Read output line by line
        start_time = time.time()
        time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        
        # Read output line by line with timeout
        start_time = time.time()
        last_output_time = time.time()
        time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        
        while True:
            # Check for output with 60s timeout
            reads = [process.stdout.fileno()]
            ret = select.select(reads, [], [], 60.0)
            
            if not ret[0]:
                # Timeout reached
                logger.error("❌ FFmpeg process stalled (no output for 60s). Killing.")
                process.kill()
                raise RuntimeError("FFmpeg process stalled")

            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                last_output_time = time.time()
                line = line.strip()
                # Parse time=HH:MM:SS.mm
                match = time_re.search(line)
                if match and duration_sec > 0:
                    h, m, s = match.groups()
                    current_sec = int(h)*3600 + int(m)*60 + float(s)
                    pct = (current_sec / duration_sec) * 100
                    
                    if time.time() - start_time > 2:
                        omega_db.update_job_via_track(stem, progress=round(pct, 1), status=f"Burning {int(pct)}%")
                        start_time = time.time()
        
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)
            
        return output_file
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Burn Failed: {e}")
        raise e


def publish(video_path: Path, srt_path: Path, subtitle_style: str = "Classic", 
            delivery_profile: str = None):
    """
    Burns subtitles into video using the specified style and delivery profile.
    ...
    """
    # ... (existing content logic is unchanged until we hit subprocess calls) ...
    # Wait, I cannot replace the whole function easily.
    # I should insert the helper BEFORE publish, and modify calls inside publish.
    pass 

# I will restart this Replace to be granular.
# First insert the helper.

    """
    Burns subtitles into video using the specified style and delivery profile.
    
    Args:
        video_path: Path to source video
        srt_path: Path to subtitle SRT file  
        subtitle_style: "Classic" (RuvBox), "Modern" (Default/Shadow), or "Apple"
        delivery_profile: Encoding profile key from config.DELIVERY_PROFILES
                         (e.g., "broadcast_hevc", "broadcast_h264", "web", "archive", "universal")
                         If None, uses config.DEFAULT_DELIVERY_PROFILE
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    # KEY CHANGE: Use SRT stem (Job ID) for output naming, not Video stem (Original Name)
    stem = srt_path.stem.replace("_SUBBED", "")
    output_dir = config.DELIVERY_DIR / "VIDEO"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_path = output_dir / f"{stem}_SUBBED.mp4"
    
    # Get delivery profile
    profile_key = delivery_profile or config.DEFAULT_DELIVERY_PROFILE
    profile = config.DELIVERY_PROFILES.get(profile_key)
    if not profile:
        logger.warning(f"Unknown delivery profile '{profile_key}', falling back to broadcast_hevc")
        profile = config.DELIVERY_PROFILES["broadcast_hevc"]
        profile_key = "broadcast_hevc"
    
    logger.info(f"🔥 Burning Subtitles: {stem} (Style: {subtitle_style}, Profile: {profile['name']})")
    
    # Map User Style to ASS Style Name
    style_map = config.BURN_METHOD_MAP
    ass_style_name = style_map.get(subtitle_style, "Apple")
    
    if ass_style_name == "Apple":
        logger.info("🍎 Using Apple Style (Overlay Engine)")
        
        # 1. Convert SRT to JSON for the Overlay Engine
        temp_json_path = config.VAULT_DATA / f"{stem}_OVERLAY_INPUT.json"
        parse_srt_to_overlay_json(srt_path, temp_json_path)
        
        # 2. Render Overlay (ProRes 4444 MOV)
        overlay_mov_path = config.VAULT_DATA / f"{stem}_OVERLAY.mov"
        
        render_overlay(
            video_path=str(video_path),
            subs_json_path=str(temp_json_path),
            output_path=str(overlay_mov_path),
            profile_name="AppleTV_IS",
            stem=stem
        )
        
        # 3. Composite Overlay onto Video (uses delivery profile encoder)
        logger.info("   Compositing Overlay...")
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-i", str(overlay_mov_path),
            "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p",
            "-map", "0:a",
        ]
        # Add encoder args from profile
        cmd.extend(build_encoder_args(profile))
        # Add color and output settings
        cmd.extend([
            "-color_primaries", "1",
            "-color_trc", "1", 
            "-colorspace", "1",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "copy",
            str(output_path)
        ])
        
    else:
        # Standard ASS Burn-in (Classic / Modern)
        
        # 1. Generate ASS
        ass_path = config.VAULT_DATA / f"{stem}.ass"
        generate_ass_from_srt(srt_path, ass_path, style_name=ass_style_name)
        
        # ESCAPE PATH FOR FFMPEG FILTER
        ass_path_str = str(ass_path)
        ass_path_escaped = ass_path_str.replace("\\", "/").replace(":", "\\:").replace("'", "'\\\\''"  )
        
        # Build filter chain: ass with fontsdir, then format conversion
        vf_filter = f"ass='{ass_path_escaped}':fontsdir='/System/Library/Fonts/',format=yuv420p"
        
        # STABILITY OVERRIDE: 
        # The 'ass' filter is notoriously unstable with hardware acceleration or high-speed hardware encoders (Code 254).
        # For Classic/Modern looks, we force use of the 'universal' (libx264) profile or at least a software encoder.
        if "videotoolbox" in profile.get("encoder", "") or subtitle_style in ["Classic", "Modern"]:
            logger.info(f"   ⚠️ Forcing stable software encoding for {subtitle_style} style")
            stable_profile = config.DELIVERY_PROFILES.get("universal", profile)
            encoder_args = build_encoder_args(stable_profile)
        else:
            encoder_args = build_encoder_args(profile)

        # Build command
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-map", "0:v", "-map", "0:a",
            "-vf", vf_filter,
        ]
        # Add encoder args
        cmd.extend(encoder_args)
        # Add color and output settings
        cmd.extend([
            "-color_primaries", "1",
            "-color_trc", "1",
            "-colorspace", "1",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "copy",
            str(output_path)
        ])
    
    logger.info(f"   Running FFmpeg ({profile['name']}): {' '.join(cmd)}")
    
    # --- RENDER TO LOCAL TEMP FIRST ---
    # To prevent external drive failures (USB IO saturation) during high-speed encoding,
    # we render to the internal drive first, then move the result.
    temp_dir = config.BASE_DIR / "temp_render"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_output_path = temp_dir / output_path.name
    
    # Replace output path in command
    # cmd[-1] is the output path
    cmd[-1] = str(temp_output_path)
    
    try:
        # Prevent SIGTTOU suspension by explicitly detaching stdin
        _run_ffmpeg_with_progress(cmd, stem, temp_output_path, video_path)
        
        # Move to final destination
        logger.info(f"   🚚 Moving local render to destination: {output_path}")
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(temp_output_path), str(output_path))
        
        return output_path
    
    except Exception as e:
        # Cleanup temp
        if temp_output_path.exists():
            temp_output_path.unlink()
        raise e
    
    return output_path

def parse_srt_to_overlay_json(srt_path, json_path):
    """
    Parses SRT and saves as JSON for subs_render_overlay.
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()
        
    blocks = srt_content.strip().split('\n\n')
    events = []
    
    for block in blocks:
        block = block.strip()  # Remove leading/trailing whitespace
        lines = block.split('\n')
        if len(lines) < 3: continue
        
        time_line = lines[1]
        text_lines = lines[2:]
        
        # Validate time line format
        if ' --> ' not in time_line:
            continue
            
        try:
            start_str, end_str = time_line.split(' --> ')
        except ValueError:
            continue
        
        # Convert time to seconds
        def time_to_sec(t_str):
            # 00:00:01,500
            h, m, s = t_str.replace(',', '.').split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
            
        start_sec = time_to_sec(start_str)
        end_sec = time_to_sec(end_str)
        
        # Check for positioning tags
        position = "bottom"
        clean_lines = []
        for line in text_lines:
            line = line.strip()
            if "{\\an8}" in line:
                position = "top"
                line = line.replace("{\\an8}", "")
            clean_lines.append(line)

        events.append({
            "start": start_sec,
            "end": end_sec,
            "lines": clean_lines,
            "position": position
        })
        
    data = {
        "video_width": 1920,
        "video_height": 1080,
        "events": events
    }
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def convert_srt_time_to_ass(srt_time):
    # 00:00:01,500 -> 0:00:01.50
    try:
        h, m, s_ms = srt_time.split(':')
        s, ms = s_ms.split(',')
        return f"{int(h)}:{m}:{s}.{ms[:2]}"
    except ValueError:
        return "0:00:00.00"

def generate_ass_from_srt(srt_path, ass_path, style_name="RuvBox"):
    """
    Converts SRT to ASS with the specific style applied to all events.
    """
    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()
        
    # Parse SRT (simple regex or library)
    # We can use a simple parser since our SRTs are clean.
    blocks = srt_content.strip().split('\n\n')
    
    events = []
    for block in blocks:
        block = block.strip()  # Remove leading/trailing whitespace
        lines = block.split('\n')
        if len(lines) < 3: continue
        
        # index = lines[0]
        time_line = lines[1]
        
        # Validate time line format
        if ' --> ' not in time_line:
            logger.warning(f"Skipping malformed SRT block (no -->): {repr(time_line[:50])}")
            continue
            
        try:
            start_str, end_str = time_line.split(' --> ')
        except ValueError:
            logger.warning(f"Skipping malformed SRT time line: {repr(time_line)}")
            continue
            
        start_ass = convert_srt_time_to_ass(start_str)
        end_ass = convert_srt_time_to_ass(end_str)

        # Custom RuvBox Logic: Split lines to control box overlap/spacing
        if style_name == "RuvBox":
            # FORCE CLEARANCE: Subtract 100ms from end time to prevent stacking
            # Use end_ass (H:MM:SS.cs) not end_str (H:MM:SS,mmm) to avoid math errors
            end_ass_adjusted = adjust_ass_time(end_ass, -100)
            
            base_margin_v = 65
            line_height = 68 # Balanced spacing (65 Font + 2 Outline + 1px Overlap)
            
            # Check for Top positioning override
            raw_lines = lines[2:]
            is_top = False
            if raw_lines and raw_lines[0].startswith("{\\an8}"):
                is_top = True
                raw_lines[0] = raw_lines[0].replace("{\\an8}", "")
            
            if is_top:
                # Top Alignment: Render specific events Top-Down
                # We interpret {\an8} as request for Top positioning.
                # Since we are generating separate events, we must tag EACH event with {\an8}
                # and calculate margin from TOP (which \an8 implies for MarginV).
                for i, line in enumerate(raw_lines):
                    margin_v = base_margin_v + (i * line_height)
                    clean_line = f"\\h\\h{line.strip()}\\h\\h"
                    # We must prepend {\an8} to every line so it anchors to top
                    events.append(f"Dialogue: 0,{start_ass},{end_ass_adjusted},{style_name},,0,0,{margin_v},,{{\\an8}}{clean_line}")
            else:
                # Bottom Alignment: Render Bottom-Up (Reversed)
                # MarginV is from Bottom.
                for i, line in enumerate(reversed(raw_lines)):
                    margin_v = base_margin_v + (i * line_height)
                    clean_line = f"\\h\\h{line.strip()}\\h\\h"
                    events.append(f"Dialogue: 0,{start_ass},{end_ass_adjusted},{style_name},,0,0,{margin_v},,{clean_line}")
        else:
            # Standard handling
            text = "\\N".join(lines[2:])
            events.append(f"Dialogue: 0,{start_ass},{end_ass},{style_name},,0,0,0,,{text}")
        
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ASS_HEADER + "\n")
        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for event in events:
            f.write(event + "\n")
            
    return ass_path

# Alias for compatibility
srt_to_ass = generate_ass_from_srt

def find_video_file(stem):
    extensions = {'.mp4', '.mov', '.mkv', '.m4v', '.mpg', '.mpeg', '.moc'}
    for ext in extensions:
        path = config.VAULT_VIDEOS / f"{stem}{ext}"
        if path.exists():
            logger.info(f"✅ Found video: {path.name}")
            return path
            
    clean_stem = stem.replace("_RUVBOX", "").replace("_MODERN", "")
    if clean_stem != stem:
        for ext in extensions:
            path = config.VAULT_VIDEOS / f"{clean_stem}{ext}"
            if path.exists():
                logger.info(f"✅ Found video (Fuzzy): {path.name}")
                return path
    return None

def burn(srt_file: Path, forced_style=None):
    """
    Burns subtitles into video.
    Returns output video path.
    """
    stem = srt_file.stem
    logger.info(f"🔥 Processing: {srt_file.name}")
    
    # Fetch job for style info and source video path
    job = omega_db.get_job_via_track(stem) or {}
    meta = job.get("meta") or {}
    if isinstance(meta, str):
        try:
             meta = json.loads(meta)
        except Exception:
             meta = {}
             
    # Try finding video via Meta (Best for Job-ID system)
    video_path = None
    if meta.get("vault_path"):
        cand = Path(meta["vault_path"])
        if cand.exists():
            video_path = cand
            logger.info(f"✅ Found video (via Meta): {video_path.name}")

    # Fallback to legacy lookup
    if not video_path:
        video_path = find_video_file(stem)
        
    if not video_path:
        raise Exception(f"Video not found in Vault for {stem}")

    # --- DETERMINE STYLE ---
    # Priority:
    # 1. Forced Style (Function Arg - not used here yet)
    # 2. Filename Tags (_RUVBOX, _MODERN)
    # 3. DB 'subtitle_style' field
    # 4. Config Map based on Show Name
    # 5. Default
    
    chosen_style = None
    
    # 1. Filename Tags
    if "_RUVBOX" in stem:
        chosen_style = "RUV_BOX"
    elif "_MODERN" in stem:
        chosen_style = "OMEGA_MODERN"
        
    # 2. DB Subtitle Style
    if not chosen_style:
        db_style = job.get("subtitle_style")
        if db_style:
            # Map "Classic" -> "RuvBox" using config
            # We need to import config if not already imported or use hardcoded map
            # The legacy file imports config? No, it imports os, time... 
            # Let's check imports. It imports omega_db.
            # I'll add a simple map here to be safe and consistent with config.py
            style_map_internal = {
                "Classic": "RUV_BOX",
                "RuvBox": "RUV_BOX",
                "Modern": "OMEGA_MODERN",
                "OMEGA_MODERN": "OMEGA_MODERN",
                "Apple": "OMEGA_MODERN"
            }
            chosen_style = style_map_internal.get(db_style, "RUV_BOX") # Default to RUV_BOX if unknown
            
    # 3. Show Name Match (Fallback)
    if not chosen_style:
         for key, style in config.STYLE_MAP.items():
            if key != "DEFAULT" and key.lower() in stem.lower().replace("_", " "):
                chosen_style = style
                break
                
    if not chosen_style:
        chosen_style = "RUV_BOX" # Default to RUV_BOX for safety as per user request
            
    print(f"   🎨 Style Selected: {chosen_style}")
    output_file = config.VIDEO_DIR / f"{stem}_SUBBED.mp4"

    if chosen_style == "RUV_BOX":
        logger.info("🎬 Burning RÚV Style (Direct ASS)...")
        temp_ass = config.VIDEO_DIR / f"{stem}_temp.ass"
        srt_to_ass(srt_file, temp_ass, style_name="RuvBox")
        
        ass_path_escaped = str(temp_ass).replace(":", "\\:")
        # Force CPU Encoding (libx264) for stability
        # Hardware encoding (h264_videotoolbox) caused corruption/playback issues.
        logger.info("   🐢 Using CPU Encoding (libx264) for maximum compatibility")
        cmd = [
            config.FFMPEG_BIN, "-y",
            "-i", str(video_path),
            "-vf", f"ass='{ass_path_escaped}'",
            "-c:v", "libx264", "-preset", "faster", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart",
            str(output_file)
        ]
        
        try:
            # Use Popen for real-time progress parsing
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, 
                universal_newlines=True,
                bufsize=1
            )
            
            # Get total duration for progress calculation
            total_duration = 0
            try:
                # Try getting from DB first
                if job.get("program_id"):
                    prog = omega_db.get_program(job.get("program_id"))
                    if prog:
                        total_duration = float(prog.get("duration_seconds") or 0)
                
                # Fallback: estimate from video file if DB missing
                if total_duration <= 0 and video_path.exists():
                    # Quick ffprobe
                    probe_cmd = [
                        config.FFPROBE_BIN, 
                        "-v", "error", 
                        "-show_entries", "format=duration", 
                        "-of", "default=noprint_wrappers=1:nokey=1", 
                        str(video_path)
                    ]
                    total_duration = float(subprocess.check_output(probe_cmd).strip())
            except Exception as e:
                logger.warning(f"Could not determine duration for progress: {e}")
                total_duration = 0

            logger.info(f"   🐢 Encoding with progress tracking (Duration: {total_duration}s)...")
            
            # Progress loop
            import re
            time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")
            last_progress_update = 0
            
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    # Parse time=00:00:00.00
                    match = time_pattern.search(line)
                    if match and total_duration > 0:
                        h, m, s = match.groups()
                        current_seconds = int(h) * 3600 + int(m) * 60 + float(s)
                        progress = min(99.0, (current_seconds / total_duration) * 100)
                        
                        # Update DB every 2 seconds or 5% change to save DB writes
                        now = time.time()
                        if (now - last_progress_update > 2.0):
                            # Update job progress
                            omega_db.update_job_via_track(stem, progress=progress, status=f"Burning {int(progress)}%")
                            last_progress_update = now
            
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd)
                
            logger.info(f"✅ Burn Complete: {output_file.name}")
            omega_db.update_job_via_track(stem, progress=100.0, status="Start Uploading")
            temp_ass.unlink(missing_ok=True)
            return output_file
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Burn Failed: {e}")
            raise e

    else:
        # OMEGA MODERN (Overlay)
        logger.info("🎬 Burning Omega Modern Style (Overlay)...")
        
        # 1. Check Normalized JSON
        normalized_json = config.SRT_DIR / f"{stem}_normalized.json"
        if not normalized_json.exists():
             # Try to regenerate? Or fail?
             # For now, fail. Manager should have ensured it.
             raise Exception("Normalized JSON not found")

        # 2. Render Overlay
        import hashlib
        safe_stem = hashlib.md5(stem.encode('utf-8')).hexdigest()
        temp_frames_dir = config.VIDEO_DIR / f"temp_frames_{safe_stem}"
        
        # Cleanup old
        if temp_frames_dir.exists(): shutil.rmtree(temp_frames_dir)
        
        overlay_path = render_overlay(str(video_path), str(normalized_json), str(config.VIDEO_DIR / f"{stem}_overlay.mov"), "AppleTV_IS", stem=stem, skip_encoding=True)
        
        # Move to safe path if needed (render_overlay returns path)
        if overlay_path != temp_frames_dir:
             if temp_frames_dir.exists(): shutil.rmtree(temp_frames_dir)
             shutil.move(str(overlay_path), str(temp_frames_dir))
             overlay_path = temp_frames_dir

        # 3. Composite
        try:
            burn_in_composite(str(video_path), overlay_path, str(output_file), stem=stem)
            
            # Cleanup
            if overlay_path.is_dir(): shutil.rmtree(overlay_path)
            if normalized_json.exists(): normalized_json.unlink()
            
            logger.info(f"✅ Burn Complete: {output_file.name}")
            return output_file
            
        except Exception as e:
            if overlay_path.is_dir(): shutil.rmtree(overlay_path)
            raise e
