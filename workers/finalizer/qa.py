import logging
from typing import List
from .models import SubtitleEvent

logger = logging.getLogger("omega_finalizer.qa")

def generate_qa_report(events: List[SubtitleEvent]) -> dict:
    """
    Passively generate a Quality Assurance report on the finalized subtitles.
    Does NOT mutate any data. Just measures and produces metrics.
    """
    total = len(events)
    high_cps = 0
    short_duration = 0
    long_duration = 0
    max_cps = 0.0
    
    # Check for dangling words (prepositions leaving the viewer hanging)
    # Icelandic orphans
    orphans = {"og", "en", "eða", "að", "sem", "er", "við", "um", "á", "í", "úr", "af", "til", "svo", "ég"}
    dangling_count = 0
    
    for event in events:
        if event.cps > 20:
            high_cps += 1
        if event.cps > max_cps:
            max_cps = event.cps
            
        if event.duration < 1.0 and not event.is_audio_event:
            short_duration += 1
        elif event.duration > 7.0:
            long_duration += 1
            
        if event.lines:
            import string
            last_word = event.lines[-1].split()[-1].lower()
            last_word = last_word.translate(str.maketrans('', '', string.punctuation))
            if last_word in orphans:
                dangling_count += 1
                
    return {
        "total_subtitles": total,
        "high_cps_violations": high_cps,
        "short_duration_violations": short_duration,
        "long_duration_violations": long_duration,
        "dangling_words_detected": dangling_count,
        "max_cps_recorded": round(max_cps, 2)
    }
