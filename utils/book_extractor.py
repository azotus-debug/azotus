"""
Book Text Extraction Utilities

Extract text and chapter structure from PDF and DOCX files.
Supports:
- PDF: via pymupdf (fitz)
- DOCX: via python-docx

Usage:
    from utils.book_extractor import extract_book_text

    chapters = extract_book_text("book.pdf")
    for ch in chapters:
        print(ch['chapter_number'], ch['title'], len(ch['text']))
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("OmegaLiterati.BookExtractor")


# =============================================================================
# PDF EXTRACTION (using pymupdf/fitz)
# =============================================================================

def extract_pdf_text(pdf_path: Path) -> str:
    """
    Extract raw text from PDF using pymupdf.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Full text of PDF as single string
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ImportError("pymupdf not installed. Run: pip install pymupdf")

    logger.info(f"Extracting text from PDF: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    full_text = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        full_text.append(text)

    doc.close()

    combined_text = "\n\n".join(full_text)
    logger.info(f"Extracted {len(combined_text)} characters from {len(doc)} pages")

    return combined_text


# =============================================================================
# DOCX EXTRACTION (using python-docx)
# =============================================================================

def extract_docx_text(docx_path: Path) -> str:
    """
    Extract raw text from DOCX using python-docx.

    Args:
        docx_path: Path to DOCX file

    Returns:
        Full text of DOCX as single string
    """
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx not installed. Run: pip install python-docx")

    logger.info(f"Extracting text from DOCX: {docx_path}")

    doc = Document(str(docx_path))
    full_text = []

    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text)

    combined_text = "\n\n".join(full_text)
    logger.info(f"Extracted {len(combined_text)} characters from {len(doc.paragraphs)} paragraphs")

    return combined_text


# =============================================================================
# CHAPTER DETECTION
# =============================================================================

def detect_chapters(text: str) -> List[Dict[str, any]]:
    """
    Detect chapter boundaries in raw text.

    Uses heuristics:
    1. Lines starting with "Chapter 1", "CHAPTER ONE", etc.
    2. Page breaks followed by numbered headings
    3. Lines with only a number (e.g., "1", "2", "3")

    Args:
        text: Full book text

    Returns:
        List of dicts: [{"chapter_number": 1, "title": "Introduction", "text": "...", "word_count": 2500}, ...]
    """
    logger.info("Detecting chapter boundaries...")

    # Common chapter patterns
    patterns = [
        r'^Chapter\s+(\d+)[:\s]*(.*)$',           # "Chapter 1: Introduction"
        r'^CHAPTER\s+(\d+)[:\s]*(.*)$',           # "CHAPTER 1: Introduction"
        r'^(\d+)\.\s+(.+)$',                      # "1. Introduction"
        r'^(\d+)[:\s]+(.+)$',                     # "1: Introduction" or "1 Introduction"
        r'^\s*(\d+)\s*$',                         # Just a number on its own line
    ]

    lines = text.split('\n')
    chapter_boundaries = []

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        for pattern in patterns:
            match = re.match(pattern, line_stripped, re.IGNORECASE)
            if match:
                chapter_num = int(match.group(1))
                chapter_title = match.group(2).strip() if match.lastindex >= 2 else ""

                chapter_boundaries.append({
                    'line_index': i,
                    'chapter_number': chapter_num,
                    'title': chapter_title
                })
                break

    # If no chapters detected, treat entire text as single chapter
    if not chapter_boundaries:
        logger.warning("No chapter markers found - treating as single chapter")
        return [{
            'chapter_number': 1,
            'title': 'Full Text',
            'text': text.strip(),
            'word_count': len(text.split())
        }]

    # Extract text for each chapter
    chapters = []
    for i, boundary in enumerate(chapter_boundaries):
        start_line = boundary['line_index']

        # End line is either next chapter or end of text
        if i < len(chapter_boundaries) - 1:
            end_line = chapter_boundaries[i + 1]['line_index']
        else:
            end_line = len(lines)

        chapter_lines = lines[start_line:end_line]
        chapter_text = '\n'.join(chapter_lines).strip()

        chapters.append({
            'chapter_number': boundary['chapter_number'],
            'title': boundary['title'],
            'text': chapter_text,
            'word_count': len(chapter_text.split())
        })

    logger.info(f"Detected {len(chapters)} chapters")
    return chapters


# =============================================================================
# MAIN API
# =============================================================================

def extract_book_text(
    file_path: Path,
    detect_chapters_flag: bool = True
) -> List[Dict[str, any]]:
    """
    Extract text from PDF or DOCX and optionally detect chapters.

    Args:
        file_path: Path to PDF or DOCX file
        detect_chapters_flag: If True, attempt to detect chapter boundaries

    Returns:
        List of chapters: [{"chapter_number": 1, "title": "...", "text": "...", "word_count": 1234}, ...]

    Raises:
        ValueError: If file format is not supported
        FileNotFoundError: If file doesn't exist
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Extract raw text based on file type
    suffix = file_path.suffix.lower()

    if suffix == '.pdf':
        raw_text = extract_pdf_text(file_path)
    elif suffix in ['.docx', '.doc']:
        raw_text = extract_docx_text(file_path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Only .pdf and .docx are supported.")

    # Detect chapters if requested
    if detect_chapters_flag:
        return detect_chapters(raw_text)
    else:
        # Return entire text as single chapter
        return [{
            'chapter_number': 1,
            'title': file_path.stem,
            'text': raw_text.strip(),
            'word_count': len(raw_text.split())
        }]


def extract_book_metadata(file_path: Path) -> Dict[str, str]:
    """
    Extract metadata from PDF or DOCX.

    Args:
        file_path: Path to PDF or DOCX file

    Returns:
        Dict with keys: title, author, subject (if available)
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    metadata = {
        'title': file_path.stem,
        'author': None,
        'subject': None
    }

    try:
        if suffix == '.pdf':
            import fitz
            doc = fitz.open(str(file_path))
            meta = doc.metadata
            metadata['title'] = meta.get('title') or file_path.stem
            metadata['author'] = meta.get('author')
            metadata['subject'] = meta.get('subject')
            doc.close()

        elif suffix in ['.docx', '.doc']:
            from docx import Document
            doc = Document(str(file_path))
            core_props = doc.core_properties
            metadata['title'] = core_props.title or file_path.stem
            metadata['author'] = core_props.author
            metadata['subject'] = core_props.subject

    except Exception as e:
        logger.warning(f"Failed to extract metadata from {file_path}: {e}")

    return metadata


# =============================================================================
# GLOSSARY AUTO-EXTRACTION
# =============================================================================

# Predefined theological glossary with correct Icelandic translations
THEOLOGICAL_GLOSSARY = {
    # Core terms
    "god": "Guð",
    "lord": "Drottinn",
    "jesus": "Jesús",
    "christ": "Kristur",
    "holy spirit": "Heilagur Andi",
    "father": "Faðirinn",
    "savior": "Frelsari",
    
    # Salvation
    "grace": "Náð",
    "salvation": "Hjálpræði",
    "redemption": "Endurlausn",
    "sin": "Synd",
    "repentance": "Iðrun",
    "forgiveness": "Fyrirgefning",
    "faith": "Trú",
    "justification": "Réttlæting",
    "sanctification": "Helgun",
    "atonement": "Friðþæging",
    "righteousness": "Réttlæti",
    
    # Church
    "gospel": "Fagnaðarerindi",
    "church": "Kirkja",
    "baptism": "Skírn",
    "communion": "Kvöldmáltíð Drottins",
    "pastor": "Prestur",
    "worship": "Tilbeiðsla",
    
    # Scripture
    "bible": "Biblían",
    "scripture": "Ritningin",
    "covenant": "Sáttmáli",
    "prophecy": "Spádómur",
    "resurrection": "Upprisa",
    
    # Spiritual
    "heaven": "Himnaríki",
    "hell": "Helvíti",
    "angel": "Engill",
    "satan": "Satan",
    "blessing": "Blessun",
    "anointing": "Smurning",
    "revival": "Vakning",
    "born again": "Endurfæddur",
}

# Theological term patterns for extraction
THEOLOGICAL_TERM_PATTERNS = [
    # Trinity & God
    r'\b(God|Lord|Father|Son|Holy Spirit|Trinity|Almighty|Jehovah|Yahweh)\b',
    r'\b(Messiah|Christ|Savior|Redeemer|King of Kings|Lord of Lords)\b',
    
    # Salvation concepts
    r'\b(grace|salvation|redemption|justification|sanctification|atonement)\b',
    r'\b(sin|repentance|forgiveness|mercy|righteousness|holiness)\b',
    r'\b(faith|belief|trust|hope|love|joy|peace)\b',
    
    # Church & Practice
    r'\b(baptism|communion|worship|prayer|preaching|gospel)\b',
    r'\b(church|congregation|pastor|elder|deacon|minister)\b',
    r'\b(covenant|blessing|anointing|revival|awakening)\b',
    
    # Scripture
    r'\b(Scripture|Bible|Word of God|Gospel|Old Testament|New Testament)\b',
    r'\b(parable|prophecy|revelation|commandment)\b',
    
    # Spiritual entities
    r'\b(angel|demon|Satan|devil|heaven|hell|kingdom)\b',
]

# Place and organization indicators
PLACE_INDICATORS = {"City", "Mountain", "River", "Lake", "Island", "Valley", 
                    "Street", "Road", "Church", "Temple", "Kingdom", "Land"}
ORG_INDICATORS = {"Ministry", "Church", "Foundation", "Institute", "Council",
                  "Assembly", "Fellowship", "Mission", "Organization"}


def extract_proper_nouns(text: str) -> List[Dict]:
    """
    Extract character names, place names, and other proper nouns.
    
    Heuristics:
    1. Capitalized words not at sentence start
    2. Multi-word capitalized phrases
    3. Words following name indicators (Mr., Dr., Pastor, King)
    4. Recurring capitalized words (minimum 3 occurrences)
    
    Args:
        text: Full book text
        
    Returns:
        List of dicts with source_term, category, occurrences, icelandic
    """
    from collections import Counter
    
    # Pattern for potential proper nouns (capitalized words/phrases)
    # Supports: Hyphenated (Jean-Luc), All-Caps (ZEUS), Standard (John Doe)
    proper_noun_pattern = r'\b[A-Z][a-zA-Z]*(?:[- ][A-Z][a-zA-Z]*)*\b'
    
    # Find all candidates
    candidates = re.findall(proper_noun_pattern, text)
    
    # Count occurrences
    counts = Counter(candidates)
    
    # Filter: Keep terms appearing 3+ times, not common English words
    COMMON_WORDS = {
        "The", "This", "That", "These", "Those", "There", "Then", "When", "Where",
        "What", "Which", "Who", "How", "Why", "But", "And", "For", "Not", "You",
        "All", "Can", "Her", "Was", "One", "Our", "Out", "Day", "Had", "Has",
        "His", "Him", "She", "Now", "May", "Its", "New", "Old", "See", "Way",
        "Come", "Made", "Find", "Here", "Said", "Each", "Make", "Like", "Just",
        "Over", "Such", "Into", "Year", "Your", "Some", "Could", "Them", "Than",
        "Other", "About", "After", "First", "Also", "Back", "Because", "Before",
        "Being", "Between", "Both", "During", "Even", "From", "Good", "Great",
        "Have", "Know", "Life", "Little", "Long", "Look", "Many", "More", "Most",
        "Must", "Never", "Only", "People", "Place", "Right", "Same", "Should",
        "Still", "Think", "Through", "Time", "Under", "Very", "Want", "Well",
        "While", "Without", "Work", "World", "Would", "Chapter", "Part", "Section",
    }
    
    recurring = [term for term, count in counts.items() 
                 if count >= 3 and term not in COMMON_WORDS]
    
    # Categorize each term
    results = []
    for term in recurring:
        category = _categorize_proper_noun(term)
        results.append({
            "source_term": term,
            "category": category,
            "occurrences": counts[term],
            "confidence": _calculate_confidence(term, counts[term], category),
            "icelandic": ""  # To be filled by translator
        })
    
    # Sort by occurrences (most frequent first)
    results.sort(key=lambda x: x['occurrences'], reverse=True)
    
    return results


def _categorize_proper_noun(term: str) -> str:
    """Categorize a proper noun based on context clues."""
    words = term.split()
    
    if any(word in PLACE_INDICATORS for word in words):
        return "place"
    if any(word in ORG_INDICATORS for word in words):
        return "organization"
    
    # Default to character for single/double word proper nouns
    return "character"


def extract_theological_terms(text: str) -> List[Dict]:
    """
    Extract theological terms requiring consistent translation.
    
    Args:
        text: Full book text
        
    Returns:
        List of dicts with source_term, category, occurrences, icelandic
    """
    from collections import Counter
    
    found_terms = []
    
    for pattern in THEOLOGICAL_TERM_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found_terms.extend([m.lower() for m in matches])
    
    counts = Counter(found_terms)
    
    results = []
    seen = set()  # Avoid duplicates
    
    for term, count in counts.most_common():
        term_lower = term.lower()
        if term_lower in seen:
            continue
        seen.add(term_lower)
        
        # Check if we have a known translation
        icelandic = THEOLOGICAL_GLOSSARY.get(term_lower, "")
        
        # Determine display form (capitalize first letter)
        display_term = term.title() if term == term.lower() else term
        
        results.append({
            "source_term": display_term,
            "category": "theological",
            "occurrences": count,
            "icelandic": icelandic
        })
    
    return results


def extract_character_speech_patterns(text: str, character_name: str) -> Dict:
    """
    Extract speech patterns for a specific character.
    
    Analyzes dialogue attributed to the character for:
    - Common phrases/catchphrases
    - Formality level
    - Sample quotes
    
    Args:
        text: Full book text
        character_name: Name of character to analyze
        
    Returns:
        Dict with name, dialogue_count, sample_quotes, register, catchphrases
    """
    # Find dialogue attributed to character
    # Pattern: Character said, "..." or "..." said Character
    dialogue_patterns = [
        rf'{re.escape(character_name)}\s+(?:said|replied|asked|whispered|shouted|cried|answered|muttered|exclaimed)[,:]?\s*["\'\"](.+?)["\'\"]',
        rf'["\'\"](.+?)["\'\"].*{re.escape(character_name)}\s+(?:said|replied|asked)',
    ]
    
    dialogues = []
    for pattern in dialogue_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.DOTALL)
        dialogues.extend(matches)
    
    # Analyze speech patterns
    return {
        "name": character_name,
        "dialogue_count": len(dialogues),
        "sample_quotes": dialogues[:5],  # First 5 for review
        "register": _determine_register(dialogues),
        "catchphrases": _extract_recurring_phrases(dialogues),
        "icelandic_notes": ""  # For translator to add guidance
    }


def _determine_register(dialogues: List[str]) -> str:
    """Determine formality level from dialogue samples."""
    informal_markers = ["gonna", "wanna", "gotta", "ain't", "yeah", "nope", "hey", 
                        "kinda", "sorta", "dunno", "y'know", "lemme", "gimme"]
    formal_markers = ["indeed", "therefore", "furthermore", "nevertheless", "hence",
                      "perhaps", "certainly", "absolutely", "precisely", "consequently"]
    
    text = " ".join(dialogues).lower()
    
    informal_count = sum(text.count(m) for m in informal_markers)
    formal_count = sum(text.count(m) for m in formal_markers)
    
    if informal_count > formal_count * 2:
        return "informal"
    elif formal_count > informal_count * 2:
        return "formal"
    return "neutral"


def _extract_recurring_phrases(dialogues: List[str], min_occurrences: int = 2) -> List[str]:
    """Find phrases that appear multiple times in dialogue."""
    from collections import Counter
    
    if not dialogues:
        return []
    
    # Split into 3-5 word phrases
    phrases = []
    for d in dialogues:
        words = d.split()
        for n in [3, 4, 5]:
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i+n])
                # Clean up punctuation
                phrase = re.sub(r'[.,!?;:]+$', '', phrase.strip())
                if phrase:
                    phrases.append(phrase.lower())
    
    counts = Counter(phrases)
    return [p for p, c in counts.items() if c >= min_occurrences]


def _calculate_confidence(term: str, occurrences: int, category: str) -> float:
    """
    Calculate confidence score (0.0 - 1.0) for an extracted term.
    
    Factors:
    - Base score: 0.5
    - Frequency: +0.1 per 5 occurrences (max +0.3)
    - Formatting: +0.2 for standard Title Case
    - Category: +0.1 for known categories
    """
    score = 0.5
    
    # Frequency boost
    score += min(0.3, (occurrences / 5) * 0.1)
    
    # Formatting boost
    if term[0].isupper() and not term.isupper():
         score += 0.2
    
    # Category boost
    if category != "character": # Places/Orgs are usually detected via specific keywords
        score += 0.1
        
    return min(1.0, score)


def _infer_gender(text: str, name: str) -> str:
    """
    Infer gender based on pronouns surrounding the character name.
    Scans a +/- 50 char window around occurrences.
    """
    name_esc = re.escape(name)
    # Find all occurrences with context
    matches = list(re.finditer(rf".{{0,50}}\b{name_esc}\b.{{0,50}}", text, re.DOTALL))
    
    male_pronouns = {"he", "him", "his", "himself"}
    female_pronouns = {"she", "her", "hers", "herself"}
    
    male_count = 0
    female_count = 0
    
    for m in matches:
        context = m.group(0).lower()
        words = set(re.findall(r'\b\w+\b', context))
        
        male_count += len(words.intersection(male_pronouns))
        female_count += len(words.intersection(female_pronouns))
        
    if male_count > female_count and male_count > 0:
        return "male"
    elif female_count > male_count and female_count > 0:
        return "female"
    
    return "unknown"


def extract_glossary(text: str, book_id: str) -> Dict:
    """
    Auto-extract glossary from book text.
    
    Combines proper noun extraction, theological term extraction,
    and character analysis to build a comprehensive per-book glossary.
    
    Args:
        text: Full book text
        book_id: Database ID of the book
        
    Returns:
        Dict matching the BookGlossary JSON schema
    """
    from datetime import datetime
    
    logger.info(f"Extracting glossary for book {book_id}")
    
    # Extract proper nouns
    proper_nouns = extract_proper_nouns(text)
    characters = [n for n in proper_nouns if n["category"] == "character"]
    places = [n for n in proper_nouns if n["category"] == "place"]
    organizations = [n for n in proper_nouns if n["category"] == "organization"]
    
    # Extract theological terms
    theological_terms = extract_theological_terms(text)
    
    # Build character bible for top characters
    character_bible = []
    for char in characters[:10]:  # Top 10 characters by occurrence
        patterns = extract_character_speech_patterns(text, char["source_term"])
        if patterns["dialogue_count"] > 0:
            character_bible.append({
                "source_name": char["source_term"],
                "icelandic_name": "",
                "occurrences": char["occurrences"],
                "description": "",
                "gender": "unknown",
                **patterns
            })
    
    # Build glossary structure
    glossary = {
        "metadata": {
            "book_id": book_id,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "source_language": "en",
            "target_language": "is",
            "version": 1
        },
        "characters": [
            {
                "source_name": c["source_term"],
                "icelandic_name": c.get("icelandic", ""),
                "occurrences": c["occurrences"],
                "description": "",
                "gender": _infer_gender(text, c["source_term"]),
                "confidence": c.get("confidence", 0.5)
            }
            for c in characters[:50]  # Limit to top 50
        ],
        "places": [
            {
                "source_name": p["source_term"],
                "icelandic_name": p.get("icelandic", ""),
                "type": "landmark",
                "occurrences": p["occurrences"]
            }
            for p in places[:30]  # Limit to top 30
        ],
        "organizations": [
            {
                "source_name": o["source_term"],
                "icelandic_name": o.get("icelandic", ""),
                "occurrences": o["occurrences"]
            }
            for o in organizations[:20]  # Limit to top 20
        ],
        "theological_terms": [
            {
                "source_term": t["source_term"],
                "icelandic_term": t.get("icelandic", ""),
                "category": "theological",
                "occurrences": t["occurrences"],
                "notes": ""
            }
            for t in theological_terms[:100]  # Limit to top 100
        ],
        "custom_terms": [],  # For user-added terms
        "character_bible": character_bible
    }
    
    logger.info(f"Extracted glossary: {len(characters)} characters, {len(places)} places, "
                f"{len(theological_terms)} theological terms")
    
    return glossary


# =============================================================================
# CLI TESTING
# =============================================================================


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python book_extractor.py <path-to-pdf-or-docx>")
        sys.exit(1)

    file_path = Path(sys.argv[1])

    # Extract metadata
    print("\n=== METADATA ===")
    metadata = extract_book_metadata(file_path)
    for key, value in metadata.items():
        print(f"{key}: {value}")

    # Extract chapters
    print("\n=== CHAPTERS ===")
    chapters = extract_book_text(file_path)

    for ch in chapters:
        print(f"\nChapter {ch['chapter_number']}: {ch['title']}")
        print(f"Word count: {ch['word_count']}")
        print(f"Preview: {ch['text'][:200]}...")
