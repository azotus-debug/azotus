from pathlib import Path
from typing import List
import logging
from ..models import SubtitleEvent

logger = logging.getLogger("omega_finalizer.exporters.srt")

def format_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def generate_srt(events: List[SubtitleEvent], output_path: Path) -> Path:
    """Export subtitle events to standard SubRip (.srt) format."""
    blocks = []
    
    for i, event in enumerate(events, 1):
        if not event.text.strip() and not event.lines:
            continue
            
        start_ts = format_timestamp(event.start)
        end_ts = format_timestamp(event.end)
        
        # Position tag for Top / Bottom
        position_tag = "{\\an8}" if event.position == "top" else ""
        
        # Prefer pre-formatted lines, fallback to raw text
        text = "\n".join(event.lines) if event.lines else event.text
        
        blocks.append(f"{i}\n{start_ts} --> {end_ts}\n{position_tag}{text}\n\n")
        
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(blocks)
        
    logger.info(f"✅ Created SRT: {output_path.name}")
    return output_path
