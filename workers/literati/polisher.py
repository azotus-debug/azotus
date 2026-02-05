"""
Literati Step 3: Polisher Agent.

Role:
- The "Editor-in-Chief".
- Uses Claude Opus 4.5 for final stylistic polish.
- Focuses on flow, register, and removing subtle Anglicisms.
"""

import logging
import json
import os
import anthropic
import config
from typing import Dict, Any

logger = logging.getLogger("Literati.Polisher")

CLAUDE_MODEL = getattr(config, "OMEGA_CLAUDE_MODEL", "claude-opus-4-5-20251101")

def run(chapter_id: str, source_text: str, reviewed_text: str, book_context: Dict[str, Any]) -> str:
    """
    Step 3: Final Polish using Claude Opus 4.5.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY") or config.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set for Polisher (Step 3)")

    client = anthropic.Anthropic(api_key=api_key)
    logger.info(f"Polishing chapter {chapter_id} with {CLAUDE_MODEL}")

    system_prompt = """ROLE: You are Bókaritstjóri — Iceland's most celebrated literary editor.

MISSION: Transform this translation into prose worthy of Iceland's finest publishers.

FOCUS AREAS:
1. GRAMMAR: Absolute perfection in case endings and agreement.
2. FLOW: Vary sentence length and rhythm. Avoid monotony.
3. ANGLICISMS: Ruthlessly remove English sentence structures or calques.
4. EMOTION: Ensure the Icelandic carries the same weight and nuance as the original.
"""

    char_bible = book_context.get('character_bible', {})
    char_bible_json = json.dumps(char_bible, indent=2, ensure_ascii=False) if char_bible else "None"

    user_prompt = f"""ORIGINAL ENGLISH (Reference):
{source_text}

CURRENT DRAFT (Icelandic):
{reviewed_text}

CHARACTER VOICES:
{char_bible_json}

Polish this text to publication quality. Output ONLY the polished Icelandic text."""

    from workers.literati.literati_utils import retry_api_call

    # Define a protected generation function
    @retry_api_call(max_retries=5, retry_exceptions=(anthropic.APIError, anthropic.RateLimitError, anthropic.APIStatusError))
    def _generate_polish_protected(sys_prompt, usr_prompt):
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=32000,
            temperature=0.3,
            system=sys_prompt,
            messages=[{"role": "user", "content": usr_prompt}]
        )

    try:
        response = _generate_polish_protected(system_prompt, user_prompt)
        
        if not response.content:
            raise ValueError("Empty response from Claude")
            
        return response.content[0].text.strip()
        
    except Exception as e:
        logger.error(f"Polisher failed: {e}")
        raise
