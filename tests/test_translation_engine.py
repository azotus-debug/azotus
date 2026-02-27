import sys
import os
import json
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from workers.translation_engine import TranslationEngine, TranslationParagraph

def test_prompt_construction():
    # Mock Paragraph
    segments = [
        {"id": "s1", "start": 0.0, "end": 2.0, "text": "Hello, welcome to Omega TV."},
        {"id": "s2", "start": 2.0, "end": 5.0, "text": "We are so glad you could join us today for this special broadcast."}, # 3s duration -> ~45 chars max (Icelandic)
        {"id": "s3", "start": 5.0, "end": 6.0, "text": "Amen."}
    ]
    
    paragraph = TranslationParagraph(
        id="p1", 
        segments=segments, 
        start_time=0.0, 
        end_time=6.0, 
        speaker="Host", 
        context_tags=["[SCENE: Studio]", "<music_intro>"]
    )
    
    engine = TranslationEngine(model_name="mock-model")
    
    # Test Icelandic (Strict CPS)
    print("\n--- ICELANDIC PROMPT ---")
    prompt_is = engine._build_prompt(paragraph, "is", "standard")
    print(prompt_is)
    
    # Check for Constraints
    if "MAX CPS: 15" in prompt_is:
        print("✅ Icelandic CPS limit (15) found.")
    else:
        print("❌ Icelandic CPS limit DEFAULTED or MISSING.")

    if "HIERARCHY OF OMISSION" in prompt_is:
        print("✅ Omission Hierarchy found.")
    else:
        print("❌ Omission Hierarchy MISSING.")

    # Test English (Higher CPS)
    print("\n--- ENGLISH PROMPT ---")
    prompt_en = engine._build_prompt(paragraph, "en", "standard")
    
    if "MAX CPS: 17" in prompt_en:
        print("✅ English CPS limit (17) found.")
    else:
        print("❌ English CPS limit MISSING.")

if __name__ == "__main__":
    test_prompt_construction()
