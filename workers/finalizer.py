import json
import os
import re
import logging
import shutil
from pathlib import Path
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional
import config
import omega_db
from subtitle_standards import (
    MAX_CHARS_PER_LINE,
    MAX_LINES,
    MIN_DURATION,
    MAX_DURATION,
    GAP_SECONDS,
    DEFAULT_FRAMERATE,
    IDEAL_CPS,
    TIGHT_CPS,
    MAX_CPS,
    MERGE_GAP_MAX,
    MERGE_MAX_DURATION,
    MERGE_CPS_TRIGGER,
    MERGE_SHORT_TRIGGER,
    get_cps_for_language,
)
from workers.timing_utils import (
    apply_broadcast_timing,
    get_video_framerate,
    run_alass,
)

logger = logging.getLogger("OmegaManager.Finalizer")

# --- BROADCAST STANDARDS (derived from subtitle_standards.py) ---
# These are now imported from subtitle_standards.py for consistency
# Local aliases for backwards compatibility
MIN_SUBTITLE_DURATION = MIN_DURATION  # 1.5s (updated from 1.0s)
MAX_BROADCAST_CPS = MAX_CPS           # 20.0 (hard ceiling)
MIN_READABLE_DURATION = 1.2           # Minimum for comfortable reading

# Speaker differentiation mode: "off", "dash", "separate"
# "off" = no speaker differentiation
# "dash" = prefix with "- " when speaker changes (BBC style)
# "separate" = force separate subtitles for different speakers
SPEAKER_MODE = os.environ.get("OMEGA_SPEAKER_MODE", "dash").strip().lower()


def _apply_speaker_dash(events: list[dict], prev_speaker: str = None) -> tuple[list[dict], str]:
    """
    Apply BBC-style dash indicators for speaker changes.
    """
    if SPEAKER_MODE == "off":
        return events, prev_speaker

    for event in events:
        speaker = event.get("speaker")
        text = event.get("text", "").strip()

        # 1. Change between subtitle blocks
        if speaker is not None and prev_speaker is not None and speaker != prev_speaker:
            if not (text.startswith("- ") or text.startswith("– ") or text.startswith("— ")):
                event["text"] = f"- {text}"
                event["speaker_changed"] = True
        
        # 2. Case where AI might have already signaled multiple speakers in one block
        # (e.g. "Hello.\n- Hi.") - ensures the FIRST line also gets a dash for consistency
        if "\n-" in text or "\n–" in text or "\n—" in text:
            if not (text.startswith("- ") or text.startswith("– ") or text.startswith("— ")):
                event["text"] = f"- {text}"
                event["speaker_changed"] = True

        prev_speaker = speaker if speaker is not None else prev_speaker

    return events, prev_speaker


def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def _timing_mode() -> str:
    mode = str(os.environ.get("OMEGA_TIMING_MODE", "balanced") or "balanced").strip().lower()
    if mode not in {"balanced", "strict"}:
        return "balanced"
    return mode


def _strict_timing_limits() -> tuple[float, float]:
    max_extend = _safe_float_env("OMEGA_TIMING_STRICT_MAX_EXTEND", 0.0)
    fragment_shift = _safe_float_env("OMEGA_TIMING_STRICT_FRAGMENT_SHIFT", 0.0)
    return max_extend, fragment_shift

def _caps_upper_ratio(text: str) -> tuple[float, int]:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0, 0
    upper = sum(1 for ch in letters if ch.isupper())
    lower = sum(1 for ch in letters if ch.islower())
    total = upper + lower
    if total <= 0:
        return 0.0, 0
    return upper / total, total


def _collect_dangling_word_warnings(events: list[dict], target_language: str = "is") -> list[tuple[int, str]]:
    """
    Detect subtitles that end with orphaned prepositions/articles.

    These create awkward reading flow when sentence continues in next subtitle.
    Example: "En een nieuw rapport documenteert de toename van" [next] "Azië tot Afrika"

    Returns list of (subtitle_index, dangling_word) tuples.
    """
    # Language-specific words that shouldn't end a subtitle (creates orphan)
    dangling_words_by_lang = {
        "is": {"í", "á", "um", "til", "við", "frá", "með", "hinn", "hin", "hið", "og", "en", "að", "því", "er", "sem"},
        "nl": {"de", "het", "een", "van", "voor", "naar", "met", "in", "op", "aan", "om", "bij", "tot", "over", "en", "maar", "dat", "of"},
        "es": {"el", "la", "los", "las", "un", "una", "de", "del", "al", "y", "o", "que", "pero", "en", "con", "por", "para"},
        "de": {"der", "die", "das", "ein", "eine", "von", "für", "mit", "auf", "an", "und", "oder", "aber", "dass"},
        "pt": {"o", "a", "os", "as", "um", "uma", "de", "do", "da", "para", "com", "por", "em", "e", "ou", "mas", "que"},
        "fr": {"le", "la", "les", "un", "une", "de", "du", "des", "à", "au", "aux", "et", "ou", "mais", "que", "pour", "avec"},
    }

    dangling_set = dangling_words_by_lang.get(target_language, dangling_words_by_lang.get("is", set()))
    warnings = []

    for i, event in enumerate(events):
        lines = event.get("lines") or []
        text = " ".join([str(line).strip() for line in lines if str(line).strip()]).strip()
        if not text:
            continue

        # Get last word, stripping punctuation
        words = text.split()
        if not words:
            continue
        last_word = words[-1].lower().strip(".,:;?!\"'()[]")

        # Check if it's a dangling word AND the text doesn't end with sentence-ending punctuation
        # (If it ends with . ! ? then it's a complete sentence, not a continuation)
        if last_word in dangling_set and not text.rstrip()[-1] in ".!?":
            warnings.append((i + 1, last_word))

    return warnings


def _collect_caps_warnings(events: list[dict]) -> dict:
    """
    Detects suspicious ALL-CAPS / mostly-caps subtitle lines for broadcast QA.
    """
    full_caps = 0
    mostly_caps = 0
    samples: list[str] = []

    for event in events:
        lines = event.get("lines") or []
        text = " ".join([str(line).strip() for line in lines if str(line).strip()]).strip()
        if not text:
            continue

        ratio, letter_count = _caps_upper_ratio(text)
        # Ignore very short strings (likely acronyms).
        if letter_count < 8:
            continue

        if ratio >= 0.95:
            full_caps += 1
            if len(samples) < 3:
                samples.append(text[:160])
        elif ratio >= 0.85:
            mostly_caps += 1
            if len(samples) < 3:
                samples.append(text[:160])

    total = len(events)
    return {
        "full_caps": full_caps,
        "mostly_caps": mostly_caps,
        "total": total,
        "samples": samples,
    }


def _collect_srt_qc(events: list[dict]) -> dict:
    orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    total = len(events)
    high_cps_17 = 0
    high_cps_20 = 0
    short_duration = 0
    long_duration = 0
    long_lines = 0
    dangling = 0
    max_cps = 0.0
    max_duration = 0.0

    for event in events:
        lines = event.get("lines") or []
        cleaned_lines = [str(line).strip() for line in lines if str(line).strip()]
        if not cleaned_lines:
            continue

        text = " ".join(cleaned_lines).strip()
        duration = max(0.0, float(event.get("end", 0.0)) - float(event.get("start", 0.0)))
        cps = (len(text) / duration) if duration > 0 else 0.0
        max_cps = max(max_cps, cps)
        max_duration = max(max_duration, duration)

        if cps > IDEAL_CPS:
            high_cps_17 += 1
        if cps > 20:
            high_cps_20 += 1
        if duration < MIN_DURATION:
            short_duration += 1
        if duration > 7.0:
            long_duration += 1

        for line in cleaned_lines:
            if len(line) > MAX_CHARS_PER_LINE:
                long_lines += 1

        last_word = cleaned_lines[-1].split()[-1].lower().strip(".,:;?!\"'")
        if last_word in orphans:
            dangling += 1

    return {
        "total": total,
        "high_cps_17": high_cps_17,
        "high_cps_20": high_cps_20,
        "short_duration": short_duration,
        "long_duration": long_duration,
        "long_lines": long_lines,
        "dangling": dangling,
        "max_cps": round(max_cps, 2),
        "max_duration": round(max_duration, 2),
    }


def _collect_timing_qc(events: list[dict]) -> dict:
    total = len(events)
    with_words = 0
    missing_words = 0
    overlaps = 0
    max_overlap = 0.0
    min_gap = None
    max_gap = 0.0
    short_duration = 0
    zero_duration = 0

    start_delta_sum = 0.0
    end_delta_sum = 0.0
    start_delta_min = None
    start_delta_max = None
    end_delta_min = None
    end_delta_max = None
    start_early = 0
    start_late = 0
    end_cutoff = 0
    end_tail = 0

    start_early_threshold = -0.35
    start_late_threshold = 0.25
    end_cutoff_threshold = -0.15
    end_tail_threshold = 0.40

    prev_end = None

    for event in events:
        start = float(event.get("start", 0.0))
        end = float(event.get("end", 0.0))
        duration = end - start

        if duration <= 0:
            zero_duration += 1
        if duration < MIN_DURATION:
            short_duration += 1

        if prev_end is not None:
            gap = start - prev_end
            min_gap = gap if min_gap is None else min(min_gap, gap)
            max_gap = max(max_gap, gap)
            if gap < 0:
                overlaps += 1
                max_overlap = max(max_overlap, abs(gap))
        prev_end = end

        words = event.get("words")
        if isinstance(words, list) and words:
            word_start = words[0].get("start")
            word_end = words[-1].get("end")
            if word_start is None or word_end is None:
                missing_words += 1
                continue

            word_start = float(word_start)
            word_end = float(word_end)
            with_words += 1

            start_delta = start - word_start
            end_delta = end - word_end

            start_delta_sum += start_delta
            end_delta_sum += end_delta

            start_delta_min = start_delta if start_delta_min is None else min(start_delta_min, start_delta)
            start_delta_max = start_delta if start_delta_max is None else max(start_delta_max, start_delta)
            end_delta_min = end_delta if end_delta_min is None else min(end_delta_min, end_delta)
            end_delta_max = end_delta if end_delta_max is None else max(end_delta_max, end_delta)

            if start_delta < start_early_threshold:
                start_early += 1
            if start_delta > start_late_threshold:
                start_late += 1
            if end_delta < end_cutoff_threshold:
                end_cutoff += 1
            if end_delta > end_tail_threshold:
                end_tail += 1
        else:
            missing_words += 1

    start_delta_avg = start_delta_sum / with_words if with_words else 0.0
    end_delta_avg = end_delta_sum / with_words if with_words else 0.0

    return {
        "total": total,
        "with_words": with_words,
        "missing_words": missing_words,
        "zero_duration": zero_duration,
        "short_duration": short_duration,
        "overlaps": overlaps,
        "max_overlap": round(max_overlap, 3),
        "min_gap": round(min_gap, 3) if min_gap is not None else None,
        "max_gap": round(max_gap, 3),
        "start_delta_avg": round(start_delta_avg, 3),
        "start_delta_min": round(start_delta_min, 3) if start_delta_min is not None else None,
        "start_delta_max": round(start_delta_max, 3) if start_delta_max is not None else None,
        "end_delta_avg": round(end_delta_avg, 3),
        "end_delta_min": round(end_delta_min, 3) if end_delta_min is not None else None,
        "end_delta_max": round(end_delta_max, 3) if end_delta_max is not None else None,
        "start_early": start_early,
        "start_late": start_late,
        "end_cutoff": end_cutoff,
        "end_tail": end_tail,
        "thresholds": {
            "start_early": start_early_threshold,
            "start_late": start_late_threshold,
            "end_cutoff": end_cutoff_threshold,
            "end_tail": end_tail_threshold,
        },
    }


def _is_music_only(text: str) -> bool:
    if not text:
        return True
    cleaned = text.strip()
    cleaned = cleaned.replace("♪", "").strip()
    stripped = cleaned.strip("[]()").strip()
    if not stripped:
        return "♪" in text
    tokens = [t for t in stripped.split() if t]
    if len(tokens) != 1:
        return False
    return tokens[0].lower() in {"music", "song", "singing", "choir", "instrumental"}


def _strip_metadata_tags(text: str) -> str:
    """
    Remove metadata tags from subtitle text.

    Tags like <MUSIC>, <APPLAUSE>, <LAUGHTER>, [MUSIC], (UPBEAT MUSIC), (TÓNLIST), etc.
    should not appear in final SRT output. They are internal markers only.

    STRATEGY: Find all parenthetical content and check if it's metadata:
    1. If ALL characters are uppercase letters/spaces -> metadata (remove)
    2. If content matches known audio term list -> metadata (remove)
    3. Otherwise -> keep (it's probably real dialogue)
    """
    if not text:
        return text

    # Remove angle bracket tags: <MUSIC>, <APPLAUSE>, <LAUGHTER>, etc.
    cleaned = re.sub(r'<[A-Z_]+>', '', text)

    # Remove square bracket tags that are audio markers
    cleaned = re.sub(r'\[(MUSIC|APPLAUSE|LAUGHTER|CHEERING|SINGING|INSTRUMENTAL)\]', '', cleaned, flags=re.IGNORECASE)

    # Known metadata terms (lowercase for matching)
    metadata_terms = {
        'music', 'applause', 'laughter', 'laughs', 'laugh', 'cheering', 'singing',
        'instrumental', 'audio', 'phone', 'crowd', 'clapping', 'claps',
        'upbeat music', 'gentle music', 'soft music', 'dramatic music',
        'tónlist', 'róleg tónlist', 'fjörug tónlist', 'hlær', 'hlátur',
        'áhorfendur hlæja', 'áhorfendur klappa', 'klappa', 'klappar',
        'muziek', 'gejuich', 'gelach', 'applaus', 'telefoon', 'vrolijke muziek',
    }

    def _is_metadata(content):
        content = content.strip()
        if not content:
            return False
        if content.lower() in metadata_terms:
            return True
        letters = [c for c in content if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            return True
        return False

    def _replace_paren(match):
        if _is_metadata(match.group(1)):
            return ''
        return match.group(0)

    cleaned = re.sub(r'\(([^)]+)\)', _replace_paren, cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)

    return cleaned.strip()


def _is_audio_event(segment: dict) -> bool:
    """Check if segment is an audio event from ElevenLabs Scribe v2."""
    return segment.get("is_audio_event", False)


def _should_filter_audio_event(segment: dict) -> bool:
    """
    Determine if audio event should be filtered from subtitles.

    Preserved events (shown to viewer): laughter, applause, cheering
    Filtered events (hidden): ALL other audio events (music, beeping, etc.)

    SAFEGUARD: If segment has meaningful text (3+ words), never filter.
    This catches ElevenLabs mis-classifications where speech is tagged as music.

    Args:
        segment: Segment dict with is_audio_event, is_music, preserve_in_subtitle fields

    Returns:
        True if segment should be filtered (not shown), False if it should appear
    """
    if not segment.get("is_audio_event"):
        return False

    # Check if explicitly marked for preservation (laughter, applause)
    if segment.get("preserve_in_subtitle"):
        return False

    # SAFEGUARD: Don't filter if there's meaningful speech content
    # ElevenLabs sometimes mis-tags speech as music during worship/background music sections
    text = str(segment.get("text") or segment.get("translation") or "").strip()
    if text:
        # Count actual words (not just "music" or similar)
        words = [w for w in text.split() if len(w) > 1 and w.lower() not in {"music", "song", "singing"}]
        if len(words) >= 3:
            # This looks like real speech, not a music marker - don't filter
            logger.debug(f"   ℹ️ Preserving speech marked as music: '{text[:40]}...'")
            return False

    # Filter ALL audio events that aren't explicitly preserved
    # This includes: music, beeping, ringing, static, etc.
    return True


def _format_audio_event_text(segment: dict) -> str:
    """
    Format audio event for subtitle display.

    Args:
        segment: Segment dict with audio_event_type field

    Returns:
        Formatted text like "[LAUGHTER]" or "[APPLAUSE]"
    """
    event_type = segment.get("audio_event_type", "sound").upper()
    return f"[{event_type}]"


def _find_word_boundary_time(words: list[dict], char_position: int, use_end: bool = True) -> float:
    """
    Find the timing at a character position using word-level boundaries.
    
    Args:
        words: List of word dicts with 'text', 'start', 'end' keys
        char_position: Character offset in the concatenated text
        use_end: If True, return the end time of the boundary word; else start time
        
    Returns:
        The word boundary time in seconds, or None if words data unavailable.
    """
    if not words:
        return None
    
    chars_seen = 0
    for i, word in enumerate(words):
        word_text = word.get("text", "")
        word_len = len(word_text)
        
        # Check if the split point falls within or after this word
        if chars_seen + word_len >= char_position:
            return word.get("end") if use_end else word.get("start")
        
        chars_seen += word_len + 1  # +1 for space between words
    
    # If we're past all words, return the last word's end time
    return words[-1].get("end") if words else None

def format_timestamp(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def format_timestamp_vtt(seconds):
    """VTT uses . instead of , for milliseconds."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def format_timestamp_ttml(seconds):
    """TTML uses HH:MM:SS.mmm format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def generate_vtt(events: list[dict], output_path: Path):
    """
    Generate WebVTT subtitle file from normalized events.
    """
    lines = ["WEBVTT", ""]
    for i, event in enumerate(events, 1):
        start = format_timestamp_vtt(event["start"])
        end = format_timestamp_vtt(event["end"])
        text = "\n".join(event.get("lines", []))
        lines.append(f"{i}")
        if event.get("position") == "top":
             lines.append(f"{start} --> {end} line:10%")
        else:
             lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"✅ Created VTT: {output_path.name}")
    return output_path


def generate_ttml(events: list[dict], output_path: Path, lang_code: str = "is"):
    """
    Generate TTML (Timed Text Markup Language) subtitle file.
    Compatible with Netflix, YouTube, and broadcast workflows.
    """
    # Escape XML special characters
    def escape_xml(text):
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))
    
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
        start = format_timestamp_ttml(event["start"])
        end = format_timestamp_ttml(event["end"])
        text = "<br/>".join([escape_xml(line) for line in event.get("lines", [])])
        region_id = "top" if event.get("position") == "top" else "bottom"
        paragraphs.append(f'      <p begin="{start}" end="{end}" region="{region_id}">{text}</p>')
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ttml_header)
        f.write("\n".join(paragraphs))
        f.write("\n")
        f.write(ttml_footer)
    
    logger.info(f"✅ Created TTML: {output_path.name}")
    return output_path

def split_into_balanced_lines(text, target_language="is"):
    if len(text) <= MAX_CHARS_PER_LINE:
        return [text]

    # If text is extremely long (>84), we might need 3 lines, but let's stick to 2 for now and force split
    middle = len(text) // 2
    candidates = []

    # Words that should NOT start a new line (conjunctions, articles that would orphan)
    bad_starters = set()
    # Words that should NOT end a line (articles, prepositions - would orphan their object)
    bad_enders = set()

    if target_language == "is":
        bad_starters = {"og", "en", "sem", "að", "eða", "því"}
        bad_enders = {"í", "á", "um", "til", "við", "frá", "með", "hinn", "hin", "hið"}
    elif target_language in ["es", "spanish"]:
        bad_starters = {"y", "o", "que", "pero", "de", "en"}
        bad_enders = {"el", "la", "los", "las", "un", "una", "de", "del", "al"}
    elif target_language == "nl":
        # Dutch: don't start line with conjunctions
        bad_starters = {"en", "maar", "dat", "of", "omdat", "want", "dus", "als", "toen", "terwijl"}
        # Dutch: don't end line with articles/prepositions (keeps them with their noun)
        # This prevents "de hele | wereld" or "van | Californië"
        bad_enders = {
            # Articles
            "de", "het", "een",
            # Prepositions
            "van", "voor", "naar", "met", "in", "op", "aan", "om", "bij", "tot", "over",
            # Possessive/demonstrative that precede nouns
            "zijn", "haar", "hun", "ons", "deze", "die", "dit", "dat",
            # Adjective markers
            "hele", "grote", "kleine", "nieuwe", "oude",
            # Separable verb prefixes (critical for Dutch - keeps verb parts together)
            "uit", "af", "toe", "vast", "weg", "door", "mee", "terug",
            # Pronouns that shouldn't end a line alone
            "te", "zich", "mij", "je", "hem", "ons", "jullie", "hen",
        }
    elif target_language == "de":
        bad_starters = {"und", "oder", "aber", "dass", "weil", "wenn"}
        bad_enders = {"der", "die", "das", "ein", "eine", "von", "für", "mit", "auf", "an"}

    # Search window: Try to stay within 42 chars for the first line
    # Ideally split around the middle, but MUST NOT exceed MAX_CHARS_PER_LINE for the first line

    # Heuristic: Find best split point
    start = max(0, middle - 15)
    end = min(len(text), middle + 15)

    for i in range(start, end):
        if text[i] == ' ':
            dist = abs(i - middle)
            score = 15 - dist

            # Semantic Bonus for natural break points
            if i > 0 and text[i-1] in ',.;:?!': score += 20

            # Ellipsis handling: Strong preference to break AFTER ellipsis, not before
            # Prevents awkward splits like "said... | and" - keep ellipsis with preceding text
            if i >= 3 and text[i-3:i] == '...':
                score += 30  # Strong bonus to break AFTER ellipsis
            elif i < len(text) - 3 and text[i+1:i+4] == '...':
                score -= 30  # Strong penalty to break BEFORE ellipsis

            left = text[:i].strip()
            right = text[i:].strip()
            remaining = text[i + 1 :].lstrip()
            next_word = remaining.split(" ", 1)[0].lower().strip(".,:;?!\"'")

            # Penalty: Don't start second line with conjunctions/connectors
            if next_word in bad_starters:
                score -= 20

            # Penalty: Don't end first line with articles/prepositions (keeps them with noun)
            last_word = left.split()[-1].lower().strip(".,:;?!\"'") if left.split() else ""
            if last_word in bad_enders:
                score -= 25  # Higher penalty - this is a critical readability issue

            imbalance = abs(len(left) - len(right))
            score -= min(imbalance, 40) * 0.6
            if len(left) < 12 or len(right) < 12:
                score -= 15

            # Penalty for exceeding max length
            if len(left) > MAX_CHARS_PER_LINE or len(right) > MAX_CHARS_PER_LINE:
                continue

            candidates.append((i, score))
            
    if not candidates:
        # Fallback: Hard split at max length or middle
        split_idx = min(MAX_CHARS_PER_LINE, middle)
        # Find nearest space backwards
        fallback = text.rfind(' ', 0, split_idx)
        if fallback != -1:
            lines = [text[:fallback].strip(), text[fallback:].strip()]
        else:
            lines = [text[:split_idx].strip(), text[split_idx:].strip()]
    else:
        best_split = max(candidates, key=lambda x: x[1])[0]
        lines = [text[:best_split].strip(), text[best_split:].strip()]

    # Widow prevention: if second line is very short (<6 chars), try to rebalance
    # This prevents orphaned short words like "en" or "de" on the second line
    if len(lines) == 2 and len(lines[1]) < 6 and len(lines[0]) > 20:
        first_words = lines[0].split()
        if len(first_words) > 2:
            # Move last word from first line to start of second line
            moved_word = first_words[-1]
            new_first = ' '.join(first_words[:-1])
            new_second = f"{moved_word} {lines[1]}"

            # Only apply if it improves balance and stays within limits
            old_imbalance = abs(len(lines[0]) - len(lines[1]))
            new_imbalance = abs(len(new_first) - len(new_second))

            if (new_imbalance < old_imbalance and
                len(new_first) <= MAX_CHARS_PER_LINE and
                len(new_second) <= MAX_CHARS_PER_LINE):
                lines = [new_first, new_second]
                logger.debug(f"   📐 Rebalanced to prevent widow: '{lines[1][:15]}...'")

    # FINAL ENFORCEMENT: Ensure no line exceeds MAX_CHARS_PER_LINE (42)
    # This catches edge cases from fallback splits
    final_lines = []
    for line in lines:
        if len(line) > MAX_CHARS_PER_LINE:
            # Force split at max length, finding nearest space
            split_pos = line.rfind(' ', 0, MAX_CHARS_PER_LINE)
            if split_pos > 10:  # Found a reasonable space
                final_lines.append(line[:split_pos].strip())
                remainder = line[split_pos:].strip()
                if remainder:
                    final_lines.append(remainder)
            else:
                # No good space found - hard truncate with ellipsis
                final_lines.append(line[:MAX_CHARS_PER_LINE-1] + "…")
            logger.warning(f"   ⚠️ Line exceeded {MAX_CHARS_PER_LINE} chars, force split: '{line[:30]}...'")
        else:
            final_lines.append(line)

    # Limit to 2 lines max for broadcast
    if len(final_lines) > MAX_LINES:
        final_lines = final_lines[:MAX_LINES]
        logger.warning(f"   ⚠️ Truncated to {MAX_LINES} lines")

    return final_lines

def abbreviate_bible_refs(text, target_language="is"):
    """
    Abbreviates Icelandic Bible references to save space.
    e.g. "Fyrra Korintubréfi 10" -> "1. Kor. 10"
    """
    if target_language == "is":
        replacements = {
            r"(?i)Fyrra Korintubréfi": "1. Kor.",
            r"(?i)Síðara Korintubréfi": "2. Kor.",
            r"(?i)Fyrra Pétursbréfi": "1. Pét.",
            r"(?i)Síðara Pétursbréfi": "2. Pét.",
            r"(?i)Fyrra Jóhannesarbréfi": "1. Jóh.",
            r"(?i)Síðara Jóhannesarbréfi": "2. Jóh.",
            r"(?i)Þriðja Jóhannesarbréfi": "3. Jóh.",
            r"(?i)Fyrra Tessaloníkubréfi": "1. Tess.",
            r"(?i)Síðara Tessaloníkubréfi": "2. Tess.",
            r"(?i)Fyrra Tímóteusarbréfi": "1. Tím.",
            r"(?i)Síðara Tímóteusarbréfi": "2. Tím.",
            r"(?i)Jóhannesarguðspjall": "Jóh.",
            r"(?i)Lúkasarguðspjall": "Lúk.",
            r"(?i)Markúsarguðspjall": "Mark.",
            r"(?i)Matteusarguðspjall": "Matt.",
            r"(?i)Postulasagan": "Post.",
            r"(?i)Rómverjabréfið": "Róm.",
            r"(?i)Galatabréfið": "Gal.",
            r"(?i)Efesusbréfið": "Ef.",
            r"(?i)Filippíbréfið": "Fil.",
            r"(?i)Kólossebréfið": "Kól.",
            r"(?i)Jakobsbréfið": "Jak.",
            r"(?i)Opinberunarbókin": "Op.",
            r"(?i)Hebreabréfið": "Hebr.",
            r"(?i)Fyrsta Mósebók": "1. Mós.",
            r"(?i)Önnur Mósebók": "2. Mós.",
            r"(?i)Þriðja Mósebók": "3. Mós.",
            r"(?i)Fjórða Mósebók": "4. Mós.",
            r"(?i)Fimmta Mósebók": "5. Mós.",
            r"(?i)Sálmarnir": "Sálm.",
            r"(?i)Orðskviðirnir": "Orðskv.",
            r"(?i)Jesaja": "Jes.",
            r"(?i)Jeremía": "Jer.",
            r"(?i)Esekíel": "Esek.",
            r"(?i)Daníel": "Dan."
        }
    elif target_language in ["es", "spanish"]:
        replacements = {
            r"(?i)Primera de Corintios": "1 Cor.",
            r"(?i)Segunda de Corintios": "2 Cor.",
            r"(?i)Primera de Pedro": "1 Ped.",
            r"(?i)Segunda de Pedro": "2 Ped.",
            r"(?i)Primera de Juan": "1 Jn.",
            r"(?i)Segunda de Juan": "2 Jn.",
            r"(?i)Tercera de Juan": "3 Jn.",
            r"(?i)Apocalipsis": "Apoc.",
            r"(?i)Hechos": "Hch.",
            r"(?i)Romanos": "Rom.",
            r"(?i)Mateo": "Mat.",
            r"(?i)Marcos": "Mar.",
            r"(?i)Lucas": "Luc.",
            r"(?i)Juan": "Jn.",
        }
    elif target_language == "nl":
        # Dutch: EXPAND common abbreviations for clarity (not abbreviate)
        # CEO feedback: "Op." is confusing, should be "Openbaring"
        # These expand short forms that might appear in translations
        replacements = {
            # Expand abbreviations to full names for Dutch readability
            r"\bOp\.": "Openbaring",
            r"\bHand\.": "Handelingen",
            r"\bRom\.": "Romeinen",
            r"\bGal\.": "Galaten",
            r"\bEf\.": "Efeziërs",
            r"\bFil\.": "Filippenzen",
            r"\bKol\.": "Kolossenzen",
            r"\bHebr\.": "Hebreeën",
            r"\bJak\.": "Jakobus",
            r"\bMat\.": "Matteüs",
            r"\bMar\.": "Marcus",
            r"\bLuc\.": "Lucas",
            r"\bGen\.": "Genesis",
            r"\bEx\.": "Exodus",
            r"\bLev\.": "Leviticus",
            r"\bNum\.": "Numeri",
            r"\bDeut\.": "Deuteronomium",
            r"\bPs\.": "Psalmen",
            r"\bSpr\.": "Spreuken",
            r"\bJes\.": "Jesaja",
            r"\bJer\.": "Jeremia",
            r"\bEz\.": "Ezechiël",
            r"\bDan\.": "Daniël",
            # Numbered books: keep abbreviated (clear and saves space)
            r"(?i)Eerste Korintiërs": "1 Korintiërs",
            r"(?i)Tweede Korintiërs": "2 Korintiërs",
            r"(?i)Eerste Tessalonicenzen": "1 Tessalonicenzen",
            r"(?i)Tweede Tessalonicenzen": "2 Tessalonicenzen",
            r"(?i)Eerste Timoteüs": "1 Timoteüs",
            r"(?i)Tweede Timoteüs": "2 Timoteüs",
            r"(?i)Eerste Petrus": "1 Petrus",
            r"(?i)Tweede Petrus": "2 Petrus",
            r"(?i)Eerste Johannes": "1 Johannes",
            r"(?i)Tweede Johannes": "2 Johannes",
            r"(?i)Derde Johannes": "3 Johannes",
        }
    else:
        replacements = {}
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    
    return text

def _merge_high_cps_events(events: list[dict]) -> list[dict]:
    if not events:
        return events

    merged: list[dict] = []
    i = 0
    max_chars = MAX_CHARS_PER_LINE * MAX_LINES
    while i < len(events):
        curr = events[i]
        if i < len(events) - 1:
            nxt = events[i + 1]
            try:
                gap = float(nxt["start"]) - float(curr["end"])
            except Exception:
                gap = MERGE_GAP_MAX + 1

            if gap <= MERGE_GAP_MAX and gap >= -0.05:
                curr_text = str(curr.get("text") or "").strip()
                next_text = str(nxt.get("text") or "").strip()
                if curr_text and next_text:
                    curr_dur = max(0.01, float(curr["end"]) - float(curr["start"]))
                    next_dur = max(0.01, float(nxt["end"]) - float(nxt["start"]))
                    curr_cps = len(curr_text) / curr_dur
                    next_cps = len(next_text) / next_dur

                    combined_text = f"{curr_text} {next_text}".strip()
                    combined_dur = max(0.01, float(nxt["end"]) - float(curr["start"]))
                    combined_cps = len(combined_text) / combined_dur

                    needs_merge = (
                        curr_cps > MERGE_CPS_TRIGGER
                        or next_cps > MERGE_CPS_TRIGGER
                        or curr_dur < MERGE_SHORT_TRIGGER
                        or next_dur < MERGE_SHORT_TRIGGER
                    )

                    if (
                        needs_merge
                        and combined_dur <= MERGE_MAX_DURATION
                        and len(combined_text) <= max_chars
                        and (combined_cps <= MERGE_CPS_TRIGGER or combined_cps <= max(curr_cps, next_cps) - 0.5)
                    ):
                        combined_words = None
                        curr_words = curr.get("words")
                        next_words = nxt.get("words")
                        if isinstance(curr_words, list) and isinstance(next_words, list):
                            combined_words = curr_words + next_words

                        merged.append(
                            {
                                "start": curr["start"],
                                "end": nxt["end"],
                                "text": combined_text,
                                **({"words": combined_words} if combined_words is not None else {}),
                            }
                        )
                        i += 2
                        continue

        merged.append(curr)
        i += 1

    return merged


def _emergency_merge_short_subtitles(events: list[dict], min_dur: float = None) -> list[dict]:
    """
    Last-resort merge for subtitles that slipped through all other passes.
    Merges any subtitle below min_dur with its neighbor.

    This is a safety net - ideally no subtitles should reach this function.
    """
    if min_dur is None:
        min_dur = MIN_SUBTITLE_DURATION

    if not events:
        return events

    result = []
    i = 0
    max_chars = MAX_CHARS_PER_LINE * MAX_LINES

    while i < len(events):
        current = events[i].copy()
        duration = current['end'] - current['start']

        if duration < min_dur and i < len(events) - 1:
            # Merge with next subtitle
            next_event = events[i + 1]
            combined_text = f"{current['text']} {next_event['text']}".strip()

            # Only merge if combined text fits
            if len(combined_text) <= max_chars:
                current['text'] = combined_text
                current['end'] = next_event['end']
                # Merge word-level timing if available
                curr_words = current.get('words', [])
                next_words = next_event.get('words', [])
                if isinstance(curr_words, list) and isinstance(next_words, list):
                    current['words'] = curr_words + next_words
                # Preserve position: if either is 'top', merged is 'top' (avoid graphics)
                if current.get('position') == 'top' or next_event.get('position') == 'top':
                    current['position'] = 'top'
                logger.warning(f"   🔧 Emergency merged short subtitle ({duration:.2f}s) with next")
                i += 2  # Skip next since we merged it
                result.append(current)
                continue

        if duration < min_dur and i > 0 and result:
            # Can't merge with next, try merging with previous
            prev = result[-1]
            combined_text = f"{prev['text']} {current['text']}".strip()

            if len(combined_text) <= max_chars:
                prev['text'] = combined_text
                prev['end'] = current['end']
                # Merge word-level timing if available
                prev_words = prev.get('words', [])
                curr_words = current.get('words', [])
                if isinstance(prev_words, list) and isinstance(curr_words, list):
                    prev['words'] = prev_words + curr_words
                # Preserve position: if either is 'top', merged is 'top' (avoid graphics)
                if prev.get('position') == 'top' or current.get('position') == 'top':
                    prev['position'] = 'top'
                logger.warning(f"   🔧 Emergency merged short subtitle ({duration:.2f}s) with previous")
                i += 1
                continue

        result.append(current)
        i += 1

    return result


def _fix_high_cps_subtitles(events: list[dict]) -> list[dict]:
    """
    Fix subtitles that have too-high CPS by merging with neighbors.

    This catches cases where duration is technically OK (e.g., 0.7s) but
    the text is too long for comfortable reading (e.g., 25 chars = 35 CPS).

    Strategy:
    1. Find subtitles with CPS > MAX_BROADCAST_CPS (20)
    2. Try to merge with previous or next subtitle
    3. If merge not possible, extend duration if there's room
    """
    if not events:
        return events

    result = []
    max_chars = MAX_CHARS_PER_LINE * MAX_LINES
    i = 0

    while i < len(events):
        current = events[i].copy()
        duration = current['end'] - current['start']
        text = current.get('text', '')
        char_count = len(text)
        cps = char_count / duration if duration > 0 else 999

        # Check if this subtitle has problematic CPS (trigger on CPS alone, not duration)
        if cps > MAX_BROADCAST_CPS:
            # Calculate how much time we need for acceptable CPS
            required_duration = char_count / MAX_BROADCAST_CPS

            # Option 1: Try to extend into gap before next subtitle
            if i < len(events) - 1:
                next_start = events[i + 1]['start']
                available_extension = next_start - current['end'] - GAP_SECONDS

                if available_extension > 0:
                    new_end = min(current['start'] + required_duration,
                                  current['end'] + available_extension)
                    if new_end > current['end']:
                        old_cps = cps
                        current['end'] = new_end
                        new_duration = new_end - current['start']
                        new_cps = char_count / new_duration if new_duration > 0 else 999
                        if new_cps <= MAX_BROADCAST_CPS:
                            logger.info(f"   📏 Extended subtitle to fix CPS: {old_cps:.1f} → {new_cps:.1f}")
                            result.append(current)
                            i += 1
                            continue

            # Option 2: Merge with next subtitle if gap is small
            if i < len(events) - 1:
                next_event = events[i + 1]
                gap = next_event['start'] - current['end']

                if gap <= MERGE_GAP_MAX + 0.1:  # Allow slightly larger gap for CPS fixes
                    combined_text = f"{text} {next_event.get('text', '')}".strip()
                    combined_duration = next_event['end'] - current['start']
                    combined_cps = len(combined_text) / combined_duration if combined_duration > 0 else 999

                    if (len(combined_text) <= max_chars and
                        combined_cps <= MAX_BROADCAST_CPS and
                        combined_duration <= MERGE_MAX_DURATION):
                        # Merge is beneficial
                        current['text'] = combined_text
                        current['end'] = next_event['end']
                        # Merge word-level timing
                        curr_words = current.get('words', [])
                        next_words = next_event.get('words', [])
                        if isinstance(curr_words, list) and isinstance(next_words, list):
                            current['words'] = curr_words + next_words
                        logger.info(f"   🔗 Merged high-CPS subtitle ({cps:.1f} CPS) with next → {combined_cps:.1f} CPS")
                        result.append(current)
                        i += 2  # Skip next since we merged
                        continue

            # Option 3: Merge with previous subtitle
            if result:
                prev = result[-1]
                gap = current['start'] - prev['end']

                if gap <= MERGE_GAP_MAX + 0.1:
                    combined_text = f"{prev.get('text', '')} {text}".strip()
                    combined_duration = current['end'] - prev['start']
                    combined_cps = len(combined_text) / combined_duration if combined_duration > 0 else 999

                    if (len(combined_text) <= max_chars and
                        combined_cps <= MAX_BROADCAST_CPS and
                        combined_duration <= MERGE_MAX_DURATION):
                        # Merge with previous
                        prev['text'] = combined_text
                        prev['end'] = current['end']
                        # Merge word-level timing
                        prev_words = prev.get('words', [])
                        curr_words = current.get('words', [])
                        if isinstance(prev_words, list) and isinstance(curr_words, list):
                            prev['words'] = prev_words + curr_words
                        logger.info(f"   🔗 Merged high-CPS subtitle ({cps:.1f} CPS) with previous → {combined_cps:.1f} CPS")
                        i += 1
                        continue

            # If we couldn't fix it, log a warning
            logger.warning(f"   ⚠️ Could not fix high-CPS subtitle ({cps:.1f} CPS, {duration:.2f}s): {text[:30]}...")

        result.append(current)
        i += 1

    return result


def _extract_proper_names(text: str) -> list[str]:
    """
    Extract potential proper names from text.
    Looks for capitalized words that aren't at sentence start.
    """
    # Pattern: Titles (Dr., Mr., Mrs., Pastor) followed by capitalized names
    title_pattern = r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Pastor|Rev\.|Prof\.)\s+([A-Z][a-zëïéèü]+(?:\s+[A-Z][a-zëïéèü]+)*)'

    # Pattern: Two+ capitalized words in sequence (likely a name)
    name_pattern = r'\b([A-Z][a-zëïéèü]+(?:\s+[A-Z][a-zëïéèü]+)+)\b'

    names = []

    # Find titled names first
    for match in re.finditer(title_pattern, text):
        full_name = f"{match.group(1)} {match.group(2)}"
        names.append(full_name)

    # Find multi-word capitalized sequences
    for match in re.finditer(name_pattern, text):
        name = match.group(1)
        # Skip common phrases that aren't names
        skip_phrases = {'The Lord', 'Holy Spirit', 'New Testament', 'Old Testament',
                       'United States', 'New York', 'Los Angeles', 'San Francisco',
                       'De Heer', 'Heilige Geest', 'Nieuwe Testament', 'Oude Testament'}
        if name not in skip_phrases and name not in names:
            names.append(name)

    return names


def _similarity_ratio(a: str, b: str) -> float:
    """Calculate similarity between two strings (0-1)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _standardize_names(events: list[dict], source_events: list[dict] = None) -> list[dict]:
    """
    Find and standardize name variations across all segments.

    Strategy:
    1. Extract all names from all segments
    2. Group similar names (>0.7 similarity)
    3. Use the most frequent spelling as canonical
    4. Replace all variants with canonical spelling
    """
    if not events:
        return events

    # Extract all names and count occurrences
    all_names = []
    for event in events:
        text = event.get('text', '')
        names = _extract_proper_names(text)
        all_names.extend(names)

    # Also extract from source if available (for ground truth)
    source_names = []
    if source_events:
        for event in source_events:
            text = event.get('text', '')
            names = _extract_proper_names(text)
            source_names.extend(names)

    if not all_names:
        return events

    # Count name frequencies
    name_counts = Counter(all_names)
    source_counts = Counter(source_names) if source_names else Counter()

    # Group similar names
    name_groups: dict[str, list[str]] = {}
    processed = set()

    for name in name_counts:
        if name in processed:
            continue

        # Find all similar names
        group = [name]
        for other in name_counts:
            if other == name or other in processed:
                continue
            if _similarity_ratio(name, other) > 0.7:
                group.append(other)
                processed.add(other)

        processed.add(name)

        # Only care about groups with variations
        if len(group) > 1:
            # Choose canonical name: prefer source spelling, then most frequent
            canonical = None

            # Check if any variant matches source exactly
            for variant in group:
                if source_counts.get(variant, 0) > 0:
                    canonical = variant
                    break

            # Otherwise use most frequent
            if not canonical:
                canonical = max(group, key=lambda x: name_counts[x])

            name_groups[canonical] = [n for n in group if n != canonical]

    # If no variations found, return unchanged
    if not name_groups:
        return events

    # Log what we're fixing
    for canonical, variants in name_groups.items():
        logger.info(f"   📛 Standardizing names: {variants} → '{canonical}'")

    # Apply fixes to all events
    result = []
    total_fixes = 0
    for event in events:
        new_event = event.copy()
        text = event.get('text', '')

        for canonical, variants in name_groups.items():
            for variant in variants:
                if variant in text:
                    text = text.replace(variant, canonical)
                    total_fixes += 1

        new_event['text'] = text
        result.append(new_event)

    if total_fixes > 0:
        logger.info(f"   ✅ Fixed {total_fixes} name variation(s)")

    return result


def normalize_segments_for_review(
    segments: list[dict],
    target_language: str = "is",
) -> list[dict]:
    """
    Normalize raw translation segments into broadcast-ready format for human review.

    This function applies all text formatting and timing adjustments BEFORE review,
    so reviewers see exactly what will appear on screen. After review, the segments
    go directly to SRT generation without further modification.

    Processing steps:
    1. Filter audio events (music, etc.)
    2. Split long segments (>84 chars) into multiple subtitles
    3. Merge high-CPS segments to improve readability
    4. Fix remaining high-CPS issues
    5. Standardize name variations (ASR artifacts)
    6. Rescue orphaned words (language-specific)
    7. Balance line lengths (max 42 chars per line)
    8. Apply timing constraints (min duration, CPS limits)

    Args:
        segments: List of raw translation segments with {start, end, text} fields
        target_language: ISO language code (is, nl, es, etc.)

    Returns:
        List of normalized segments ready for review, each with:
        - start: float (seconds)
        - end: float (seconds)
        - text: str (formatted with line breaks if needed)
        - lines: list[str] (individual lines)
        - original_text: str (pre-normalization text for diff display)
    """
    logger.info(f"📝 Normalizing {len(segments)} segments for review ({target_language.upper()})")

    # Get language-specific CPS targets
    ideal_cps, tight_cps = get_cps_for_language(target_language)
    logger.info(f"   📊 Language: {target_language.upper()} | Target CPS: {ideal_cps} (max: {tight_cps})")

    processed_events = []

    # PASS 1: AGGRESSIVE PRE-PROCESS SPLITTING
    for item in segments:
        start = item.get('start', 0)
        end = item.get('end', 0)
        original_text = item.get('text', '').replace("\n", " ").strip()
        text = original_text

        # Strip metadata tags like <MUSIC>, [APPLAUSE] that shouldn't appear in output
        text = _strip_metadata_tags(text)

        words = item.get('words')  # Word-level timing data (may be None)
        speaker = item.get('speaker')  # Speaker ID for diarization

        # Handle audio events from ElevenLabs Scribe v2
        if _is_audio_event(item):
            if _should_filter_audio_event(item):
                continue
            text = _format_audio_event_text(item)
            processed_events.append({
                'start': start,
                'end': end,
                'text': text,
                'original_text': original_text,
                'speaker': speaker,
                'is_audio_event': True,
            })
            continue

        text = abbreviate_bible_refs(text, target_language)

        if not text:
            continue
        if _is_music_only(text):
            continue

        queue = [{'start': start, 'end': end, 'text': text, 'original_text': original_text, 'words': words, 'speaker': speaker}]

        while queue:
            curr = queue.pop(0)
            curr_text = curr['text']
            curr_len = len(curr_text)
            curr_words = curr.get('words')

            if curr_len > (MAX_CHARS_PER_LINE * MAX_LINES):
                mid = curr_len // 2
                search_start = max(0, mid - 20)
                search_end = min(curr_len, mid + 20)
                split_point = -1

                split_point = curr_text.rfind('. ', search_start, search_end)
                if split_point == -1:
                    split_point = curr_text.rfind(', ', search_start, search_end)
                if split_point == -1:
                    split_point = curr_text.rfind(' ', search_start, search_end)
                if split_point == -1:
                    split_point = mid

                part1_text = curr_text[:split_point+1].strip()
                part2_text = curr_text[split_point+1:].strip()

                mid_time = _find_word_boundary_time(curr_words, split_point + 1)
                if mid_time is None:
                    ratio = len(part1_text) / curr_len
                    mid_time = curr['start'] + ((curr['end'] - curr['start']) * ratio)

                words1, words2 = None, None
                if curr_words:
                    chars_seen = 0
                    split_word_idx = 0
                    for idx, w in enumerate(curr_words):
                        chars_seen += len(w.get("text", "")) + 1
                        if chars_seen >= split_point + 1:
                            split_word_idx = idx + 1
                            break
                    words1 = curr_words[:split_word_idx]
                    words2 = curr_words[split_word_idx:]

                queue.insert(0, {'start': mid_time, 'end': curr['end'], 'text': part2_text, 'original_text': curr.get('original_text', ''), 'words': words2, 'speaker': curr.get('speaker')})
                queue.insert(0, {'start': curr['start'], 'end': mid_time, 'text': part1_text, 'original_text': curr.get('original_text', ''), 'words': words1, 'speaker': curr.get('speaker')})
            else:
                processed_events.append(curr)

    # PASS 1.5: CPS RESCUE MERGE
    for _ in range(2):
        merged_events = _merge_high_cps_events(processed_events)
        if len(merged_events) == len(processed_events):
            break
        processed_events = merged_events

    # PASS 1.55: HIGH CPS FIX
    processed_events = _fix_high_cps_subtitles(processed_events)

    # PASS 1.57: NAME CONSISTENCY
    processed_events = _standardize_names(processed_events)

    # PASS 1.58: DUPLICATE TITLE FIX
    for event in processed_events:
        text = event.get('text', '')
        text = re.sub(r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Pastor|Rev\.|Prof\.)\s+\1', r'\1', text)
        event['text'] = text

    # PASS 1.6: ORPHAN RESCUER
    if target_language == "is":
        orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    elif target_language == "nl":
        orphans = {"en", "of", "de", "het", "een", "om", "te", "in", "op", "aan", "dat", "die"}
    elif target_language in ["es", "spanish"]:
        orphans = {"y", "o", "el", "la", "un", "una", "de", "en", "a", "que"}
    elif target_language == "de":
        orphans = {"und", "der", "die", "das", "ein", "eine", "zu", "in", "auf", "an"}
    else:
        orphans = {"and", "or", "the", "a", "an", "to", "in", "on", "at", "of"}

    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]

        words = curr['text'].split()
        if not words:
            continue

        last_word = words[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            word_to_move = words[-1]
            curr['text'] = " ".join(words[:-1])
            next_item['text'] = f"{word_to_move} {next_item['text']}"

            curr_words = curr.get("words")
            next_words = next_item.get("words")
            if isinstance(curr_words, list) and curr_words:
                moved_word = curr_words.pop()
                if isinstance(next_words, list):
                    next_words.insert(0, moved_word)
                else:
                    next_words = [moved_word]
                curr["words"] = curr_words
                next_item["words"] = next_words
                if curr_words:
                    curr["end"] = curr_words[-1].get("end", curr["end"])
                if next_words:
                    next_item["start"] = next_words[0].get("start", next_item["start"])

    # PASS 2: TIMING ADJUSTMENTS (keep text as single line for now)
    for i, current in enumerate(processed_events):
        if not current.get('text', '').strip():
            continue

        # Calculate constraints
        max_end_time = processed_events[i+1]['start'] - GAP_SECONDS if i < len(processed_events) - 1 else current['end'] + 10.0

        # Apply minimum duration
        duration = current['end'] - current['start']
        if duration < MIN_SUBTITLE_DURATION:
            potential_end = current['start'] + MIN_SUBTITLE_DURATION
            if potential_end <= max_end_time:
                current['end'] = potential_end

        # CPS check and timing extension
        text = current['text']
        char_count = len(text)
        duration = current['end'] - current['start']
        cps = char_count / duration if duration > 0 else 999

        TARGET_CPS = 21.0
        if cps > TARGET_CPS:
            required_duration = char_count / TARGET_CPS
            potential_end = current['start'] + required_duration
            if potential_end <= max_end_time:
                current['end'] = potential_end

    # PASS 3: LINE SPLITTING (final pass - after all text modifications)
    # This is done AFTER all text manipulation to ensure proper 2-line splits
    normalized_segments = []
    prev_speaker = None

    for current in processed_events:
        text = current.get('text', '').strip()
        if not text:
            continue

        # Split into balanced lines (max 2 lines, max 42 chars each)
        lines = split_into_balanced_lines(text, target_language)

        # Speaker dash (BBC style)
        speaker = current.get('speaker')
        if speaker is not None and prev_speaker is not None and speaker != prev_speaker:
            if not lines[0].startswith("- "):
                lines[0] = f"- {lines[0]}"
        prev_speaker = speaker if speaker is not None else prev_speaker

        # Build normalized segment
        normalized_segments.append({
            'start': current['start'],
            'end': current['end'],
            'text': '\n'.join(lines),
            'lines': lines,
            'original_text': current.get('original_text', text),
            'speaker': current.get('speaker'),
        })

    # PASS 4: EMERGENCY MERGE FOR SHORT SUBTITLES
    events_for_check = [
        {'start': s['start'], 'end': s['end'], 'text': ' '.join(s['lines'])}
        for s in normalized_segments
    ]
    short_count = sum(1 for e in events_for_check if e['end'] - e['start'] < MIN_SUBTITLE_DURATION)
    if short_count > 0:
        logger.warning(f"   ⚠️ Found {short_count} subtitles below {MIN_SUBTITLE_DURATION}s - applying emergency merge")
        merged_events = _emergency_merge_short_subtitles(events_for_check)
        # Rebuild normalized segments from merged
        normalized_segments = []
        for event in merged_events:
            lines = split_into_balanced_lines(event['text'], target_language)
            normalized_segments.append({
                'start': event['start'],
                'end': event['end'],
                'text': '\n'.join(lines),
                'lines': lines,
                'original_text': event['text'],
            })
        logger.info(f"   ✅ Emergency merge complete: {len(merged_events)} subtitles")

    logger.info(f"✅ Normalized {len(segments)} → {len(normalized_segments)} segments for review")
    return normalized_segments


def segments_to_srt(
    segments: list[dict],
    output_path: Path,
) -> Path:
    """
    Convert normalized segments directly to SRT file.

    This is used after review approval to generate the final SRT.
    The segments should already be normalized (via normalize_segments_for_review).

    Args:
        segments: List of normalized segments with {start, end, text/lines} fields
        output_path: Path for the output SRT file

    Returns:
        Path to the created SRT file
    """
    logger.info(f"📄 Converting {len(segments)} segments to SRT: {output_path.name}")

    srt_blocks = []
    for i, segment in enumerate(segments):
        start = segment.get('start', 0)
        end = segment.get('end', 0)

        # Use 'lines' if available, otherwise split 'text'
        if 'lines' in segment:
            text = '\n'.join(segment['lines'])
        else:
            text = segment.get('text', '')

        srt_blocks.append(f"{i+1}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}\n\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(srt_blocks)

    logger.info(f"✅ Created SRT: {output_path.name}")
    return output_path


def finalize(
    approved_path: Path,
    target_language: str = "is",
    video_path: Optional[Path] = None,
    apply_scene_snap: bool = True,
    apply_alass: bool = False,
):
    """
    Converts APPROVED JSON -> SRT with broadcast-quality formatting.

    Applies:
    1. Smart Splitting (42 chars max per line)
    2. Orphan Rescue (language-specific dangling words)
    3. CPS Optimization (language-specific reading speeds)
    4. Music Filtering (from ElevenLabs audio events)
    5. Bible Ref Abbreviation (language-specific)
    6. Frame Quantization (snap to frame boundaries)
    7. Scene-Aware Timing (optional, requires video_path)

    Args:
        approved_path: Path to the approved JSON file
        target_language: ISO language code (is, nl, es, etc.)
        video_path: Optional path to video for scene detection
        apply_scene_snap: Whether to snap subtitles to scene cuts
        apply_alass: Whether to run Alass for drift correction (requires video)

    Returns:
        Tuple of (srt_path, normalized_json_path)
    """
    logger.info(f"🎬 Finalizing: {approved_path.name}")

    # Get language-specific CPS targets
    ideal_cps, tight_cps = get_cps_for_language(target_language)
    logger.info(f"   📊 Language: {target_language.upper()} | Target CPS: {ideal_cps} (max: {tight_cps})")

    # Detect video framerate if video provided
    fps = DEFAULT_FRAMERATE
    if video_path and video_path.exists():
        fps = get_video_framerate(video_path)
        logger.info(f"   🎬 Video framerate: {fps:.3f} fps")

    # Extract clean job_id stem from various filename patterns
    stem = approved_path.name
    for suffix in [".json", "_ICELANDIC", "_APPROVED", "_normalized", "_SKELETON", "_SKELETON_DONE"]:
        stem = stem.replace(suffix, "")
    timing_mode = _timing_mode()
    strict_mode = timing_mode == "strict"
    strict_max_extend, strict_fragment_shift = _strict_timing_limits()
    
    with open(approved_path, "r", encoding="utf-8") as f:
        data_wrapper = json.load(f)
    
    if isinstance(data_wrapper, dict):
        data = data_wrapper.get("segments", [])
        graphic_zones = data_wrapper.get("graphic_zones", [])
    else:
        data = data_wrapper
        graphic_zones = []

    # Load danger zones from multimodal (Azotus) pipeline if available
    # Danger zones are saved with original_stem (video file name without timestamp/language)
    # Try to extract original_stem from stem by removing timestamp pattern and language suffix
    import re
    # Pattern: {original_stem}[-_]{lang_code}-{timestamp} or {original_stem}-{timestamp}
    # Timestamp format: YYYYMMDDTHHMMSS...Z
    original_stem = re.sub(r'[-_][a-z]{2}-\d{8}T\d+Z?$', '', stem)  # With language
    original_stem = re.sub(r'-\d{8}T\d+Z?$', '', original_stem)      # Without language

    # Try original_stem first (shared across language tracks), then fall back to full stem
    danger_zones_path = config.VAULT_DATA / f"{original_stem}_DANGER_ZONES.json"
    if not danger_zones_path.exists():
        danger_zones_path = config.VAULT_DATA / f"{stem}_DANGER_ZONES.json"

    if danger_zones_path.exists():
        try:
            with open(danger_zones_path, "r", encoding="utf-8") as f:
                danger_zones = json.load(f)
            # Convert danger zones to graphic_zones format and merge
            for dz in danger_zones:
                if "bottom-center" in dz.get("avoid_regions", []):
                    graphic_zones.append({
                        "startTime": dz.get("start", 0),
                        "endTime": dz.get("end", 0),
                        "reason": dz.get("reason", "danger zone"),
                    })
            logger.info(f"   👁️ Loaded {len(danger_zones)} danger zones from {danger_zones_path.name}")
        except Exception as e:
            logger.warning(f"   Could not load danger zones: {e}")

    def is_in_zone(start, end, zones):
        """Check if segment overlaps with any graphic zone."""
        center = start + (end - start) / 2
        for zone in zones:
            # Check center point or overlap
            # Using center point is safer to avoid edge jitters
            z_start = zone.get("startTime", 0)
            z_end = zone.get("endTime", 0)
            if z_start <= center <= z_end:
                return True
        return False

    processed_events = []
    
    # PASS 1: AGGRESSIVE PRE-PROCESS SPLITTING
    for item in data:
        start = item['start']
        end = item['end']
        text = item['text'].replace("\n", " ").strip()
        # Strip metadata tags like <MUSIC>, [APPLAUSE] that shouldn't appear in output
        text = _strip_metadata_tags(text)
        words = item.get('words')  # Word-level timing data (may be None for old skeletons)
        speaker = item.get('speaker')  # Speaker ID for diarization

        # Handle audio events from ElevenLabs Scribe v2
        if _is_audio_event(item):
            if _should_filter_audio_event(item):
                # Skip music segments entirely
                continue
            # Preserve laughter, applause, etc. with formatted text
            text = _format_audio_event_text(item)
            processed_events.append({
                'start': start,
                'end': end,
                'text': text,
                'speaker': speaker,
                'is_audio_event': True,
            })
            continue

        text = abbreviate_bible_refs(text, target_language)

        if not text:
            continue
        if _is_music_only(text):
            continue
            
        queue = [{'start': start, 'end': end, 'text': text, 'words': words}]

        while queue:
            curr = queue.pop(0)
            curr_text = curr['text']
            curr_len = len(curr_text)
            curr_words = curr.get('words')
            
            if curr_len > (MAX_CHARS_PER_LINE * MAX_LINES): 
                mid = curr_len // 2
                search_start = max(0, mid - 20)
                search_end = min(curr_len, mid + 20)
                split_point = -1
                
                split_point = curr_text.rfind('. ', search_start, search_end)
                if split_point == -1: split_point = curr_text.rfind(', ', search_start, search_end)
                if split_point == -1: split_point = curr_text.rfind(' ', search_start, search_end)
                if split_point == -1: split_point = mid

                part1_text = curr_text[:split_point+1].strip()
                part2_text = curr_text[split_point+1:].strip()
                
                # Use word-level timing if available, otherwise fall back to ratio
                mid_time = _find_word_boundary_time(curr_words, split_point + 1)
                if mid_time is None:
                    # Fallback: ratio-based timing for old skeletons without word data
                    ratio = len(part1_text) / curr_len
                    mid_time = curr['start'] + ((curr['end'] - curr['start']) * ratio)
                
                # Split word data if available (for recursive splits)
                words1, words2 = None, None
                if curr_words:
                    # Find which word the split occurs at
                    chars_seen = 0
                    split_word_idx = 0
                    for idx, w in enumerate(curr_words):
                        chars_seen += len(w.get("text", "")) + 1
                        if chars_seen >= split_point + 1:
                            split_word_idx = idx + 1
                            break
                    words1 = curr_words[:split_word_idx]
                    words2 = curr_words[split_word_idx:]
                
                queue.insert(0, {'start': mid_time, 'end': curr['end'], 'text': part2_text, 'words': words2})
                queue.insert(0, {'start': curr['start'], 'end': mid_time, 'text': part1_text, 'words': words1})
            else:
                processed_events.append(curr)

    # PASS 1.5: CPS RESCUE MERGE
    for _ in range(2):
        merged_events = _merge_high_cps_events(processed_events)
        if len(merged_events) == len(processed_events):
            break
        processed_events = merged_events

    # PASS 1.55: HIGH CPS FIX (for short-but-dense subtitles)
    # This catches subtitles like 0.7s with 25 chars = 35 CPS
    processed_events = _fix_high_cps_subtitles(processed_events)

    # PASS 1.57: NAME CONSISTENCY (fixes ASR variations like Curci/Koichi/Korci)
    processed_events = _standardize_names(processed_events)

    # PASS 1.58: DUPLICATE TITLE FIX (fixes "Dr. Dr." or "Pastor Pastor")
    for event in processed_events:
        text = event.get('text', '')
        # Fix duplicate titles
        text = re.sub(r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Pastor|Rev\.|Prof\.)\s+\1', r'\1', text)
        event['text'] = text

    # PASS 1.6: ORPHAN RESCUER (BBC/Netflix Standard)
    # Move dangling words to the next block (language-specific)
    if target_language == "is":
        orphans = {"og", "en", "að", "því", "er", "sem", "var"}
    elif target_language == "nl":
        # Dutch: short conjunctions and articles that shouldn't end a line alone
        orphans = {"en", "of", "de", "het", "een", "om", "te", "in", "op", "aan", "dat", "die"}
    elif target_language in ["es", "spanish"]:
        orphans = {"y", "o", "el", "la", "un", "una", "de", "en", "a", "que"}
    elif target_language == "de":
        orphans = {"und", "der", "die", "das", "ein", "eine", "zu", "in", "auf", "an"}
    else:
        orphans = {"and", "or", "the", "a", "an", "to", "in", "on", "at", "of"}
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]

        words = curr['text'].split()
        if not words:
            continue

        last_word = words[-1].lower().strip(".,:;?!\"")
        if last_word in orphans:
            # Move the word to the next block
            word_to_move = words[-1]
            curr['text'] = " ".join(words[:-1])
            next_item['text'] = f"{word_to_move} {next_item['text']}"
            logger.info(f"   🧹 Rescued orphan '{word_to_move}' from block {i+1}")

            curr_words = curr.get("words")
            next_words = next_item.get("words")
            if isinstance(curr_words, list) and curr_words:
                moved_word = curr_words.pop()
                if isinstance(next_words, list):
                    next_words.insert(0, moved_word)
                else:
                    next_words = [moved_word]
                curr["words"] = curr_words
                next_item["words"] = next_words
                if curr_words:
                    curr["end"] = curr_words[-1].get("end", curr["end"])
                if next_words:
                    next_item["start"] = next_words[0].get("start", next_item["start"])

    # PASS 1.7: STRANDED FRAGMENT RESCUER (Reverse Orphan)
    # Detects: [Block N] "ending" [Block N+1] "and. New sentence" -> Merge "and." back to N.
    for i in range(len(processed_events) - 1):
        curr = processed_events[i]
        next_item = processed_events[i+1]
        
        curr_text = curr['text'].strip()
        next_text = next_item['text'].strip()
        
        if not curr_text or not next_text:
            continue
            
        # Check if current sentence is "open" (no punctuation)
        curr_ends_open = curr_text[-1] not in ".?!\""
        
        if curr_ends_open:
            next_words = next_text.split()
            if not next_words: continue
            
            first_word = next_words[0]
            # Criteria: Short word (<4 chars), ends with sentence punctuation, starting lowercase usually
            # Example: "að." or "til." or "því."
            clean_word = first_word.strip(".,:;?!")
            has_closing_punct = first_word[-1] in ".?!"
            
            # Allow slightly longer words if they are clearly closing a sentence (e.g. "heim.")
            is_fragment = len(clean_word) <= 4 and has_closing_punct
            
            if is_fragment:
                # MOVE THE FRAGMENT BACK
                curr['text'] = curr_text + " " + first_word
                next_item['text'] = " ".join(next_words[1:])

                curr_words = curr.get("words")
                next_words_timing = next_item.get("words")
                used_word_timing = False

                if isinstance(next_words_timing, list) and next_words_timing:
                    moved_word = next_words_timing.pop(0)
                    if isinstance(curr_words, list):
                        curr_words.append(moved_word)
                    else:
                        curr_words = [moved_word]
                    curr["words"] = curr_words
                    next_item["words"] = next_words_timing
                    if curr_words:
                        curr["end"] = curr_words[-1].get("end", curr["end"])
                    if next_words_timing:
                        next_item["start"] = next_words_timing[0].get("start", next_item["start"])
                    used_word_timing = True

                if not used_word_timing and not strict_mode:
                    # ADJUST TIMING (Heuristic: Shift boundary by 0.35s)
                    # We steal 0.35s from Next and give it to Curr to account for the spoken word
                    shift = 0.35
                    curr['end'] = curr['end'] + shift
                    next_item['start'] = next_item['start'] + shift
                elif not used_word_timing and strict_mode and strict_fragment_shift > 0:
                    shift = strict_fragment_shift
                    curr['end'] = curr['end'] + shift
                    next_item['start'] = next_item['start'] + shift

                # Ensure we didn't break causality (start >= end)
                # Bug fix: Also check for start == end which creates zero-duration subtitle
                if next_item['start'] >= next_item['end']:
                     # If next became too short, give it minimum duration
                     min_duration_needed = 0.5  # Half second minimum
                     next_item['start'] = curr['end'] + GAP_SECONDS
                     next_item['end'] = max(next_item['end'], next_item['start'] + min_duration_needed)

                logger.info(f"   🩹 Rescued stranded fragment '{first_word}' back to block {i+1}")

    # PASS 1.8: SPEAKER DIFFERENTIATION (BBC-style dash indicators)
    # When speaker changes between subtitles, prefix with "- " for clarity
    if SPEAKER_MODE != "off":
        processed_events, _ = _apply_speaker_dash(processed_events)
        speaker_changes = sum(1 for e in processed_events if e.get("speaker_changed"))
        if speaker_changes > 0:
            logger.info(f"   🎙️ Added speaker change indicators to {speaker_changes} segments")

    # PASS 2: APPLY TIMING RULES & CPS OPTIMIZER
    # Note: ideal_cps and tight_cps are already set at function start
    final_srt_blocks = []
    normalized_events = []
    srt_counter = 1

    for i in range(len(processed_events)):
        current = processed_events[i]

        char_count = len(current['text'])
        # CPS Optimizer: Allow extending duration to meet language-specific CPS target
        required_time = char_count / ideal_cps
        original_duration = current['end'] - current['start']
        current_words = current.get("words")
        word_end = None
        if isinstance(current_words, list) and current_words:
            word_end = current_words[-1].get("end")
        
        next_start = processed_events[i+1]['start'] if i < len(processed_events) - 1 else 999999
        max_end_time = next_start - GAP_SECONDS
        
        if strict_mode:
            base_end = word_end if word_end is not None else current["end"]
            max_extend = max(0.0, strict_max_extend)
            extended_target = base_end + max_extend
            actual_end = min(extended_target, max_end_time)
        else:
            # Allow stealing up to 0.8s from the gap/next segment if available
            # Use MIN_SUBTITLE_DURATION (1.0s) as floor for broadcast readability
            extended_target = current['start'] + max(original_duration, MIN_SUBTITLE_DURATION, required_time)
            actual_end = min(extended_target, max_end_time)

        # HARD FLOOR: Enforce absolute minimum duration before finalizing
        min_required_end = current['start'] + MIN_SUBTITLE_DURATION
        if actual_end < min_required_end:
            # Try to extend to meet minimum - if this fits before next subtitle
            if min_required_end <= max_end_time + GAP_SECONDS:
                actual_end = min_required_end
                logger.debug(f"   📏 Extended subtitle {i+1} to meet {MIN_SUBTITLE_DURATION}s minimum")
            else:
                # Cannot extend without severe overlap - flag for emergency merge later
                current["_needs_merge"] = True
                logger.warning(f"   ⚠️ Subtitle {i+1} cannot meet {MIN_SUBTITLE_DURATION}s - flagged for merge")

        actual_end = max(actual_end, current["start"] + 0.01)
        current["end"] = actual_end
        
        # If still too fast, log it (use language-specific tight_cps threshold)
        final_duration = actual_end - current['start']
        final_cps = char_count / final_duration if final_duration > 0 else 0
        if final_cps > tight_cps:
            logger.warning(f"   ⚠️ High CPS ({final_cps:.1f} > {tight_cps}): {current['text'][:20]}...")

        # Enforce minimum duration for readability
        if final_duration < MIN_SUBTITLE_DURATION:
            # Try to extend if there's room before next subtitle
            min_end = current['start'] + MIN_SUBTITLE_DURATION
            if min_end <= max_end_time + GAP_SECONDS:
                actual_end = min_end
                current["end"] = actual_end
                logger.debug(f"   ⏱️ Extended subtitle {i+1} to {MIN_SUBTITLE_DURATION}s minimum")
            else:
                logger.warning(f"   ⚠️ Subtitle {i+1} squashed to <{MIN_SUBTITLE_DURATION}s: {current['text'][:20]}...")

        lines = split_into_balanced_lines(current['text'], target_language)

        # CPS MANAGEMENT: Extend duration to fit text - NEVER truncate
        # Professional subtitling rule: Text is sacred, timing is adjustable
        final_duration = actual_end - current['start']
        full_text = '\n'.join(lines)
        full_char_count = len(full_text.replace('\n', ' '))
        final_cps_check = full_char_count / final_duration if final_duration > 0 else 999

        # Target CPS for comfortable reading (industry standard allows up to 25)
        TARGET_CPS = 21.0

        if final_cps_check > TARGET_CPS:
            # Extend duration to meet CPS target
            required_duration = full_char_count / TARGET_CPS
            potential_end = current['start'] + required_duration

            # Check if we can extend without overlapping next subtitle
            if potential_end <= max_end_time:
                actual_end = potential_end
                current['end'] = actual_end
                final_duration = actual_end - current['start']
                final_cps_check = full_char_count / final_duration
                logger.debug(f"   ⏱️ Extended subtitle to {final_duration:.2f}s for {final_cps_check:.1f} CPS")
            else:
                # Extend as much as possible (up to max_end_time)
                if max_end_time > actual_end:
                    actual_end = max_end_time
                    current['end'] = actual_end
                    final_duration = actual_end - current['start']
                    final_cps_check = full_char_count / final_duration if final_duration > 0 else 999

                # Log high CPS but NEVER truncate - the text must remain complete
                if final_cps_check > TARGET_CPS:
                    logger.warning(f"   ⚠️ High CPS ({final_cps_check:.1f}) - no room to extend: {current['text'][:40]}...")

        # Check Graphic Zones for positioning
        position_tag = ""
        if is_in_zone(current['start'], actual_end, graphic_zones):
             position_tag = "{\\an8}"

        # Strip metadata tags from final output
        stripped_text = _strip_metadata_tags("\n".join(lines))
        if not stripped_text.strip():
            continue  # Skip empty subtitles after metadata stripping (e.g., music-only segments)

        final_text = position_tag + stripped_text
        stripped_lines = stripped_text.split('\n')

        final_srt_blocks.append(f"{srt_counter}\n{format_timestamp(current['start'])} --> {format_timestamp(actual_end)}\n{final_text}\n\n")
        normalized_events.append({
            "start": current['start'],
            "end": actual_end,
            "lines": stripped_lines,
            "position": "top" if position_tag else "bottom"
        })
        srt_counter += 1

    # PRE-SANITY: Verify minimum duration enforcement
    # This catches any subtitles that slipped through all timing passes
    min_duration_violations = [
        (i, e['end'] - e['start'], ' '.join(e.get('lines', [''])))
        for i, e in enumerate(normalized_events)
        if e['end'] - e['start'] < MIN_SUBTITLE_DURATION
    ]
    if min_duration_violations:
        logger.warning(f"   ⚠️ Found {len(min_duration_violations)} subtitles below {MIN_SUBTITLE_DURATION}s - applying emergency merge")
        for idx, dur, text in min_duration_violations[:5]:
            logger.warning(f"      Subtitle {idx+1}: {dur:.3f}s - '{text[:30]}...'")
        # Convert normalized_events back to format expected by emergency merge
        # Preserve position info for danger zone avoidance
        events_for_merge = [
            {'start': e['start'], 'end': e['end'], 'text': ' '.join(e.get('lines', [])), 'position': e.get('position', 'bottom')}
            for e in normalized_events
        ]
        merged_events = _emergency_merge_short_subtitles(events_for_merge)
        # Rebuild final_srt_blocks and normalized_events from merged
        final_srt_blocks = []
        normalized_events = []
        for i, event in enumerate(merged_events):
            lines = split_into_balanced_lines(event['text'], target_language)
            final_text = _strip_metadata_tags('\n'.join(lines))
            if not final_text.strip():
                continue  # Skip empty subtitles after metadata stripping
            # Preserve position tag for danger zone avoidance
            position = event.get('position', 'bottom')
            position_tag = "{\\an8}" if position == "top" else ""
            final_srt_blocks.append(f"{i+1}\n{format_timestamp(event['start'])} --> {format_timestamp(event['end'])}\n{position_tag}{final_text}\n\n")
            normalized_events.append({
                'start': event['start'],
                'end': event['end'],
                'lines': final_text.split('\n'),
                'position': position
            })
        logger.info(f"   ✅ Emergency merge complete: {len(merged_events)} subtitles")

    # SANITY CHECK
    # Count only segments that will produce output (exclude music-only which are filtered in PASS 1)
    valid_input_count = sum(
        1 for item in data
        if item.get('text', '').strip() and not _is_music_only(item.get('text', ''))
    )
    output_count = len(final_srt_blocks)

    if valid_input_count > 0:
        drop_ratio = 1.0 - (output_count / valid_input_count)
        if drop_ratio > 0.2:
            logger.error(f"❌ CRITICAL SANITY CHECK FAILED: Dropped {drop_ratio:.1%}")
            raise Exception("Sanity Check Failed: Too many subtitles dropped")

    # PASS 3: BROADCAST TIMING (Frame Quantization + Scene Snapping)
    # This pass applies video-aware timing adjustments for professional output
    if video_path and video_path.exists():
        logger.info(f"   🎬 Applying broadcast timing (scene snap: {apply_scene_snap})")
        
        # Phase 4: Full Multimodal Integration
        # Attempt to load Gemini Vision data for Genetic Shot Detection
        external_cuts = None
        if apply_scene_snap:
             try:
                 # Check for cached vision scan (saved by vision_scanner.py)
                 vision_path = config.VAULT_DATA / f"{video_path.stem}_VISION_SCAN.json"
                 if vision_path.exists():
                     with open(vision_path, "r") as f:
                         vision_data = json.load(f)
                         shots = vision_data.get("shots", [])
                         if shots:
                             external_cuts = [float(s.get("start", 0)) for s in shots]
                             logger.info(f"   🧬 Using Gemini Vision shots ({len(external_cuts)} cuts detected)")
             except Exception as e:
                 logger.warning(f"   ⚠️ Failed to load vision data for timing: {e}")

        normalized_events = apply_broadcast_timing(
            normalized_events,
            video_path=video_path if apply_scene_snap else None,
            fps=fps,
            scene_snap=apply_scene_snap,
            external_cuts=external_cuts
        )
        # Rebuild SRT blocks with quantized timing
        final_srt_blocks = []
        for i, event in enumerate(normalized_events):
            final_text = _strip_metadata_tags('\n'.join(event.get('lines', [])))
            if not final_text.strip():
                continue  # Skip empty subtitles after metadata stripping
            # Preserve position tag for danger zone avoidance
            position_tag = "{\\an8}" if event.get("position") == "top" else ""
            final_srt_blocks.append(
                f"{i+1}\n{format_timestamp(event['start'])} --> {format_timestamp(event['end'])}\n{position_tag}{final_text}\n\n"
            )
        logger.info(f"   ✅ Broadcast timing applied at {fps:.3f} fps")
    else:
        logger.debug("   ℹ️ No video path - skipping scene-aware timing")

    # TIMING VALIDATION: Catch invalid timings AND overlaps before writing
    timing_errors = []
    overlap_fixes = []

    for i, event in enumerate(normalized_events):
        start = event.get('start', 0)
        end = event.get('end', 0)

        # Fix 1: end time before or equal to start time
        if end <= start:
            timing_errors.append((i + 1, start, end))
            event['end'] = start + MIN_SUBTITLE_DURATION
            logger.error(f"   ❌ TIMING BUG: Subtitle {i+1} has end ({end:.3f}) <= start ({start:.3f}). Fixed to {event['end']:.3f}")

        # Fix 2: Check for overlap with NEXT subtitle
        if i < len(normalized_events) - 1:
            next_event = normalized_events[i + 1]
            next_start = next_event.get('start', 0)
            current_end = event.get('end', 0)

            if current_end > next_start:
                # Overlap detected - push next subtitle's start forward
                overlap_fixes.append((i + 1, i + 2, current_end, next_start))
                next_event['start'] = current_end + GAP_SECONDS
                logger.warning(f"   ⚠️ OVERLAP FIX: Subtitle {i+1} end ({current_end:.3f}) > Subtitle {i+2} start ({next_start:.3f}). Pushed to {next_event['start']:.3f}")

                # Also ensure next subtitle still has valid duration
                if next_event['start'] >= next_event.get('end', 0):
                    next_event['end'] = next_event['start'] + MIN_SUBTITLE_DURATION

    if timing_errors or overlap_fixes:
        logger.warning(f"   ⚠️ Fixed {len(timing_errors)} timing errors and {len(overlap_fixes)} overlaps before saving")
        # Rebuild SRT blocks with corrected timing
        final_srt_blocks = []
        for i, event in enumerate(normalized_events):
            final_text = _strip_metadata_tags('\n'.join(event.get('lines', [])))
            if not final_text.strip():
                continue  # Skip empty subtitles after metadata stripping
            # Preserve position tag for danger zone avoidance
            position_tag = "{\\an8}" if event.get("position") == "top" else ""
            final_srt_blocks.append(
                f"{i+1}\n{format_timestamp(event['start'])} --> {format_timestamp(event['end'])}\n{position_tag}{final_text}\n\n"
            )

    # SAVE SRT
    srt_path = config.SRT_DIR / f"{stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(final_srt_blocks)
    logger.info(f"✅ Created SRT: {srt_path.name}")

    # SAVE NORMALIZED JSON
    normalized_path = config.SRT_DIR / f"{stem}_normalized.json"
    normalized_payload = {
        "events": normalized_events,
        "video_width": 1920,
        "video_height": 1080,
        "framerate": fps,
        "language": target_language,
        "cps_target": ideal_cps,
        "cps_max": tight_cps,
    }
    with open(normalized_path, "w", encoding="utf-8") as f:
        json.dump(normalized_payload, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Created Normalized JSON: {normalized_path.name}")

    # SAVE VTT (WebVTT for web players)
    vtt_path = config.SRT_DIR / f"{stem}.vtt"
    generate_vtt(normalized_events, vtt_path)

    # SAVE TTML (Netflix/YouTube/Broadcast compatible)
    ttml_path = config.SRT_DIR / f"{stem}.ttml"
    generate_ttml(normalized_events, ttml_path, lang_code=target_language)

    # QA: Flag suspicious casing (ALL CAPS) so it can be corrected upstream.
    try:
        caps_qa = _collect_caps_warnings(normalized_events)
        if (caps_qa.get("full_caps") or 0) > 0 or (caps_qa.get("mostly_caps") or 0) > 0:
            logger.warning(
                "   ⚠️ QA Caps: %s full-caps, %s mostly-caps (of %s blocks)",
                caps_qa.get("full_caps"),
                caps_qa.get("mostly_caps"),
                caps_qa.get("total"),
            )
        omega_db.update_job_via_track(stem, meta={"qa_caps": caps_qa})
    except Exception as e:
        logger.warning("   ⚠️ QA Caps check failed: %s", e)

    try:
        qa_srt = _collect_srt_qc(normalized_events)
        omega_db.update_job_via_track(stem, meta={"qa_srt": qa_srt})
    except Exception as e:
        logger.warning("   ⚠️ SRT QA summary failed: %s", e)

    # QA: Check for dangling prepositions/articles at subtitle boundaries
    # These create awkward reading flow and should be flagged
    try:
        dangling_words = _collect_dangling_word_warnings(normalized_events, target_language)
        if dangling_words:
            logger.warning(f"   ⚠️ QA Sentence Flow: {len(dangling_words)} subtitles end with orphaned preposition/article")
            for idx, word in dangling_words[:3]:  # Show first 3
                logger.warning(f"      Subtitle {idx}: ends with '{word}'")
            omega_db.update_job_via_track(stem, meta={"qa_dangling_words": dangling_words})
    except Exception as e:
        logger.warning("   ⚠️ Dangling word QA check failed: %s", e)

    try:
        qa_timing = _collect_timing_qc(processed_events)
        qa_timing["mode"] = timing_mode
        omega_db.update_job_via_track(stem, meta={"qa_timing": qa_timing})
    except Exception as e:
        logger.warning("   ⚠️ Timing QA summary failed: %s", e)

    return srt_path, normalized_path
