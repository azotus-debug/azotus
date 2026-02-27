from pathlib import Path
from typing import List
import logging
from ..models import SubtitleEvent

logger = logging.getLogger("omega_finalizer.exporters.ttml")

def format_timestamp_ttml(seconds: float) -> str:
    """TTML uses HH:MM:SS.mmm format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"

def escape_xml(text: str) -> str:
    """Escape XML special characters."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))

def generate_ttml(events: List[SubtitleEvent], output_path: Path, lang_code: str = "is") -> Path:
    """
    Generate TTML (Timed Text Markup Language) subtitle file.
    Compatible with Netflix, YouTube, and broadcast workflows.
    """
    ttml_header = f'''<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" xml:lang="{lang_code}">
  <head>
    <styling>
      <style xml:id="defaultStyle" tts:fontFamily="Arial" tts:fontSize="100%" tts:textAlign="center"/>
    </styling>
    <layout>
      <region xml:id="bottom" tts:origin="10% 80%" tts:extent="80% 20%" tts:textAlign="center"/>
      <region xml:id="top" tts:origin="10% 10%" tts:extent="80% 20%" tts:textAlign="center"/>
    </layout>
  </head>
  <body>
    <div>
'''
    ttml_footer = '''    </div>
  </body>
</tt>
'''
    paragraphs = []
    for event in events:
        if not event.text.strip() and not event.lines:
            continue
            
        start = format_timestamp_ttml(event.start)
        end = format_timestamp_ttml(event.end)
        
        lines_list = event.lines if event.lines else [event.text]
        text = "<br/>".join([escape_xml(line) for line in lines_list])
        
        region_id = "top" if event.position == "top" else "bottom"
        paragraphs.append(f'      <p begin="{start}" end="{end}" region="{region_id}">{text}</p>')
        
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ttml_header)
        f.write("\n".join(paragraphs))
        f.write("\n")
        f.write(ttml_footer)
        
    logger.info(f"✅ Created TTML: {output_path.name}")
    return output_path
