import re
from typing import List
from subtitle_standards import MAX_CHARS_PER_LINE, MAX_LINES
from .models import SubtitleEvent
from workers.text_sanitizer import repair_tab_escaped_t

def strip_metadata_tags(text: str) -> str:
    """
    Remove metadata tags from subtitle text.
    Tags like <MUSIC>, <APPLAUSE>, <LAUGHTER>, [MUSIC], (UPBEAT MUSIC)
    should not appear in final SRT output.
    """
    if not text:
        return text

    # Remove angle bracket tags
    cleaned = re.sub(r'<[A-Z_]+>', '', text)

    # Remove square bracket tags that are audio markers
    cleaned = re.sub(r'\[(MUSIC|APPLAUSE|LAUGHTER|CHEERING|SINGING|INSTRUMENTAL)\]', '', cleaned, flags=re.IGNORECASE)

    # Known metadata terms in parens
    metadata_terms = {
        'music', 'applause', 'laughter', 'laughs', 'laugh', 'cheering', 'singing',
        'instrumental', 'audio', 'phone', 'crowd', 'clapping', 'claps',
        'upbeat music', 'gentle music', 'soft music', 'dramatic music',
        'tónlist', 'róleg tónlist', 'fjörug tónlist', 'hlær', 'hlátur',
        'áhorfendur hlæja', 'áhorfendur klappa', 'klappa', 'klappar',
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

    # Remove leading dots from translation artifacts (.. or ...)
    cleaned = re.sub(r'^(-\s*)\.{2,}\s*', r'\1', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\.{2,}\s*', '', cleaned, flags=re.MULTILINE)

    # Normalize whitespace but preserve newlines
    normalized_lines = []
    for raw_line in cleaned.splitlines():
        # Some upstream model JSON responses can encode literal "t" as "\t".
        raw_line = repair_tab_escaped_t(raw_line)
        # Collapse spaces/tabs only. Do not match literal "t" characters.
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def abbreviate_bible_refs(text: str, target_language: str = "is") -> str:
    """Abbreviate Bible references to save space."""
    if target_language != "is":
        return text

    replacements = {
        r'\bFyrsta Mósebók(i?|b?\b)': '1. Mós.',
        r'\bÖnnur Mósebók(i?|b?\b)': '2. Mós.',
        r'\bÞriðja Mósebók(i?|b?\b)': '3. Mós.',
        r'\bFjórða Mósebók(i?|b?\b)': '4. Mós.',
        r'\bFimmta Mósebók(i?|b?\b)': '5. Mós.',
        r'\bMatteusarguðspjall(i?|s?\b)': 'Matt.',
        r'\bMarkúsarguðspjall(i?|s?\b)': 'Mark.',
        r'\bLúkasarguðspjall(i?|s?\b)': 'Lúk.',
        r'\bJóhannesarguðspjall(i?|s?\b)': 'Jóh.',
        r'\bRómverjabréfið\b': 'Róm.',
        r'\bRómverjabréfinu\b': 'Róm.',
        r'\bFyrra Korintubréf(i?|ið?\b)': '1. Kor.',
        r'\bSíðara Korintubréf(i?|ið?\b)': '2. Kor.',
        r'\bFyrra Þessaloníkubréf(i?|ið?\b)': '1. Þess.',
        r'\bSíðara Þessaloníkubréf(i?|ið?\b)': '2. Þess.',
        r'\bFyrra Tímóteusarbréf(i?|ið?\b)': '1. Tím.',
        r'\bSíðara Tímóteusarbréf(i?|ið?\b)': '2. Tím.',
        r'\bFyrra Pétursbréf(i?|ið?\b)': '1. Pét.',
        r'\bSíðara Pétursbréf(i?|ið?\b)': '2. Pét.',
        r'\bFyrsta Jóhannesarbréf(i?|ið?\b)': '1. Jóh.',
        r'\bAnnað Jóhannesarbréf(i?|ið?\b)': '2. Jóh.',
        r'\bÞriðja Jóhannesarbréf(i?|ið?\b)': '3. Jóh.',
        r'\bHebreabréfi(ð|nu)?\b': 'Hebr.',
        r'\bOpinberunarbókin(ni)?\b': 'Opinb.',
    }
    
    result = text
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def split_into_balanced_lines(text: str, target_language: str = "is") -> List[str]:
    """
    Netflix/BBC preference: two balanced shorter lines are easier to read 
    than one long line. Split any subtitle longer than 42 chars into lines.
    """
    TWO_LINE_THRESHOLD = 35
    if len(text) <= TWO_LINE_THRESHOLD:
        return [text]

    middle = len(text) // 2
    
    # Simple split attempt near middle
    split_pos = text.rfind(' ', 0, middle + 10)
    if split_pos <= 5:
        # If no good space found backwards, search forwards
        split_pos = text.find(' ', middle)
        
    if split_pos > 0:
        lines = [text[:split_pos].strip(), text[split_pos:].strip()]
    else:
        # Fallback if no spaces
        lines = [text]
        
    final_lines = []
    # Hard wrap lines that still exceed MAX_CHARS_PER_LINE
    for line in lines:
        remainder = line.strip()
        if not remainder:
            continue
            
        while len(remainder) > MAX_CHARS_PER_LINE:
            spos = remainder.rfind(' ', 0, MAX_CHARS_PER_LINE + 1)
            if spos <= 10:
                spos = MAX_CHARS_PER_LINE
            chunk = remainder[:spos].strip()
            if chunk:
                final_lines.append(chunk)
            remainder = remainder[spos:].strip()
            
        if remainder:
            final_lines.append(remainder)
            
    # Abbreviate or fold if we exceed MAX_LINES
    if len(final_lines) > MAX_LINES:
        joined = " ".join(final_lines).strip()
        condensed = abbreviate_bible_refs(joined, target_language)
        if len(condensed) < len(joined):
            # Abbreviation helped, re-run simple split
            if len(condensed) <= MAX_CHARS_PER_LINE:
                return [condensed]
            mid = len(condensed) // 2
            spos = condensed.rfind(' ', 0, mid + 10)
            if spos <= 5: spos = condensed.find(' ', mid)
            if spos > 0:
                c_lines = [condensed[:spos].strip(), condensed[spos:].strip()]
                if all(len(l) <= MAX_CHARS_PER_LINE for l in c_lines):
                    return c_lines
                    
        # Fold overflow
        overflow = " ".join(final_lines[MAX_LINES - 1:]).strip()
        final_lines = final_lines[:MAX_LINES - 1] + [overflow]
        
    return final_lines

def format_audio_event_text(event: SubtitleEvent) -> str:
    """Format audio event for subtitle display (e.g., '[LAUGHTER]')."""
    event_type = (event.audio_event_type or "sound").upper()
    return f"[{event_type}]"
