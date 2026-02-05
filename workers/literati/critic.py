"""
Literati Step 2: Critic Agent.

Role:
- The "Theologian" and "Grammar Policeman".
- Uses Gemini 3 Pro (best for logic/fact-checking).
- Reviews the draft for doctrinal accuracy and Biblical citation compliance.
"""

import logging
import json
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig, SafetySetting, HarmCategory, HarmBlockThreshold
import config
from typing import Dict, Any

logger = logging.getLogger("Literati.Critic")

# Use the Editor Model (Gemini) defined in config
GEMINI_MODEL = getattr(config, "MODEL_EDITOR", "gemini-3.0-pro-preview")
PROJECT_ID = config.OMEGA_CLOUD_PROJECT
LOCATION = getattr(config, "GEMINI_LOCATION", "global")

# Init Vertex AI lazy to avoid import time cost if not used
_INIT_DONE = False

def _ensure_init():
    global _INIT_DONE
    if not _INIT_DONE:
        try:
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            _INIT_DONE = True
        except Exception as e:
            logger.warning(f"Vertex AI init failed: {e}")

def run(chapter_id: str, source_text: str, draft_text: str, book_context: Dict[str, Any]) -> str:
    """
    Step 2: Theological Review using Gemini 3 Pro.
    """
    # Genre check
    THEOLOGICAL_GENRES = {'theology', 'ministry', 'devotional', 'biblical', 'christian', 'spiritual'}
    genre = str(book_context.get('genre', '')).lower()
    if genre not in THEOLOGICAL_GENRES:
        logger.info(f"Skipping Critic (Genre '{genre}' is not theological)")
        return draft_text

    _ensure_init()
    logger.info(f"Critiquing chapter {chapter_id} with {GEMINI_MODEL}")

    system_prompt = """ROLE: You are Guðfræðiritari — a theological editor for Icelandic Christian publications.

MISSION: Review the Icelandic translation for theological accuracy. Correct any doctrinal 
errors, ensure Scripture quotes match the Icelandic Bible (Biblían 2007), and verify ministry terminology.

KEY RULES:
1. SCRIPTURE: Direct quotes MUST match Biblían 2007 exactly.
2. DIVINE PRONOUNS: God is "Hann" (masc), addressed as "Þú" (singular).
3. TERMINOLOGY: Use standard Icelandic church terms (e.g. "Náð" for Grace).
"""

    glossary = book_context.get('glossary', {})
    glossary_json = json.dumps(glossary, indent=2, ensure_ascii=False) if glossary else "None"

    user_prompt = f"""SOURCE TEXT (English):
{source_text}

DRAFT TRANSLATION (Icelandic):
{draft_text}

GLOSSARY (Authoritative):
{glossary_json}

Review the translation for theological accuracy. Correct errors in doctrine, scripture citations, or terminology.
Return the COMPLETE corrected text."""

    from workers.literati.literati_utils import retry_api_call
    from google.api_core import exceptions as google_exceptions

    # Define protected call
    # Vertex AI throws ResourceExhausted for 429s, ServiceUnavailable/InternalServerError for 5xx
    @retry_api_call(max_retries=5, retry_exceptions=(google_exceptions.ResourceExhausted, google_exceptions.ServiceUnavailable, google_exceptions.InternalServerError, google_exceptions.GoogleAPICallError))
    def _generate_critique_protected(prompt_text):
        # Re-init model each time or reuse if safe? GenerativeModel is lightweight.
        model = GenerativeModel(GEMINI_MODEL)
        return model.generate_content(
            prompt_text,
            generation_config=GenerationConfig(
                temperature=0.2, # Low temp for precision
                max_output_tokens=32000,
            ),
            safety_settings=[
                SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
                SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
                SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
                SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
            ]
        )

    try:
        response = _generate_critique_protected(f"{system_prompt}\n\n{user_prompt}")
        return response.text.strip()
    except Exception as e:
        logger.error(f"Critic failed: {e}")
        raise
