"""
Vision Scanner for Multimodal (Azotus) Pipeline

Uses Gemini Flash to analyze video for:
- Shot detection (scene cuts with timestamps)
- OCR (on-screen text with positions)
- Speaker detection (count and screen positions)
- Lower-thirds detection (graphics that subtitles should avoid)
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    Part,
    GenerationConfig,
    SafetySetting,
    HarmCategory,
    HarmBlockThreshold,
)

import config
import omega_db
from gcp_auth import ensure_google_application_credentials

logger = logging.getLogger("OmegaManager.VisionScanner")

# Safety settings - BLOCK_NONE for religious content
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

# Vision analysis prompt for Gemini Flash
VISION_SCAN_PROMPT = """
Analyze this video and return a JSON object with the following structure:

{
    "shots": [
        {"start": 0.0, "end": 5.2, "type": "cut"},
        {"start": 5.2, "end": 12.8, "type": "dissolve"}
    ],
    "ocr_text": [
        {"start": 0.0, "end": 8.0, "text": "JERUSALEM DATELINE", "position": "bottom", "confidence": 0.95}
    ],
    "speakers": [
        {"start": 0.0, "end": 30.0, "count": 1, "positions": ["center"]}
    ],
    "lower_thirds": [
        {"start": 5.0, "end": 12.0, "has_graphics": true, "description": "Name title graphic"}
    ]
}

Guidelines:
1. **shots**: Identify scene transitions. Types: "cut" (instant), "dissolve" (gradual), "fade" (to/from black), "wipe"
2. **ocr_text**: Detect any on-screen text (titles, lower thirds, scripture references, chyrons). Position is "top", "center", or "bottom"
3. **speakers**: Estimate visible speakers on screen and their general position ("left", "center", "right")
4. **lower_thirds**: Identify any graphics in the lower portion of the screen that subtitles should avoid

Focus on accuracy over completeness. Only include high-confidence detections.
Return ONLY valid JSON, no markdown formatting.
"""


def _init_vertexai() -> bool:
    """Initialize Vertex AI with credentials."""
    try:
        ensure_google_application_credentials()
        vertexai.init(
            project=config.OMEGA_CLOUD_PROJECT,
            location=config.GEMINI_LOCATION,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Vertex AI: {e}")
        return False


def scan_video(
    video_gcs_uri: str,
    job_id: str,
    output_dir: Optional[Path] = None,
    file_stem: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze video using Gemini Flash for vision metadata.

    Args:
        video_gcs_uri: GCS URI to the video proxy (gs://bucket/path/proxy.mp4)
        job_id: Job identifier for progress updates (full job_id with timestamp)
        output_dir: Optional directory to save results locally
        file_stem: Optional file stem for output naming (defaults to job_id).
                   Use original video stem for multi-language jobs.

    Returns:
        Dict containing:
        - shots: List of shot boundaries with types
        - ocr_text: List of detected on-screen text
        - speakers: List of speaker detections
        - lower_thirds: List of lower-third graphics

    Raises:
        RuntimeError: If vision scan fails
    """
    if not _init_vertexai():
        raise RuntimeError("Failed to initialize Vertex AI")

    omega_db.update_job_via_track(job_id, status="Running vision scan", progress=0.15)
    logger.info(f"👁️ Running vision scan for {job_id}: {video_gcs_uri}")

    # Create model
    model = GenerativeModel(config.MODEL_ASSISTANT)  # Gemini 3 Flash

    # Create video part from GCS URI
    video_part = Part.from_uri(uri=video_gcs_uri, mime_type="video/mp4")

    # Generate response
    try:
        response = model.generate_content(
            [VISION_SCAN_PROMPT, video_part],
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,  # Low temperature for consistent structured output
                max_output_tokens=8192,
            ),
            safety_settings=SAFETY_SETTINGS,
        )

        # Parse response
        result_text = response.text.strip()

        # Clean up any markdown formatting if present
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()

        result = json.loads(result_text)

        # Validate structure
        result = _validate_and_clean_result(result)

        # Log summary
        logger.info(f"   ✅ Vision scan complete:")
        logger.info(f"      - {len(result.get('shots', []))} shot transitions")
        logger.info(f"      - {len(result.get('ocr_text', []))} OCR text regions")
        logger.info(f"      - {len(result.get('speakers', []))} speaker segments")
        logger.info(f"      - {len(result.get('lower_thirds', []))} lower-third graphics")

        # Save locally if output_dir specified
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            # Use file_stem if provided, otherwise fall back to job_id
            output_name = file_stem or job_id
            output_path = output_dir / f"{output_name}_VISION_SCAN.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"   💾 Saved: {output_path.name}")

        omega_db.update_job_via_track(job_id, status="Vision scan complete", progress=0.2)
        return result

    except json.JSONDecodeError as e:
        logger.error(f"   ❌ Failed to parse vision scan response as JSON: {e}")
        logger.error(f"   Response was: {response.text[:500] if hasattr(response, 'text') else 'N/A'}")
        raise RuntimeError(f"Vision scan returned invalid JSON: {e}")

    except Exception as e:
        logger.error(f"   ❌ Vision scan failed: {e}")
        raise RuntimeError(f"Vision scan failed: {e}")


def _validate_and_clean_result(result: Dict) -> Dict:
    """
    Validate and clean vision scan result.
    Ensures all required fields exist with proper types.
    """
    cleaned = {
        "shots": [],
        "ocr_text": [],
        "speakers": [],
        "lower_thirds": [],
    }

    # Process shots
    for shot in result.get("shots", []):
        if isinstance(shot, dict) and "start" in shot and "end" in shot:
            cleaned["shots"].append({
                "start": float(shot.get("start", 0)),
                "end": float(shot.get("end", 0)),
                "type": str(shot.get("type", "cut")),
            })

    # Process OCR text
    for ocr in result.get("ocr_text", []):
        if isinstance(ocr, dict) and "text" in ocr:
            cleaned["ocr_text"].append({
                "start": float(ocr.get("start", 0)),
                "end": float(ocr.get("end", 0)),
                "text": str(ocr.get("text", "")),
                "position": str(ocr.get("position", "center")),
                "confidence": float(ocr.get("confidence", 0.5)),
            })

    # Process speakers
    for speaker in result.get("speakers", []):
        if isinstance(speaker, dict):
            cleaned["speakers"].append({
                "start": float(speaker.get("start", 0)),
                "end": float(speaker.get("end", 0)),
                "count": int(speaker.get("count", 1)),
                "positions": speaker.get("positions", ["center"]),
            })

    # Process lower thirds
    for lt in result.get("lower_thirds", []):
        if isinstance(lt, dict):
            cleaned["lower_thirds"].append({
                "start": float(lt.get("start", 0)),
                "end": float(lt.get("end", 0)),
                "has_graphics": bool(lt.get("has_graphics", False)),
                "description": str(lt.get("description", "")),
            })

    return cleaned


def upload_vision_scan_to_gcs(
    vision_scan: Dict,
    bucket: str,
    blob_path: str,
) -> str:
    """
    Upload vision scan results to GCS.

    Args:
        vision_scan: Vision scan result dict
        bucket: GCS bucket name
        blob_path: Destination blob path

    Returns:
        GCS URI
    """
    from google.cloud import storage

    logger.info(f"☁️ Uploading vision scan to gs://{bucket}/{blob_path}")

    client = storage.Client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_path)

    data = json.dumps(vision_scan, ensure_ascii=False, indent=2).encode("utf-8")
    blob.upload_from_string(data, content_type="application/json")

    gcs_uri = f"gs://{bucket}/{blob_path}"
    logger.info(f"   ✅ Uploaded: {gcs_uri}")
    return gcs_uri


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def get_shot_boundaries(vision_scan: Dict) -> List[float]:
    """
    Extract shot boundary timestamps for subtitle timing.

    Returns list of timestamps where scene changes occur.
    """
    boundaries = []
    for shot in vision_scan.get("shots", []):
        if shot.get("type") in ("cut", "dissolve", "fade", "wipe"):
            boundaries.append(shot.get("start", 0))
    return sorted(set(boundaries))


def get_danger_zones_from_scan(vision_scan: Dict) -> List[Dict]:
    """
    Extract danger zones (where subtitles should avoid) from vision scan.

    Returns list of danger zone dicts for ocr_layout processing.
    """
    danger_zones = []

    # Lower thirds are danger zones
    for lt in vision_scan.get("lower_thirds", []):
        if lt.get("has_graphics"):
            danger_zones.append({
                "start": lt["start"],
                "end": lt["end"],
                "avoid_regions": ["bottom-center"],
                "reason": f"lower-third: {lt.get('description', 'graphic')}",
            })

    # OCR text at top or bottom is a danger zone
    for ocr in vision_scan.get("ocr_text", []):
        pos = ocr.get("position", "center")
        if pos in ("top", "bottom") and ocr.get("confidence", 0) > 0.7:
            danger_zones.append({
                "start": ocr["start"],
                "end": ocr["end"],
                "avoid_regions": [pos],
                "reason": f"on-screen text: {ocr.get('text', '')[:30]}",
            })

    return danger_zones
