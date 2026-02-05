"""
Literati Orchestrator.

Coordinating the 3-step book translation pipeline:
1. Terminologist (Naming Guide)
2. Drafter (Step 1 - Claude)
3. Critic (Step 2 - Gemini)
4. Polisher (Step 3 - Claude)
"""

import logging
from typing import Dict, Any, Optional

import omega_db
from workers.literati import terminologist, drafter, critic, polisher

logger = logging.getLogger("Literati.Orchestrator")

def translate_chapter(chapter_id: str, force_restart: bool = False) -> Dict[str, str]:
    """
    Orchestrate full 3-step translation pipeline for a single chapter.
    """
    logger.info(f"Starting Literati pipeline for chapter {chapter_id}")

    # 1. Fetch Data
    chapter = omega_db.get_book_chapter(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter {chapter_id} not found")

    book = omega_db.get_book_project(chapter['book_id'])
    if not book:
        raise ValueError(f"Book {chapter['book_id']} not found")

    # 2. Build Context
    book_context = {
        'book_title': book.get('title'),
        'author': book.get('author'),
        'genre': book.get('meta', {}).get('genre', 'general'),
        'glossary': book.get('glossary') or {},
        'style_guide': book.get('style_guide', 'Standard'),
        'character_bible': book.get('character_bible') or {},
    }
    
    source_text = chapter.get('source_text')
    if not source_text:
        raise ValueError(f"Chapter {chapter_id} has no source text")

    # 3. Terminologist (Naming Guide)
    # We generate this fresh each time for Step 1 context
    term_agent = terminologist.Terminologist()
    # Extract terms from glossary to build guide
    glossary_terms = []
    raw_glossary = book_context['glossary']
    # Normalize glossary structure (it's complex in book_extractor, simplistic in DB sometimes)
    # We'll just look for 'characters', 'places' lists if present, or flat list
    if isinstance(raw_glossary, dict):
        glossary_terms.extend(raw_glossary.get('characters', []))
        glossary_terms.extend(raw_glossary.get('places', []))
    
    naming_guide = term_agent.generate_naming_guide(glossary_terms)

    # 4. Step 1: Draft
    if force_restart or not chapter.get('step1_translation'):
        logger.info(f"Step 1: Drafting {chapter_id}")
        step1_text = drafter.run(chapter_id, source_text, book_context, naming_guide)
        
        omega_db.update_chapter_translation(
            chapter_id=chapter_id,
            step=1,
            translation_text=step1_text,
            status="Step 1 Complete"
        )
    else:
        step1_text = chapter['step1_translation']
        logger.info("Step 1: Using cached draft")

    # 5. Step 2: Critic
    if force_restart or not chapter.get('step2_theology_review'):
        logger.info(f"Step 2: Critiquing {chapter_id}")
        step2_text = critic.run(chapter_id, source_text, step1_text, book_context)
        
        omega_db.update_chapter_translation(
            chapter_id=chapter_id,
            step=2,
            translation_text=step2_text,
            status="Step 2 Complete"
        )
    else:
        step2_text = chapter['step2_theology_review']
        logger.info("Step 2: Using cached critique")

    # 6. Step 3: Polish
    if force_restart or not chapter.get('step3_polish'):
        logger.info(f"Step 3: Polishing {chapter_id}")
        step3_text = polisher.run(chapter_id, source_text, step2_text, book_context)
        
        omega_db.update_chapter_translation(
            chapter_id=chapter_id,
            step=3,
            translation_text=step3_text,
            status="Translation Complete"
        )
        # Mark final
        omega_db.update_chapter_translation(
            chapter_id=chapter_id,
            stage="COMPLETE",
            final_text=step3_text
        )
    else:
        step3_text = chapter['step3_polish']
        logger.info("Step 3: Using cached polish")

    return {
        'step1': step1_text,
        'step2': step2_text,
        'step3': step3_text,
        'final': step3_text
    }

def translate_book(book_id: str):
    """Loop through chapters."""
    chapters = omega_db.get_book_chapters(book_id)
    for ch in chapters:
        try:
            translate_chapter(ch['id'])
        except Exception as e:
            logger.error(f"Failed chapter {ch['id']}: {e}")
