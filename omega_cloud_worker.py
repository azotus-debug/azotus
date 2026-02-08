import argparse
import json
import logging
import os
import re
import sys
import time
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import storage
import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    GenerationConfig,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
)

import config
import profiles
from gcp_auth import ensure_google_application_credentials
from subtitle_standards import get_duration_hint
from gcs_jobs import (
    GcsJobPaths,
    backoff_sleep,
    is_rate_limit_error,
    rate_limit_backoff,
    blob_exists,
    download_json,
    try_download_json,
    upload_json,
    utc_iso_now,
)
from utils.circuit_breaker import get_breaker

logger = logging.getLogger("OmegaCloudWorker")


SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

MUSIC_MARKERS = (
    "(music)",
    "[music]",
    "(song)",
    "[song]",
    "(singing)",
    "[singing]",
    "(choir)",
    "[choir]",
)

# Opening music/worship detection (configurable via env)
def _get_opening_music_seconds() -> float:
    try:
        return float(os.environ.get("OMEGA_OPENING_MUSIC_SECONDS", "90"))
    except (ValueError, TypeError):
        return 90.0

OPENING_MUSIC_SECONDS = _get_opening_music_seconds()
WORSHIP_PATTERNS = (
    "almighty", "hallelujah", "praise", "glory", "holy", "amen",
    "worship", "lord", "jesus", "savior", "king of kings",
    "we praise", "we worship", "i love you", "thank you",
    "you are", "my god", "my lord",
)
# Speech indicators that override worship detection
SPEECH_INDICATORS = (
    "today", "tonight", "we're going to", "i want to", "let me",
    "good morning", "good evening", "welcome to", "we're calling",
    "our message", "this message", "my subtitle", "chapter", "verse",
    "this week", "last week", "teaching", "series",
)

# Checkpoint validation - prevents stale checkpoints from wrong job/language/profile
CHECKPOINT_SCHEMA_VERSION = 2

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿÞþÐð]+", re.UNICODE)


def _is_music_marker_text(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if "♪" in text:
        return True
    if any(marker in lowered for marker in MUSIC_MARKERS):
        return True
    cleaned = re.sub(r"[^a-z]", "", lowered)
    return cleaned in {"music", "song", "singing", "choir", "instrumental"}


def _looks_like_speech(text: str) -> bool:
    words = _WORD_RE.findall(text or "")
    if len(words) >= 3:
        return True
    if len(words) >= 2 and any(ch in (text or "") for ch in ".?!"):
        return True
    return False


def _is_opening_worship(segment: dict) -> bool:
    """
    Check if a segment in the opening window looks like worship/singing.

    Used to catch worship lyrics that transcription services misidentify as speech.
    Only applied to segments within OPENING_MUSIC_SECONDS.
    """
    start = float(segment.get("start", 0))
    if start > OPENING_MUSIC_SECONDS:
        return False

    text = str(segment.get("text") or "").strip()
    lowered = text.lower()
    word_count = len(_WORD_RE.findall(text))

    # Speech indicators override worship detection
    for indicator in SPEECH_INDICATORS:
        if indicator in lowered:
            return False

    # Only check short phrases (<=10 words) for worship patterns
    if word_count > 10:
        return False

    # Check for worship keywords
    for pattern in WORSHIP_PATTERNS:
        if pattern in lowered:
            return True

    return False


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


from google.api_core.exceptions import DeadlineExceeded


def _vertex_generate(model: GenerativeModel, *args, **kwargs):
    """Call model.generate_content with a hard timeout that cannot block.

    Previous implementation used ``with ThreadPoolExecutor`` which calls
    ``shutdown(wait=True)`` on context-manager exit -- that blocks forever
    when the underlying gRPC thread hangs.  This version spawns a daemon
    thread and abandons it on timeout so the caller always returns/raises
    within the deadline.
    """
    breaker = get_breaker("vertex_ai")
    if breaker.is_open():
        raise RuntimeError("Vertex AI circuit breaker is open")

    timeout = kwargs.pop("timeout", 30.0)
    max_retries = 1

    for attempt in range(max_retries + 1):
        result_holder: list = [None]
        error_holder: list = [None]
        done_event = threading.Event()

        def _call():
            try:
                result_holder[0] = model.generate_content(*args, **kwargs)
            except Exception as exc:
                error_holder[0] = exc
            finally:
                done_event.set()

        t = threading.Thread(target=_call, daemon=True)
        t.start()

        if not done_event.wait(timeout=timeout):
            # Daemon thread is abandoned -- it will die when the process exits
            if attempt < max_retries:
                logger.warning(
                    "Vertex AI timeout (attempt %d/%d), retrying...",
                    attempt + 1, max_retries + 1,
                )
                continue
            breaker.record_failure()
            raise TimeoutError(f"Vertex AI timed out after {timeout}s")

        if error_holder[0] is not None:
            exc = error_holder[0]
            is_timeout = isinstance(exc, DeadlineExceeded) or "timeout" in str(exc).lower()
            if is_timeout and attempt < max_retries:
                logger.warning(
                    "Vertex AI timeout/error (attempt %d/%d): %s, retrying...",
                    attempt + 1, max_retries + 1, exc,
                )
                continue
            breaker.record_failure()
            raise exc

        breaker.record_success()
        return result_holder[0]


# Common female and male names for gender inference
FEMALE_NAMES = {
    "wendy", "mary", "sarah", "elizabeth", "jennifer", "jessica", "michelle", "patricia",
    "linda", "barbara", "nancy", "karen", "betty", "dorothy", "margaret", "lisa", "sandra",
    "ashley", "kimberly", "donna", "emily", "carol", "amanda", "melissa", "deborah", "stephanie",
    "rebecca", "sharon", "laura", "cynthia", "kathleen", "amy", "angela", "anna", "brenda",
    "virginia", "rachel", "heather", "diane", "ruth", "joyce", "julie", "catherine", "christine",
    "samantha", "megan", "emma", "olivia", "sophia", "charlotte", "victoria", "natalie", "grace"
}
MALE_NAMES = {
    "james", "john", "robert", "michael", "william", "david", "richard", "joseph", "charles",
    "thomas", "christopher", "daniel", "matthew", "anthony", "mark", "donald", "steven", "paul",
    "andrew", "joshua", "kenneth", "kevin", "brian", "george", "edward", "ronald", "timothy",
    "jason", "jeffrey", "ryan", "jacob", "gary", "nicholas", "eric", "jonathan", "stephen",
    "larry", "justin", "scott", "brandon", "benjamin", "samuel", "frank", "raymond", "gregory",
    "jack", "dennis", "jerry", "tyler", "aaron", "henry", "peter", "patrick", "billy", "graham"
}

# Multimodal cache (video + optional audio)
def _create_multimodal_cache(
    *,
    model_name: str,
    video_gcs_uri: str,
    audio_gcs_uri: Optional[str] = None,
    timeout: float = 120.0,
) -> Any:
    """Create a Vertex AI content cache with a hard timeout.

    Cache creation talks to a preview API that can hang indefinitely.
    We use the same daemon-thread pattern as ``_vertex_generate`` so the
    caller is never blocked longer than *timeout* seconds.
    """
    from vertexai.preview import caching
    from vertexai.generative_models import Part, Content

    parts = [Part.from_uri(mime_type="video/mp4", uri=video_gcs_uri)]
    if audio_gcs_uri:
        parts.append(Part.from_uri(mime_type="audio/wav", uri=audio_gcs_uri))

    result_holder: list = [None]
    error_holder: list = [None]
    done_event = threading.Event()

    def _create():
        try:
            result_holder[0] = caching.CachedContent.create(
                model_name=model_name,
                system_instruction=(
                    "You are a Master Translator engine with visual and aural perception. "
                    "Use the video and audio to resolve tone, deixis, and on-screen text. "
                    "Prefer audio/visual evidence when it conflicts with the transcript."
                ),
                contents=[Content(role="user", parts=parts)],
                ttl=datetime.timedelta(minutes=60),
            )
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_create, daemon=True)
    t.start()

    if not done_event.wait(timeout=timeout):
        raise TimeoutError(f"Content cache creation timed out after {timeout}s")
    if error_holder[0] is not None:
        raise error_holder[0]
    return result_holder[0]


def _extract_visual_anchors(vision_data: Optional[dict]) -> Dict[str, str]:
    if not vision_data:
        return {}
    anchors: Dict[str, str] = {}

    # Lower-thirds often contain names/titles; keep as-is
    for item in vision_data.get("lower_thirds", []) or []:
        name = str(item.get("name") or "").strip()
        title = str(item.get("title") or "").strip()
        if name:
            anchors[name] = name
        if title and len(title) <= 60:
            anchors[title] = title

    # OCR text from shots (names/places/verses); keep as-is
    for shot in vision_data.get("shots", []) or []:
        ocr_text = str(shot.get("ocr_text") or "").strip()
        if not ocr_text:
            continue
        for line in [p.strip() for p in ocr_text.split("\n") if p.strip()]:
            if 2 <= len(line) <= 60:
                anchors[line] = line

    # Cap anchors to avoid prompt bloat
    if len(anchors) > 60:
        trimmed = dict(list(anchors.items())[:60])
        return trimmed
    return anchors


def _infer_gender_from_name(name: str) -> str:
    first = (name or "").split()[0].strip().lower()
    if not first:
        return "unknown"
    if first in FEMALE_NAMES:
        return "female"
    if first in MALE_NAMES:
        return "male"
    return "unknown"


def _extract_visual_speaker_info(vision_data: Optional[dict]) -> Dict[str, dict]:
    if not vision_data:
        return {}
    speakers: Dict[str, dict] = {}

    for item in vision_data.get("lower_thirds", []) or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = str(item.get("title") or "").strip()
        gender = _infer_gender_from_name(name)
        speakers[name] = {"name": name, "gender": gender, "role": role}

    return speakers


def _build_visual_context_for_chunk(chunk: List[dict], vision_data: Optional[dict]) -> str:
    if not vision_data or not chunk:
        return "No visual data"

    try:
        batch_start = min(float(seg.get("start", 0)) for seg in chunk)
    except Exception:
        batch_start = 0.0

    shots = vision_data.get("shots", []) or []
    speakers = vision_data.get("speakers", []) or []
    lower_thirds = vision_data.get("lower_thirds", []) or []

    current_shot = {"scene": "", "text_on_screen": "", "transition": ""}
    for shot in shots:
        try:
            if float(shot.get("start", 0)) <= batch_start:
                current_shot = {
                    "scene": str(shot.get("description") or ""),
                    "text_on_screen": str(shot.get("ocr_text") or ""),
                    "transition": str(shot.get("type") or ""),
                }
            else:
                break
        except Exception:
            continue

    current_speakers = {"count": 1, "positions": []}
    for sp in speakers:
        try:
            if float(sp.get("start", 0)) <= batch_start:
                current_speakers = {
                    "count": int(sp.get("count", 1)),
                    "positions": sp.get("positions") or [],
                }
            else:
                break
        except Exception:
            continue

    active_graphics: List[str] = []
    for lt in lower_thirds:
        try:
            start = float(lt.get("start", 0))
            desc = str(lt.get("description") or "").strip()
            if start <= batch_start <= start + 10 and desc:
                active_graphics.append(desc)
        except Exception:
            continue

    parts: List[str] = []
    if current_shot.get("scene"):
        parts.append(f"Scene: {current_shot['scene']}")
    if current_shot.get("text_on_screen"):
        parts.append(f"On-screen text: {current_shot['text_on_screen']}")
    if current_shot.get("transition"):
        parts.append(f"Transition: {current_shot['transition']}")
    if current_speakers.get("count", 1) > 1:
        positions = ", ".join(current_speakers.get("positions") or [])
        parts.append(f"Speakers on screen: {current_speakers['count']} ({positions})")
    if active_graphics:
        parts.append(f"Graphics: {', '.join(active_graphics)}")

    return ". ".join(parts) if parts else "No visual data"


def _merge_speaker_info(text_speakers: Dict[str, dict], visual_speakers: Dict[str, dict]) -> Dict[str, dict]:
    merged: Dict[str, dict] = dict(text_speakers or {})
    if not visual_speakers:
        return merged
    for key, info in visual_speakers.items():
        if key in merged:
            continue
        merged[key] = info
    return merged



def _extract_speaker_info(segments: list[dict]) -> dict:
    """
    Extract speaker information from segments for gender-aware translation.

    Looks for self-introduction patterns like:
    - "I'm Wendy" / "I'm Wendy Griffith"
    - "My name is John"
    - "This is David"

    Also detects guest introductions (common in broadcast):
    - "Please welcome Pastor John Smith"
    - "Joining us is Dr. Mary Johnson"
    - "With us today is Sarah"

    Returns dict of speaker_id -> {name, gender, role}
    """
    import re

    speaker_info = {}
    # Track names mentioned by hosts (for guests who don't self-introduce)
    mentioned_names = {}

    # Patterns to detect self-introduction
    intro_patterns = [
        r"(?:I'm|I am|My name is|This is|It's)\s+([A-Z][a-z]+)",
        r"(?:Hi,?\s+)?(?:I'm|I am)\s+([A-Z][a-z]+)\s+([A-Z][a-z]+)?",
    ]

    # Patterns for host introducing guests (broadcast style)
    # These capture the name being introduced, not the speaker
    guest_intro_patterns = [
        r"(?:Please welcome|Joining us(?: today| now)?(?:\s+is)?|With us(?: today| now)?(?:\s+is)?|Here(?:'s| is)|Let me introduce|I'd like you to meet|We have|Our guest(?: today)? is)\s+(?:Dr\.?|Pastor|Rabbi|Father|Sister|Brother|Reverend|Rev\.?)?\s*([A-Z][a-z]+)",
        r"(?:Welcome|Thanks for (?:being|joining) us),?\s+([A-Z][a-z]+)",
        r"(?:So|And|Now),?\s+([A-Z][a-z]+),?\s+(?:tell us|what do you|how do you|can you)",
    ]

    # First pass: collect self-introductions
    for seg in segments:
        speaker_id = seg.get("speaker")
        if not speaker_id:
            continue

        # Skip if we already have good info for this speaker
        if speaker_id in speaker_info and speaker_info[speaker_id].get("gender") != "unknown":
            continue

        text = str(seg.get("text") or "")

        # Try to find name introduction
        for pattern in intro_patterns:
            match = re.search(pattern, text)
            if match:
                first_name = match.group(1).lower()

                # Determine gender from name
                gender = "unknown"
                if first_name in FEMALE_NAMES:
                    gender = "female"
                elif first_name in MALE_NAMES:
                    gender = "male"

                speaker_info[speaker_id] = {
                    "name": match.group(1),
                    "gender": gender,
                    "role": "",
                }
                break

        # Also check if this speaker is introducing someone else
        for pattern in guest_intro_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                guest_name = match.group(1)
                guest_name_lower = guest_name.lower()
                # Store the mentioned name for later association
                if guest_name_lower not in mentioned_names:
                    gender = "unknown"
                    if guest_name_lower in FEMALE_NAMES:
                        gender = "female"
                    elif guest_name_lower in MALE_NAMES:
                        gender = "male"
                    mentioned_names[guest_name_lower] = {
                        "name": guest_name,
                        "gender": gender,
                        "introduced_by": speaker_id,
                    }

    # Second pass: associate mentioned names with speakers who respond
    # If speaker A introduces "John", and then speaker B (unknown) starts talking,
    # speaker B is likely John
    prev_speaker = None
    for seg in segments:
        speaker_id = seg.get("speaker")
        if not speaker_id:
            continue

        # If this is a new speaker we don't have info for
        if speaker_id not in speaker_info or speaker_info[speaker_id].get("gender") == "unknown":
            # Check if the previous speaker introduced someone
            if prev_speaker and prev_speaker in speaker_info:
                # Find if there's a mentioned name that was introduced by prev_speaker
                for name_lower, info in mentioned_names.items():
                    if info.get("introduced_by") == prev_speaker and info.get("gender") != "unknown":
                        # This new speaker is likely the person who was just introduced
                        speaker_info[speaker_id] = {
                            "name": info["name"],
                            "gender": info["gender"],
                            "role": "guest",
                        }
                        # Mark as used so we don't assign twice
                        info["assigned_to"] = speaker_id
                        break

        prev_speaker = speaker_id

    # Initialize any remaining unknown speakers
    for seg in segments:
        speaker_id = seg.get("speaker")
        if speaker_id and speaker_id not in speaker_info:
            speaker_info[speaker_id] = {
                "name": f"Speaker {speaker_id}",
                "gender": "unknown",
                "role": "",
            }

    return speaker_info


def _quality_tier_from_rating(rating: float) -> str:
    """Convert a numeric rating to a quality tier label."""
    if rating >= 8.5:
        return "Broadcast Ready"
    elif rating >= 7.0:
        return "Needs Minor Polish"
    elif rating >= 5.0:
        return "Needs Review"
    else:
        return "Draft"


def _clean_model_json(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _iter_input_ids(segments: list[dict]) -> list[int]:
    ids: list[int] = []
    for seg in segments:
        seg_id = seg.get("id")
        if seg_id is None:
            raise ValueError("Segment missing 'id'")
        ids.append(int(seg_id))
    return ids


def _sample_segments_for_brief(segments: list[dict], max_segments: int) -> list[dict]:
    if max_segments <= 0 or not segments:
        return []
    if len(segments) <= max_segments:
        return segments
    if max_segments == 1:
        return [segments[0]]
    total = len(segments)
    step = (total - 1) / float(max_segments - 1)
    selected: list[int] = []
    seen: set[int] = set()
    for i in range(max_segments):
        idx = int(round(i * step))
        if idx in seen:
            continue
        selected.append(idx)
        seen.add(idx)
    return [segments[i] for i in selected]


def _build_document_brief(
    model: GenerativeModel,
    *,
    segments: list[dict],
    max_segments: int,
    max_chars: int,
) -> str:
    sampled = _sample_segments_for_brief(segments, max_segments)
    if not sampled:
        return ""

    lines: list[str] = []
    total_chars = 0
    for seg in sampled:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        seg_id = seg.get("id")
        line = f"{seg_id}: {text}" if seg_id is not None else text
        if max_chars and (total_chars + len(line) + 1) > max_chars:
            break
        lines.append(line)
        total_chars += len(line) + 1

    if not lines:
        return ""

    excerpt = "\n".join(lines)
    prompt = f"""
ROLE: You are a broadcast program summarizer for Omega TV.

TASK:
- Read the transcript excerpts.
- Produce a concise "document brief" to guide translation consistency.
- Output plain text with 3 sections on separate lines:
  Summary: <3-5 sentences>
  Keywords: <comma-separated list of key terms/names>
  Tone: <one sentence about tone/register>
- Keep under 900 characters total.
- Do not invent facts or add details not in the excerpts.
- Write the brief in English.

EXCERPTS:
{excerpt}
"""

    try:
        response = _vertex_generate(
            model,
            prompt,
            generation_config=GenerationConfig(temperature=0.2),
            safety_settings=SAFETY_SETTINGS,
            timeout=60,
        )
    except Exception as exc:
        logger.warning("   ⚠️ Document brief failed: %s", exc)
        return ""

    brief = _clean_model_json(getattr(response, "text", "") or "").strip()
    if len(brief) > 1200:
        brief = brief[:1200].rstrip()
    return brief


def _translate_chunk_once(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    doc_brief: str,
    extra_terms: dict = None,
    entity_anchors: dict = None,
    speaker_info: dict = None,
    visual_context: str = "",
    visual_anchors: dict = None,
) -> list[dict]:
    # Build input payload with speaker and timing information
    input_payload = []
    for seg in chunk:
        item = {"id": int(seg["id"]), "text": str(seg.get("text") or "").strip()}
        # Include speaker ID if available (for gender/voice distinction)
        if seg.get("speaker"):
            item["speaker"] = seg["speaker"]
        # Include duration hint for length-aware translation
        seg_start = float(seg.get("start") or 0)
        seg_end = float(seg.get("end") or seg_start)
        seg_duration = max(0.0, seg_end - seg_start)
        if seg_duration > 0:
            item["timing"] = get_duration_hint(seg_duration)
        input_payload.append(item)

    continuity_payload = [
        {
            "id": int(seg["id"]),
            "source": str(seg.get("source") or "").strip(),
            "translated": str(seg.get("translated") or "").strip(),
        }
        for seg in (continuity or [])
        if seg.get("translated")
    ]

    system_instruction = profiles.get_system_instruction(target_language_code, program_profile, extra_terms=extra_terms)
    brief_block = ""
    if doc_brief:
        brief_block = f"""
DOCUMENT BRIEF (for consistency only; do not infer facts):
{doc_brief}
"""

    merged_anchors = dict(entity_anchors or {})
    if visual_anchors:
        for key, value in visual_anchors.items():
            if key not in merged_anchors:
                merged_anchors[key] = value

    # Build entity anchors block (names/places that must be translated consistently)
    anchors_block = ""
    if merged_anchors:
        anchors_lines = []
        for source_text, target_text in merged_anchors.items():
            if source_text == target_text:
                anchors_lines.append(f"- {source_text} → KEEP AS-IS")
            else:
                anchors_lines.append(f"- {source_text} → {target_text}")
        if anchors_lines:
            anchors_block = f"""
ENTITY ANCHORS (translate these consistently throughout):
{chr(10).join(anchors_lines)}
"""

    # Build speaker context block for gender-aware translation
    speaker_block = ""
    if speaker_info:
        speaker_lines = []
        for speaker_id, info in speaker_info.items():
            name = info.get("name", speaker_id)
            gender = info.get("gender", "unknown")
            role = info.get("role", "")
            if role:
                speaker_lines.append(f"- {speaker_id}: {name} ({gender}, {role})")
            else:
                speaker_lines.append(f"- {speaker_id}: {name} ({gender})")
        if speaker_lines:
            speaker_block = f"""
SPEAKER INFORMATION (use correct grammatical gender for each speaker):
{chr(10).join(speaker_lines)}
NOTE: For gendered languages, use the correct verb/adjective endings based on speaker gender.
When a speaker introduces themselves (e.g., "I'm Wendy"), that speaker is female.
When speaker changes, treat each speaker's lines independently.
"""

    # Check if this is a gendered language that needs speaker awareness
    gendered_languages = {"is", "de", "es", "pt", "fr", "it", "ru", "pl", "cs", "nl"}
    gender_instruction = ""
    if target_language_code in gendered_languages:
        gender_instruction = f"""
GENDER AGREEMENT ({target_language_name} requires grammatical gender):
- Pay attention to the "speaker" field in each segment.
- Different speakers may have different genders - use correct endings for EACH speaker.
- If a segment contains "I'm [Name]" or "My name is [Name]", infer gender from the name.
- For female speakers: use feminine verb/adjective forms (e.g., Icelandic: -in/-un endings).
- For male speakers: use masculine forms.
- When unsure, prefer natural-sounding phrasing over strict gender matching.
"""

    prompt = f"""ROLE: You are the Lead Translator for Omega TV (Professional Broadcast Subtitles).

SYSTEM INSTRUCTION (obey strictly):
{system_instruction}

TASK:
1. Translate the INPUT segments into natural, spoken {target_language_name}.
2. Ensure the translation flows smoothly across segment boundaries.
3. Respect the TONE and KEYWORDS from the Document Brief if provided.
4. Return ONLY a valid JSON array of objects: {{ "id": <int>, "text": <string> }}.
5. STRICTLY preserve all IDs from the input. No extras, none missing.

STYLE GUIDELINES:
- **Natural Flow**: Translate meaning, not just words. Avoid "Translationese". Produce natural {target_language_name} a native speaker would use.
- **Conciseness**: Subtitles must be readable. Condensed phrasing is preferred over wordy literalism.
- **Formatting**: Do NOT use ALL CAPS. Use standard sentence case. Preserve acronyms (USA, TV, I-690).
- **Terminology**: Keep proper nouns, mandated titles, and glossary terms exactly as specified.

TIMING-AWARE CONDENSATION:
Each input segment has a "timing" field indicating how much screen time is available:
- "very_short": Maximum condensation needed. Drop fillers ("you know", "well", "I mean"), tag questions, and repetitions entirely. Use the shortest natural phrasing possible. Every word must earn its place.
- "short": Concise phrasing. Drop fillers and redundant phrases. Prefer compact constructions.
- "normal": Natural phrasing. Standard broadcast quality. Minor condensation of fillers is still good.
- "long": Full natural translation. No condensation pressure.
When condensing, NEVER drop core meaning, theological terms, names, or scripture references. Cut spoken-language redundancy first: filler words, hedging phrases, self-corrections, tag questions, and unnecessary repetitions.

CRITICAL — CONTENT PRESERVATION:
- NEVER omit, censor, or filter any segment. This is professional broadcast content.
- Translate ALL content exactly as provided, including references to violence, politics, or sensitive topics.
- News content about conflicts, protests, executions, or crises must be translated faithfully.
- Do NOT skip segments or return empty translations. Every input segment MUST have a translation.
- NEVER drop core theological meaning. Terms and concepts like God, Jesus Christ, Holy Spirit, salvation, redemption, grace, sin, repentance, gospel, scripture, sermon, prayer, and faith must be preserved when present.
- Do not leave incomplete sentence fragments that lose nouns or final theological meaning.

MULTIMODAL PRIORITY (video + audio):
- You have access to the video/audio context cache.
- If transcript conflicts with audio or visuals, correct it. Trust your ears/eyes.
- Use on-screen OCR (lower-thirds, graphics) for names, places, and scripture references.
- Resolve deixis ("this/that/here") using what is shown on screen.
VISUAL CONTEXT (current scene):
{visual_context}
{gender_instruction}
{brief_block}{anchors_block}{speaker_block}
CONTINUITY CONTEXT (Preceding segments — for flow only):
{json.dumps(continuity_payload, ensure_ascii=False)}

INPUT SEGMENTS (Translate these):
{json.dumps(input_payload, ensure_ascii=False)}

FINAL SELF-CHECK BEFORE OUTPUT:
1. Every input ID appears exactly once in output.
2. No segment is blank.
3. No segment drops key nouns/concepts from the source.
4. Translation is complete (not abruptly cut off).

Return ONLY a JSON array: [{{"id": <int>, "text": "<translation>"}}]"""

    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
            },
        },
        temperature=0.25,
    )

    # Enable Gemini thinking mode for deeper reasoning on condensation and phrasing
    thinking_budget = int(os.environ.get("OMEGA_CLOUD_THINKING_BUDGET", "8192") or 0)
    if thinking_budget > 0:
        try:
            from google.cloud.aiplatform_v1beta1.types import GenerationConfig as _ProtoGenConfig
            generation_config._raw_generation_config.thinking_config = (
                _ProtoGenConfig.ThinkingConfig(thinking_budget=thinking_budget)
            )
        except Exception:
            pass  # SDK version may not support thinking — degrade gracefully

    response = _vertex_generate(
        model,
        prompt,
        generation_config=generation_config,
        safety_settings=SAFETY_SETTINGS,
        timeout=60,
    )

    cleaned = _clean_model_json(getattr(response, "text", "") or "")
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Model response is not a JSON array")

    expected_ids = set(_iter_input_ids(input_payload))
    result_map: Dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        text = item.get("text")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if seg_id_int not in expected_ids:
            continue
        if not isinstance(text, str):
            continue
        result_map[seg_id_int] = text

    missing = [seg_id for seg_id in expected_ids if seg_id not in result_map]
    if missing:
        raise ValueError(f"Missing IDs in model response: {sorted(missing)[:8]}")

    # Omission guard: reject obviously incomplete outputs before downstream review.
    for seg in input_payload:
        seg_id = int(seg.get("id"))
        source_text = str(seg.get("text") or "").strip()
        translated_text = str(result_map.get(seg_id) or "").strip()
        if not translated_text:
            raise ValueError(f"Empty translation for id={seg_id}")
        source_word_count = len(_WORD_RE.findall(source_text))
        translated_word_count = len(_WORD_RE.findall(translated_text))
        # If the source is substantial and translation is only 1-2 words, this is usually an omission.
        if source_word_count >= 8 and translated_word_count <= 2:
            raise ValueError(
                f"Suspiciously short translation for id={seg_id} "
                f"(source_words={source_word_count}, translated_words={translated_word_count})"
            )

    ordered_ids = _iter_input_ids(input_payload)
    return [{"id": seg_id, "text": result_map[seg_id]} for seg_id in ordered_ids]


def _translate_chunk(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    target_language_name: str,
    target_language_code: str,
    program_profile: str,
    continuity: list[dict],
    doc_brief: str,
    max_attempts: int,
    split_after_attempts: int,
    extra_terms: dict = None,
    entity_anchors: dict = None,
    speaker_info: dict = None,
    vision_data: Optional[dict] = None,
    visual_anchors: dict = None,
    _depth: int = 0,
) -> list[dict]:
    if not chunk:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            visual_context = _build_visual_context_for_chunk(chunk, vision_data)
            return _translate_chunk_once(
                model,
                chunk=chunk,
                target_language_name=target_language_name,
                target_language_code=target_language_code,
                program_profile=program_profile,
                continuity=continuity,
                doc_brief=doc_brief,
                extra_terms=extra_terms,
                entity_anchors=entity_anchors,
                speaker_info=speaker_info,
                visual_context=visual_context,
                visual_anchors=visual_anchors,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(chunk) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Translate retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)
        except Exception as exc:
            last_exc = exc
            if is_rate_limit_error(exc):
                logger.warning("   ⏳ Rate limit hit (retry %s/%s): %s", attempt, max_attempts, exc)
                rate_limit_backoff(attempt)
            else:
                logger.warning("   ⚠️ Translate retry %s/%s: %s", attempt, max_attempts, exc)
                backoff_sleep(attempt)

    if len(chunk) <= 1:
        raise last_exc or RuntimeError("Chunk translation failed")

    mid = max(1, len(chunk) // 2)
    logger.warning(
        "   🔪 Splitting chunk (%s segments) at depth %s after failures: %s",
        len(chunk),
        _depth,
        last_exc,
    )
    left = _translate_chunk(
        model,
        chunk=chunk[:mid],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=continuity,
        doc_brief=doc_brief,
        max_attempts=max_attempts,
        extra_terms=extra_terms,
        entity_anchors=entity_anchors,
        speaker_info=speaker_info,
        vision_data=vision_data,
        visual_anchors=visual_anchors,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    tail_context = _build_continuity_window(continuity, left, max_items=len(continuity) or 8)
    right = _translate_chunk(
        model,
        chunk=chunk[mid:],
        target_language_name=target_language_name,
        target_language_code=target_language_code,
        program_profile=program_profile,
        continuity=tail_context,
        doc_brief=doc_brief,
        max_attempts=max_attempts,
        extra_terms=extra_terms,
        entity_anchors=entity_anchors,
        speaker_info=speaker_info,
        vision_data=vision_data,
        visual_anchors=visual_anchors,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    return left + right


def _music_heuristic_ids(segments: list[dict]) -> set[int]:
    """
    Identify music segments using heuristics before LLM classification.

    Checks:
    1. Explicit music markers like (MUSIC), [music], ♪
    2. Opening worship patterns (short phrases with worship keywords in first 90s)
    """
    ids: set[int] = set()
    for seg in segments:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        # Check for explicit music markers
        if _is_music_marker_text(text):
            try:
                ids.add(int(seg.get("id")))
            except Exception:
                pass
            continue
        # Check for opening worship patterns (within first 90 seconds)
        if _is_opening_worship(seg):
            try:
                ids.add(int(seg.get("id")))
                logger.debug(f"🎵 Opening worship heuristic: [{seg.get('id')}] {text[:50]}")
            except Exception:
                pass
    return ids


def _detect_music_chunk_once(model: GenerativeModel, *, chunk: list[dict]) -> list[int]:
    input_payload = [{"id": int(seg["id"]), "text": str(seg.get("text") or "").strip()} for seg in chunk]

    prompt = f"""
ROLE: You are a broadcast segment classifier for Omega TV.

TASK:
- Identify segments that are clearly music/lyrics/choir/worship singing (non-spoken content).
- Be conservative: ONLY return IDs when you are confident it is singing/lyrics.
- Do NOT mark segments where speakers merely talk about music.
- CRITICAL: If speech is present over music (e.g., organ/piano under speech), do NOT mark it as music. This is common in sermons.
- CRITICAL: If the text looks like an Intro or Greeting ("Welcome", "Hallelujah", "Amen"), ASSUME SPEECH.
- Return ONLY JSON (no markdown fences).
- Output MUST be a JSON array of integer IDs.

INPUT:
{json.dumps(input_payload, ensure_ascii=False)}
"""

    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={"type": "array", "items": {"type": "integer"}},
        temperature=0.0,
    )

    response = _vertex_generate(
        model,
        prompt,
        generation_config=generation_config,
        safety_settings=SAFETY_SETTINGS,
        timeout=60,
    )

    cleaned = _clean_model_json(getattr(response, "text", "") or "")
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Music detector response is not a JSON array")

    expected_ids = set(_iter_input_ids(input_payload))
    music_ids: list[int] = []
    for item in parsed:
        try:
            seg_id = int(item)
        except Exception:
            continue
        if seg_id in expected_ids:
            music_ids.append(seg_id)
    return music_ids


def _detect_music_chunk(
    model: GenerativeModel,
    *,
    chunk: list[dict],
    max_attempts: int,
    split_after_attempts: int,
    _depth: int = 0,
) -> list[int]:
    if not chunk:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _detect_music_chunk_once(model, chunk=chunk)
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(chunk) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Music detect retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)
        except Exception as exc:
            last_exc = exc
            logger.warning("   ⚠️ Music detect retry %s/%s: %s", attempt, max_attempts, exc)
            backoff_sleep(attempt)

    if len(chunk) <= 1:
        raise last_exc or RuntimeError("Music detection failed")

    mid = max(1, len(chunk) // 2)
    logger.warning(
        "   🔪 Splitting music chunk (%s segments) at depth %s after failures: %s",
        len(chunk),
        _depth,
        last_exc,
    )
    left = _detect_music_chunk(
        model,
        chunk=chunk[:mid],
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    right = _detect_music_chunk(
        model,
        chunk=chunk[mid:],
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
    )
    return left + right


def _detect_music_ids(
    model: GenerativeModel,
    *,
    segments: list[dict],
    max_attempts: int,
    split_after_attempts: int,
    chunk_size: int,
) -> set[int]:
    music_ids = _music_heuristic_ids(segments)
    remaining: list[dict] = []
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        if seg_id in music_ids:
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if _looks_like_speech(text):
            continue
        remaining.append(seg)

    for offset in range(0, len(remaining), chunk_size):
        chunk = remaining[offset : offset + chunk_size]
        music_ids.update(
            _detect_music_chunk(
                model,
                chunk=chunk,
                max_attempts=max_attempts,
                split_after_attempts=split_after_attempts,
            )
        )
    safe_ids: set[int] = set()
    for seg in segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        if seg_id not in music_ids:
            continue
        text = str(seg.get("text") or "").strip()
        if _is_music_marker_text(text) or not _looks_like_speech(text):
            safe_ids.add(seg_id)
    return safe_ids


def _build_continuity_window(
    prior: list[dict],
    translated: list[dict],
    *,
    max_items: int,
) -> list[dict]:
    merged: list[dict] = []
    for item in prior or []:
        if item.get("translated"):
            merged.append(item)
    # Add newest translations (source text is optional here; we keep translated for consistency).
    for item in translated or []:
        merged.append({"id": item["id"], "source": "", "translated": item["text"]})
    return merged[-max_items:] if max_items and len(merged) > max_items else merged


def _checkpoint_is_valid(
    checkpoint: Optional[dict],
    *,
    job_id: str,
    target_language_code: str,
    program_profile: str,
) -> bool:
    """
    Returns True if checkpoint matches current job parameters.
    
    If checkpoint is from a different job/language/profile, we discard it
    to prevent stale translations from leaking into the new run.
    """
    if not checkpoint or not isinstance(checkpoint, dict):
        return False
    # Schema version check (new field not present in old checkpoints)
    if checkpoint.get("version", 1) < CHECKPOINT_SCHEMA_VERSION:
        logger.warning("   ⚠️ Checkpoint schema version mismatch; discarding")
        return False
    if str(checkpoint.get("job_id") or "") != str(job_id):
        logger.warning("   ⚠️ Checkpoint job_id mismatch (%s != %s); discarding",
                       checkpoint.get("job_id"), job_id)
        return False
    if str(checkpoint.get("target_language_code") or "").lower() != str(target_language_code).lower():
        logger.warning("   ⚠️ Checkpoint target_language_code mismatch; discarding")
        return False
    if str(checkpoint.get("program_profile") or "").lower() != str(program_profile).lower():
        logger.warning("   ⚠️ Checkpoint program_profile mismatch; discarding")
        return False
    return True


def _lang_name(code: str) -> str:
    mapping = {
        "is": "Icelandic",
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "pt": "Portuguese",
        "it": "Italian",
        "nl": "Dutch",
    }
    return mapping.get((code or "").lower().strip(), (code or "Icelandic").strip() or "Icelandic")


def _write_progress(
    storage_client: storage.Client,
    *,
    paths: GcsJobPaths,
    stage: str,
    status: str,
    progress: float,
    meta: Optional[dict] = None,
) -> None:
    progress_meta = dict(meta or {})
    trace_id = str(progress_meta.get("trace_id") or "").strip()
    payload = {
        "stage": stage,
        "status": status,
        "progress": float(progress),
        "updated_at": utc_iso_now(),
        "meta": progress_meta,
    }
    if trace_id:
        payload["trace_id"] = trace_id
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.progress_json(), payload=payload)


def _extract_trace_id(job: dict, job_id: str) -> str:
    meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
    value = (
        job.get("trace_id")
        or job.get("correlation_id")
        or meta.get("trace_id")
        or meta.get("correlation_id")
        or job_id
    )
    return str(value or job_id).strip() or job_id



def run_job(*, bucket: str, prefix: str, job_id: str) -> None:
    """Entry point for Cloud Run."""
    run_job_2step(bucket=bucket, prefix=prefix, job_id=job_id)


def run_job_2step(*, bucket: str, prefix: str, job_id: str) -> None:
    """2-step cloud pipeline: Translation → Review/Polish (multimodal)."""
    # Prefer Workload Identity / attached service accounts in Cloud Run; for local runs
    # we fall back to ./service_account.json.
    ensure_google_application_credentials()

    storage_client = storage.Client()
    paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)

    _write_progress(
        storage_client,
        paths=paths,
        stage="CLOUD_STARTING",
        status="Job received",
        progress=5.0,
        meta={"trace_id": job_id},
    )

    job = download_json(storage_client, bucket=paths.bucket, blob_name=paths.job_json())
    trace_id = _extract_trace_id(job, job_id)
    logger.info("☁️ Cloud job loaded: job_id=%s trace_id=%s", job_id, trace_id)

    def write_progress(stage: str, status: str, progress: float, meta: Optional[dict] = None) -> None:
        progress_meta = dict(meta or {})
        progress_meta.setdefault("trace_id", trace_id)
        _write_progress(
            storage_client,
            paths=paths,
            stage=stage,
            status=status,
            progress=progress,
            meta=progress_meta,
        )
    # Support both field names: target_language_code (new) and target_language (legacy)
    target_language_code = str(
        job.get("target_language_code") or job.get("target_language") or job.get("language") or ""
    ).strip().lower()
    if not target_language_code:
        raise ValueError("Job missing required field: target_language_code (or target_language). Cannot determine target language.")
    program_profile = str(job.get("program_profile") or "standard").strip() or "standard"

    translator_model_name = str(job.get("translator_model") or config.MODEL_TRANSLATOR).strip()
    editor_model_name = str(job.get("editor_model") or config.MODEL_EDITOR).strip()
    # Two-step pipeline: Translation → Review/Polish (no separate polish pass)
    music_detect = _is_truthy(job.get("music_detect", config.OMEGA_CLOUD_MUSIC_DETECT))
    doc_brief_enabled = _is_truthy(job.get("doc_brief", config.OMEGA_CLOUD_DOC_BRIEF))

    max_attempts = config.OMEGA_CLOUD_TRANSLATE_MAX_ATTEMPTS
    split_after_attempts = config.OMEGA_CLOUD_TRANSLATE_SPLIT_AFTER
    chunk_size = config.OMEGA_CLOUD_TRANSLATE_CHUNK_SIZE
    chunk_size = max(10, min(chunk_size, 220))
    continuity_size = config.OMEGA_CLOUD_CONTINUITY_SIZE
    continuity_size = max(0, min(continuity_size, 30))
    music_chunk_size = config.OMEGA_CLOUD_MUSIC_CHUNK_SIZE
    music_chunk_size = max(20, min(music_chunk_size, 240))
    max_editor_attempts = config.OMEGA_CLOUD_EDITOR_MAX_ATTEMPTS
    max_editor_attempts = max(1, min(max_editor_attempts, 5))
    doc_brief_segments = config.OMEGA_CLOUD_DOC_BRIEF_SEGMENTS
    doc_brief_segments = max(20, min(doc_brief_segments, 240))
    doc_brief_chars = config.OMEGA_CLOUD_DOC_BRIEF_CHARS
    doc_brief_chars = max(2000, min(doc_brief_chars, 24000))

    write_progress(
        stage="CLOUD_TRANSLATING",
        status="Loading skeleton",
        progress=40.0,
        meta={
            "translator_model": translator_model_name,
            "editor_model": editor_model_name,
            "music_detect": music_detect,
            "target_language_code": target_language_code,
            "program_profile": program_profile,
        },
    )

    skeleton = download_json(storage_client, bucket=paths.bucket, blob_name=paths.skeleton_json())
    segments = skeleton.get("segments", skeleton) if isinstance(skeleton, dict) else skeleton
    if not isinstance(segments, list):
        raise ValueError("skeleton.json is not a list (or {segments: [...]})")

    # Extract entities from ElevenLabs skeleton for consistent translation anchors
    entity_anchors = {}
    if isinstance(skeleton, dict) and skeleton.get("entities"):
        raw_entities = skeleton.get("entities", [])
        entity_anchors = profiles.get_entity_anchors(raw_entities, target_language_code)
        if entity_anchors:
            logger.info(f"🏷️ Entity anchors: {len(entity_anchors)} items for consistent translation")

    # Load optional termbook for per-job custom terminology
    extra_terms = {}
    termbook = try_download_json(storage_client, bucket=paths.bucket, blob_name=paths.termbook_json())
    if termbook and isinstance(termbook, dict):
        extra_terms = termbook.get("terms", {})
        if extra_terms:
            logger.info(f"📖 Loaded termbook with {len(extra_terms)} terms")

    target_language_name = _lang_name(target_language_code)

    vertexai.init(project=job.get("project_id") or config.OMEGA_CLOUD_PROJECT, location=config.GEMINI_LOCATION)

    proxy_gcs_uri = f"gs://{paths.bucket}/{paths.proxy_blob()}"
    audio_gcs_uri = str(job.get("audio_gcs_uri") or "").strip() or None
    if not audio_gcs_uri and blob_exists(storage_client, paths.bucket, paths.audio_blob()):
        audio_gcs_uri = f"gs://{paths.bucket}/{paths.audio_blob()}"

    vision_data = try_download_json(storage_client, bucket=paths.bucket, blob_name=paths.vision_scan_json())
    if vision_data:
        n_shots = len(vision_data.get("shots", []))
        n_speakers = len(vision_data.get("speakers", []))
        n_ocr = len(vision_data.get("ocr_text", []))
        n_thirds = len(vision_data.get("lower_thirds", []))
        logger.info(f"👁️ Vision data loaded: {n_shots} shots, {n_speakers} speakers, {n_ocr} OCR, {n_thirds} lower-thirds")

    visual_anchors = _extract_visual_anchors(vision_data)
    if visual_anchors:
        for key, value in visual_anchors.items():
            if key not in entity_anchors:
                entity_anchors[key] = value

    cached_content = _create_multimodal_cache(
        model_name=translator_model_name,
        video_gcs_uri=proxy_gcs_uri,
        audio_gcs_uri=audio_gcs_uri,
    )
    from vertexai.preview import caching
    translator_model = GenerativeModel.from_cached_content(cached_content=cached_content)
    doc_brief = ""
    if doc_brief_enabled:
        write_progress(
            stage="CLOUD_TRANSLATING",
            status="Summarizing program",
            progress=40.2,
        )
        doc_brief = _build_document_brief(
            translator_model,
            segments=segments,
            max_segments=doc_brief_segments,
            max_chars=doc_brief_chars,
        )

    checkpoint = None
    if blob_exists(storage_client, paths.bucket, paths.translation_checkpoint_json()):
        checkpoint = download_json(storage_client, bucket=paths.bucket, blob_name=paths.translation_checkpoint_json())
    translated_map: Dict[str, str] = {}
    if _checkpoint_is_valid(
        checkpoint,
        job_id=job_id,
        target_language_code=target_language_code,
        program_profile=program_profile,
    ):
        raw = checkpoint.get("translated") or {}
        if isinstance(raw, dict):
            translated_map = {str(k): str(v) for k, v in raw.items() if v is not None}
            logger.info("   ✅ Resumed from valid checkpoint (%d segments)", len(translated_map))
    elif checkpoint:
        logger.warning("   ⚠️ Checkpoint discarded; starting fresh translation")

    if music_detect:
        write_progress(
            stage="CLOUD_DETECTING_MUSIC",
            status="Detecting music segments",
            progress=41.0,
        )
        music_ids = _detect_music_ids(
            translator_model,
            segments=segments,
            max_attempts=max_attempts,
            split_after_attempts=split_after_attempts,
            chunk_size=music_chunk_size,
        )
        applied_music_ids: list[int] = []
        if music_ids:
            for seg in segments:
                try:
                    seg_id = int(seg.get("id"))
                except Exception:
                    continue
                if seg_id not in music_ids:
                    continue
                text = str(seg.get("text") or "").strip()
                if _is_music_marker_text(text) or not text:
                    seg["music_original_text"] = seg.get("text")
                    seg["text"] = "(MUSIC)"
                    applied_music_ids.append(seg_id)
                else:
                    seg["music_hint"] = True
            for seg_id in applied_music_ids:
                translated_map[str(seg_id)] = "(MUSIC)"
        write_progress(
            stage="CLOUD_DETECTING_MUSIC",
            status=f"Marked {len(applied_music_ids)} music segments",
            progress=42.0,
            meta={"music_segments": len(applied_music_ids), "music_detected": len(music_ids)},
        )

    input_ids = [int(seg.get("id")) for seg in segments]
    total = len(input_ids)

    def _progress(done: int) -> float:
        if total <= 0:
            return 40.0
        return 40.0 + (max(0.0, min(1.0, done / total)) * 15.0)

    # Extract speaker information for gender-aware translation (text + visual)
    speaker_info = _extract_speaker_info(segments)
    visual_speaker_info = _extract_visual_speaker_info(vision_data)
    speaker_info = _merge_speaker_info(speaker_info, visual_speaker_info)
    if speaker_info:
        detected_speakers = [f"{k}: {v.get('name')} ({v.get('gender')})" for k, v in speaker_info.items()]
        logger.info(f"🎤 Detected speakers: {', '.join(detected_speakers)}")

    # Determine what still needs translation.
    to_translate: list[dict] = []
    for seg in segments:
        seg_id = str(seg.get("id"))
        existing = translated_map.get(seg_id)
        if not existing or not existing.strip():
            to_translate.append(seg)

    if not to_translate and translated_map:
        logger.info("✅ Translation already complete; re-emitting draft from checkpoint.")
    else:
        write_progress(
            stage="CLOUD_TRANSLATING",
            status=f"Translating ({target_language_code})",
            progress=_progress(total - len(to_translate)),
            meta={"remaining": len(to_translate), "total": total},
        )

        continuity: list[dict] = []
        if continuity_size > 0:
            continuity = []

        for offset in range(0, len(to_translate), chunk_size):
            chunk = to_translate[offset : offset + chunk_size]
            translated_chunk = _translate_chunk(
                translator_model,
                chunk=chunk,
                target_language_name=target_language_name,
                target_language_code=target_language_code,
                program_profile=program_profile,
                continuity=continuity,
                doc_brief=doc_brief,
                max_attempts=max_attempts,
                split_after_attempts=split_after_attempts,
                extra_terms=extra_terms,
                entity_anchors=entity_anchors,
                speaker_info=speaker_info,
                vision_data=vision_data,
                visual_anchors=visual_anchors,
            )
            for item in translated_chunk:
                translated_map[str(item["id"])] = item["text"]

            if continuity_size > 0:
                continuity = _build_continuity_window(continuity, translated_chunk, max_items=continuity_size)

            done_count = sum(1 for seg_id in input_ids if str(seg_id) in translated_map)
            checkpoint_payload = {
                "version": CHECKPOINT_SCHEMA_VERSION,
                "job_id": job_id,
                "target_language_code": target_language_code,
                "program_profile": program_profile,
                "translated": translated_map,
                "translated_count": int(done_count),
                "total_count": int(total),
                "updated_at": utc_iso_now(),
            }
            upload_json(
                storage_client,
                bucket=paths.bucket,
                blob_name=paths.translation_checkpoint_json(),
                payload=checkpoint_payload,
            )
            write_progress(
                stage="CLOUD_TRANSLATING",
                status=f"Translating ({done_count}/{total})",
                progress=_progress(done_count),
                meta={"segments_done": done_count, "segments_total": total},
            )

    missing = [seg_id for seg_id in input_ids if str(seg_id) not in translated_map]
    if missing:
        raise RuntimeError(f"Translation incomplete; missing {len(missing)} segments")

    translated_segments = [{"id": seg_id, "text": translated_map[str(seg_id)]} for seg_id in input_ids]
    draft_payload = {"source_data": segments, "translated_data": translated_segments}
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.translation_draft_json(), payload=draft_payload)

    review_segments = translated_segments

    write_progress(
        stage="CLOUD_REVIEWING",
        status="Chief Editor reviewing",
        progress=60.0,
    )

    editor_model = GenerativeModel(editor_model_name)

    # Use chunked parallel review for faster processing (especially for long videos)
    # Threshold: Use chunked review if > 100 segments (configurable via env)
    chunked_review_threshold = int(os.environ.get("OMEGA_CLOUD_CHUNKED_REVIEW_THRESHOLD", "100") or 100)
    chunked_review_chunk_size = int(os.environ.get("OMEGA_CLOUD_REVIEW_CHUNK_SIZE", "80") or 80)
    chunked_review_workers = int(os.environ.get("OMEGA_CLOUD_REVIEW_WORKERS", "4") or 4)
    use_chunked_review = len(segments) > chunked_review_threshold

    corrections: list[dict] = []
    report: dict = {}

    if use_chunked_review:
        # Chunked parallel review - 3-5x faster for long videos
        logger.info(f"   🚀 Using chunked parallel review ({len(segments)} segments > {chunked_review_threshold} threshold)")
        write_progress(
            stage="CLOUD_REVIEWING",
            status=f"Chief Editor reviewing ({len(segments)} segments in parallel)",
            progress=60.0,
        )
        corrections, report = _run_chunked_editor_review(
            editor_model,
            source_segments=segments,
            translated_segments=review_segments,
            lang_suffix=target_language_code.upper(),
            chunk_size=chunked_review_chunk_size,
            max_workers=chunked_review_workers,
            max_attempts=max_editor_attempts,
        )
    else:
        # Original single-call review for short videos
        logger.info(f"   📝 Using single-call review ({len(segments)} segments)")
        editor_prompt = _build_editor_prompt(
            source_segments=segments,
            translated_segments=review_segments,
            lang_suffix=target_language_code.upper(),
        )
        for attempt in range(1, max_editor_attempts + 1):
            try:
                editor_response = _vertex_generate(
                    editor_model,
                    editor_prompt,
                    generation_config=GenerationConfig(
                        response_mime_type="application/json",
                        response_schema={
                            "type": "object",
                            "properties": {
                                "corrections": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "fix": {"type": "string"},
                                            "reason": {"type": "string"},
                                        },
                                        "required": ["id", "fix"],
                                    },
                                },
                                "report": {"type": "object"},
                            },
                            "required": ["corrections", "report"],
                        },
                        temperature=0.1,
                    ),
                    safety_settings=SAFETY_SETTINGS,
                    timeout=60,
                )
                corrections, report = _parse_editor_response(getattr(editor_response, "text", "") or "")
                break
            except Exception as exc:
                logger.warning("   ⚠️ Chief editor retry %s/%s: %s", attempt, max_editor_attempts, exc)
                backoff_sleep(attempt)

    approved_segments = _apply_editor_corrections(
        source_segments=segments,
        translated_segments=review_segments,
        corrections=corrections,
    )
    # Upload editor report immediately so it appears in dashboard during Polish phase
    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.editor_report_json(), payload=report or {})

    post_polish_segments = approved_segments
    
    approved_payload = {
        "segments": post_polish_segments,
        "meta": {
            "editor_model": editor_model_name,
            "rating": report.get("rating") if isinstance(report, dict) else None,
            "quality_tier": report.get("quality_tier") if isinstance(report, dict) else None,
            "generated_at": utc_iso_now(),
            "trace_id": trace_id,
        },
    }

    upload_json(storage_client, bucket=paths.bucket, blob_name=paths.approved_json(), payload=approved_payload)

    write_progress(
        stage="CLOUD_DONE",
        status="Approved",
        progress=70.0,
    )


def _build_chunk_editor_prompt(
    *,
    source_segments: list[dict],
    translated_segments: list[dict],
    lang_suffix: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """Build a lightweight editor prompt for a single chunk."""

    # Build minimal segment payloads - just id and text
    source_payload = []
    for seg in source_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        source_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    trans_payload = []
    for seg in translated_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        trans_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    lang_label = lang_suffix.upper()

    if lang_label in {"ICELANDIC", "IS"}:
        rules = """CHECKS:
1. God addressed as "Þú" (never "Þér"). Humans also "Þú".
2. LEXICAL ANGLICISMS: "fyrir þig" → "vegna þín", "á eldi" → "brennandi".
3. STRUCTURAL ANGLICISMS (critical — these make subtitles feel translated):
   - English word order in subordinate clauses → use Icelandic V2 word order.
   - Over-literal relative constructions: avoid "sá sem" when a simpler form works.
   - Passive voice calques: "var verið að" → prefer active or impersonal constructions.
   - Fronted English-style adverbs: "Augljóslega, hann..." → postpose in Icelandic.
   - Progressive tense calques: "Er að gera" when simple present suffices → "Gerir".
4. Terminology: "Partners" → "Bakhjarlar", "I AM" → "ÉG ER", "Pastor" → "Prestur".
5. ALL CAPS → sentence case (keep acronyms: USA, TV, ÉG ER).
6. Natural phrasing: avoid "Við höfum fengið" for states; use "Það er/hefur verið".
7. ASR homophones: "hole"→"hold", "Halloween"→"Hallowed" in religious context.
8. BREVITY: If a segment is correct but unnecessarily verbose, you MAY condense it for readability."""
    else:
        rules = f"""CHECKS:
1. Natural {lang_label} flow — not translated English.
2. ALL CAPS → sentence case (preserve acronyms).
3. ASR errors that led to wrong translations.
4. Theological accuracy and terminology."""

    return f"""ROLE: Chief Editor for Omega TV (Chunk {chunk_index + 1}/{total_chunks}).

TASK: Review {lang_label} translation for errors. Fix only what is wrong.
Do NOT fix line length, CPS, or formatting — that is handled by post-processing.
Priority: detect missing words/phrases and theological meaning loss before stylistic tweaks.

{rules}

SOURCE (English):
{json.dumps(source_payload, ensure_ascii=False)}

TRANSLATION ({lang_label}):
{json.dumps(trans_payload, ensure_ascii=False)}

OUTPUT: {{"corrections": [{{"id": 10, "fix": "Corrected", "reason": "Why"}}]}}
Only include segments that NEED fixing. Empty array if all correct."""


def _review_chunk(
    editor_model: GenerativeModel,
    source_chunk: list[dict],
    trans_chunk: list[dict],
    lang_suffix: str,
    chunk_index: int,
    total_chunks: int,
    max_attempts: int = 3,
) -> Tuple[List[dict], Optional[str]]:
    """Review a single chunk and return corrections."""
    prompt = _build_chunk_editor_prompt(
        source_segments=source_chunk,
        translated_segments=trans_chunk,
        lang_suffix=lang_suffix,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )

    for attempt in range(1, max_attempts + 1):
        try:
            response = _vertex_generate(
                editor_model,
                prompt,
                generation_config=GenerationConfig(
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "corrections": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "fix": {"type": "string"},
                                        "reason": {"type": "string"},
                                    },
                                    "required": ["id", "fix"],
                                },
                            },
                        },
                        "required": ["corrections"],
                    },
                    temperature=0.1,
                ),
                safety_settings=SAFETY_SETTINGS,
                timeout=60,
            )
            text = getattr(response, "text", "") or ""
            cleaned = _clean_model_json(text)
            result = json.loads(cleaned)
            corrections = result.get("corrections", [])
            return corrections, None
        except Exception as exc:
            if attempt < max_attempts:
                backoff_sleep(attempt)
            else:
                return [], f"Chunk {chunk_index + 1} failed: {exc}"
    return [], None


def _run_chunked_editor_review(
    editor_model: GenerativeModel,
    *,
    source_segments: list[dict],
    translated_segments: list[dict],
    lang_suffix: str,
    chunk_size: int = 80,
    max_workers: int = 4,
    max_attempts: int = 3,
) -> Tuple[List[dict], dict]:
    """
    Run editor review in parallel chunks for faster processing.

    Instead of sending all segments in one massive prompt (which can take 3-5 min),
    we split into chunks of ~80 segments and process them in parallel.

    Returns:
        Tuple of (all_corrections, quality_report)
    """
    total = len(source_segments)
    if total == 0:
        return [], {"rating": 10, "quality_tier": "Broadcast Ready", "summary": "No segments to review."}

    # Build ID-indexed maps for alignment
    source_by_id = {int(s.get("id")): s for s in source_segments if s.get("id") is not None}
    trans_by_id = {int(t.get("id")): t for t in translated_segments if t.get("id") is not None}

    # Split into chunks
    source_ids = list(source_by_id.keys())
    chunks = []
    for i in range(0, len(source_ids), chunk_size):
        chunk_ids = source_ids[i:i + chunk_size]
        src_chunk = [source_by_id[sid] for sid in chunk_ids if sid in source_by_id]
        trans_chunk = [trans_by_id[sid] for sid in chunk_ids if sid in trans_by_id]
        chunks.append((src_chunk, trans_chunk))

    total_chunks = len(chunks)
    logger.info(f"   📦 Chunked review: {total} segments → {total_chunks} chunks of ~{chunk_size}")

    all_corrections: List[dict] = []
    errors: List[str] = []

    # Process chunks in parallel
    with ThreadPoolExecutor(max_workers=min(max_workers, total_chunks)) as executor:
        futures = {}
        for idx, (src_chunk, trans_chunk) in enumerate(chunks):
            future = executor.submit(
                _review_chunk,
                editor_model,
                src_chunk,
                trans_chunk,
                lang_suffix,
                idx,
                total_chunks,
                max_attempts,
            )
            futures[future] = idx

        for future in as_completed(futures):
            chunk_idx = futures[future]
            try:
                corrections, error = future.result()
                if corrections:
                    all_corrections.extend(corrections)
                if error:
                    errors.append(error)
                logger.info(f"   ✅ Chunk {chunk_idx + 1}/{total_chunks}: {len(corrections)} corrections")
            except Exception as exc:
                errors.append(f"Chunk {chunk_idx + 1} exception: {exc}")
                logger.warning(f"   ⚠️ Chunk {chunk_idx + 1} failed: {exc}")

    # Generate quality report based on corrections
    # NOTE: The rating here reflects the INITIAL translation quality before Step 2 fixes.
    # A high correction count means Step 1 had issues, but Step 2 FIXED them.
    # The final output quality should be high regardless of correction count.
    correction_count = len(all_corrections)
    correction_rate = correction_count / total if total > 0 else 0

    # Rating reflects PRE-CORRECTION quality (for diagnostics)
    # Thresholds are more lenient since corrections are applied
    if correction_rate < 0.03:  # <3% corrections = excellent first pass
        pre_rating = 9.5
        quality_tier = "Broadcast Ready"
    elif correction_rate < 0.08:  # <8% = good first pass, minor polish applied
        pre_rating = 8.5
        quality_tier = "Broadcast Ready"
    elif correction_rate < 0.15:  # <15% = acceptable, moderate polish applied
        pre_rating = 7.5
        quality_tier = "Needs Minor Polish"
    elif correction_rate < 0.25:  # <25% = needs attention but fixable
        pre_rating = 6.5
        quality_tier = "Needs Review"
    else:  # >25% = significant issues in Step 1
        pre_rating = max(5.0, 6.5 - correction_rate * 5)
        quality_tier = "Needs Review"

    pre_rating = round(min(10.0, max(1.0, pre_rating)), 1)

    # Post-correction quality estimate: after applying fixes, quality should be higher
    # Assume 90% of corrections are valid and improve quality
    post_correction_boost = min(1.5, correction_count * 0.02)  # Small boost for corrections applied
    post_rating = min(9.8, pre_rating + post_correction_boost)

    report = {
        "rating": post_rating,  # Report the POST-correction estimated quality
        "pre_correction_rating": pre_rating,  # For diagnostics
        "quality_tier": quality_tier,
        "summary": f"QA Review: {correction_count} improvements across {total} segments ({correction_rate:.1%} correction rate). Processed in {total_chunks} chunks. Post-correction quality: {post_rating}/10.",
        "major_issues": list(set(c.get("reason", "")[:50] for c in all_corrections[:10] if c.get("reason"))),
        "suggestions": "Corrections applied. Review flagged segments if needed." if correction_count > 0 else "Translation looks excellent!",
        "chunked_review": True,
        "chunks_processed": total_chunks,
        "corrections_applied": correction_count,
        "errors": errors if errors else None,
    }

    return all_corrections, report


def _build_editor_prompt(*, source_segments: list[dict], translated_segments: list[dict], lang_suffix: str) -> str:
    # Build minimal segment payloads - just id and text
    source_payload = []
    for seg in source_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        source_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    trans_payload = []
    for seg in translated_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        trans_payload.append({"id": seg_id, "text": str(seg.get("text") or "").strip()})

    lang_label = lang_suffix.upper()

    if lang_label in {"ICELANDIC", "IS"}:
        rules = """ICELANDIC-SPECIFIC CHECKS:
1. ADDRESS FORMS: God is addressed as "Þú" (NEVER "Þér"). Humans also "Þú" (casual).
2. LEXICAL ANGLICISMS:
   - "fyrir þig" (for you, spiritual) → "vegna þín"
   - "á eldi" (on fire) → "brennandi"
   - "Bless" (impartation) → "Guð blessi þig"
   - Literal "Við höfum fengið" for states → "Það er/hefur verið"
3. STRUCTURAL ANGLICISMS (critical — these make subtitles feel translated):
   - English word order in subordinate clauses → use Icelandic V2 word order.
   - Over-literal relative constructions: avoid "sá sem" when simpler works.
   - Passive voice calques: "var verið að" → prefer active or impersonal constructions.
   - Fronted English-style adverbs: "Augljóslega, hann..." → postpose in Icelandic.
   - Progressive tense calques: "Er að gera" when simple present suffices → "Gerir".
4. TERMINOLOGY:
   - "Partners" → "Bakhjarlar", "I AM" → "ÉG ER", "Covenant" → "Sáttmáli", "Pastor" → "Prestur"
5. ASR CONTEXTUAL CORRECTION:
   - Fix homophones the transcriber got wrong: "hole" vs "hold", "Halloween" vs "Hallowed" in prayers.
6. GENDER AGREEMENT: Ensure verb/adjective endings match the speaker's gender throughout.
7. BREVITY REWRITES: If a segment is correct but unnecessarily verbose for subtitle reading, you MAY condense it. Shorter natural phrasing is always preferred over wordy accuracy."""
    else:
        rules = f"""LANGUAGE-SPECIFIC CHECKS ({lang_label}):
1. NATURAL FLOW: Ensure translations sound like native {lang_label}, not translated English.
2. ANGLICISM DETECTION: Flag calques and literal translations that sound unnatural.
3. THEOLOGICAL ACCURACY: Verify scripture references and religious terminology.
4. GENDER AGREEMENT: Ensure grammatical gender is consistent with speaker identity."""

    return f"""ROLE: Chief Editor for Omega TV broadcast subtitles.

TASK: Review the {lang_label} translation against the English source. Fix errors only.

WHAT TO FIX:
- Mistranslations, missing meaning, or added content not in the source.
- Anglicisms and unnatural phrasing (translated English instead of native {lang_label}).
- Theological errors (wrong scripture version, incorrect terms, wrong address forms).
- ALL CAPS text (convert to sentence case; preserve acronyms like USA, TV, ÉG ER).
- Obvious ASR errors in the source that led to wrong translations.
- Spoken content wrongly marked as "(MUSIC)" or left blank.

WHAT NOT TO FIX:
- Line length, line breaks, or character counts (handled by post-processing).
- Reading speed or CPS (handled by post-processing).
- Dialogue dashes or punctuation formatting (handled by post-processing).
- Segments that are already correct and concise — do not "improve" working translations.
  EXCEPTION: If a correct segment is unnecessarily verbose, you MAY condense it for subtitle readability.

{rules}

SOURCE (English):
{json.dumps(source_payload, ensure_ascii=False)}

DRAFT ({lang_label}):
{json.dumps(trans_payload, ensure_ascii=False)}

OUTPUT: Return JSON with corrections and a quality report.
{{
  "corrections": [{{"id": 10, "fix": "Corrected text", "reason": "Brief explanation"}}],
  "report": {{
    "rating": 8.5,
    "quality_tier": "Broadcast Ready",
    "summary": "Brief quality analysis.",
    "major_issues": ["Category of issues found"],
    "suggestions": "Actionable advice."
  }}
}}
Only include segments that NEED fixing. Empty corrections array if all correct."""


def _parse_editor_response(text: str) -> tuple[list[dict], dict]:
    cleaned = _clean_model_json(text)
    try:
        result = json.loads(cleaned)
    except Exception as exc:
        logger.error(f"❌ JSON PARSE FAILED. Raw response start: {cleaned[:1000]}")
        logger.error(f"❌ JSON PARSE FAILED. Raw response end: {cleaned[-1000:]}")
        raise ValueError(f"Failed to parse editor JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise ValueError("Editor response is not a JSON object")

    corrections = result.get("corrections") or []
    report = result.get("report") or {}
    if not isinstance(corrections, list):
        corrections = []
    if not isinstance(report, dict):
        report = {}
    return corrections, report


def _apply_editor_corrections(
    *,
    source_segments: list[dict],
    translated_segments: list[dict],
    corrections: list[dict],
) -> list[dict]:
    correction_map: Dict[int, str] = {}
    for item in corrections or []:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        fix = item.get("fix")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if isinstance(fix, str) and fix.strip():
            correction_map[seg_id_int] = fix.strip()

    source_map: Dict[int, dict] = {}
    for seg in source_segments:
        try:
            seg_id = int(seg.get("id"))
        except Exception:
            continue
        source_map[seg_id] = {
            "start": seg.get("start"),
            "end": seg.get("end"),
            "source_text": seg.get("text"),
            "words": seg.get("words"),  # Preserve word-level timing for Finalizer
        }

    final_segments: list[dict] = []
    for seg in translated_segments:
        seg_id = int(seg.get("id"))
        text = str(seg.get("text") or "").strip()
        if seg_id in correction_map:
            text = correction_map[seg_id]

        merged = {"id": seg_id, "text": text}
        if seg_id in source_map:
            merged.update(source_map[seg_id])
        final_segments.append(merged)

    return final_segments


def _start_deadman_timer(bucket: str, prefix: str, job_id: str, max_seconds: int) -> threading.Timer:
    """Last-resort safety net: force-kill the process after *max_seconds*.

    Before exiting, we attempt to write a CLOUD_ERROR progress entry so the
    dashboard shows a meaningful status instead of an empty hang.
    """

    def _kill():
        logger.error(
            "DEADMAN TIMER FIRED after %ds -- writing error and force-exiting",
            max_seconds,
        )
        try:
            sc = storage.Client()
            paths = GcsJobPaths(bucket=bucket, prefix=prefix, job_id=job_id)
            _write_progress(
                sc,
                paths=paths,
                stage="CLOUD_ERROR",
                status=f"Deadman timer: exceeded {max_seconds}s",
                progress=0.0,
                meta={"trace_id": job_id},
            )
        except Exception:
            pass
        os._exit(2)

    timer = threading.Timer(max_seconds, _kill)
    timer.daemon = True
    timer.start()
    logger.info("Deadman timer set: %ds", max_seconds)
    return timer


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Omega cloud-first worker (GCS artifacts + Vertex).")
    parser.add_argument("--bucket", default=config.OMEGA_JOBS_BUCKET, help="GCS bucket for job artifacts")
    parser.add_argument("--prefix", default=config.OMEGA_JOBS_PREFIX, help="GCS prefix (folder) for job artifacts")
    parser.add_argument("--job-id", required=True, help="Job id (GCS folder name)")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Start dead-man timer (last-resort kill switch)
    deadman_minutes = config.OMEGA_CLOUD_DEADMAN_MINUTES  # default 60
    deadman_timer = _start_deadman_timer(
        bucket=args.bucket,
        prefix=args.prefix,
        job_id=args.job_id,
        max_seconds=deadman_minutes * 60,
    )

    logger.info("☁️ Omega Cloud Worker starting: job_id=%s bucket=%s", args.job_id, args.bucket)
    start = time.time()
    try:
        run_job(bucket=args.bucket, prefix=args.prefix, job_id=args.job_id)
    except Exception as exc:
        logger.error("❌ Cloud worker failed: %s", exc)
        try:
            storage_client = storage.Client()
            paths = GcsJobPaths(bucket=args.bucket, prefix=args.prefix, job_id=args.job_id)
            _write_progress(
                storage_client,
                paths=paths,
                stage="CLOUD_ERROR",
                status=f"Error: {exc}",
                progress=0.0,
                meta={"trace_id": args.job_id},
            )
        except Exception:
            pass
        return 1
    finally:
        deadman_timer.cancel()
        elapsed = time.time() - start
        logger.info("🏁 Done in %.1fs", elapsed)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
