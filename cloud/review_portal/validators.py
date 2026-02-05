"""
Subtitle Quality Validators
Comprehensive validation rules for subtitle quality control.

Incorporates learnings from real translation reviews:
- CPS threshold: 20 max for broadcast (Netflix: 17, BBC: 20)
- Name consistency: Detect ASR-induced name variations (Curci/Koichi/Korci)
- Duplicate titles: Detect "Dr. Dr." patterns
- Short-but-dense: Flag subtitles with OK duration but excessive text
"""

import logging
import re
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass
from collections import Counter
from difflib import SequenceMatcher

from subtitle_standards import (
    MAX_CHARS_PER_LINE,
    MAX_LINES,
    MIN_DURATION,
    MAX_DURATION,
    GAP_SECONDS,
    IDEAL_CPS,
    TIGHT_CPS,
    MAX_CPS,
)
logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """Quality validation issue"""
    type: str  # 'cps', 'length', 'timing', 'punctuation', 'consistency', 'format'
    severity: str  # 'error', 'warning', 'info'
    message: str
    segment_index: Optional[int] = None
    suggestion: Optional[str] = None


class SubtitleValidator:
    """Real-time subtitle quality validation"""

    # Broadcast-compatible subtitle standards (BBC/Netflix)
    # Updated based on real translation review feedback
    DEFAULT_RULES = {
        'max_cps': MAX_CPS,  # Characters per second - broadcast ceiling
        'warning_cps': TIGHT_CPS,  # Warn above this threshold
        'max_line_length': MAX_CHARS_PER_LINE,  # Characters per line
        'max_lines': MAX_LINES,  # Lines per subtitle
        'min_duration': MIN_DURATION,  # Minimum display time
        'max_duration': MAX_DURATION,  # Maximum display time
        'min_gap': GAP_SECONDS,  # Minimum gap between subtitles (~2 frames)
        'allow_all_caps': False,  # Disallow ALL CAPS (except acronyms)
        # Name consistency settings
        'name_similarity_threshold': 0.7,  # Fuzzy match threshold for names
    }

    def __init__(self, rules: Optional[Dict] = None):
        """
        Initialize validator with custom rules

        Args:
            rules: Optional dict of validation rules (merges with defaults)
        """
        self.rules = self.DEFAULT_RULES.copy()
        if rules:
            self.rules.update(rules)

    def validate_segment(
        self,
        segment: Dict,
        segment_index: Optional[int] = None
    ) -> List[ValidationIssue]:
        """
        Validate a single subtitle segment

        Args:
            segment: Segment dict with 'start', 'end', 'text'
            segment_index: Optional index for issue reporting

        Returns:
            List of validation issues found
        """
        issues = []

        text = segment.get('text', '')
        if not text or not text.strip():
            issues.append(ValidationIssue(
                type='format',
                severity='error',
                message='Empty subtitle text',
                segment_index=segment_index
            ))
            return issues

        # Parse timing
        try:
            start_sec = self._parse_timestamp(segment.get('start', '0:00:00.000'))
            end_sec = self._parse_timestamp(segment.get('end', '0:00:00.000'))
            duration = end_sec - start_sec

            # Validate duration
            if duration < self.rules['min_duration']:
                issues.append(ValidationIssue(
                    type='timing',
                    severity='error',
                    message=f'Subtitle too short: {duration:.2f}s (min: {self.rules["min_duration"]}s)',
                    segment_index=segment_index
                ))
            elif duration > self.rules['max_duration']:
                issues.append(ValidationIssue(
                    type='timing',
                    severity='warning',
                    message=f'Subtitle too long: {duration:.1f}s (max: {self.rules["max_duration"]}s)',
                    segment_index=segment_index
                ))

            # Validate CPS (characters per second)
            # Based on real review feedback: 35 CPS in 0.7s is unreadable
            char_count = len(text)
            cps = char_count / duration if duration > 0 else 0

            if cps > self.rules['max_cps']:
                # Critical: Above broadcast standard (20 CPS)
                issues.append(ValidationIssue(
                    type='cps',
                    severity='error',
                    message=f'Reading speed too fast: {cps:.1f} CPS (max: {self.rules["max_cps"]} CPS)',
                    segment_index=segment_index,
                    suggestion=f'Reduce text by ~{int(char_count - (self.rules["max_cps"] * duration))} chars OR extend duration'
                ))
            elif cps > self.rules.get('warning_cps', 20):
                # Warning: Above comfort zone but technically OK
                issues.append(ValidationIssue(
                    type='cps',
                    severity='warning',
                    message=f'Reading speed high: {cps:.1f} CPS (recommended: ≤{self.rules.get("warning_cps", 20)} CPS)',
                    segment_index=segment_index,
                    suggestion='Consider simplifying text for easier reading'
                ))

            # Special case: Short duration with dense text (learned from Dutch V3 review)
            # e.g., 0.7s with 25 chars = 35 CPS - technically passes min_duration but is unreadable
            if duration < MIN_DURATION and char_count > 20:
                effective_cps = char_count / duration if duration > 0 else 999
                max_cps = self.rules['max_cps']
                if effective_cps > max_cps:
                    issues.append(ValidationIssue(
                        type='cps',
                        severity='error',
                        message=f'Short subtitle ({duration:.2f}s) has too much text ({char_count} chars = {effective_cps:.0f} CPS)',
                        segment_index=segment_index,
                        suggestion=f'Either extend to {char_count/max_cps:.1f}s minimum OR reduce text to {int(max_cps*duration)} chars'
                    ))

        except Exception as e:
            logger.debug(f"Timing validation error for segment {segment_index}: {e}")
            issues.append(ValidationIssue(
                type='format',
                severity='error',
                message=f'Invalid timestamp format: {e}',
                segment_index=segment_index
            ))

        # Validate line count and length
        lines = text.split('\n')
        if len(lines) > self.rules['max_lines']:
            issues.append(ValidationIssue(
                type='length',
                severity='error',
                message=f'Too many lines: {len(lines)} (max: {self.rules["max_lines"]})',
                segment_index=segment_index,
                suggestion=f'Split into {len(lines)} separate subtitles'
            ))

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if len(line_stripped) > self.rules['max_line_length']:
                issues.append(ValidationIssue(
                    type='length',
                    severity='error',
                    message=f'Line {i+1} too long: {len(line_stripped)} chars (max: {self.rules["max_line_length"]})',
                    segment_index=segment_index,
                    suggestion='Break into two lines or simplify text'
                ))

            # Check for ALL CAPS (excluding acronyms)
            if not self.rules['allow_all_caps'] and line_stripped.isupper() and len(line_stripped.split()) > 1:
                issues.append(ValidationIssue(
                    type='format',
                    severity='warning',
                    message=f'Line {i+1} uses ALL CAPS (not recommended)',
                    segment_index=segment_index,
                    suggestion='Use sentence case for better readability'
                ))

        # Validate punctuation
        text_stripped = text.strip()

        # Check for missing ending punctuation (if it looks like a complete sentence)
        if text_stripped and len(text_stripped.split()) > 3:
            if text_stripped[0].isupper() and text_stripped[-1] not in '.!?…':
                issues.append(ValidationIssue(
                    type='punctuation',
                    severity='info',
                    message='Missing ending punctuation',
                    segment_index=segment_index
                ))

        # Check for double spaces
        if '  ' in text:
            issues.append(ValidationIssue(
                type='format',
                severity='warning',
                message='Contains double spaces',
                segment_index=segment_index
            ))

        # Check for leading/trailing whitespace
        if text != text.strip():
            issues.append(ValidationIssue(
                type='format',
                severity='warning',
                message='Leading or trailing whitespace',
                segment_index=segment_index
            ))

        return issues

    def validate_consistency(
        self,
        segments: List[Dict]
    ) -> List[ValidationIssue]:
        """
        Validate terminology, names, and style consistency across all segments.

        Includes learnings from real translation reviews:
        - Name variations from ASR errors (Curci/Koichi/Korci)
        - Duplicate titles (Dr. Dr.)
        - Capitalization inconsistencies

        Args:
            segments: List of all segments

        Returns:
            List of consistency issues found
        """
        issues = []

        # Track capitalization patterns for common terms
        term_capitalizations = {}

        # Track all proper names for consistency checking
        all_names: List[Tuple[str, int]] = []  # (name, segment_index)

        for i, segment in enumerate(segments):
            text = segment.get('text', '')

            # Check for duplicate titles (learned from Dutch V3: "Dr. Dr. Curci")
            duplicate_title_issues = self._check_duplicate_titles(text, i)
            issues.extend(duplicate_title_issues)

            # Extract proper names for later consistency check
            names = self._extract_proper_names(text)
            for name in names:
                all_names.append((name, i))

            # Check capitalization consistency
            words = text.split()
            for word in words:
                word_lower = word.lower()
                if word_lower in term_capitalizations:
                    if word != term_capitalizations[word_lower]:
                        issues.append(ValidationIssue(
                            type='consistency',
                            severity='info',
                            message=f'Inconsistent capitalization: "{word}" vs "{term_capitalizations[word_lower]}"',
                            segment_index=i
                        ))
                else:
                    term_capitalizations[word_lower] = word

        # Check for name variations across all segments
        # This catches ASR errors like Curci/Koichi/Korci
        name_issues = self._check_name_consistency(all_names)
        issues.extend(name_issues)

        return issues

    def _check_duplicate_titles(self, text: str, segment_index: int) -> List[ValidationIssue]:
        """
        Check for duplicate titles like 'Dr. Dr.' or 'Pastor Pastor'.
        Common error from ASR or translation pipeline.
        """
        issues = []

        # Pattern: Title followed by same title
        title_pattern = r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Pastor|Rev\.|Prof\.)\s+\1'
        matches = re.findall(title_pattern, text)

        for title in matches:
            issues.append(ValidationIssue(
                type='duplicate',
                severity='error',
                message=f'Duplicate title detected: "{title} {title}"',
                segment_index=segment_index,
                suggestion=f'Remove duplicate - should be just "{title}"'
            ))

        return issues

    def _extract_proper_names(self, text: str) -> List[str]:
        """
        Extract potential proper names from text.
        Used for cross-segment consistency checking.
        """
        names = []

        # Pattern: Title followed by name(s)
        title_pattern = r'\b(Dr\.|Mr\.|Mrs\.|Ms\.|Pastor|Rev\.|Prof\.)\s+([A-Z][a-zëïéèüáàâäíìîïóòôöúùûü]+(?:\s+[A-Z][a-zëïéèüáàâäíìîïóòôöúùûü]+)*)'
        for match in re.finditer(title_pattern, text):
            full_name = f"{match.group(1)} {match.group(2)}"
            names.append(full_name)
            # Also add just the name without title
            names.append(match.group(2))

        # Pattern: Multi-word capitalized sequence (likely a name)
        name_pattern = r'\b([A-Z][a-zëïéèüáàâäíìîïóòôöúùûü]+(?:\s+[A-Z][a-zëïéèüáàâäíìîïóòôöúùûü]+)+)\b'
        skip_phrases = {
            'The Lord', 'Holy Spirit', 'New Testament', 'Old Testament',
            'United States', 'New York', 'Los Angeles', 'San Francisco',
            'De Heer', 'Heilige Geest', 'Nieuwe Testament', 'Oude Testament',
            'Jesus Christ', 'Jezus Christus'
        }

        for match in re.finditer(name_pattern, text):
            name = match.group(1)
            if name not in skip_phrases and name not in names:
                names.append(name)

        return names

    def _check_name_consistency(self, all_names: List[Tuple[str, int]]) -> List[ValidationIssue]:
        """
        Check for name variations that might be ASR errors.
        Uses fuzzy matching to detect similar names with different spellings.

        Example: "Curci", "Koichi", "Korci" are likely the same person
        """
        issues = []

        if len(all_names) < 2:
            return issues

        # Count name occurrences
        name_counts = Counter(name for name, _ in all_names)
        unique_names = list(name_counts.keys())

        # Find similar names
        threshold = self.rules.get('name_similarity_threshold', 0.7)
        reported_pairs = set()

        for i, name1 in enumerate(unique_names):
            for name2 in unique_names[i+1:]:
                # Skip if already reported
                pair_key = tuple(sorted([name1, name2]))
                if pair_key in reported_pairs:
                    continue

                # Calculate similarity
                similarity = SequenceMatcher(None, name1.lower(), name2.lower()).ratio()

                if similarity > threshold and similarity < 1.0:
                    # Found potential variation
                    reported_pairs.add(pair_key)

                    # Find first segment where each appears
                    seg1 = next((idx for n, idx in all_names if n == name1), 0)
                    seg2 = next((idx for n, idx in all_names if n == name2), 0)

                    # Prefer more frequent spelling
                    if name_counts[name1] >= name_counts[name2]:
                        canonical, variant = name1, name2
                        report_segment = seg2
                    else:
                        canonical, variant = name2, name1
                        report_segment = seg1

                    issues.append(ValidationIssue(
                        type='name_variation',
                        severity='warning',
                        message=f'Possible name variation: "{variant}" vs "{canonical}" ({similarity:.0%} similar)',
                        segment_index=report_segment,
                        suggestion=f'If same person, standardize to "{canonical}" (appears {name_counts[canonical]}x vs {name_counts[variant]}x)'
                    ))

        return issues

    def validate_timing(
        self,
        segments: List[Dict]
    ) -> List[ValidationIssue]:
        """
        Validate timing relationships between segments (gaps, overlaps)

        Args:
            segments: List of all segments (must be sorted by start time)

        Returns:
            List of timing issues found
        """
        issues = []

        for i in range(len(segments) - 1):
            current = segments[i]
            next_seg = segments[i + 1]

            try:
                current_end = self._parse_timestamp(current.get('end', '0:00:00.000'))
                next_start = self._parse_timestamp(next_seg.get('start', '0:00:00.000'))

                gap = next_start - current_end

                # Check for overlaps
                if gap < 0:
                    issues.append(ValidationIssue(
                        type='timing',
                        severity='error',
                        message=f'Overlap detected: {abs(gap):.3f}s',
                        segment_index=i,
                        suggestion='Adjust timing to eliminate overlap'
                    ))

                # Check for insufficient gap
                elif gap < self.rules['min_gap']:
                    issues.append(ValidationIssue(
                        type='timing',
                        severity='warning',
                        message=f'Gap too small: {gap:.3f}s (min: {self.rules["min_gap"]}s)',
                        segment_index=i,
                        suggestion='Increase gap between subtitles'
                    ))

            except Exception as e:
                logger.debug(f"Gap validation error between segments {i} and {i+1}: {e}")

        return issues

    def validate_all(
        self,
        segments: List[Dict],
        include_consistency: bool = True,
        include_timing: bool = True,
        language_code: Optional[str] = None
    ) -> Dict[str, any]:
        """
        Run full validation suite on all segments

        Args:
            segments: List of all segments
            include_consistency: Check terminology consistency
            include_timing: Check timing gaps/overlaps
            language_code: Optional language code for language-specific validation (e.g., 'nl' for Dutch)

        Returns:
            Dict with validation results:
            {
                'issues': List[ValidationIssue],
                'error_count': int,
                'warning_count': int,
                'info_count': int,
                'quality_score': float (0-100)
            }
        """
        all_issues = []

        # Validate each segment
        for i, segment in enumerate(segments):
            issues = self.validate_segment(segment, segment_index=i)
            all_issues.extend(issues)

            # Language-specific validation
            if language_code == 'nl':
                # Dutch de/het gender validation
                text = segment.get('text', '')
                dutch_issues = validate_dutch_gender(text, segment_index=i)
                all_issues.extend(dutch_issues)

        # Validate consistency
        if include_consistency:
            consistency_issues = self.validate_consistency(segments)
            all_issues.extend(consistency_issues)

        # Validate timing
        if include_timing:
            timing_issues = self.validate_timing(segments)
            all_issues.extend(timing_issues)

        # Count by severity
        error_count = sum(1 for issue in all_issues if issue.severity == 'error')
        warning_count = sum(1 for issue in all_issues if issue.severity == 'warning')
        info_count = sum(1 for issue in all_issues if issue.severity == 'info')

        # Calculate quality score (100 = perfect, 0 = many errors)
        total_segments = len(segments)
        if total_segments == 0:
            quality_score = 0.0
        else:
            # Deduct points based on issue severity
            deductions = (error_count * 5) + (warning_count * 2) + (info_count * 0.5)
            max_score = 100
            quality_score = max(0, max_score - (deductions / total_segments * 10))

        return {
            'issues': all_issues,
            'error_count': error_count,
            'warning_count': warning_count,
            'info_count': info_count,
            'quality_score': round(quality_score, 1),
            'total_segments': total_segments
        }

    def _parse_timestamp(self, timestamp: str) -> float:
        """
        Parse timestamp to seconds

        Supports formats:
        - "0:00:05.500" (with hours)
        - "00:05.500" (minutes:seconds)
        - "5.500" (seconds only)

        Returns:
            Float seconds
        """
        timestamp = str(timestamp).strip()

        # Remove leading "0:" if present (common in exports)
        if timestamp.startswith("0:"):
            timestamp = timestamp[2:]

        parts = timestamp.split(':')

        if len(parts) == 3:
            # HH:MM:SS.mmm
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            # MM:SS.mmm
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        else:
            # SS.mmm
            return float(parts[0])


# =============================================================================
# Dutch de/het Gender Validation
# =============================================================================

# Common Dutch nouns with their correct article
# This is a subset of the most frequently confused words in religious/broadcast context
DUTCH_HET_WORDS = {
    # Religious terms
    "geloof", "gebed", "evangelie", "kruis", "woord", "hart", "licht", "leven",
    "koninkrijk", "heil", "wonder", "teken", "verbond", "offer", "bloed",
    # Common nouns
    "kind", "meisje", "huis", "water", "land", "volk", "werk", "moment",
    "begin", "einde", "probleem", "antwoord", "verschil", "idee", "gevoel",
    "lichaam", "hoofd", "oog", "oor", "been", "haar", "gezicht",
    # Abstract
    "belang", "gevolg", "voordeel", "nadeel", "recht", "onrecht", "geluk",
}

DUTCH_DE_WORDS = {
    # Religious terms
    "Here", "Heer", "God", "Geest", "Vader", "Zoon", "kerk", "gemeente",
    "bijbel", "psalm", "profeet", "apostel", "discipel", "zonde", "genade",
    "liefde", "hoop", "vrede", "waarheid", "wijsheid", "glorie", "hemel", "aarde",
    # People
    "man", "vrouw", "mens", "persoon", "moeder", "vader", "zoon", "dochter",
    "broer", "zus", "vriend", "vijand", "buurman", "koning", "koningin",
    # Common nouns
    "dag", "week", "maand", "tijd", "plaats", "stad", "straat", "wereld",
    "weg", "deur", "tafel", "stoel", "auto", "fiets", "trein",
    # Abstract
    "vraag", "reden", "manier", "kans", "zaak", "situatie",
}


def validate_dutch_gender(text: str, segment_index: Optional[int] = None) -> List[ValidationIssue]:
    """
    Validate Dutch de/het gender agreement.

    Checks for common errors like:
    - "het vrouw" should be "de vrouw"
    - "de kind" should be "het kind"

    Args:
        text: Dutch text to validate
        segment_index: Optional segment number for error reporting

    Returns:
        List of ValidationIssue objects
    """
    issues = []

    if not text:
        return issues

    text_lower = text.lower()

    # Check for "het" + de-word errors
    for word in DUTCH_DE_WORDS:
        pattern = rf'\bhet\s+{word}\b'
        if re.search(pattern, text_lower):
            issues.append(ValidationIssue(
                type='grammar',
                severity='warning',
                message=f'Gender mismatch: "het {word}" should be "de {word}"',
                segment_index=segment_index,
                suggestion=text_lower.replace(f"het {word}", f"de {word}")
            ))

    # Check for "de" + het-word errors
    for word in DUTCH_HET_WORDS:
        pattern = rf'\bde\s+{word}\b'
        if re.search(pattern, text_lower):
            issues.append(ValidationIssue(
                type='grammar',
                severity='warning',
                message=f'Gender mismatch: "de {word}" should be "het {word}"',
                segment_index=segment_index,
                suggestion=text_lower.replace(f"de {word}", f"het {word}")
            ))

    # Also check adjective agreement patterns (more complex)
    # e.g., "het Iraanse vrouw" is wrong because vrouw takes "de"
    # Pattern: "het [adjective] [de-word]"
    for word in DUTCH_DE_WORDS:
        # Match "het" + adjective + de-word
        pattern = rf'\bhet\s+\w+e\s+{word}\b'  # Dutch adjectives often end in -e
        if re.search(pattern, text_lower):
            issues.append(ValidationIssue(
                type='grammar',
                severity='warning',
                message=f'Gender mismatch: "{word}" requires "de", not "het"',
                segment_index=segment_index,
                suggestion=f'Use "de ... {word}" instead of "het ... {word}"'
            ))

    return issues


# Singleton instance
_validator = None

def get_validator(rules: Optional[Dict] = None) -> SubtitleValidator:
    """Get or create validator singleton"""
    global _validator
    if _validator is None or rules is not None:
        _validator = SubtitleValidator(rules)
    return _validator


# =============================================================================
# Review Portal 2.0: Confidence-Based Validation
# =============================================================================

# Human-readable messages for issue types (no technical jargon)
ISSUE_MESSAGES = {
    "reading_speed_critical": "This subtitle is too fast to read comfortably",
    "reading_speed_warning": "This subtitle may be slightly fast",
    "line_too_long": "This line may extend beyond the screen",
    "name_inconsistent": "This name is spelled differently elsewhere",
    "timing_overlap": "This subtitle overlaps with the next one",
    "duration_short": "This subtitle appears too briefly",
    "duration_long": "This subtitle stays on screen too long",
    "empty_text": "This subtitle is empty",
    "too_many_lines": "This subtitle has too many lines",
    "all_caps": "This subtitle uses all capital letters",
}


def parse_timestamp(ts) -> float:
    """Convert timestamp string to seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        ts = ts.strip().replace(',', '.')
        try:
            return float(ts)
        except ValueError:
            # Handle MM:SS.mmm or H:MM:SS.mmm format
            parts = ts.split(':')
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0


def generate_shorter_text(text: str, target_cps: float, duration: float) -> str:
    """
    Generate a shorter version of text to meet CPS target.
    For now returns a placeholder - AI integration can be added later.
    """
    target_chars = int(target_cps * duration)
    if len(text) <= target_chars:
        return text
    
    # Simple fallback: return original with note
    # TODO: Integrate with ai_assistant.py for smart shortening
    return text


def smart_line_break(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> str:
    """Break text into lines of max_chars length."""
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + 1 <= max_chars:
            current_line.append(word)
            current_length += len(word) + 1
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            current_length = len(word)
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return '\n'.join(lines)


def validate_segment_with_confidence(
    segment: dict, 
    index: int, 
    all_segments: list,
    confidence_threshold: float = 0.9
) -> dict:
    """
    Validates a segment and returns confidence score.
    
    High confidence (>=0.9) = auto-approve (no review needed)
    Low confidence (<0.9) = needs human review
    
    Args:
        segment: Segment dict with 'text', 'start', 'end'
        index: Segment index
        all_segments: All segments for context
        confidence_threshold: Threshold above which to auto-approve
        
    Returns:
        dict with keys: needs_review, confidence, issue_type, human_message, severity, suggestion
    """
    text = segment.get("text", "").strip()
    start = parse_timestamp(segment.get("start", "0"))
    end = parse_timestamp(segment.get("end", "0"))
    duration = end - start if end > start else 1.0
    
    # Default: no issues, high confidence
    result = {
        "needs_review": False,
        "confidence": 1.0,
        "issue_type": None,
        "human_message": None,
        "severity": None,
        "suggestion": None
    }
    
    # Empty text check
    if not text:
        return {
            "needs_review": True,
            "confidence": 0.1,
            "issue_type": "empty_text",
            "human_message": ISSUE_MESSAGES["empty_text"],
            "severity": "error",
            "suggestion": "Add subtitle text or remove this segment"
        }
    
    # Calculate CPS (characters per second)
    cps = len(text) / duration if duration > 0 else 0
    
    # Reading speed checks (most common issue)
    if cps > MAX_CPS:
        # Critical: Above broadcast ceiling
        return {
            "needs_review": True,
            "confidence": 0.3,
            "issue_type": "reading_speed_critical",
            "human_message": ISSUE_MESSAGES["reading_speed_critical"],
            "severity": "error",
            "suggestion": generate_shorter_text(text, target_cps=MAX_CPS, duration=duration)
        }
    elif cps > TIGHT_CPS:
        # High: Above tight threshold
        return {
            "needs_review": True,
            "confidence": 0.6,
            "issue_type": "reading_speed_warning",
            "human_message": ISSUE_MESSAGES["reading_speed_warning"],
            "severity": "warning",
            "suggestion": generate_shorter_text(text, target_cps=TIGHT_CPS, duration=duration)
        }
    elif cps > IDEAL_CPS:
        # Minor: Above ideal threshold
        result = {
            "needs_review": False,
            "confidence": 0.92,
            "issue_type": None,
            "human_message": None,
            "severity": None,
            "suggestion": None
        }
    
    # Line length checks
    # For Review Portal 2.0: Auto-apply line breaks instead of flagging
    # Only flag if breaking would create 3+ lines AND CPS is already borderline
    lines = text.split('\n')
    max_line_len = max(len(line) for line in lines) if lines else 0

    if max_line_len > MAX_CHARS_PER_LINE:
        # Apply line breaks automatically
        broken_text = smart_line_break(text, max_chars=MAX_CHARS_PER_LINE)
        broken_lines = broken_text.split('\n')

        # Only flag if it creates 4+ lines (extreme) or CPS is already borderline
        # 3 lines is acceptable for dense content
        if len(broken_lines) > 3:
            return {
                "needs_review": True,
                "confidence": 0.4,
                "issue_type": "line_too_long",
                "human_message": "This text is very long. Consider splitting into multiple subtitles.",
                "severity": "warning",
                "suggestion": broken_text
            }
        # 3 or fewer lines: auto-fix with line breaks
    
    # Too many lines
    if len(lines) > MAX_LINES:
        return {
            "needs_review": True,
            "confidence": 0.4,
            "issue_type": "too_many_lines",
            "human_message": ISSUE_MESSAGES["too_many_lines"],
            "severity": "error",
            "suggestion": "Split into multiple subtitles"
        }
    
    # Duration checks
    if duration < MIN_DURATION:
        return {
            "needs_review": True,
            "confidence": 0.5,
            "issue_type": "duration_short",
            "human_message": ISSUE_MESSAGES["duration_short"],
            "severity": "warning",
            "suggestion": "Extend duration or merge with adjacent subtitle"
        }
    
    # Check for timing overlap with next segment
    if index < len(all_segments) - 1:
        next_seg = all_segments[index + 1]
        next_start = parse_timestamp(next_seg.get("start", "0"))
        if end > next_start:
            return {
                "needs_review": True,
                "confidence": 0.3,
                "issue_type": "timing_overlap",
                "human_message": ISSUE_MESSAGES["timing_overlap"],
                "severity": "error",
                "suggestion": f"Adjust end time to before {next_start:.2f}s"
            }
    
    return result


def get_review_summary(
    segments: list,
    confidence_threshold: float = 0.9
) -> dict:
    """
    Generate a summary of segments needing review.
    
    Args:
        segments: List of segment dicts
        confidence_threshold: Only flag segments with confidence below this
        
    Returns:
        dict with issues list and summary stats
    """
    issues = []
    auto_approved_count = 0
    
    for i, segment in enumerate(segments):
        validation = validate_segment_with_confidence(
            segment, i, segments, confidence_threshold
        )
        
        if validation["needs_review"]:
            issues.append({
                "id": f"issue_{i}",
                "segment_index": i,
                "type": validation["issue_type"],
                "message": validation["human_message"],
                "severity": validation["severity"],
                "confidence": validation["confidence"],
                "original_text": segment.get("source_text", segment.get("text", "")),
                "current_text": segment.get("text", ""),
                "suggested_text": validation.get("suggestion", ""),
                "timestamp_start": parse_timestamp(segment.get("start", "0")),
                "timestamp_end": parse_timestamp(segment.get("end", "0"))
            })
        else:
            auto_approved_count += 1
    
    return {
        "issues": issues,
        "auto_approved_count": auto_approved_count,
        "total_segments": len(segments)
    }
