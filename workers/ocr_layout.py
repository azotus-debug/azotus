"""
OCR Layout / Danger Zone Detection for Multimodal (Azotus) Pipeline

Processes vision scan results to identify "danger zones" - screen regions
where subtitles would conflict with on-screen graphics.

Output is used by finalizer.py to position subtitles dynamically.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

import config
import omega_db

logger = logging.getLogger("OmegaManager.OCRLayout")


def detect_danger_zones(
    vision_scan: Dict[str, Any],
    video_duration: float,
    job_id: str,
    output_dir: Optional[Path] = None,
    file_stem: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Analyze vision scan results to identify danger zones.

    A "danger zone" is a time range where subtitles should avoid certain
    screen regions to prevent overlapping with graphics or text.

    Args:
        vision_scan: Output from vision_scanner.scan_video()
        video_duration: Total video duration in seconds
        job_id: Job identifier for progress updates (full job_id with timestamp)
        output_dir: Optional directory to save results locally
        file_stem: Optional file stem for output naming (defaults to job_id).
                   Use original video stem for multi-language jobs.

    Returns:
        List of danger zones:
        [
            {
                "start": 0.0,
                "end": 8.0,
                "avoid_regions": ["bottom-center"],
                "reason": "lower-third: Name title",
                "priority": "high"
            }
        ]

        avoid_regions can include:
        - "top": Avoid top third of screen
        - "bottom": Avoid bottom third of screen
        - "bottom-center": Avoid bottom center (standard subtitle position)
        - "center": Avoid center of screen
    """
    omega_db.update_job_via_track(job_id, status="Detecting danger zones", progress=0.22)
    logger.info(f"🎯 Detecting danger zones for {job_id}")

    danger_zones = []

    # 1. Process lower-thirds (highest priority - always avoid)
    for lt in vision_scan.get("lower_thirds", []):
        if lt.get("has_graphics"):
            danger_zones.append({
                "start": float(lt.get("start", 0)),
                "end": float(lt.get("end", 0)),
                "avoid_regions": ["bottom-center"],
                "reason": f"lower-third: {lt.get('description', 'graphic')}",
                "priority": "high",
            })
            logger.debug(f"   Lower-third: {lt.get('start'):.1f}s - {lt.get('end'):.1f}s")

    # 2. Process OCR text (avoid conflicting regions)
    for ocr in vision_scan.get("ocr_text", []):
        position = ocr.get("position", "center")
        confidence = float(ocr.get("confidence", 0))
        text = ocr.get("text", "")

        # Only high-confidence detections
        if confidence < 0.7:
            continue

        # Skip very short text (likely noise)
        if len(text) < 3:
            continue

        # Map position to avoid regions
        if position == "bottom":
            avoid_regions = ["bottom-center"]
            priority = "high"
        elif position == "top":
            avoid_regions = ["top"]
            priority = "medium"
        else:
            continue  # Center text doesn't usually conflict with subtitles

        danger_zones.append({
            "start": float(ocr.get("start", 0)),
            "end": float(ocr.get("end", 0)),
            "avoid_regions": avoid_regions,
            "reason": f"on-screen text: {text[:30]}",
            "priority": priority,
        })
        logger.debug(f"   OCR ({position}): {ocr.get('start'):.1f}s - {ocr.get('end'):.1f}s: {text[:20]}")

    # 3. Merge overlapping zones with same avoid_regions
    danger_zones = _merge_overlapping_zones(danger_zones)

    # 4. Log summary
    logger.info(f"   ✅ Found {len(danger_zones)} danger zones:")
    high_priority = [z for z in danger_zones if z.get("priority") == "high"]
    medium_priority = [z for z in danger_zones if z.get("priority") == "medium"]
    logger.info(f"      - {len(high_priority)} high priority (lower-thirds, bottom text)")
    logger.info(f"      - {len(medium_priority)} medium priority (top text)")

    # 5. Calculate coverage
    total_danger_time = sum(z["end"] - z["start"] for z in danger_zones)
    coverage_pct = (total_danger_time / video_duration * 100) if video_duration > 0 else 0
    logger.info(f"      - {coverage_pct:.1f}% of video has danger zones")

    # 6. Save locally if requested
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Use file_stem if provided, otherwise fall back to job_id
        output_name = file_stem or job_id
        output_path = output_dir / f"{output_name}_DANGER_ZONES.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(danger_zones, f, indent=2, ensure_ascii=False)
        logger.info(f"   💾 Saved: {output_path.name}")

    omega_db.update_job_via_track(job_id, status="Danger zones detected", progress=0.25)
    return danger_zones


def _merge_overlapping_zones(zones: List[Dict]) -> List[Dict]:
    """
    Merge overlapping danger zones with the same avoid_regions.

    Zones that are within 0.5 seconds of each other and have the same
    avoid_regions are merged into a single zone.
    """
    if not zones:
        return []

    # Sort by start time
    sorted_zones = sorted(zones, key=lambda z: z["start"])

    merged = []
    current = sorted_zones[0].copy()

    for zone in sorted_zones[1:]:
        # Check if zones overlap or are close (within 0.5s)
        if (zone["start"] <= current["end"] + 0.5 and
            set(zone["avoid_regions"]) == set(current["avoid_regions"])):
            # Extend current zone
            current["end"] = max(current["end"], zone["end"])
            # Keep the higher priority
            if zone.get("priority") == "high":
                current["priority"] = "high"
            # Append reasons
            if zone.get("reason") and zone["reason"] not in current.get("reason", ""):
                current["reason"] = f"{current.get('reason', '')}; {zone['reason']}"
        else:
            # Start new zone
            merged.append(current)
            current = zone.copy()

    merged.append(current)
    return merged


def upload_danger_zones_to_gcs(
    danger_zones: List[Dict],
    bucket: str,
    blob_path: str,
) -> str:
    """
    Upload danger zones to GCS.

    Args:
        danger_zones: List of danger zone dicts
        bucket: GCS bucket name
        blob_path: Destination blob path

    Returns:
        GCS URI
    """
    from google.cloud import storage

    logger.info(f"☁️ Uploading danger zones to gs://{bucket}/{blob_path}")

    client = storage.Client()
    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_path)

    data = json.dumps(danger_zones, ensure_ascii=False, indent=2).encode("utf-8")
    blob.upload_from_string(data, content_type="application/json")

    gcs_uri = f"gs://{bucket}/{blob_path}"
    logger.info(f"   ✅ Uploaded: {gcs_uri}")
    return gcs_uri


# =============================================================================
# SUBTITLE POSITIONING HELPERS
# =============================================================================

def get_subtitle_position(
    start_time: float,
    end_time: float,
    danger_zones: List[Dict],
    default_position: str = "bottom-center",
) -> str:
    """
    Determine optimal subtitle position for a given time range.

    Args:
        start_time: Subtitle start time in seconds
        end_time: Subtitle end time in seconds
        danger_zones: List of danger zones
        default_position: Default position if no conflicts

    Returns:
        Position string: "top", "bottom-center", or "center"
    """
    for zone in danger_zones:
        # Check if subtitle overlaps with danger zone
        if start_time < zone["end"] and end_time > zone["start"]:
            avoid = zone.get("avoid_regions", [])
            priority = zone.get("priority", "medium")

            # High priority zones force position change
            if priority == "high" and "bottom-center" in avoid:
                return "top"
            elif priority == "high" and "top" in avoid:
                return "bottom-center"

            # Medium priority: only change if default conflicts
            if default_position == "bottom-center" and "bottom-center" in avoid:
                return "top"
            if default_position == "top" and "top" in avoid:
                return "bottom-center"

    return default_position


def calculate_ass_alignment(position: str) -> int:
    """
    Convert position string to ASS alignment code.

    ASS alignment codes:
    1: bottom-left    4: center-left    7: top-left
    2: bottom-center  5: center         8: top-center
    3: bottom-right   6: center-right   9: top-right

    Args:
        position: "top", "center", or "bottom-center"

    Returns:
        ASS alignment code
    """
    mapping = {
        "top": 8,           # Top center
        "center": 5,        # Center
        "bottom-center": 2, # Bottom center (default)
        "bottom": 2,        # Alias for bottom-center
    }
    return mapping.get(position, 2)


def load_danger_zones(job_id: str) -> List[Dict]:
    """
    Load danger zones from local file system.

    Args:
        job_id: Job identifier

    Returns:
        List of danger zones, or empty list if not found
    """
    paths_to_check = [
        config.VAULT_DATA / f"{job_id}_DANGER_ZONES.json",
        config.PROXIES_DIR / f"{job_id}_DANGER_ZONES.json",
    ]

    for path in paths_to_check:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    zones = json.load(f)
                logger.info(f"Loaded {len(zones)} danger zones from {path.name}")
                return zones
            except Exception as e:
                logger.warning(f"Failed to load danger zones from {path}: {e}")

    logger.debug(f"No danger zones found for {job_id}")
    return []
