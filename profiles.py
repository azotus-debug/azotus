import json

# --- 1. THE PHYSICS (Language Rules) ---
# These apply to EVERYONE speaking this language.

LANGUAGES = {
    "en": {
        "name": "English",
        "bible": "English Standard Version (ESV)",
        "god_address": "Reverent second-person (You/Your) capitalized when customary",
        "human_address": "Natural broadcast English",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["and", "but", "that", "or", "because", "so"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **English Standard Version (ESV)** for scripture.
           - Honorifics: God -> capitalized pronouns where customary.
           
        2. BROADCAST CLARITY:
           - Clear, neutral English; remove filler and keep phrasing concise.
        """
    },
    "is": {
        "name": "Icelandic",
        "bible": "Biblían 2007",
        "god_address": "Þú (Broadcast Standard)",
        "human_address": "Þú (Casual)",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["og", "en", "sem", "að", "eða", "því"],
        "abbreviations": {
            r"(?i)Fyrra Korintubréfi": "1. Kor.",
            r"(?i)Síðara Korintubréfi": "2. Kor.",
            # ... (Full list from finalizer.py) ...
        },
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - God is addressed as "Þú".
           - Do NOT use "Þér" for God.
           - Humans/Friends are addressed as "Þú" (Casual).
           - Never use "ég er" for God's title; use "ÉG ER".
           
        2. SCRIPTURE PROTOCOL:
           - Use **Biblían 2007**.
           - If English speaker paraphrases, RECALL the official Icelandic verse.
           
        3. AVOID ANGLICISMS & ROBOTIC FLOW:
           - "Died for you" -> "Dó vegna þín".
           - "On fire" -> "Brennandi" (NOT "á eldi").
           - **NATURAL FLOW**: Avoid literal "We have received/gotten" (Við höfum fengið) for weather or states. Use existential forms: "It has been/there is" (Það hefur verið / Það er).
        
        4. TERMINOLOGY:
           - "Pastor" -> "Prestur".
           - "Hallowed" -> "Heilagt" (NOT "halls", "halloween").
           - "Hold" -> "Tak" or "Halda" (NOT "hole").
        """
    },
    "es": {
        "name": "Spanish",
        "bible": "Reina-Valera 1960 (RVR1960)",
        "god_address": "Tú (Reverent Capitalized)",
        "human_address": "Tú (Casual)",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["y", "o", "que", "pero", "de", "en"],
        "abbreviations": {
            r"(?i)Primera de Corintios": "1 Cor.",
            # ... (Full list from finalizer.py) ...
        },
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Reina-Valera 1960**.
           - Address God as "Tú" (Capitalized: Tú, Ti, Él).
           - Titles: "YO SOY", "Señor", "Espíritu Santo".
           
        2. BROADCAST CLARITY:
           - Use neutral Latin American Evangelical Standard.
        """
    },
    "pt": {
        "name": "Portuguese",
        "bible": "Almeida Revista e Atualizada (ARA)",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["e", "mas", "que", "ou", "porque", "para"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Almeida Revista e Atualizada (ARA)** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use neutral, contemporary Portuguese; avoid slang.
        """
    },
    "fr": {
        "name": "French",
        "bible": "Louis Segond 1910 (LSG)",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["et", "mais", "que", "ou", "parce", "pour"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Louis Segond 1910 (LSG)** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use clear, neutral French; avoid slang.
        """
    },
    "de": {
        "name": "German",
        "bible": "Luther 2017",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["und", "aber", "dass", "oder", "weil", "für"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Luther 2017** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).
           
        2. BROADCAST CLARITY:
           - Use clear, neutral German; avoid slang.
        """
    },
    "it": {
        "name": "Italian",
        "bible": "Nuova Riveduta 2006",
        "god_address": "Reverent second-person (capitalize pronouns as customary)",
        "human_address": "Natural broadcast second-person",
        "music_prompt": "(MUSIC)",
        "conjunctions": ["e", "ma", "che", "o", "perché", "per"],
        "abbreviations": {},
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **Nuova Riveduta 2006** for scripture.
           - Honorifics: God -> reverent second-person (capitalize where customary).

        2. BROADCAST CLARITY:
           - Use clear, neutral Italian; avoid slang.
        """
    },
    "nl": {
        "name": "Dutch",
        "bible": "NBV21 (Nieuwe Bijbelvertaling 2021)",
        "god_address": "U/Gij (Reverent formal - traditional religious convention)",
        "human_address": "Jij/Je (Informal - modern Dutch standard)",
        "music_prompt": "(MUZIEK)",
        "conjunctions": ["en", "maar", "dat", "of", "omdat", "want", "dus"],
        "abbreviations": {
            r"(?i)Eerste Korintiërs": "1 Kor.",
            r"(?i)Tweede Korintiërs": "2 Kor.",
            r"(?i)Eerste Tessalonicenzen": "1 Tess.",
            r"(?i)Tweede Tessalonicenzen": "2 Tess.",
            r"(?i)Eerste Timoteüs": "1 Tim.",
            r"(?i)Tweede Timoteüs": "2 Tim.",
            r"(?i)Eerste Petrus": "1 Petr.",
            r"(?i)Tweede Petrus": "2 Petr.",
            r"(?i)Eerste Johannes": "1 Joh.",
            r"(?i)Tweede Johannes": "2 Joh.",
            r"(?i)Derde Johannes": "3 Joh.",
        },
        "base_prompt": """
        1. THEOLOGICAL ACCURACY (Strict):
           - Use **NBV21 (Nieuwe Bijbelvertaling 2021)** for scripture references.
           - God is addressed reverently: use "U" (formal) or "Gij" (archaic/biblical).
           - Humans/Friends are addressed informally: use "jij/je" (modern Dutch standard).
           - Key theological terms:
             * "Holy Spirit" -> "Heilige Geest"
             * "Lord" -> "de Heer" / "Heer"
             * "God the Father" -> "God de Vader"
             * "Son of God" -> "Zoon van God"
             * "I AM" (God's title) -> "IK BEN"
             * "Amen" -> "Amen"

        2. SCRIPTURE PROTOCOL:
           - If the English speaker paraphrases, RECALL the official Dutch verse from NBV21.
           - Book abbreviations: Gen., Ex., Lev., Num., Deut., Joz., Richt., Ruth, 1 Sam., 2 Sam., etc.

        3. AVOID ANGLICISMS & MAINTAIN NATURAL DUTCH:
           - Dutch is NOT German - do not use German constructions.
           - Use natural Dutch word order (verb-second in main clauses).
           - Avoid literal English-to-Dutch translations that sound unnatural.
           - "Saved" (spiritual) -> "gered" or "behouden" (NOT "gespaard").
           - "Grace" -> "genade" (NOT "gratie" which means pardon/clemency).
           - "Faith" -> "geloof" (NOT "vertrouwen" unless meaning trust).
           - "Worship" -> "aanbidding" or "eredienst" (context-dependent).
           - "Blessing" -> "zegen" / "zegening".
           - "Sin" -> "zonde".
           - "Repent" -> "bekeren" / "berouw hebben".
           - "Pray" -> "bidden".
           - "Pastor" -> "Dominee" or "Predikant" (Protestant) / "Pastoor" (Catholic).

        4. DUTCH-SPECIFIC BROADCAST STYLE:
           - The Netherlands prefers direct, clear communication.
           - Use contemporary Dutch suitable for broadcast (not overly formal).
           - Maintain warmth and accessibility while being reverent for religious content.
           - Subtitles: Keep concise; Dutch can be verbose - aim for brevity.
        """
    },
}

# --- 2. THE SOUL (Persona/Program Profiles) ---
# These apply ACROSS languages.

PROFILES = {
    "standard": {
        "name": "Standard (Omega TV)",
        "tone": "Professional, Accurate, Broadcast-Quality.",
        "glossary": {} 
    },
    "in_touch": {
        "name": "In Touch (Charles Stanley)",
        "tone": "Fatherly, Teaching, Calm, Educational, Gentle.",
        "glossary": {
            "In Touch": {"is": "In Touch", "es": "En Contacto", "nl": "In Touch"},
            "Life Principles": {"is": "Lífsreglur", "es": "Principios de Vida", "nl": "Levensprincipes"},
            "Walk with God": {"is": "Ganga með Guði", "es": "Caminar con Dios", "nl": "Wandelen met God"},
            "Dr. Stanley": {"is": "Dr. Stanley", "es": "Dr. Stanley", "nl": "Dr. Stanley"}
        }
    },
    "benny_hinn": {
        "name": "Benny Hinn",
        "tone": "Dynamic, Prophetic, High-Energy, Reverent, Authoritative.",
        "glossary": {
            "Anointing": {"is": "Smurning", "es": "Unción", "nl": "Zalving"},
            "Crusade": {"is": "Trúboðsfundur", "es": "Cruzada", "nl": "Kruistocht"},
            "Presence": {"is": "Nærvera", "es": "Presencia", "nl": "Aanwezigheid"},
            "Glory": {"is": "Dýrð", "es": "Gloria", "nl": "Glorie"},
            "Touch": {"is": "Snerting", "es": "Toque", "nl": "Aanraking"}
        }
    }
}

def get_system_instruction(lang_code="is", profile_key="standard", extra_terms=None):
    """
    Composes the final System Prompt by merging Language Physics + Persona Soul.
    
    Args:
        lang_code: Target language code (e.g., "is", "es")
        profile_key: Program profile key (e.g., "standard", "in_touch")
        extra_terms: Optional dict of additional terms from job-specific termbook.
                     Format: {"source_term": "translated_term", ...}
                     These override profile terms for per-job customization.
    """
    lang = LANGUAGES.get(lang_code, LANGUAGES["is"])
    profile = PROFILES.get(profile_key, PROFILES["standard"])
    
    # Build Glossary for this specific language
    # We extract only the terms relevant to the target language
    active_glossary = {}
    for term, translations in profile["glossary"].items():
        if lang_code in translations:
            active_glossary[term] = translations[lang_code]
        else:
            # Fallback to English/Key if translation missing
            active_glossary[term] = term
    
    # Merge extra terms from job-specific termbook (overrides profile terms)
    if extra_terms and isinstance(extra_terms, dict):
        active_glossary.update(extra_terms)

    prompt = f"""
    ROLE: You are the Lead Translator for Omega TV.
    CURRENT PROGRAM: **{profile['name']}**
    TARGET LANGUAGE: **{lang['name']}**
    
    --- TONE & STYLE (The Soul) ---
    Tone: {profile['tone']}
    
    --- LANGUAGE RULES (The Physics) ---
    {lang['base_prompt']}
    
    --- GLOSSARY (Strict Terminology) ---
    {json.dumps(active_glossary, indent=2, ensure_ascii=False)}
    
    --- UNIVERSAL RULES ---
    1. MUSIC: If a segment is purely singing/lyrics or instrumental with no speech, output `{lang['music_prompt']}`. If speech is present over music (e.g., organ under speech), translate the speech and do NOT output `{lang['music_prompt']}`.
    2. FORMAT: Return JSON array matching input IDs.
    3. BREVITY (Broadcast): Prefer concise, natural phrasing; remove filler; keep sentences tight to reduce CPS.
    4. CAPITALIZATION (Broadcast): Use normal sentence case (not ALL CAPS). If the source segment is ALL CAPS, convert it to natural casing. Preserve acronyms/initialisms (e.g., USA, TV, I-690), Bible abbreviations, and mandatory theological titles (e.g., ÉG ER / YO SOY).
    5. ASR CLEANUP: If the source contains an obvious speech-to-text error and the intended word is clear, fix it before translating. If unsure, keep the original wording.
    """
    
    return prompt

# --- 3. THE POLITICS (Delivery Policies) ---
# Defaults for Dubbing vs. Subtitling based on region.

LANGUAGE_POLICIES = {
    # Subtitling Markets (Scandinavia, Benelux, etc.)
    "is": {"mode": "sub", "voice": "alloy"},
    "en": {"mode": "sub", "voice": "alloy"}, # SDH
    "no": {"mode": "sub", "voice": "alloy"},
    "sv": {"mode": "sub", "voice": "alloy"},
    "da": {"mode": "sub", "voice": "alloy"},
    "nl": {"mode": "sub", "voice": "alloy"},
    "pt": {"mode": "sub", "voice": "onyx"}, # Portugal (European)
    
    # Dubbing Markets (DACH, Romance, LatAm)
    "es": {"mode": "dub", "voice": "echo"}, 
    "fr": {"mode": "dub", "voice": "shimmer"}, 
    "de": {"mode": "dub", "voice": "onyx"}, 
    "it": {"mode": "dub", "voice": "fable"},
    "ru": {"mode": "dub", "voice": "echo"},
}

def get_language_policy(lang_code):
    """Returns the default delivery policy (mode, voice) for a language."""
    # Default to Subtitling if unknown (safest)
    return LANGUAGE_POLICIES.get(lang_code, {"mode": "sub", "voice": "alloy"})


# =============================================================================
# ENHANCED TRANSLATION PIPELINE HELPERS (for 3-step process)
# =============================================================================

def get_language_profile(lang_code: str) -> dict:
    """
    Returns the full language profile for a given language code.
    Used by Claude and review portal for language-specific validation.
    """
    return LANGUAGES.get(lang_code, LANGUAGES.get("en", {}))


def get_error_detection_prompt(lang_code: str, profile_key: str = "standard") -> str:
    """
    Generates a focused error-detection prompt for Step 2 (QA Pass).

    This is intentionally DIFFERENT from the translation prompt to avoid
    rubber-stamping. The model is tasked with FINDING ERRORS, not creating.
    """
    lang = LANGUAGES.get(lang_code, LANGUAGES["is"])
    profile = PROFILES.get(profile_key, PROFILES["standard"])

    # Extract key error categories for this language
    error_categories = _get_language_error_categories(lang_code)

    return f"""
ROLE: You are the QA Reviewer for Omega TV translations.
Your job is to FIND AND FIX ERRORS. Do not create new translations.

TARGET LANGUAGE: {lang['name']}
BIBLE VERSION: {lang['bible']}
GOD ADDRESS: {lang['god_address']}
HUMAN ADDRESS: {lang['human_address']}

--- ERROR CATEGORIES TO CHECK ---
{error_categories}

--- YOUR TASK ---
For each segment, check if the DRAFT correctly represents the SOURCE.
If correct: Return the draft unchanged.
If wrong: Fix it and explain why in your reasoning.

CRITICAL RULES:
1. Do NOT add information not in the source.
2. Do NOT remove meaningful content from the source.
3. Do NOT change correct translations just for style preference.
4. ONLY change things that are WRONG (mistranslation, missing, wrong register).

Return JSON array: [{{"id": <int>, "text": <fixed_or_unchanged>}}]
"""


def _get_language_error_categories(lang_code: str) -> str:
    """
    Returns language-specific error categories for QA checking.
    These are the common mistakes for each language.
    """
    categories = {
        "is": """
1. REGISTER ERRORS:
   - God must be "Þú" (NOT "Þér" which is too formal)
   - "I AM" (God's title) must be "ÉG ER" (capitalized)

2. ANGLICISMS (very common):
   - "Died for you" → "Dó vegna þín" (NOT literal "Dó fyrir þig")
   - "On fire" → "Brennandi" (NOT "á eldi")
   - Avoid "Við höfum fengið" for states → Use "Það hefur verið"

3. THEOLOGICAL TERMS:
   - "Pastor" → "Prestur"
   - "Saved" → "Frelsaður" (NOT "sparaður")
   - Scripture from Biblían 2007

4. MISSING/ADDED CONTENT:
   - Verify all source information is present
   - Verify nothing was added that's not in source
""",
        "nl": """
1. REGISTER ERRORS:
   - God: "U" or "Gij" (formal/reverent)
   - Humans: "jij/je" (informal modern Dutch)
   - NEVER mix these up

2. THEOLOGICAL TERMS (critical):
   - "Grace" → "genade" (NOT "gratie" = clemency)
   - "Faith" → "geloof" (NOT "vertrouwen" unless meaning trust)
   - "Saved" → "gered/behouden" (NOT "gespaard")
   - "Worship" → "aanbidding" or "eredienst"

3. GERMAN CONFUSION:
   - Dutch is NOT German - avoid German constructions
   - Use Dutch word order (verb-second in main clauses)

4. MISSING/ADDED CONTENT:
   - Verify all source information is present
   - Verify nothing was added that's not in source
""",
        "es": """
1. REGISTER ERRORS:
   - God: "Tú" (capitalized, reverent)
   - Titles: "YO SOY", "Señor", "Espíritu Santo"

2. BIBLE VERSION:
   - Use Reina-Valera 1960 terminology

3. REGIONAL NEUTRALITY:
   - Use neutral Latin American Evangelical Standard
   - Avoid country-specific slang

4. MISSING/ADDED CONTENT:
   - Verify all source information is present
""",
        "de": """
1. REGISTER ERRORS:
   - God: reverent second-person (capitalize where customary)
   - Use formal register for religious content

2. BIBLE VERSION:
   - Use Luther 2017 terminology

3. COMPOUND WORDS:
   - German creates compounds - ensure correct spelling

4. MISSING/ADDED CONTENT:
   - Verify completeness
""",
        "pt": """
1. REGISTER ERRORS:
   - God: reverent second-person
   - Use Almeida Revista e Atualizada (ARA)

2. THEOLOGICAL TERMS:
   - Match ARA Bible terminology

3. MISSING/ADDED CONTENT:
   - Verify completeness
""",
        "fr": """
1. REGISTER ERRORS:
   - God: reverent second-person
   - Use Louis Segond 1910 terminology

2. FORMAL VS INFORMAL:
   - Religious content should use formal register

3. MISSING/ADDED CONTENT:
   - Verify completeness
""",
    }

    # Default for new languages
    default = """
1. REGISTER ERRORS:
   - Check formal vs informal address is appropriate
   - God should be addressed reverently

2. THEOLOGICAL TERMS:
   - Verify religious terminology is accurate
   - Use the correct Bible version for this language

3. MISSING/ADDED CONTENT:
   - Verify all source information is present
   - Verify nothing was added that's not in source
"""

    return categories.get(lang_code, default)


def get_claude_polish_context(lang_code: str, profile_key: str = "standard") -> dict:
    """
    Returns all context Claude needs for Step 3 Polish.
    This includes the full language rules that Gemini used.
    """
    lang = LANGUAGES.get(lang_code, LANGUAGES["is"])
    profile = PROFILES.get(profile_key, PROFILES["standard"])

    return {
        "language_name": lang["name"],
        "language_code": lang_code,
        "bible_version": lang["bible"],
        "god_address": lang["god_address"],
        "human_address": lang["human_address"],
        "music_prompt": lang["music_prompt"],
        "base_rules": lang["base_prompt"],
        "error_categories": _get_language_error_categories(lang_code),
        "program_name": profile["name"],
        "program_tone": profile["tone"],
    }


def get_entity_anchors(entities: list, lang_code: str) -> dict:
    """
    Converts ElevenLabs entity detection results into translation anchors.

    These anchors ensure consistent translation of names and places
    across all chunks of a document.

    Args:
        entities: List of detected entities from transcription
                  Format: [{"text": "Jerusalem", "type": "location_city"}, ...]
        lang_code: Target language code

    Returns:
        Dict of entity -> suggested translation (or keep-as-is marker)
    """
    anchors = {}

    # Entity types that should generally be kept as-is
    keep_as_is_types = {
        "name_given",      # First names: Erik, John
        "name_family",     # Last names: Stanley, Graham
        "name_full",       # Full names
        "organization",    # CBN, 700 Club
        "product",         # Product names
    }

    # Entity types that may need translation
    translatable_types = {
        "location_country",
        "location_city",
        "location",
        "origin",  # "Icelandic", "American"
    }

    # Known translations for common religious locations
    location_translations = {
        "is": {
            "Jerusalem": "Jerúsalem",
            "Bethlehem": "Betlehem",
            "Galilee": "Galíleu",
            "Israel": "Ísrael",
            "Egypt": "Egyptaland",
            "Jordan": "Jórdan",
        },
        "nl": {
            "Jerusalem": "Jeruzalem",
            "Bethlehem": "Betlehem",
            "Galilee": "Galilea",
            "Israel": "Israël",
            "Egypt": "Egypte",
            "Jordan": "Jordaan",
        },
        "es": {
            "Jerusalem": "Jerusalén",
            "Bethlehem": "Belén",
            "Galilee": "Galilea",
            "Israel": "Israel",
            "Egypt": "Egipto",
            "Jordan": "Jordán",
        },
        "de": {
            "Jerusalem": "Jerusalem",
            "Bethlehem": "Bethlehem",
            "Galilee": "Galiläa",
            "Israel": "Israel",
            "Egypt": "Ägypten",
            "Jordan": "Jordanien",
        },
    }

    lang_locations = location_translations.get(lang_code, {})

    for entity in entities:
        text = entity.get("text", "").strip()
        etype = entity.get("type", "")

        if not text:
            continue

        # Names: keep as-is (don't translate "Dr. Stanley" to "Dr. Standardur")
        if etype in keep_as_is_types:
            anchors[text] = text  # Explicit keep-as-is

        # Locations: check if we have a known translation
        elif etype in translatable_types:
            if text in lang_locations:
                anchors[text] = lang_locations[text]
            # For unknown locations, let the model decide but flag it
            # (We don't add to anchors, model handles it)

    return anchors

