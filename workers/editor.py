import os
import json
import shutil
import logging
from pathlib import Path
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
import config
import omega_db
from subtitle_standards import build_priority_context

logger = logging.getLogger("OmegaManager.Editor")
def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = config.BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

def review(translation_path: Path):
    """
    Reviews the translation using Gemini 3 Pro.
    Returns path to _APPROVED.json.
    """
    if not ensure_credentials():
        raise Exception("Google Credentials not found")

    # Infer stem and language from filename
    # Expected format: {stem}_{LANG}.json
    parts = translation_path.stem.split("_")
    if len(parts) >= 2:
        lang_suffix = parts[-1]
        stem = "_".join(parts[:-1])
    else:
        # Fallback
        stem = translation_path.stem.replace("_ICELANDIC", "")
        lang_suffix = "ICELANDIC"
        
    logger.info(f"🕵️‍♂️ Starting Review: {stem} ({lang_suffix})")
    
    with open(translation_path, 'r') as f: data = json.load(f)
    
    if "source_data" not in data or "translated_data" not in data:
        raise ValueError("Invalid file format: Missing source/translated data")
        
    source = data["source_data"]
    translation = data["translated_data"]
    
    # Re-init for Global (Required for Gemini 3 Preview)
    vertexai.init(project=config.OMEGA_CLOUD_PROJECT, location=config.GEMINI_LOCATION)
    
    # Use model from config
    model_name = config.MODEL_EDITOR
    
    logger.info(f"💎 Connecting to {model_name}...")
    model = GenerativeModel(model_name)
    
    priority_context = build_priority_context(source, translation, include_tight=True)

    if lang_suffix.upper() in ["ICELANDIC", "IS"]:
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.
        
        YOUR TASK: 
        Review the Icelandic translation against the English source. 
        You are looking for "Robot Mistakes," theological errors, and awkward phrasing.
        
        STRICT RULES:
        1. "Þú" vs "Þér": God is addressed as "Þú" (do NOT use "Þér").
        2. NO ANGLICISMS: 
           - Reject "fyrir þig" (used for "died for you"). Use "vegna þín".
           - Reject "á eldi" (on fire). Use "brennandi".
           - Reject "Bless" if used for impartation. Use "Guð blessi þig".
        3. TERMINOLOGY:
           - "Partners" -> "Bakhjarlar".
           - "I AM" -> "ÉG ER".
           - "Covenant" -> "Sáttmáli".
           - "Pastor" -> "Prestur".
        4. CAPITALIZATION (Broadcast):
           - Treat ALL CAPS as a robot mistake; convert to normal sentence case.
           - Preserve acronyms/initialisms (USA, TV, I-690) and the mandatory title "ÉG ER".
        5. ASR CLEANUP:
           - Fix obvious speech-to-text errors in the SOURCE when the intended word is clear.
           - If uncertain, leave the original wording.
        6. MUSIC VS SPEECH:
           - If the SOURCE contains spoken content (not a pure music marker), the translation must NOT be "(MUSIC)" or blank.
        7. TECHNICAL CONSTRAINTS (Broadcast):
           - Each segment has max 2 lines (<=42 chars each; <=84 total).
           - Use the provided `effective_duration`, `gap_to_next`, and `current_cps` to keep CPS <= 17.
           - If status is TIGHT, shorten only if it improves CPS without losing meaning.
           - If status is CRITICAL, you MUST shorten while preserving theology.
           - If shortening would change theology or remove Scripture, keep meaning and note it in `reason`.
        8. CONTEXT WINDOW:
           - `context_prev` / `context_next` are read-only.
           - Use them to maintain gender/case agreement in Icelandic.
           
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT (Icelandic) ---
        {json.dumps(translation, ensure_ascii=False)}

        --- PRIORITY SEGMENTS (Constraint-Aware Window) ---
        {json.dumps(priority_context, ensure_ascii=False)}
        
        OUTPUT: 
        Return a JSON object with 'corrections' and a 'report'.
        
        Format: 
        {{ 
            "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation" }} ],
            "report": {{
                "rating": 8.5,  // 1-10 Score (Float)
                "quality_tier": "Broadcast Ready", // "Broadcast Ready", "Needs Minor Polish", "Needs Review", "Draft"
                "summary": "Brief analysis of the translation quality.",
                "major_issues": ["Anglicisms", "Theological Errors"], // List of strings
                "suggestions": "Actionable advice for the translator."
            }}
        }}
        """
    elif lang_suffix.upper() in ["DUTCH", "NL"]:
        # Dutch-specific Editor Prompt
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.

        YOUR TASK:
        Review the Dutch translation against the English source.
        You are looking for "Robot Mistakes," theological errors, Anglicisms, and unnatural phrasing.

        STRICT RULES FOR DUTCH:
        1. REGISTER CONSISTENCY:
           - God is addressed as "U" or "Gij" (formal, reverent)
           - Humans are addressed as "jij"/"je" (informal, modern)
           - Do NOT mix registers within the same context

        2. NO ANGLICISMS - These are CRITICAL errors:
           - "gratie" -> use "genade" (grace)
           - "passie" (for suffering) -> use "lijden"
           - "community" -> use "gemeenschap" or "gemeente"
           - "level" -> use "niveau"
           - "relaten" -> use "verhalen tot" or rephrase
           - Do NOT literally translate English idioms

        3. THEOLOGICAL TERMINOLOGY (NBV21 Standard):
           - "Holy Spirit" -> "Heilige Geest" (with article "de")
           - "the Lord" -> "de Heer" (ALWAYS with article)
           - "I AM" -> "IK BEN" (capitalized in divine context)
           - "salvation" -> "verlossing" (NOT "redding" in spiritual context)
           - "grace" -> "genade" (NEVER "gratie")
           - "faith" -> "geloof"
           - "covenant" -> "verbond"
           - "congregation" -> "gemeente"

        4. GRAMMAR & WORD ORDER:
           - Dutch uses SVO order (Subject-Verb-Object), NOT German-style verb-final
           - Check separable verbs: "op|staan" parts should be semantically connected
           - Compound words are written as ONE word: "aardbeienjam" NOT "aardbeien jam"
           - Check article gender: "de" (common) vs "het" (neuter)

        5. CAPITALIZATION (Broadcast):
           - Treat ALL CAPS as a robot mistake; convert to normal sentence case
           - Preserve acronyms/initialisms (USA, TV) and divine titles (IK BEN)

        6. ASR CLEANUP:
           - Fix obvious speech-to-text errors in the SOURCE when the intended word is clear
           - If uncertain, leave the original wording

        7. MUSIC VS SPEECH:
           - If the SOURCE contains spoken content, translation must NOT be "(MUZIEK)" or blank

        8. TECHNICAL CONSTRAINTS (Broadcast):
           - Each segment has max 2 lines (<=42 chars each; <=84 total)
           - Target CPS (characters per second) <= 15 for Dutch (slower than English)
           - If status is TIGHT, shorten only if it improves CPS without losing meaning
           - If status is CRITICAL, you MUST shorten while preserving theology

        9. CONTEXT WINDOW:
           - `context_prev` / `context_next` are read-only
           - Use them to maintain grammatical agreement and flow

        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}

        --- DRAFT (Dutch) ---
        {json.dumps(translation, ensure_ascii=False)}

        --- PRIORITY SEGMENTS (Constraint-Aware Window) ---
        {json.dumps(priority_context, ensure_ascii=False)}

        OUTPUT:
        Return a JSON object with 'corrections' and a 'report'.

        Format:
        {{
            "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation" }} ],
            "report": {{
                "rating": 8.5,  // 1-10 Score (Float)
                "quality_tier": "Broadcast Ready", // "Broadcast Ready", "Needs Minor Polish", "Needs Review", "Draft"
                "summary": "Brief analysis of the translation quality.",
                "major_issues": ["Anglicisms", "Theological Errors"], // List of strings
                "suggestions": "Actionable advice for the translator."
            }}
        }}
        """
    else:
        # Generic / Spanish Editor
        prompt = f"""
        ROLE: You are the Chief Editor and Quality Control Auditor for Omega TV.

        YOUR TASK:
        Review the {lang_suffix} translation against the English source.
        Ensure flow, grammar, and theological accuracy.

        STRICT RULES:
        - CAPITALIZATION (Broadcast): Do NOT allow ALL CAPS sentences; convert to natural sentence case while preserving acronyms/initialisms and mandatory titles (e.g., ÉG ER / YO SOY).
        - ASR CLEANUP: Fix obvious speech-to-text errors in the SOURCE when the intended word is clear. If uncertain, leave the original wording.
        - MUSIC VS SPEECH: If the SOURCE contains spoken content (not a pure music marker), the translation must NOT be "(MUSIC)" or blank.
        - TECHNICAL CONSTRAINTS: Use provided `effective_duration`, `gap_to_next`, and `current_cps` in the priority list to keep CPS <= 17; if CRITICAL, shorten without losing meaning.
        - CONTEXT WINDOW: `context_prev` / `context_next` are read-only; use them for grammatical agreement.
        
        INPUT DATA:
        --- SOURCE (English) ---
        {json.dumps(source, ensure_ascii=False)}
        
        --- DRAFT ({lang_suffix}) ---
        {json.dumps(translation, ensure_ascii=False)}

        --- PRIORITY SEGMENTS (Constraint-Aware Window) ---
        {json.dumps(priority_context, ensure_ascii=False)}
        
        OUTPUT: 
        Return a JSON object with 'corrections' and a 'report'.
        
        Format: 
        {{ 
            "corrections": [ {{ "id": 10, "fix": "Corrected Text", "reason": "Explanation of error" }} ],
            "report": {{
                "rating": 8.0,
                "quality_tier": "Broadcast Ready",
                "summary": "Brief analysis.",
                "major_issues": [],
                "suggestions": ""
            }}
        }}
        """

    try:
        response = model.generate_content(
            prompt, 
            generation_config=GenerationConfig(response_mime_type="application/json", temperature=0.1)
        )
        
        # Parse Response
        try:
            text = response.text.replace("```json", "").replace("```", "").strip()
            result = json.loads(text)
            corrections = result.get("corrections", [])
            report = result.get("report", {})
            
            # Save Report to DB
            if report:
                omega_db.update_job_via_track(stem, editor_report=json.dumps(report))
                logger.info(f"📝 Editor Report: {report.get('rating')}/10 - {report.get('quality_tier')}")
                
        except Exception as e:
            logger.error(f"Failed to parse editor response: {e}")
            logger.error(f"Raw response: {response.text}")
            corrections = []

        final_segments = translation # Start with draft
        
        if corrections:
            logger.info(f"🛠️ Applied {len(corrections)} fixes.")
            # Apply corrections to the draft
            # Corrections format: {id, fix, reason}
            correction_map = {c['id']: c['fix'] for c in corrections}
            
            for seg in final_segments:
                seg_id = seg.get('id')
                if seg_id in correction_map:
                    seg['text'] = correction_map[seg_id]
        else:
            logger.info("✅ Perfect translation. No fixes needed.")

        # CRITICAL FIX: Merge Timestamps AND Source Text from Source
        # Translator output often lacks start/end, so we must re-attach them from source.
        source_map = {
            s["id"]: {
                "start": s.get("start"),
                "end": s.get("end"),
                "text": s.get("text"),
                "words": s.get("words"),
            }
            for s in source
        }
        
        for seg in final_segments:
            seg_id = seg.get('id')
            if seg_id in source_map:
                seg["start"] = source_map[seg_id]["start"]
                seg["end"] = source_map[seg_id]["end"]
                seg["source_text"] = source_map[seg_id]["text"]
                if source_map[seg_id].get("words") is not None:
                    seg["words"] = source_map[seg_id]["words"]
            else:
                logger.warning(f"⚠️ Segment {seg_id} has no matching source timestamp!")

        # Save Approved Version to VAULT_DATA (canonical location)
        # CRITICAL: All components (dashboard, assistant, finalizer) read from VAULT_DATA
        output_path = config.VAULT_DATA / f"{stem}_APPROVED.json"
        
        # Wrap in "segments" key for consistency with Finalizer expectations
        final_payload = {
            "segments": final_segments,
            "meta": {
                "editor_model": model_name,
                "rating": report.get("rating"),
                "quality_tier": report.get("quality_tier")
            }
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_payload, f, indent=2, ensure_ascii=False)
            
        logger.info(f"✅ Editor Approved: {output_path.name}")
        
        # Cleanup input
        translation_path.unlink()
        
        return output_path

    except Exception as e:
        logger.error(f"Editor Failed: {e}")
        raise e
