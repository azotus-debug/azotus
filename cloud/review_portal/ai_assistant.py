"""
AI Assistant for Subtitle Review
Provides context-aware translation quality analysis and suggestions using Claude API.
"""

import os
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from anthropic import Anthropic

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    """Subtitle segment"""
    start: str
    end: str
    text: str
    source_text: Optional[str] = None


@dataclass
class Suggestion:
    """AI-generated translation suggestion"""
    original: str
    suggestion: str
    reasoning: str
    confidence: float  # 0.0 - 1.0


@dataclass
class ValidationIssue:
    """Quality validation issue"""
    type: str  # 'cps', 'length', 'timing', 'punctuation', 'consistency'
    severity: str  # 'error', 'warning', 'info'
    message: str
    segment_index: Optional[int] = None


@dataclass
class QualityReport:
    """Overall translation quality analysis"""
    score: float  # 0-100
    accuracy_score: float
    fluency_score: float
    consistency_score: float
    issues: List[ValidationIssue]
    summary: str


class ReviewAIAssistant:
    """Context-aware AI assistant for subtitle review"""

    def __init__(self):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set - AI features disabled")
            self.client = None
        else:
            self.client = Anthropic(api_key=api_key)
            logger.info("AI Assistant initialized with Claude API")

    def is_available(self) -> bool:
        """Check if AI features are available"""
        return self.client is not None

    def suggest_improvement(
        self,
        source_text: str,
        translated_text: str,
        target_language: str = "is",
        context: Optional[Dict] = None
    ) -> Optional[Suggestion]:
        """
        Generate AI-powered translation suggestion

        Args:
            source_text: Original English text
            translated_text: Current translation
            target_language: Target language code (default: 'is' for Icelandic)
            context: Additional context (genre, style, previous segments, etc.)

        Returns:
            Suggestion object or None if AI unavailable
        """
        if not self.is_available():
            return None

        try:
            # Build context string
            context_str = ""
            if context:
                if context.get("genre"):
                    context_str += f"Genre: {context['genre']}\n"
                if context.get("style"):
                    context_str += f"Style: {context['style']}\n"
                if context.get("previous_segments"):
                    context_str += f"Previous context: {context['previous_segments']}\n"

            # Language names for clarity
            lang_names = {
                "is": "Icelandic",
                "no": "Norwegian",
                "sv": "Swedish",
                "da": "Danish",
                "nl": "Dutch",
                "de": "German",
                "es": "Spanish",
                "fr": "French",
                "it": "Italian",
                "pt": "Portuguese"
            }
            target_lang_name = lang_names.get(target_language, target_language)

            prompt = f"""You are an expert subtitle translator and quality reviewer. You are reviewing translations from English to {target_lang_name}.

{context_str}

Original English subtitle:
{source_text}

Current {target_lang_name} translation:
{translated_text}

Analyze the translation and provide an improved version if needed.

CRITICAL QUALITY CHECKS (learned from real review feedback):

1. **Reading Speed**: The subtitle MUST be readable at normal pace
   - Max 25 characters per second (CPS) for broadcast
   - Short subtitles (under 1 second) need even fewer characters

2. **Name Consistency**: Check for ASR transcription errors
   - Names may have been mis-transcribed (e.g., "Curci" becoming "Koichi" or "Korci")
   - Proper names should be consistent throughout

3. **Duplicate Titles**: Watch for "Dr. Dr." or "Pastor Pastor" errors

4. **Natural Language**: Translation should sound natural in {target_lang_name}
   - Avoid overly literal translations
   - Use natural word order and phrasing for {target_lang_name}
   - Match formality level to context

5. **Conciseness**: Subtitles must be brief
   - If too long, simplify without losing meaning
   - Prefer short common words over long formal ones

6. **Technical**: Max 42 characters per line, max 2 lines

Respond in this exact format:
SUGGESTION: [your suggested translation, or the original if it's already good]
REASONING: [brief explanation of what you improved or why it's good as-is]
CONFIDENCE: [0.0-1.0 - higher if you made significant improvements or are certain it's correct]"""

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                temperature=0.3,  # Lower temperature for more consistent suggestions
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Parse response
            content = response.content[0].text
            suggestion_text = self._extract_field(content, "SUGGESTION")
            reasoning = self._extract_field(content, "REASONING")
            confidence_str = self._extract_field(content, "CONFIDENCE")

            try:
                confidence = float(confidence_str)
            except (ValueError, TypeError):
                confidence = 0.7  # Default if parsing fails

            return Suggestion(
                original=translated_text,
                suggestion=suggestion_text,
                reasoning=reasoning,
                confidence=confidence
            )

        except Exception as e:
            logger.error(f"AI suggestion failed: {e}")
            return None

    def analyze_translation_quality(
        self,
        source_segments: List[Segment],
        translated_segments: List[Segment],
        target_language: str = "is"
    ) -> Optional[QualityReport]:
        """
        Analyze overall translation quality for an entire job

        Args:
            source_segments: Original English segments
            translated_segments: Translated segments
            target_language: Target language code

        Returns:
            QualityReport or None if AI unavailable
        """
        if not self.is_available():
            return None

        try:
            # Sample segments for analysis (analyze up to 20 random segments)
            import random
            sample_size = min(20, len(source_segments))
            indices = random.sample(range(len(source_segments)), sample_size)

            samples = []
            for i in indices:
                if i < len(translated_segments):
                    samples.append({
                        "source": source_segments[i].source_text or source_segments[i].text,
                        "translation": translated_segments[i].text
                    })

            # Build analysis prompt
            lang_names = {
                "is": "Icelandic",
                "no": "Norwegian",
                "sv": "Swedish",
                "da": "Danish",
                "nl": "Dutch",
                "de": "German",
                "es": "Spanish",
                "fr": "French",
                "it": "Italian",
                "pt": "Portuguese"
            }
            target_lang_name = lang_names.get(target_language, target_language)

            samples_text = "\n\n".join([
                f"Segment {i+1}:\nEN: {s['source']}\n{target_language.upper()}: {s['translation']}"
                for i, s in enumerate(samples)
            ])

            prompt = f"""Analyze this sample of subtitle translations from English to {target_lang_name}.

{samples_text}

Provide a comprehensive quality assessment based on broadcast standards.

SCORING CRITERIA (from real review feedback):

1. ACCURACY (0-100): How well meaning is preserved
   - Check for mistranslations or meaning shifts
   - Verify proper names are correct (watch for ASR errors like "Curci" → "Koichi")

2. FLUENCY (0-100): How natural the {target_lang_name} sounds
   - Should read like native {target_lang_name}, not translated English
   - Natural word order and phrasing
   - Appropriate formality level

3. CONSISTENCY (0-100): Terminology and style consistency
   - Same terms translated the same way throughout
   - Name spellings consistent (no variations like Curci/Korci)
   - No duplicate titles ("Dr. Dr." errors)

4. TECHNICAL (implicit in overall): SRT formatting quality
   - Reading speed should be ≤25 CPS (characters per second)
   - Lines should be ≤42 characters
   - Maximum 2 lines per subtitle

Common issues to watch for:
- Overly literal translations that don't sound natural
- Names mis-transcribed by ASR
- Duplicate titles (Dr. Dr., Pastor Pastor)
- Text too dense for subtitle duration

Respond in this exact format:
ACCURACY: [0-100]
FLUENCY: [0-100]
CONSISTENCY: [0-100]
OVERALL: [0-100]
SUMMARY: [2-3 sentences: what's working well, what specific issues need attention]"""

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                temperature=0.2,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            content = response.content[0].text

            # Parse scores
            accuracy = float(self._extract_field(content, "ACCURACY"))
            fluency = float(self._extract_field(content, "FLUENCY"))
            consistency = float(self._extract_field(content, "CONSISTENCY"))
            overall = float(self._extract_field(content, "OVERALL"))
            summary = self._extract_field(content, "SUMMARY")

            return QualityReport(
                score=overall,
                accuracy_score=accuracy,
                fluency_score=fluency,
                consistency_score=consistency,
                issues=[],  # Will be populated by validators
                summary=summary
            )

        except Exception as e:
            logger.error(f"Quality analysis failed: {e}")
            return None

    def validate_segment(
        self,
        segment: Segment,
        rules: Optional[Dict] = None
    ) -> List[ValidationIssue]:
        """
        Validate a single segment for common subtitle issues

        Args:
            segment: Segment to validate
            rules: Validation rules (optional)

        Returns:
            List of validation issues found
        """
        issues = []

        # Default rules
        if rules is None:
            rules = {
                'max_cps': 20,  # Characters per second
                'max_line_length': 42,
                'max_lines': 2,
                'min_duration': 1.0,
                'max_duration': 7.0
            }

        # Parse timing
        try:
            start_parts = segment.start.replace("0:", "").split(":")
            end_parts = segment.end.replace("0:", "").split(":")

            start_sec = float(start_parts[-1]) + (int(start_parts[-2]) * 60 if len(start_parts) > 1 else 0)
            end_sec = float(end_parts[-1]) + (int(end_parts[-2]) * 60 if len(end_parts) > 1 else 0)

            duration = end_sec - start_sec

            # Check duration
            if duration < rules['min_duration']:
                issues.append(ValidationIssue(
                    type='timing',
                    severity='warning',
                    message=f'Segment too short ({duration:.1f}s < {rules["min_duration"]}s)'
                ))
            elif duration > rules['max_duration']:
                issues.append(ValidationIssue(
                    type='timing',
                    severity='warning',
                    message=f'Segment too long ({duration:.1f}s > {rules["max_duration"]}s)'
                ))

            # Check CPS (characters per second)
            char_count = len(segment.text)
            cps = char_count / duration if duration > 0 else 0

            if cps > rules['max_cps']:
                issues.append(ValidationIssue(
                    type='cps',
                    severity='error',
                    message=f'Reading speed too fast ({cps:.1f} CPS > {rules["max_cps"]} CPS)'
                ))

        except Exception as e:
            logger.debug(f"Timing parse error: {e}")

        # Check line count and length
        lines = segment.text.split('\n')
        if len(lines) > rules['max_lines']:
            issues.append(ValidationIssue(
                type='length',
                severity='warning',
                message=f'Too many lines ({len(lines)} > {rules["max_lines"]})'
            ))

        for i, line in enumerate(lines):
            if len(line) > rules['max_line_length']:
                issues.append(ValidationIssue(
                    type='length',
                    severity='warning',
                    message=f'Line {i+1} too long ({len(line)} > {rules["max_line_length"]} chars)'
                ))

        # Check for basic punctuation issues
        text = segment.text.strip()
        if text and not text[-1] in '.!?…':
            # Only flag if it's clearly a sentence (starts with capital, has multiple words)
            if text[0].isupper() and len(text.split()) > 3:
                issues.append(ValidationIssue(
                    type='punctuation',
                    severity='info',
                    message='Missing ending punctuation'
                ))

        return issues

    def _extract_field(self, text: str, field_name: str) -> str:
        """Extract a field value from AI response"""
        try:
            # Find field line
            for line in text.split('\n'):
                if line.strip().startswith(f"{field_name}:"):
                    return line.split(':', 1)[1].strip()
            return ""
        except Exception:
            return ""


# Singleton instance
_assistant = None

def get_ai_assistant() -> ReviewAIAssistant:
    """Get or create AI assistant singleton"""
    global _assistant
    if _assistant is None:
        _assistant = ReviewAIAssistant()
    return _assistant
