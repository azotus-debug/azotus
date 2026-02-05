import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import time

import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    GenerationConfig,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
)

import config
from gcp_auth import ensure_google_application_credentials

logger = logging.getLogger("OmegaAssistant")

SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

def _load_job_file(job_id: str) -> Tuple[Optional[Path], Optional[Dict]]:
    """
    Finds the most "advanced" subtitle file for the job.
    Priority: APPROVED -> NORMALIZED -> SRT -> SKELETON
    """
    # 0. FINAL NORMALIZED (SRT Dir - Best Source)
    normalized_path = config.SRT_DIR / f"{job_id}_normalized.json"
    # Also check without prefix if needed, but primary check first
    if normalized_path.exists():
        try:
            with open(normalized_path, "r", encoding="utf-8") as f:
                return normalized_path, json.load(f)
        except Exception:
            pass

    # 1. APPROVED (check both TRANSLATED_DONE and VAULT_DATA)
    approved_candidates = [
        config.TRANSLATED_DONE_DIR / f"{job_id}_APPROVED.json",
        config.VAULT_DATA / f"{job_id}_APPROVED.json",
    ]
    for approved_path in approved_candidates:
        if approved_path.exists():
            try:
                with open(approved_path, "r", encoding="utf-8") as f:
                    return approved_path, json.load(f)
            except Exception:
                pass

    # 2. SRT FILES (4_DELIVERY/SRT - Common for completed jobs)
    srt_candidates = [
        config.SRT_DIR / f"DONE_{job_id}.srt",
        config.SRT_DIR / f"{job_id}.srt",
    ]
    
    for srt_path in srt_candidates:
        if srt_path.exists():
            try:
                segments = _parse_srt(srt_path)
                if segments:
                    return srt_path, {"segments": segments, "source": "srt"}
            except Exception:
                pass

    # 3. SKELETON (Vault Data)
    skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
    skeleton_done_path = config.VAULT_DATA / f"{job_id}_SKELETON_DONE.json"
    
    target = skeleton_path if skeleton_path.exists() else (skeleton_done_path if skeleton_done_path.exists() else None)
    
    if target:
        try:
            with open(target, "r", encoding="utf-8") as f:
                return target, json.load(f)
        except Exception:
            pass
            
    return None, None


def _parse_srt(srt_path: Path) -> list:
    """
    Parse SRT file into segment list.
    Returns: [{"id": 1, "start": 12.804, "end": 14.147, "text": "..."}, ...]
    """
    segments = []
    
    def timecode_to_seconds(tc: str) -> float:
        """Convert HH:MM:SS,mmm to float seconds."""
        try:
            tc = tc.replace(',', '.')  # SRT uses comma for milliseconds
            parts = tc.split(':')
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except Exception:
            return 0.0
    
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Split into blocks (separated by double newlines)
        blocks = content.strip().split('\n\n')
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue  # Invalid block
            
            # Line 1: ID
            try:
                seg_id = int(lines[0])
            except ValueError:
                continue
            
            # Line 2: Timecode
            if '-->' not in lines[1]:
                continue
            
            timecode_parts = lines[1].split('-->')
            start_tc = timecode_parts[0].strip()
            end_tc = timecode_parts[1].strip()
            
            start = timecode_to_seconds(start_tc)
            end = timecode_to_seconds(end_tc)
            
            # Lines 3+: Text
            text = '\n'.join(lines[2:])
            
            segments.append({
                "id": seg_id,
                "start": start,
                "end": end,
                "text": text
            })
        
        return segments
        
    except Exception as e:
        logger.error(f"SRT parse failed for {srt_path}: {e}")
        return []

def _clean_json_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line if it's ```json or ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

def chat_with_job(job_id: str, message: str, history: List[Dict] = None) -> Dict[str, Any]:
    """
    Interact with a job's subtitles using Gemini.
    
    Args:
        job_id: The job ID.
        message: The user's query or instruction.
        history: Optional list of previous messages [{"role": "user", "content": "..."}, ...]
        
    Returns:
        Dict containing:
        - "response": The text response from AI.
        - "edits_performed": Boolean, true if file was modified.
        - "diff": Optional diff string if modified.
    """
    ensure_google_application_credentials()
    
    file_path, data = _load_job_file(job_id)
    if not data:
        return {"response": f"Could not find any subtitle data for job {job_id}", "edits_performed": False}
        
    # Extract segments for context
    segments = []
    if isinstance(data, dict):
        # Handle APPROVED format vs SKELETON format vs NORMALIZED
        if "segments" in data:
            segments = data["segments"]
        elif "events" in data:
            segments = data["events"]
        elif "translated_data" in data:  # Draft format
            segments = data.get("translated_data", [])
        else:
            # Maybe it IS the segments list?
            segments = []
    elif isinstance(data, list):
        segments = data
        
    if not segments:
         return {"response": f"Subtitle file found at {file_path}, but could not parse segments.", "edits_performed": False}
         
    # Ensure IDs exist (crucial for normalized.json which lacks them)
    for idx, seg in enumerate(segments):
        if "id" not in seg:
            seg["id"] = idx + 1
            
    # Build System Prompt
    system_prompt = f"""
ROLE: You are "Omega Assistant", an expert subtitle editor for Omega TV.
You have direct access to the subtitle file for Job ID: {job_id}.

YOUR CAPABILITIES:
1. ANSWER questions about the content ("What is this sermon about?", "Does he mention 'David'?").
2. EDIT the subtitles based on user instructions ("Remove the first 2 lines", "Change 'God' to 'Guð'", "Make the tone more casual").

STRICT RULES FOR EDITING:
- If the user asks for a CHANGE, you MUST return a strict JSON object with a 'corrections' list.
- If the user just asks a QUESTION, return a normal text response (no JSON).
- Do NOT hallucinate IDs. Only use IDs present in the file.
- Do NOT output the full file. Only output format: 
  {{ "reply": "I have updated the file...", "corrections": [ {{ "id": 10, "text": "New text" }}, {{ "id": 12, "delete": true }} ] }}

CURRENT SUBTITLE FILE (Context):
{json.dumps(segments[:300], ensure_ascii=False)} 
(Note: Context limited to first 300 segments for speed. If user refers to later segments, ask for clarification or assume you can't see them yet.)

USER REQUEST: {message}
"""

    # Init Model
    vertexai.init(project=config.OMEGA_CLOUD_PROJECT, location=config.GEMINI_LOCATION)
    model = GenerativeModel(config.MODEL_ASSISTANT)
    
    try:
        response = model.generate_content(
            system_prompt,
            generation_config=GenerationConfig(
                temperature=0.3,
                response_mime_type="application/json" # Force JSON for reliability, we can parse specific schema
            ),
            safety_settings=SAFETY_SETTINGS
        )
        
        raw_text = getattr(response, "text", "") or "{}"
        parsed = json.loads(_clean_json_response(raw_text))
        
        if isinstance(parsed, list):
            # AI returned just the corrections list
            reply = "I've applied the changes."
            corrections = parsed
        else:
            reply = parsed.get("reply", "Done.")
            corrections = parsed.get("corrections", [])
        
        if corrections:
            # Apply Edits
            _backup_file(file_path)
            new_segments = _apply_corrections(segments, corrections)
            
            # Cleanup ephemeral IDs if they weren't original? 
            # Actually, keeping IDs is fine/useful.
            
            # Save
            if isinstance(data, dict):
                # Update specific list
                if "segments" in data: data["segments"] = new_segments
                elif "events" in data: data["events"] = new_segments
                elif "translated_data" in data: data["translated_data"] = new_segments
                
                # Update meta
                if "meta" not in data: data["meta"] = {}
                data["meta"]["last_assistant_edit"] = time.time()
                data["meta"]["last_assistant_message"] = message
            else:
                data = new_segments # It was a list
                
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            return {
                "response": reply,
                "edits_performed": True,
                "corrected_count": len(corrections)
            }
        else:
            return {
                "response": reply,
                "edits_performed": False
            }
            
    except Exception as e:
        logger.error(f"Assistant error: {e}")
        return {"response": f"Error interacting with AI: {e}", "edits_performed": False}

def _backup_file(path: Path):
    """Creates a timestamped backup before editing."""
    timestamp = int(time.time())
    backup_path = path.parent / f"{path.stem}_BACKUP_{timestamp}{path.suffix}"
    try:
        with open(path, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    except Exception as e:
        logger.warning(f"Failed to backup {path}: {e}")

def _apply_corrections(segments: List[Dict], corrections: List[Dict]) -> List[Dict]:
    """
    Applies edits. 
    corrections = [ { "id": 1, "text": "New" }, { "id": 2, "delete": true } ]
    """
    correction_map = {c["id"]: c for c in corrections}
    new_list = []
    
    for seg in segments:
        seg_id = int(seg.get("id", -1))
        if seg_id in correction_map:
            change = correction_map[seg_id]
            if change.get("delete"):
                continue # Skip adding this segment
            if "text" in change:
                seg["text"] = change["text"]
                # Sync to lines if present (common in normalized JSON)
                if "lines" in seg:
                    seg["lines"] = change["text"].split("\n")
            # Handle other fields if needed
        new_list.append(seg)
        
    return new_list
