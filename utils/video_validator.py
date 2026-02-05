"""
Video validation utility for SubtitleWorkflow.

Validates video files before processing to catch issues early:
- File exists and is readable
- Has valid video/audio streams
- Has reasonable duration
- Uses supported codecs
"""

import subprocess
import json
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def validate_video(
    video_path: Path,
    max_duration_hours: float = 3.0,
    max_size_gb: float = 20.0,
    require_audio: bool = True
) -> Tuple[bool, str]:
    """
    Validate video file before processing.

    Args:
        video_path: Path to video file
        max_duration_hours: Maximum allowed duration in hours
        max_size_gb: Maximum allowed file size in GB
        require_audio: Whether audio stream is required (for transcription)

    Returns:
        (is_valid, error_message)
        - is_valid: True if video is valid, False otherwise
        - error_message: None if valid, error description if invalid
    """
    try:
        # Check 1: File exists and is readable
        if not video_path.exists():
            return False, "File not found"

        if not video_path.is_file():
            return False, "Path is not a file"

        # Check 2: File size
        size_gb = video_path.stat().st_size / (1024**3)
        if size_gb > max_size_gb:
            return False, f"File too large: {size_gb:.1f}GB (max {max_size_gb}GB)"

        # Check 3: Use ffprobe to validate video metadata
        logger.debug(f"Validating video: {video_path.name}")

        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(video_path)
            ],
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout for probe
        )

        if result.returncode != 0:
            logger.error(f"ffprobe failed for {video_path.name}: {result.stderr}")
            return False, "Invalid video file (ffprobe failed - file may be corrupt)"

        # Parse ffprobe output
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, "Invalid video file (could not parse metadata)"

        # Check 4: Has format information
        if 'format' not in data:
            return False, "Invalid video file (no format information)"

        format_info = data['format']

        # Check 5: Has duration
        duration = float(format_info.get('duration', 0))
        if duration == 0:
            return False, "Video has no duration (may be corrupt or streaming format)"

        duration_hours = duration / 3600
        if duration_hours > max_duration_hours:
            return False, f"Video too long: {duration_hours:.1f}h (max {max_duration_hours}h)"

        # Check 6: Has streams
        if 'streams' not in data or not data['streams']:
            return False, "No video or audio streams found"

        streams = data['streams']

        # Check 7: Has video stream
        video_streams = [s for s in streams if s.get('codec_type') == 'video']
        if not video_streams:
            return False, "No video stream found"

        # Check 8: Has audio stream (if required)
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
        if require_audio and not audio_streams:
            return False, "No audio stream found (required for transcription)"

        # Check 9: Video codec is supported
        video_codec = video_streams[0].get('codec_name', 'unknown')
        unsupported_codecs = []  # Add problematic codecs here if discovered
        if video_codec in unsupported_codecs:
            return False, f"Unsupported video codec: {video_codec}"

        # All checks passed
        logger.info(
            f"✅ Video validated: {video_path.name} "
            f"({duration_hours:.1f}h, {size_gb:.1f}GB, "
            f"codec: {video_codec}, "
            f"audio: {len(audio_streams)} stream(s))"
        )
        return True, None

    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timeout for {video_path.name}")
        return False, "Video validation timed out (file may be corrupt or very large)"

    except Exception as e:
        logger.error(f"Validation error for {video_path.name}: {e}")
        return False, f"Validation error: {str(e)}"


def get_video_info(video_path: Path) -> dict:
    """
    Get detailed video information using ffprobe.

    Returns dict with:
    - duration_seconds
    - duration_hours
    - size_gb
    - video_codec
    - audio_codec
    - width
    - height
    - fps
    """
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(video_path)
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return {}

        data = json.loads(result.stdout)
        format_info = data.get('format', {})
        streams = data.get('streams', [])

        video_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})
        audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), {})

        duration = float(format_info.get('duration', 0))
        size_gb = video_path.stat().st_size / (1024**3)

        return {
            'duration_seconds': duration,
            'duration_hours': duration / 3600,
            'size_gb': size_gb,
            'video_codec': video_stream.get('codec_name', 'unknown'),
            'audio_codec': audio_stream.get('codec_name', 'unknown'),
            'width': video_stream.get('width', 0),
            'height': video_stream.get('height', 0),
            'fps': eval(video_stream.get('r_frame_rate', '0/1'))  # e.g., "30000/1001"
        }

    except Exception as e:
        logger.error(f"Could not get video info: {e}")
        return {}


if __name__ == "__main__":
    # Test validation with test files
    import sys

    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        test_file = Path(sys.argv[1])
        is_valid, error = validate_video(test_file)

        if is_valid:
            print(f"✅ VALID: {test_file.name}")
            info = get_video_info(test_file)
            print(f"   Duration: {info.get('duration_hours', 0):.2f}h")
            print(f"   Size: {info.get('size_gb', 0):.2f}GB")
            print(f"   Codec: {info.get('video_codec', 'unknown')}")
        else:
            print(f"❌ INVALID: {test_file.name}")
            print(f"   Error: {error}")
    else:
        print("Usage: python video_validator.py <video_file>")
