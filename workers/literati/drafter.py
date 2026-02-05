"""
Literati Step 1: Drafter Agent.

Role:
- The "Translator".
- Uses Claude Opus 4.5 to create the initial literary draft.
- Incorporates the Terminologist's naming guide.
"""

import logging
import json
import os
import anthropic
import config
from typing import Dict, Any

logger = logging.getLogger("Literati.Drafter")

# Use the same model config as the Polisher for consistency/quality
CLAUDE_MODEL = getattr(config, "OMEGA_CLAUDE_MODEL", "claude-opus-4-5-20251101")

def run(chapter_id: str, source_text: str, book_context: Dict[str, Any], naming_guide: str = "") -> str:
    """
    Step 1: Literary Draft using Claude Opus 4.5.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY") or config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set for Drafter (Step 1)")
    
    client = anthropic.Anthropic(api_key=api_key)
    
    logger.info(f"Drafting chapter {chapter_id} with {CLAUDE_MODEL}")

    system_prompt = """ROLE: You are Bókaþýðandi — a master literary translator specializing in English → Icelandic 
translation for published books. You translate for Iceland's most discerning readers.

MISSION: Create a faithful Icelandic translation that preserves the author's unique voice, 
literary style, and emotional resonance. This is not subtitle work — you are crafting 
prose that will be printed and read."""

    # We append the Morphology/Style rules from the original prompt to ensure quality
    # (Abbreviated here for clarity, but in production we'd use the full prompt context from book_translator)
    system_prompt += """
    
    CORE PRINCIPLES:
    1. PRESERVE VOICE: Capture the author's exact tone (lyrical, punchy, academic, etc).
    2. IDIOMATIC ICELANDIC: Never translate idioms literally. Use native Icelandic equivalents.
    3. SENTENCE STRUCTURE: Use natural V2 word order. Do not mimic English syntax.
    4. NO ANGLICISMS: Avoid "Það er" structures and English loan phrases.
    """

    # Format inputs
    glossary = book_context.get('glossary', {})
    glossary_json = json.dumps(glossary, indent=2, ensure_ascii=False) if glossary else "None"
    
    style_guide = book_context.get('style_guide', 'Literary')
    
    user_prompt = f"""BOOK CONTEXT:
Title: {book_context.get('book_title')}
Author: {book_context.get('author')}

STYLE GUIDE:
{style_guide}

NAMING & GRAMMAR GUIDE (From Terminologist):
{naming_guide}

GLOSSARY (Strict Adherence):
{glossary_json}

SOURCE TEXT:
{source_text}

Translate the text above into Icelandic. Output ONLY the translation."""

    from workers.literati.literati_utils import retry_api_call

    # Define a protected generation function
    @retry_api_call(max_retries=5, retry_exceptions=(anthropic.APIError, anthropic.RateLimitError, anthropic.APIStatusError))
    def _generate_draft_protected(sys_prompt, usr_prompt):
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=32000,
            temperature=0.4,
            system=sys_prompt,
            messages=[{"role": "user", "content": usr_prompt}]
        )

    try:
        response = _generate_draft_protected(system_prompt, user_prompt)
        
        if not response.content:
            raise ValueError("Empty response from Claude")
            
        return response.content[0].text.strip()
        
    except Exception as e:
        logger.error(f"Drafter failed: {e}")
        raise
