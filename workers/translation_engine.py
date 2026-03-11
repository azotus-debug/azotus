import logging
import json
import os
import time
import re
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from google import genai
from google.genai import types

import profiles
import config
from workers.text_sanitizer import repair_tab_escaped_t

logger = logging.getLogger("Omega.TranslationEngine")

# Max characters per subtitle line (broadcast standard: 42 chars x 2 lines)
MAX_CHARS_TOTAL = 84


def _repair_embedded_tab_t(text: Any) -> Any:
    """
    Repair model outputs where "\\t" was emitted instead of the letter "t".
    After json.loads, that sequence becomes a literal tab char.
    """
    return repair_tab_escaped_t(text)


def _sanitize_model_segments(segments: Any) -> List[Dict]:
    if not isinstance(segments, list):
        return []
    cleaned: List[Dict] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        current = dict(item)
        current["text"] = _repair_embedded_tab_t(current.get("text", ""))
        cleaned.append(current)
    return cleaned


@dataclass
class TranslationParagraph:
    """
    Represents a coherent block of speech (30-60s) for translation.
    Replaces the old "batch of 45 segments".

    Attributes:
        id: Unique ID for this paragraph (e.g., "p1")
        segments: List of original ElevenLabs word-level segments
        start_time: Start time of the first segment
        end_time: End time of the last segment
        speaker: Speaker name/ID (from diarization)
        context_tags: Metadata tags (e.g., <music>, <laughter>, [SCENE: Hospital])
    """
    id: str
    segments: List[Dict]
    start_time: float
    end_time: float
    speaker: str = "Unknown"
    context_tags: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def text(self) -> str:
        return " ".join([s.get("text", "") for s in self.segments])


class TranslationEngine:
    """
    Core Translation Processor — Gemini 3 Pro via google.genai SDK.

    Uses profiles.get_system_instruction() for rich language/persona prompts,
    and google.genai with thinking_budget for Gemini 3's native reasoning.
    """

    def __init__(self, model_name: str = None, thinking_budget: int = 16384):
        self.model_name = model_name or getattr(config, 'MODEL_TRANSLATOR', 'gemini-3.1-pro-preview')
        self.thinking_budget = thinking_budget

        # Initialize google.genai client with Vertex AI backend
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or getattr(config, 'OMEGA_CLOUD_PROJECT', None)
        location = os.environ.get("GOOGLE_CLOUD_LOCATION") or getattr(config, 'GEMINI_LOCATION', 'global')
        self.client = genai.Client(vertexai=True, project=project_id, location=location)
        logger.info(f"TranslationEngine initialized: model={self.model_name}, thinking_budget={self.thinking_budget}, location={location}")

    @staticmethod
    def build_paragraphs_from_skeleton(skeleton: Dict) -> List[TranslationParagraph]:
        """
        Converts Scribe v2 word-timestamped segments into coherent Paragraphs.
        Rules:
        1. Break on Speaker Change.
        2. Break on Audio Events (Music etc).
        3. Break on Sentence End if duration > 30s.
        4. Force Break at 60s.
        """
        raw_segments = skeleton.get("segments", [])
        paragraphs = []
        current_batch = []
        current_speaker = None
        batch_start_time = 0.0

        para_idx = 1

        for seg in raw_segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            speaker = seg.get("speaker", "Unknown")
            is_event = seg.get("is_audio_event", False)
            text = seg.get("text", "")

            # Audio events are context for the pipeline, not content for translation.
            # Skip them entirely — don't break paragraphs, don't add to batches.
            if is_event:
                continue

            # Initialize first batch
            if not current_batch:
                current_batch.append(seg)
                current_speaker = speaker
                batch_start_time = seg_start
                continue

            # Check Break Conditions
            duration = seg_end - batch_start_time
            speaker_changed = speaker != current_speaker
            is_long = duration > 30.0
            is_punctuated = text.strip().endswith((".", "?", "!"))
            force_break = duration > 60.0

            # Only break on speaker change if the paragraph is already substantial (>10s).
            # Short speaker blips (diarization noise) get absorbed into the current paragraph.
            # This prevents 800 false speaker switches from creating 800 micro-paragraphs.
            effective_speaker_break = speaker_changed and duration > 10.0

            if effective_speaker_break or force_break or (is_long and is_punctuated):
                # COMPLETE CURRENT PARAGRAPH
                context = []
                if any(s.get("is_music") for s in current_batch):
                    context.append("<music>")

                p = TranslationParagraph(
                    id=f"p{para_idx}",
                    segments=current_batch,
                    start_time=current_batch[0]["start"],
                    end_time=current_batch[-1]["end"],
                    speaker=current_speaker,
                    context_tags=context
                )
                paragraphs.append(p)
                para_idx += 1

                # Start New Batch
                current_batch = [seg]
                current_speaker = speaker
                batch_start_time = seg_start
            else:
                current_batch.append(seg)

        # Flush last batch
        if current_batch:
            context = []
            if any(s.get("is_music") for s in current_batch):
                context.append("<music>")
            p = TranslationParagraph(
                id=f"p{para_idx}",
                segments=current_batch,
                start_time=current_batch[0]["start"],
                end_time=current_batch[-1]["end"],
                speaker=current_speaker,
                context_tags=context
            )
            paragraphs.append(p)

        return paragraphs

    # ─── Phase 0: Translation Brief ──────────────────────────────────────

    def generate_translation_brief(
        self,
        paragraphs: List[TranslationParagraph],
        target_lang: str,
        program_profile: str = "standard",
        entity_anchors: dict = None,
        extra_terms: dict = None,
    ) -> Optional[str]:
        """
        Generate a Translation Brief by sending the full program transcript to Gemini.

        The brief gives every paragraph translator document-level awareness:
        sermon structure, recurring themes, committed terminology translations,
        callbacks/cross-references, and translation hazards.

        Returns the brief text, or None if the program is too short or generation fails.
        """
        # 1. Assemble full transcript with speaker labels + timestamps
        transcript_lines = []
        for p in paragraphs:
            # Skip pure music paragraphs
            if p.context_tags and all(t == "<music>" for t in p.context_tags):
                continue
            start_mm = int(p.start_time // 60)
            start_ss = int(p.start_time % 60)
            end_mm = int(p.end_time // 60)
            end_ss = int(p.end_time % 60)
            timestamp = f"[{start_mm:02d}:{start_ss:02d} - {end_mm:02d}:{end_ss:02d}]"
            text = " ".join(s.get("text", "") for s in p.segments).strip()
            if text:
                transcript_lines.append(f"{timestamp} {p.speaker}:\n{text}")

        transcript = "\n\n".join(transcript_lines)

        # 2. Early exit if too short
        if len(paragraphs) < 5 or len(transcript) < 500:
            logger.info(f"📖 Brief skipped: too short ({len(paragraphs)} paragraphs, {len(transcript)} chars)")
            return None

        # 3. Get language config
        lang_config = profiles.LANGUAGES.get(target_lang, profiles.LANGUAGES["is"])
        lang_name = lang_config["name"]
        bible_version = lang_config["bible"]

        # 4. Build entity anchors reference (so Gemini knows what's already decided)
        anchors_ref = ""
        if entity_anchors:
            anchor_lines = [f"  - {src} → {tgt}" for src, tgt in entity_anchors.items()]
            anchors_ref = f"\nALREADY DECIDED (Entity Anchors — do not repeat these):\n" + "\n".join(anchor_lines) + "\n"

        # 5. Build the brief prompt
        prompt = f"""ROLE: You are the Lead Translation Analyst for Omega TV.
You are preparing a Translation Brief that will guide paragraph-by-paragraph
translation of this program from English into {lang_name}.

Your brief will be the ONLY document-level context each translator sees.
Every paragraph is translated in isolation — the translator sees only:
- The 5-30 segments in that paragraph
- The last 5 translated segments (continuity)
- This Translation Brief

Your job is to give the translator everything they need to make consistent
decisions across {len(paragraphs)} paragraphs.

TARGET LANGUAGE: {lang_name}
BIBLE VERSION: {bible_version}
{anchors_ref}
=== FULL TRANSCRIPT ({len(paragraphs)} paragraphs, ~{len(transcript.split())} words) ===

{transcript}

=== PRODUCE THE FOLLOWING SECTIONS ===

SERMON STRUCTURE:
Outline the program's narrative arc with approximate timestamps.
Identify: introduction, main teaching points, stories/illustrations,
scripture readings, altar call/invitation, closing.

KEY THEMES AND IMAGERY:
List 5-10 recurring themes, metaphors, and imagery patterns.
Note where each first appears and where it recurs.

TERMINOLOGY COMMITMENTS ({lang_name}):
List every significant English term/phrase that appears 3+ times and COMMIT
to a single {lang_name} translation for each. Format:
- "the vine" → [your committed {lang_name} translation] (appears ~N times; context)
- "abide" → [your committed {lang_name} translation] (appears ~N times; key verb)
Skip terms already in the standard glossary (God, Jesus, Holy Spirit, etc.)
Focus on sermon-specific vocabulary that MUST be consistent across all paragraphs.

CALLBACKS AND CROSS-REFERENCES:
List moments where the speaker refers back to or forward to other content. Format:
- [timestamp] "as I mentioned earlier about..." → refers back to [timestamp] re: [topic]
The translator needs this to ensure backward references make sense in translation.

NAMES AND ENTITIES:
Proper nouns NOT already in the entity anchors above. For each, decide:
- KEEP: Foreign names to preserve as-is
- TRANSLATE: Names with known {lang_name} forms
- NOTE: Names needing special handling (e.g., "the Word" as a title for Christ)

TONE AND REGISTER:
2-3 sentences on overall tone. Note any shifts (e.g., "conversational storytelling
in first 20 minutes, shifts to urgent prophetic preaching at [35:00]").

TRANSLATION HAZARDS:
3-5 specific moments likely to cause translation problems:
- Wordplay or puns that won't work in {lang_name}
- Culture-specific references needing adaptation
- Long run-on sentences requiring restructuring
- Ambiguous pronouns ("he" could refer to God or the person in the story)
For each, suggest a handling strategy."""

        # 6. Call Gemini
        logger.info(f"📖 Sending transcript to Gemini ({len(transcript)} chars, ~{len(transcript.split())} words)...")

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=8192,
                    ),
                    max_output_tokens=16384,
                    temperature=0.3,
                    safety_settings=[
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                    ],
                ),
            )
        except Exception as e:
            logger.error(f"❌ Translation Brief generation failed: {e}")
            raise

        # 7. Parse response
        brief_text = None
        try:
            brief_text = response.text
        except ValueError:
            pass

        if not brief_text:
            # Check for truncation
            finish_reason = None
            try:
                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
            except Exception:
                pass
            logger.warning(f"📖 Brief generation returned empty (finish_reason={finish_reason})")
            return None

        # 8. Truncate if too long (keep last complete section)
        if len(brief_text) > 8000:
            logger.warning(f"📖 Brief is long ({len(brief_text)} chars), truncating to 8000")
            truncated = brief_text[:8000]
            # Find last section header boundary
            last_header = max(
                truncated.rfind("\nSERMON STRUCTURE:"),
                truncated.rfind("\nKEY THEMES"),
                truncated.rfind("\nTERMINOLOGY COMMITMENTS"),
                truncated.rfind("\nCALLBACKS"),
                truncated.rfind("\nNAMES AND ENTITIES"),
                truncated.rfind("\nTONE AND REGISTER"),
                truncated.rfind("\nTRANSLATION HAZARDS"),
            )
            if last_header > 4000:  # Don't truncate too aggressively
                brief_text = truncated[:last_header].rstrip()
            else:
                brief_text = truncated.rstrip()

        logger.info(f"📖 Translation Brief: {len(brief_text)} chars, {len(brief_text.splitlines())} lines")
        return brief_text

    # ─── Sliding Context Window ──────────────────────────────────────────

    @staticmethod
    def build_sliding_context(
        paragraphs: List['TranslationParagraph'],
        current_index: int,
        window_seconds: float = 120.0,
    ) -> Optional[str]:
        """
        Build a ±window_seconds context window of English source text around
        the current paragraph. Gives Gemini awareness of what was just said
        and what's coming next.

        Returns formatted context string, or None if at the very start/end
        with no neighbors.
        """
        if not paragraphs or current_index < 0 or current_index >= len(paragraphs):
            return None

        before_lines = []
        after_lines = []

        # Walk backward
        cumulative = 0.0
        i = current_index - 1
        while i >= 0 and cumulative < window_seconds:
            p = paragraphs[i]
            cumulative += p.duration
            start_mm = int(p.start_time // 60)
            start_ss = int(p.start_time % 60)
            text = " ".join(s.get("text", "") for s in p.segments).strip()
            if text:
                before_lines.insert(0, f"[{p.speaker} at {start_mm:02d}:{start_ss:02d}] {text}")
            i -= 1

        # Walk forward
        cumulative = 0.0
        i = current_index + 1
        while i < len(paragraphs) and cumulative < window_seconds:
            p = paragraphs[i]
            cumulative += p.duration
            start_mm = int(p.start_time // 60)
            start_ss = int(p.start_time % 60)
            text = " ".join(s.get("text", "") for s in p.segments).strip()
            if text:
                after_lines.append(f"[{p.speaker} at {start_mm:02d}:{start_ss:02d}] {text}")
            i += 1

        # If no context at all, skip
        if not before_lines and not after_lines:
            return None

        parts = ["SURROUNDING CONTEXT (English source — for context only, do NOT translate):\n"]

        if before_lines:
            parts.append("[BEFORE — what was just said:]")
            parts.extend(before_lines)
            parts.append("")

        parts.append(">>> YOU ARE TRANSLATING THE NEXT PARAGRAPH <<<\n")

        if after_lines:
            parts.append("[AFTER — what comes next:]")
            parts.extend(after_lines)

        return "\n".join(parts)

    # ─── Per-Paragraph Translation ───────────────────────────────────────

    def translate_paragraph(
        self,
        paragraph: TranslationParagraph,
        target_lang: str,
        program_profile: str = "standard",
        extra_terms: dict = None,
        entity_anchors: dict = None,
        continuity_payload: list = None,
        speaker_gender: str = None,
        visual_context: str = None,
        doc_brief: str = None,
        sliding_context: str = None,
    ) -> List[Dict]:
        """
        Translates a single paragraph using Gemini 3 Pro with full language
        and persona context from profiles.get_system_instruction().
        """
        # 1. Build system instruction (rich prompt from profiles.py)
        system_instruction = self._get_system_instruction(target_lang, program_profile, extra_terms)

        # 2. Build user prompt (segment data + CPS constraints + context blocks)
        user_prompt = self._build_user_prompt(
            paragraph, target_lang, program_profile, entity_anchors,
            continuity_payload=continuity_payload,
            speaker_gender=speaker_gender,
            visual_context=visual_context,
            doc_brief=doc_brief,
            sliding_context=sliding_context,
        )

        # 3. Call Gemini 3 Pro
        logger.info(f"🧠 Translating Paragraph {paragraph.id} ({len(paragraph.segments)} segments) to {target_lang}...")

        # Budget math: with Translation Brief + sliding context, every paragraph
        # has substantial input context (~3K+ extra tokens). Give Gemini the full
        # thinking budget — the brief alone justifies deep reasoning regardless
        # of paragraph size. Response scales with segment count.
        n_segs = len(paragraph.segments)
        effective_thinking = self.thinking_budget  # 16384 — rich context needs generous thinking
        response_budget = max(2048, n_segs * 200)
        max_output = effective_thinking + response_budget

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=effective_thinking,
                    ),
                    max_output_tokens=max_output,
                    response_mime_type="application/json",
                    response_schema={
                        "type": "OBJECT",
                        "properties": {
                            "segments": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "id": {"type": "STRING"},
                                        "text": {"type": "STRING"},
                                    },
                                    "required": ["id", "text"],
                                },
                            },
                        },
                        "required": ["segments"],
                    },
                    temperature=0.25,
                    safety_settings=[
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                    ],
                ),
            )
        except Exception as e:
            logger.error(f"❌ Translation failed for {paragraph.id}: {e}")
            raise

        # 4. Parse Response — handle empty/blocked responses gracefully
        response_text = None
        try:
            response_text = response.text
        except ValueError:
            # response.text raises ValueError when there's no text content
            pass

        if not response_text:
            # Diagnose WHY there's no text
            finish_reason = None
            block_reason = None
            if response.candidates:
                finish_reason = getattr(response.candidates[0], 'finish_reason', None)
            if hasattr(response, 'prompt_feedback'):
                block_reason = getattr(response.prompt_feedback, 'block_reason', None)

            logger.error(
                f"❌ Empty response for {paragraph.id}: "
                f"finish_reason={finish_reason}, block_reason={block_reason}, "
                f"candidates={len(response.candidates) if response.candidates else 0}"
            )
            raise RuntimeError(
                f"Empty response from model for paragraph {paragraph.id} "
                f"(finish_reason={finish_reason}, block_reason={block_reason})"
            )

        # Check for truncated response (MAX_TOKENS = thinking ate the output budget)
        if response.candidates:
            finish_reason = getattr(response.candidates[0], 'finish_reason', None)
            if finish_reason and 'MAX_TOKENS' in str(finish_reason):
                logger.error(f"❌ Truncated response for {paragraph.id}: finish_reason={finish_reason}")
                raise RuntimeError(f"Truncated response (MAX_TOKENS) for paragraph {paragraph.id}")

        result = json.loads(response_text)
        translated_segments = _sanitize_model_segments(result.get("segments", []))

        # 5. Validation
        if len(translated_segments) != len(paragraph.segments):
            logger.warning(
                f"⚠️ Segment count mismatch for {paragraph.id}: "
                f"Input {len(paragraph.segments)} vs Output {len(translated_segments)}"
            )

        return translated_segments

    # ─── v8: Context-Cached Chunked Translation ─────────────────────────

    @staticmethod
    def build_full_transcript_context(
        all_segments: List[Dict],
        entity_anchors: Dict = None,
        speaker_genders: Dict = None,
    ) -> str:
        """
        Build the full English transcript with timings for context caching.

        This is the key v8 insight: every chunk sees the ENTIRE transcript.
        At ~1,600 segments × ~30 tokens each ≈ 48K tokens — well under 5% of 1M.
        """
        lines = []
        lines.append("FULL ENGLISH TRANSCRIPT (for context — translate ONLY the segments requested below)")
        lines.append(f"Total segments: {len(all_segments)}")
        lines.append("=" * 60)

        for seg in all_segments:
            start_mm = int(seg["start"] // 60)
            start_ss = int(seg["start"] % 60)
            speaker = seg.get("speaker", "?")
            text = seg.get("text", "")
            lines.append(f"[{start_mm:02d}:{start_ss:02d}] ({speaker}) {text}")

        # Append entity anchors so every chunk has consistent name handling
        if entity_anchors:
            lines.append("")
            lines.append("ENTITY ANCHORS (translate these consistently throughout):")
            for src, tgt in entity_anchors.items():
                if src == tgt:
                    lines.append(f"  - {src} → KEEP AS-IS")
                else:
                    lines.append(f"  - {src} → {tgt}")

        # Append speaker gender info for Icelandic grammatical agreement
        if speaker_genders:
            lines.append("")
            lines.append("SPEAKER GENDERS (use correct grammatical forms):")
            for name, gender in speaker_genders.items():
                lines.append(f"  - {name}: {gender}")

        return "\n".join(lines)

    def create_context_cache(
        self,
        system_instruction: str,
        transcript_context: str,
        translation_brief: str = None,
        ttl_seconds: int = 3600,
        display_name: str = "omega-v8-cache",
    ):
        """
        Create a Gemini context cache with system instruction + full transcript.

        The cache is referenced by all chunk calls, giving 90% input discount.
        Returns the cache object, or None if creation fails.
        """
        # Build the cached content: transcript + optional brief
        cached_text = transcript_context
        if translation_brief:
            cached_text += "\n\n" + "=" * 60 + "\n"
            cached_text += "TRANSLATION BRIEF (Full program context — use to maintain consistency):\n"
            cached_text += translation_brief
            cached_text += "\n\nBRIEF USAGE RULES:\n"
            cached_text += "- Follow TERMINOLOGY COMMITMENTS exactly — these are binding translation decisions.\n"
            cached_text += "- Use CALLBACKS AND CROSS-REFERENCES to ensure backward references make sense.\n"
            cached_text += "- Match the TONE AND REGISTER guidance for this section of the program.\n"
            cached_text += "- Do NOT translate the brief itself. Use it for context only."

        try:
            cache = self.client.caches.create(
                model=self.model_name,
                config=types.CreateCachedContentConfig(
                    system_instruction=system_instruction,
                    contents=[
                        types.Content(
                            parts=[types.Part(text=cached_text)],
                            role="user",
                        ),
                        types.Content(
                            parts=[types.Part(text=(
                                "I have the full English transcript and translation brief. "
                                "Ready to translate chunks into the target language."
                            ))],
                            role="model",
                        ),
                    ],
                    ttl=f"{ttl_seconds}s",
                    display_name=display_name,
                ),
            )
            logger.info(f"📦 Context cache created: {cache.name}")
            cache_usage = getattr(cache, 'usage_metadata', None)
            if cache_usage:
                logger.info(f"   Cached tokens: {getattr(cache_usage, 'total_token_count', 'N/A')}")
            return cache
        except Exception as e:
            logger.error(f"❌ Context cache creation failed: {e}")
            return None

    def delete_context_cache(self, cache):
        """Delete a context cache to avoid lingering charges."""
        if not cache:
            return
        try:
            self.client.caches.delete(name=cache.name)
            logger.info(f"🗑️  Context cache deleted: {cache.name}")
        except Exception as e:
            logger.warning(f"⚠️ Could not delete cache: {e}")

    def translate_chunk_v8(
        self,
        chunk_segments: List[Dict],
        chunk_index: int,
        total_chunks: int,
        target_lang: str,
        cache=None,
        system_instruction: str = None,
        transcript_context: str = None,
        continuity_payload: list = None,
        glossary_terms: dict = None,
        creative_mode: bool = False,
    ) -> List[Dict]:
        """
        Translate a chunk of segments using the v8 architecture.

        If a cache is provided, references it for 90% input discount.
        If no cache, falls back to sending system instruction + transcript inline.

        Args:
            chunk_segments: List of segment dicts with id, start, end, text
            chunk_index: 0-based index of this chunk
            total_chunks: Total number of chunks in this job
            target_lang: Target language code (e.g., "is")
            cache: Gemini context cache object (from create_context_cache)
            system_instruction: Fallback system instruction if no cache
            transcript_context: Fallback full transcript if no cache
            continuity_payload: Last N translated segments from previous chunk
            glossary_terms: Optional dict of {English: Target} terms for in-prompt reinforcement
            creative_mode: If True, use creative prompt variant (legacy, kept for compatibility)

        Returns:
            List of translated segment dicts [{id, text}, ...]
        """
        lang_config = profiles.LANGUAGES.get(target_lang, profiles.LANGUAGES["is"])
        is_icelandic = target_lang.lower() == "is"
        ideal_cps = 12 if is_icelandic else 15
        max_cps = 15 if is_icelandic else 17

        # Build segment array with timing info + char budget for context
        seg_array = []
        for seg in chunk_segments:
            duration = seg["end"] - seg["start"]
            # Budget = duration × ideal_cps, capped at 84 (42 chars × 2 lines)
            budget = min(int(max(duration, 0.5) * ideal_cps), 84)
            # Use pre-segmenter's budget if available (more accurate)
            if seg.get("char_budget"):
                budget = seg["char_budget"]
            entry = {
                "id": str(seg["id"]),
                "start": seg["start"],
                "end": seg["end"],
                "duration": round(duration, 2),
                "text": seg["text"],
                "budget": budget,
            }
            seg_array.append(entry)

        # Continuity block
        continuity_block = ""
        if continuity_payload:
            continuity_block = f"""
CONTINUITY (Last segments from previous chunk — for flow only, do NOT re-translate):
{json.dumps(continuity_payload, ensure_ascii=False)}
"""

        if creative_mode:
            # ── CREATIVE PROMPT (Two-Pass Mode) ──
            # Pro focuses on translation quality. Flash handles mechanics later.
            prompt = f"""Translate chunk {chunk_index + 1}/{total_chunks} ({len(seg_array)} segments) into {lang_config['name']}.

You have the full English transcript above for context. Translate ONLY these segments.
{continuity_block}
TRANSLATION GOALS:
1. Natural, idiomatic {lang_config['name']} — as if originally spoken in {lang_config['name']}.
2. Match the speaker's register: passionate preaching, calm teaching, urgent news — translate the EMOTION.
3. Return ALL {len(seg_array)} segments. No blanks. No missing IDs.
4. Short interjections ("Amen", "Já", "Halelúja") stay as-is.
5. Key phrases stay within a single segment — never split "ÉG ER", "Heilagur Andi", or proper names.
6. Use {lang_config['bible']} for scripture references.
7. Keep translations concise — aim for broadcast brevity, but NEVER sacrifice natural flow for shortness.

Each segment has a `budget` (max characters). Aim to stay within budget — if natural {lang_config['name']} needs a little more, that's fine, but never double it.
Focus on producing the most natural, beautiful {lang_config['name']} possible while respecting screen width.

INPUT SEGMENTS:
{json.dumps(seg_array, ensure_ascii=False)}

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}"""

        else:
            # ── BROADCAST-QUALITY PROMPT (Gemini 3.1 Pro) ──
            # Translation quality + budget awareness. Each segment carries a
            # char budget so Pro knows the screen constraint. Pro self-corrects
            # violations, and the deterministic finalizer handles line breaks.

            glossary_line = ""
            if glossary_terms:
                pairs = [f'"{k}" → "{v}"' for k, v in glossary_terms.items()]
                glossary_line = f"\nGLOSSARY (use these exact terms consistently):\n" + "\n".join(f"  - {p}" for p in pairs) + "\n"

            prompt = f"""Translate chunk {chunk_index + 1}/{total_chunks} ({len(seg_array)} segments) into {lang_config['name']}.

You have the full English transcript above for context. Translate ONLY these segments.
{continuity_block}
YOUR ROLE: You are a senior broadcast localizer for a premium subtitle service.
Your translations will appear on screen for viewers who are reading in real time.
The quality bar is BBC / Netflix / Apple TV+.

STEP 1 — UNDERSTAND (use your internal reasoning):
- Read the English source in context of the full transcript above.
- Identify the speaker's emotion and register (passionate sermon, calm teaching, urgent news, casual conversation).
- Note any scripture references, theological terms, or proper nouns.
- Consider how a native {lang_config['name']} speaker would naturally express each idea.

STEP 2 — TRANSLATE (your output):
- Produce natural, publication-quality {lang_config['name']} for each segment.
- The translation should sound as if originally spoken in {lang_config['name']}.
- Aim for broadcast brevity — concise is better, but NEVER sacrifice grammar or natural flow to be short.
- Translate the speaker's EMOTION, not just the words.

RULES:
1. Return ALL {len(seg_array)} segments. No blanks. No missing IDs.
2. Natural {lang_config['name']} — never translationese. Avoid anglicisms and calques.
3. Short interjections ("Amen", "Já", "Halelúja") stay as-is.
4. Key phrases stay within a single segment — never split "ÉG ER", "Heilagur Andi", or proper names across segments.
5. Use {lang_config['bible']} for scripture references. If the speaker paraphrases, recall the official {lang_config['name']} verse.
6. Do NOT end a segment on a dangling preposition or conjunction. If the natural {lang_config['name']} phrasing requires it, restructure the sentence.
7. Each segment has a `budget` (max characters). Write like a professional subtitler who knows the screen width — aim to stay within budget. If natural {lang_config['name']} needs 10-15% more, that's fine. When space is tight, be a poet: find the shorter word that carries the same weight. Never double the budget.
{glossary_line}
WHAT NOT TO DO (common mistakes):
- ❌ Translating word-for-word from English syntax into {lang_config['name']} word order.
- ❌ Using passive voice where {lang_config['name']} naturally uses active or middle voice.
- ❌ Producing a translation that is grammatically correct but sounds like a textbook.
- ❌ Padding with filler words to fill screen time — shorter is always better if meaning is preserved.
- ✅ Instead: write how a skilled {lang_config['name']} broadcaster would actually say it on air.

Focus on producing the most natural, beautiful {lang_config['name']} possible while respecting the character budget.

INPUT SEGMENTS:
{json.dumps(seg_array, ensure_ascii=False)}

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}"""

        n_segs = len(seg_array)
        response_budget = max(4096, n_segs * 200)

        # Build API config — shared between cached and non-cached paths
        shared_config = dict(
            max_output_tokens=response_budget,
            response_mime_type="application/json",
            response_schema={
                "type": "OBJECT",
                "properties": {
                    "segments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "id": {"type": "STRING"},
                                "text": {"type": "STRING"},
                            },
                            "required": ["id", "text"],
                        },
                    },
                },
                "required": ["segments"],
            },
            temperature=0.25,
            safety_settings=[
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
            ],
        )

        # Gemini 3 Pro uses thinking_level instead of thinking_budget
        # But the google.genai SDK may still use thinking_budget for preview models
        # Use thinking_budget for now (proven in tests), switch to thinking_level when stable
        shared_config["thinking_config"] = types.ThinkingConfig(
            thinking_budget=self.thinking_budget,
        )

        try:
            if cache:
                # Cached path — reference the cache for 90% input discount
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        cached_content=cache.name,
                        **shared_config,
                    ),
                )
            else:
                # Non-cached fallback — send transcript + prompt inline
                if not transcript_context:
                    raise ValueError("Either cache or transcript_context must be provided")
                combined_prompt = f"{transcript_context}\n\n---\n\n{prompt}"
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=combined_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction or "",
                        **shared_config,
                    ),
                )
        except Exception as e:
            logger.error(f"❌ v8 chunk {chunk_index+1}/{total_chunks} translation failed: {e}")
            raise

        # Parse response
        response_text = None
        try:
            response_text = response.text
        except ValueError:
            pass

        if not response_text:
            finish_reason = None
            block_reason = None
            if response.candidates:
                finish_reason = getattr(response.candidates[0], 'finish_reason', None)
            if hasattr(response, 'prompt_feedback'):
                block_reason = getattr(response.prompt_feedback, 'block_reason', None)
            logger.error(
                f"❌ Empty response for chunk {chunk_index+1}/{total_chunks}: "
                f"finish_reason={finish_reason}, block_reason={block_reason}"
            )
            raise RuntimeError(
                f"Empty response for v8 chunk {chunk_index+1}/{total_chunks} "
                f"(finish_reason={finish_reason}, block_reason={block_reason})"
            )

        # Check for truncation
        if response.candidates:
            finish_reason = getattr(response.candidates[0], 'finish_reason', None)
            if finish_reason and 'MAX_TOKENS' in str(finish_reason):
                logger.error(f"❌ Truncated response for chunk {chunk_index+1}/{total_chunks}")
                raise RuntimeError(f"Truncated response (MAX_TOKENS) for v8 chunk {chunk_index+1}/{total_chunks}")

        result = json.loads(response_text)
        translated_segments = _sanitize_model_segments(result.get("segments", []))

        # Log token usage
        usage = getattr(response, 'usage_metadata', None)
        if usage:
            cached_tokens = getattr(usage, 'cached_content_token_count', 0)
            total_tokens = getattr(usage, 'total_token_count', 'N/A')
            logger.info(
                f"📊 Chunk {chunk_index+1}/{total_chunks}: "
                f"{len(translated_segments)}/{n_segs} segs, "
                f"tokens={total_tokens}, cached={cached_tokens}"
            )

        # Validate segment count
        if len(translated_segments) != n_segs:
            logger.warning(
                f"⚠️ Segment count mismatch for chunk {chunk_index+1}/{total_chunks}: "
                f"Input {n_segs} vs Output {len(translated_segments)}"
            )

        return translated_segments

    # ─── v9: Flash QA Pass (Two-Pass Architecture) ────────────────────

    def flash_qa_pass(
        self,
        translated_segments: List[Dict],
        target_lang: str,
        glossary_terms: Dict = None,
        flash_model: str = None,
    ) -> List[Dict]:
        """
        Pass 2: Run Gemini Flash over Pro's translations to enforce mechanical constraints.

        This is the QA pass in the two-pass architecture:
        - Pass 1 (Pro): Creative translation — natural, idiomatic target language
        - Pass 2 (Flash): Mechanical enforcement — char limits, CPS, artifacts, glossary

        Flash sees both the English source and Pro's Icelandic translation for each segment.
        It enforces constraints WITHOUT re-translating or changing register.

        Args:
            translated_segments: List of dicts with at minimum:
                {id, text (target lang), source_text (English), start, end}
            target_lang: Target language code
            glossary_terms: Optional glossary for consistency checking
            flash_model: Override model name (default: config.MODEL_FLASH_QA)

        Returns:
            List of QA'd segment dicts [{id, text}, ...]
        """
        model = flash_model or getattr(config, 'MODEL_FLASH_QA', 'gemini-3-flash-preview')
        lang_config = profiles.LANGUAGES.get(target_lang, profiles.LANGUAGES["is"])
        is_icelandic = target_lang.lower() == "is"
        max_cps = 15 if is_icelandic else 17

        # Get Flash QA system instruction
        system_instruction = profiles.get_flash_qa_instruction(target_lang)

        # Build input payload with char budgets
        qa_input = []
        for seg in translated_segments:
            duration = seg.get("end", 0) - seg.get("start", 0)
            max_chars = min(int(duration * max_cps), MAX_CHARS_TOTAL)
            qa_input.append({
                "id": str(seg.get("id", "")),
                "source": seg.get("source_text", ""),
                "translation": seg.get("text", ""),
                "duration": round(duration, 2),
                "max_chars": max_chars,
            })

        # Build glossary line
        glossary_line = ""
        if glossary_terms:
            pairs = [f'"{k}"→"{v}"' for k, v in glossary_terms.items()]
            glossary_line = f"\nGLOSSARY CHECK: Verify these terms are used correctly: {', '.join(pairs)}"

        # Chunk QA input (Flash can handle larger chunks than Pro translation)
        # 300 segments per QA chunk is safe — Flash is just checking, not generating from scratch
        QA_CHUNK_SIZE = getattr(config, 'OMEGA_FLASH_QA_CHUNK_SIZE', 300)
        all_qa_results = []

        for chunk_start in range(0, len(qa_input), QA_CHUNK_SIZE):
            chunk = qa_input[chunk_start:chunk_start + QA_CHUNK_SIZE]
            chunk_num = chunk_start // QA_CHUNK_SIZE + 1
            total_qa_chunks = (len(qa_input) + QA_CHUNK_SIZE - 1) // QA_CHUNK_SIZE

            prompt = f"""QA CHECK: {len(chunk)} subtitles (chunk {chunk_num}/{total_qa_chunks}).

For each segment below, check if the translation meets ALL broadcast constraints.
If it passes all rules: return it unchanged.
If it violates any rule: fix it minimally — change as few words as possible.
{glossary_line}

INPUT (each segment has: id, source English, translated {lang_config['name']}, duration, max_chars):
{json.dumps(chunk, ensure_ascii=False)}

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}
Only return the id and the final text (fixed or unchanged)."""

            logger.info(
                f"🔍 Flash QA chunk {chunk_num}/{total_qa_chunks} "
                f"({len(chunk)} segments, model={model})"
            )

            # Determine thinking level for Flash
            thinking_level = getattr(config, 'OMEGA_FLASH_QA_THINKING_LEVEL', 'LOW')

            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=4096,
                        ),
                        max_output_tokens=max(4096, len(chunk) * 150),
                        response_mime_type="application/json",
                        response_schema={
                            "type": "OBJECT",
                            "properties": {
                                "segments": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "id": {"type": "STRING"},
                                            "text": {"type": "STRING"},
                                        },
                                        "required": ["id", "text"],
                                    },
                                },
                            },
                            "required": ["segments"],
                        },
                        temperature=0.1,  # Low temp — QA should be deterministic
                        safety_settings=[
                            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                        ],
                    ),
                )
            except Exception as e:
                logger.error(f"❌ Flash QA chunk {chunk_num} failed: {e}")
                # On QA failure, return the original translations unchanged
                logger.warning("⚠️ Falling back to unpolished translations for this chunk")
                for seg in chunk:
                    all_qa_results.append({"id": seg["id"], "text": seg["translation"]})
                continue

            # Parse response
            response_text = None
            try:
                response_text = response.text
            except ValueError:
                pass

            if not response_text:
                logger.warning(f"⚠️ Flash QA chunk {chunk_num} returned empty — using originals")
                for seg in chunk:
                    all_qa_results.append({"id": seg["id"], "text": seg["translation"]})
                continue

            result = json.loads(response_text)
            qa_segments = _sanitize_model_segments(result.get("segments", []))

            # Log token usage
            usage = getattr(response, 'usage_metadata', None)
            if usage:
                total_tokens = getattr(usage, 'total_token_count', 'N/A')
                logger.info(f"   📊 Flash QA chunk {chunk_num}: {len(qa_segments)} segs, tokens={total_tokens}")

            # Count changes
            changes = 0
            qa_map = {str(s["id"]): s["text"] for s in qa_segments}
            for seg in chunk:
                qa_text = qa_map.get(seg["id"], seg["translation"])
                if qa_text != seg["translation"]:
                    changes += 1
                all_qa_results.append({"id": seg["id"], "text": qa_text})

            if changes:
                logger.info(f"   ✏️ Flash QA chunk {chunk_num}: {changes}/{len(chunk)} segments modified")
            else:
                logger.info(f"   ✅ Flash QA chunk {chunk_num}: all segments passed (0 changes)")

        logger.info(f"🔍 Flash QA complete: {len(all_qa_results)} segments processed")
        return all_qa_results

    def _get_system_instruction(self, target_lang: str, program_profile: str, extra_terms: dict = None) -> str:
        """
        Returns the full system instruction from profiles.py.

        This includes: language physics (grammar, theology, anglicism avoidance),
        persona soul (tone, glossary), music rules, ASR cleanup, capitalization rules.
        """
        return profiles.get_system_instruction(target_lang, program_profile, extra_terms=extra_terms)

    def _build_user_prompt(
        self,
        paragraph: TranslationParagraph,
        target_lang: str,
        program_profile: str,
        entity_anchors: dict = None,
        continuity_payload: list = None,
        speaker_gender: str = None,
        visual_context: str = None,
        doc_brief: str = None,
        sliding_context: str = None,
    ) -> str:
        """
        Builds the per-paragraph user prompt with segment data, CPS constraints,
        omission hierarchy, entity anchors, continuity, speaker gender, visual
        context, and document brief.

        Context blocks are ported from the legacy worker's proven prompt structure
        (omega_cloud_worker_legacy.py lines 930-985) to maintain broadcast quality.
        """
        lang_config = profiles.LANGUAGES.get(target_lang, profiles.LANGUAGES["is"])

        # CPS limits per language
        is_icelandic = target_lang.lower() == "is"
        ideal_cps = 12 if is_icelandic else 15
        max_cps = 15 if is_icelandic else 17

        # Omission hierarchy
        omission_instructions = """HIERARCHY OF OMISSION (When you must condense):
1. DELETE FIRST (67% of cuts): Fillers ("um", "well"), Phatic phrases ("you know"), Redundant markers.
2. DELETE SECOND (32% of cuts): Non-essential details, adjectives, background chatter.
3. NEVER DELETE (0%): Theological terms, Name of God, Scripture references, specific Names/Places."""

        # Build input segments with timing and char budgets
        input_payload = []
        for seg in paragraph.segments:
            duration = seg["end"] - seg["start"]
            max_chars = min(int(duration * max_cps), MAX_CHARS_TOTAL)
            input_payload.append({
                "id": str(seg["id"]),
                "start": seg["start"],
                "end": seg["end"],
                "duration": round(duration, 2),
                "max_chars": max_chars,
                "text": seg["text"],
            })

        # --- Context Blocks (ported from legacy worker) ---

        # Entity anchors
        anchors_block = ""
        if entity_anchors:
            anchors_lines = []
            for source_text, target_text in entity_anchors.items():
                if source_text == target_text:
                    anchors_lines.append(f"- {source_text} → KEEP AS-IS")
                else:
                    anchors_lines.append(f"- {source_text} → {target_text}")
            if anchors_lines:
                anchors_block = f"""
ENTITY ANCHORS (translate these consistently throughout):
{chr(10).join(anchors_lines)}
"""

        # Speaker gender (critical for Icelandic grammatical agreement)
        gender_block = ""
        if speaker_gender:
            gender_block = f"""
SPEAKER: {paragraph.speaker} (Gender: {speaker_gender})
- Use {speaker_gender} grammatical forms for adjectives, past participles, and pronouns.
"""
        elif paragraph.speaker and paragraph.speaker != "Unknown":
            gender_block = f"\nSPEAKER: {paragraph.speaker}\n"

        # Document brief / Translation Brief (episode/program context)
        brief_block = ""
        if doc_brief:
            # Detect rich Translation Brief (Phase 0) vs simple doc_brief
            is_rich_brief = any(marker in doc_brief for marker in [
                "SERMON STRUCTURE:", "KEY THEMES", "TERMINOLOGY COMMITMENTS",
            ])
            if is_rich_brief:
                brief_block = f"""
TRANSLATION BRIEF (Full program context — use to maintain consistency):
{doc_brief}

BRIEF USAGE RULES:
- Follow TERMINOLOGY COMMITMENTS exactly — these are binding translation decisions.
- Use CALLBACKS AND CROSS-REFERENCES to ensure backward references make sense.
- Match the TONE AND REGISTER guidance for this section of the program.
- Do NOT translate the brief itself. Use it for context only.
"""
            else:
                brief_block = f"""
DOCUMENT BRIEF (Topic context — do not translate this, use for understanding):
{doc_brief}
"""

        # Sliding context window (surrounding English source text)
        sliding_block = ""
        if sliding_context:
            sliding_block = f"""
{sliding_context}
"""

        # Visual context (scene description / OCR for deixis resolution)
        visual_block = ""
        if visual_context and visual_context not in ("No visual data", "", "None"):
            visual_block = f"""
VISUAL CONTEXT (What is on screen — use to resolve "this/that/here"):
{visual_context}
"""

        # Continuity (previous translated segments for cross-paragraph flow)
        continuity_block = ""
        if continuity_payload:
            continuity_block = f"""
CONTINUITY (Previous translated segments — for flow only, do NOT re-translate):
{json.dumps(continuity_payload, ensure_ascii=False)}
"""

        prompt = f"""TASK: Translate the following segments into {lang_config['name']}.
The viewer reads at {ideal_cps} chars/second. MAX CPS: {max_cps} (Hard Limit).

STYLE GUIDELINES:
- Natural Flow: Translate meaning, not words. Avoid "Translationese". Produce natural {lang_config['name']} a native speaker would use.
- Formatting: Do NOT use ALL CAPS. Use standard sentence case. Preserve acronyms.
- Line Limits: Maximum 42 characters per line, maximum 2 lines per subtitle.

{omission_instructions}

CONTENT PRESERVATION (CRITICAL):
- NEVER omit, censor, or filter any segment. This is professional broadcast content.
- Translate ALL content faithfully, including references to violence, politics, or sensitive topics.
- NEVER drop core theological meaning: God, Jesus Christ, Holy Spirit, salvation, grace, sin, etc.
- Do not leave incomplete sentence fragments that lose the final theological point.
- Every input segment MUST have a translation. NEVER return empty text.

INPUT CONTEXT:
Speaker: {paragraph.speaker}
Context: {', '.join(paragraph.context_tags) if paragraph.context_tags else 'None'}
{gender_block}{brief_block}{sliding_block}{visual_block}{anchors_block}{continuity_block}
INPUT SEGMENTS (Translate strictly 1:1, preserving IDs):
{json.dumps(input_payload, ensure_ascii=False)}

CONSTRAINTS:
1. `text` length MUST be <= `max_chars` for each segment.
2. If the translation is too long, APPLY THE OMISSION HIERARCHY to condense it.
3. Use {lang_config['bible']} for scripture references.

FINAL SELF-CHECK BEFORE OUTPUT:
1. Every input ID appears exactly once in output.
2. No segment is blank.
3. Every translation is <= its max_chars limit.
4. Translation is complete (not abruptly cut off).
5. No segment ends with a dangling preposition (á, í, um, til, við, frá, með).
6. Key phrases must stay within a single segment — never split theological titles like "ÉG ER" (I AM), "Heilagur Andi" (Holy Spirit), or proper names across segment boundaries.

Return JSON: {{"segments": [{{"id": "...", "text": "..."}}]}}"""

        return prompt

    # Back-compat shim for tests/tools that still call the old private method name.
    def _build_prompt(self, paragraph: TranslationParagraph, target_lang: str, program_profile: str) -> str:
        return self._build_user_prompt(paragraph, target_lang, program_profile)
