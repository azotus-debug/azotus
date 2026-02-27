import logging
from pathlib import Path
from typing import List, Optional
from subtitle_standards import MIN_DURATION, IDEAL_CPS
from .models import SubtitleEvent

logger = logging.getLogger("omega_finalizer.timing")

def fix_overlaps(events: List[SubtitleEvent], min_gap: float = 0.05) -> List[SubtitleEvent]:
    """Ensure a minimum gap between subtitles to avoid visual overlapping."""
    fixed_events = []
    
    for i, event in enumerate(events):
        if i == 0:
            fixed_events.append(event)
            continue
            
        prev_event = fixed_events[-1]
        
        # Calculate gap between previous end and current start
        gap = event.start - prev_event.end
        
        if gap < min_gap:
            # Overlap or too short gap detected! 
            # We fix this by shifting the current subtitle's start time forward
            # and potentially shortening it, ensuring min_gap.
            # Netflix/BBC standard typically requires 2 or 3 frames gap (~0.08s to 0.12s)
            
            # Simple fix: push the start time forward
            new_start = prev_event.end + min_gap
            
            # If pushing start time makes the duration too short, we must shorten the previous one
            if event.end - new_start < MIN_DURATION:
                # We need to share the penalty
                push_amount = min_gap - gap
                prev_event.end -= push_amount / 2
                event.start += push_amount / 2
                # Ensure we didn't destroy prev_event
                if prev_event.end <= prev_event.start:
                    prev_event.end = prev_event.start + 0.1
            else:
                event.start = new_start
        
        fixed_events.append(event)
        
    return fixed_events


def enforce_minimum_duration(events: List[SubtitleEvent]) -> List[SubtitleEvent]:
    """Ensure no subtitle is shorter than MIN_DURATION."""
    for event in events:
        if event.duration < MIN_DURATION:
            # Extend the end time
            event.end = event.start + MIN_DURATION
    return events


def anchor_to_speech(event: SubtitleEvent, next_start: float = None, fps: float = 29.97) -> SubtitleEvent:
    """
    Adjust timing to ensure comfortable reading speed (CPS).
    Extend the subtitle if the CPS is too high, up until 'next_start'.
    """
    if event.duration <= 0:
        return event
        
    current_cps = event.cps
    
    if current_cps > IDEAL_CPS:
        char_count = len(event.text)
        required_duration = char_count / IDEAL_CPS
        natural_end = event.start + required_duration
        
        # We can extend up to next_start - min_gap
        max_end = next_start - 0.05 if next_start else natural_end
        
        new_end = min(natural_end, max_end)
        if new_end > event.end:
            event.end = new_end
            
    return event


def quantize_to_frames(events: List[SubtitleEvent], fps: float = 29.97) -> List[SubtitleEvent]:
    """Snap start/end timestamps to the nearest frame boundary."""
    if fps <= 0:
        return events
    frame = 1.0 / fps
    for ev in events:
        ev.start = round(ev.start / frame) * frame
        ev.end = round(ev.end / frame) * frame
        if ev.end <= ev.start:
            ev.end = ev.start + frame
    return events


def process_timing_pipeline(
    events: List[SubtitleEvent],
    fps: float = 29.97,
    video_path: Optional[Path] = None,
    apply_scene_snap: bool = True,
) -> List[SubtitleEvent]:
    """
    Run the full suite of timing adjustments in the correct order.
    """
    if not events:
        return events

    # 1. Enforce minimum duration so no subtitle flashes.
    events = enforce_minimum_duration(events)

    # 2. Anchor to speech based on reading speed (extend fast ones).
    for i, event in enumerate(events):
        next_start = events[i + 1].start if i + 1 < len(events) else None
        events[i] = anchor_to_speech(event, next_start, fps)

    # 3. Final overlap check and fix.
    events = fix_overlaps(events)

    # 4. Optional scene-snap hook (currently no-op until dedicated shot detector lands).
    if apply_scene_snap and video_path:
        try:
            video_exists = Path(video_path).exists()
            if not video_exists:
                logger.debug("Scene snap requested but video is missing: %s", video_path)
        except Exception:
            logger.debug("Scene snap check failed for %s", video_path)

    # 5. Quantize to frame boundaries for deterministic export.
    events = quantize_to_frames(events, fps=fps)
    return events
