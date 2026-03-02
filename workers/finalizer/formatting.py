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
    
    Semantic Line Breaking Rules (Icelandic focused):
    1. Try to break at punctuation (, . ! ?).
    2. Avoid breaking immediately after a preposition or conjunction (if possible).
    3. Try to balance line lengths.
    """
    TWO_LINE_THRESHOLD = 35
    if len(text) <= TWO_LINE_THRESHOLD:
        return [text]

    # Cleaned string
    text = text.strip()
    middle = len(text) // 2

    # Semantic non-breaking suffixes (Icelandic)
    # Don't break immediately AFTER these words if we can avoid it.
    no_break_after = {
        "og", "eða", "en", "að", "sem", "því", "svo",
        "í", "á", "við", "um", "til", "frá", "með", "af", "fyrir", "án", "að"
    }

    # Helper to score a potential split position
    def score_split(pos: int) -> float:
        if pos <= 0 or pos >= len(text):
            return -1000.0
            
        score = 0.0
        
        # 1. Punctuation gets high score (breaking AFTER punctuation is great)
        if pos > 0 and text[pos-1] in {',', '.', ';', '?', '!', ':'}:
            score += 50.0
            
        # 2. Distance from middle (closer is better, but punctuation can override)
        distance = abs(middle - pos)
        score -= distance * 0.5 
        
        # Get the word immediately before the split and immediately after
        left_part = text[:pos].strip()
        right_part = text[pos:].strip()
        
        word_before = left_part.split()[-1].lower() if left_part else ""
        word_after = right_part.split()[0].lower() if right_part else ""
        
        # 3. Avoid orphaned prepositions/conjunctions at the end of line 1 (breaking AFTER them)
        if word_before in no_break_after:
            score -= 40.0
            
        # 4. Try to keep specific adjective + noun phrases together.
        # "víðs vegar", "frábært starf", "að minnsta kosti"
        if word_before == "víðs" and word_after == "vegar": score -= 50.0
        if word_before == "að" and word_after == "minnsta": score -= 50.0
            
        return score

    # Find all space positions
    space_positions = [i for i, char in enumerate(text) if char == ' ']
    
    if not space_positions:
        return [text] # No spaces, can't split

    # Find the best split point based on semantic scoring
    best_pos = -1
    best_score = -float('inf')
    
    for pos in space_positions:
        # Don't split too close to edges (e.g. at least 10 chars per line if possible)
        if pos < 10 or pos > len(text) - 10:
            # We still consider them, but with a severe penalty unless forced
            s = score_split(pos) - 100.0
        else:
            s = score_split(pos)
            
        if s > best_score:
            best_score = s
            best_pos = pos

    if best_pos > 0:
        lines = [text[:best_pos].strip(), text[best_pos:].strip()]
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
            # Revert to math fallback for extreme overflow inside chunks
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
            # Abbreviation helped, re-run split (just call self)
            return split_into_balanced_lines(condensed, target_language)
                    
        # Fold overflow
        overflow = " ".join(final_lines[MAX_LINES - 1:]).strip()
        final_lines = final_lines[:MAX_LINES - 1] + [overflow]
        
    return final_lines

def format_audio_event_text(event: SubtitleEvent) -> str:
    """Format audio event for subtitle display (e.g., '[LAUGHTER]')."""
    event_type = (event.audio_event_type or "sound").upper()
    return f"[{event_type}]"
