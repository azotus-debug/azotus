import os
import json
import subprocess
import logging
import shutil
from pathlib import Path
from providers.openai_tts import OpenAITTSProvider

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORK_DIR = Path("jobs")  # Adjust if your structure is different
TEMP_DIR = Path("temp_dubbing")

class Dubber:
    def __init__(self, job_stem, job_dir):
        self.job_stem = job_stem
        self.job_dir = Path(job_dir)
        self.tts = OpenAITTSProvider()
        self.temp_dir = TEMP_DIR / job_stem
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def run(self):
        """
        Main execution flow:
        1. Load segments
        2. Generate TTS clips
        3. Build concat list (with silence)
        4. Render TTS Track
        5. Mix and Finalize
        """
        logger.info(f"Starting Dubbing for {self.job_stem}...")
        
        # 1. Load Data
        json_path = self.job_dir / f"{self.job_stem}_data.json"
        if not json_path.exists():
            raise FileNotFoundError(f"Job data not found: {json_path}")
            
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        segments = data.get("segments", [])
        if not segments:
            logger.warning("No segments found for dubbing.")
            return

        # 2. Generate Clips & Build Timeline
        concat_list_path = self.temp_dir / "concat_list.txt"
        clips = []
        
        current_time = 0.0
        
        with open(concat_list_path, 'w', encoding='utf-8') as list_file:
            for i, seg in enumerate(segments):
                start = float(seg.get("start", 0))
                end = float(seg.get("end", 0))
                text = seg.get("translated_text") or seg.get("text", "")
                
                # Gap Handling
                gap = start - current_time
                if gap > 0.1: # Min gap 100ms
                    silence_path = self.temp_dir / f"silence_{i}.wav"
                    self._generate_silence(gap, silence_path)
                    list_file.write(f"file '{silence_path.resolve()}'\n")
                    # list_file.write(f"duration {gap}\n") # Optional, ffmpeg reads file header
                
                # TTS Generation
                clip_path = self.temp_dir / f"clip_{i}.mp3"
                if not clip_path.exists():
                    self.tts.generate_speech(text, clip_path)
                
                # Speed Correction (if clip > slot)
                # For MVP, we effectively 'trust' the TTS or let it bleed slightly?
                # Better: Measure duration. If too long, speed up.
                # Use ffprobe to get duration
                clip_dur = self._get_duration(clip_path)
                slot_dur = end - start
                
                final_clip_path = clip_path
                
                # If clip is significantly longer than slot (e.g. > 10% overflow), compact it
                # Logic: If clip is 5s and slot is 3s -> Speedup 1.66x
                # Limit speedup to 1.5x to avoid chipmunk effect? 
                # For now, let's keep it simple: Just insert the clip.
                # If it overlaps the next, the next gap calculation will be negative?
                # We need to update current_time based on ACTUAL audio length, not segment end?
                # NO. We want to sync to video start times. 
                # So we must enforce the gap is relative to VIDEO time.
                # But if Clip A bleeds into Clip B's start time, we have a problem.
                
                # Strategy:
                # Always insert silence to reach 'start'. 
                # If 'current_time' (end of prev clip) is ALREADY past 'start', we have overlap.
                # In that case, we can't insert silence. We effectively just start the next clip immediately (desync).
                # OR we speed up the previous clip.
                
                if gap < 0:
                    logger.warning(f"Segment {i} overlaps previous by {abs(gap):.2f}s. Audio will be desynced.")
                    # We can't fix past. We just skip silence and write next file.
                
                list_file.write(f"file '{final_clip_path.resolve()}'\n")
                
                # Update current time
                # We assume the clip plays for its full duration.
                current_time = start + clip_dur # Logic: New time is Start + Duration
                # Wait, if we added silence, we are at 'start'. Then we play clip.
                # So current_time becomes start + clip_dur.
                
        # 3. Render TTS Track
        mixed_tts_path = self.temp_dir / "tts_full.wav"
        # Use concat demuxer
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path),
            "-c:a", "pcm_s16le", # Convert to wav for mix
            str(mixed_tts_path)
        ]
        subprocess.run(cmd, check=True)
        
        # 4. Mix with Video (Sidechain Ducking)
        # Input 0: Video (with audio)
        # Input 1: TTS
        # Logic: 
        # [0:a] volume=0.2 [bg]
        # [1:a] volume=1.0 [fg]
        # [bg][fg] amix [out]
        
        video_input = self.job_dir / f"{self.job_stem}.mp4" # Source
        # Or should we work from the SUBTITLED version? 
        # Usually user wants subs + dub. Let's assume we take the 'burned' video if available?
        # Let's stick to source for now or define input arg.
        
        final_output = self.job_dir / f"{self.job_stem}_dubbed.mp4"
        
        # Simple volume reduction (Duck)
        filter_complex = "[0:a]volume=0.2[bg];[1:a]volume=1.2[fg];[bg][fg]amix=inputs=2:duration=first[aout]"
        
        cmd_mix = [
            "ffmpeg", "-y",
            "-i", str(video_input),
            "-i", str(mixed_tts_path),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy", # Copy video stream (fast)
            "-c:a", "aac", "-b:a", "192k",
            str(final_output)
        ]
        
        logger.info("Mixing audio...")
        subprocess.run(cmd_mix, check=True)
        
        logger.info(f"Dubbing Complete: {final_output}")
        
        # Cleanup
        # shutil.rmtree(self.temp_dir) # Keep for debug for now

    def _generate_silence(self, duration, path):
        """Generates a silence wav file of specific duration."""
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration),
            "-q:a", "9",
            str(path)
        ]
        # Suppress output
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def _get_duration(self, path):
        """Returns duration of audio file in seconds."""
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return float(result.stdout.strip())
        except:
            return 0.0

if __name__ == "__main__":
    # Test stub
    pass
