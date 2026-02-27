from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class SubtitleEvent:
    """
    A robust, type-checked representation of a single subtitle block.
    Replacing the fragile dictionaries used in the legacy finalizer.
    """
    start: float
    end: float
    text: str = ""
    lines: List[str] = field(default_factory=list)
    position: str = "bottom"  # e.g., 'bottom' or 'top' (for \an8 positioning)
    
    # Audio Event Metadata (ElevenLabs Scribe v2)
    is_audio_event: bool = False
    audio_event_type: str = ""
    preserve_in_subtitle: bool = False
    
    # Timing constraints
    words: List[dict] = field(default_factory=list) # Optional word-level timing
    
    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)
    
    @property
    def cps(self) -> float:
        """Characters Per Second."""
        if self.duration <= 0:
            return 0.0
        # Text without audio markers like [LAUGHTER]
        clean_text = self.text
        if self.is_audio_event and "[" in self.text and "]" in self.text:
           return 0.0 # Audio events don't count towards reading CPS

        # Only count visible characters
        char_count = len(clean_text)
        return char_count / self.duration

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleEvent":
        return cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            text=str(data.get("text", "")),
            lines=data.get("lines", []),
            position=str(data.get("position", "bottom")),
            is_audio_event=bool(data.get("is_audio_event", False)),
            audio_event_type=str(data.get("audio_event_type", "")),
            preserve_in_subtitle=bool(data.get("preserve_in_subtitle", False)),
            words=data.get("words", [])
        )
        
    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "lines": self.lines,
            "position": self.position,
            "is_audio_event": self.is_audio_event,
            "audio_event_type": self.audio_event_type,
            "preserve_in_subtitle": self.preserve_in_subtitle,
            "words": self.words
        }
