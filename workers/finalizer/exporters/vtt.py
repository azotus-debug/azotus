from pathlib import Path
from typing import List
import logging
from ..models import SubtitleEvent

logger = logging.getLogger("omega_finalizer.exporters.vtt")

def format_timestamp_vtt(seconds: float) -> str:
    """VTT uses . instead of , for milliseconds."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"

def generate_vtt(events: List[SubtitleEvent], output_path: Path) -> Path:
    """Export subtitle events to WebVTT (.vtt) format for web players."""
    lines = ["WEBVTT", ""]
    
    for i, event in enumerate(events, 1):
        if not event.text.strip() and not event.lines:
            continue
            
        start_ts = format_timestamp_vtt(event.start)
        end_ts = format_timestamp_vtt(event.end)
        
        text = "\n".join(event.lines) if event.lines else event.text
        
        lines.append(f"{i}")
        if event.position == "top":
             lines.append(f"{start_ts} --> {end_ts} line:10%")
        else:
             lines.append(f"{start_ts} --> {end_ts}")
             
        lines.append(text)
        lines.append("")
        
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    logger.info(f"✅ Created VTT: {output_path.name}")
    return output_path
