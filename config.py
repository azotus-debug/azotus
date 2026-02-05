import os
import shutil
import time
import logging
import socket
import re
import site
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load .env file (if present) to ensure local secrets/configs are active
load_dotenv()

# Import centralized defaults (single source of truth for hardcoded values)
# Use importlib to avoid naming conflict with this config.py file
import importlib.util as _importlib_util
_defaults_path = Path(__file__).parent / "config" / "defaults.py"
_spec = _importlib_util.spec_from_file_location("_config_defaults", _defaults_path)
_defaults_module = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_defaults_module)
_DEFAULTS = _defaults_module.DEFAULTS

# --- BASE PATHS ---
BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "1_INBOX"
STAGE_DIR = BASE_DIR / "0_STAGE"      # Staged mode: transcribe only, wait for config
DROPZONE_DIR = BASE_DIR / "0_DROPZONE"  # Recipe-based quick processing
VAULT_DIR = BASE_DIR / "2_VAULT"
VAULT_DATA = VAULT_DIR / "Data"
VAULT_VIDEOS = VAULT_DIR / "Videos"
PROXIES_DIR = VAULT_DIR / "Proxies"
PROXY_DIR = PROXIES_DIR  # Alias for consistency
EDITOR_DIR = BASE_DIR / "3_EDITOR"
TRANSLATED_DONE_DIR = BASE_DIR / "3_TRANSLATED_DONE"
DELIVERY_DIR = BASE_DIR / "4_DELIVERY"
SRT_DIR = DELIVERY_DIR / "SRT"
VIDEO_DIR = DELIVERY_DIR / "VIDEO"
ARCHIVE_DIR = BASE_DIR / "5_ARCHIVE"
ERROR_DIR = BASE_DIR / "99_ERRORS"

# --- DATABASE ---
# PostgreSQL is required. Connection is configured via OMEGA_PG_* env vars.


logger = logging.getLogger("OmegaConfig")

def _safe_mkdir(path: Path) -> None:
    """
    Create directories when possible.

    This must not crash if the external SSD is temporarily unmounted and the
    repo paths are dangling symlinks.
    """
    try:
        if path.exists() and path.is_dir():
            return
        # Never try to mkdir over a symlink (including a dangling one).
        if path.is_symlink():
            return
        # If any parent is a dangling symlink, we can't create children safely.
        for parent in path.parents:
            if parent.is_symlink() and not parent.exists():
                return
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not ensure directory %s: %s", path, e)


# Ensure directories exist (when storage is ready)
for d in [
    INBOX_DIR,
    STAGE_DIR,         # New: Staged mode folder
    DROPZONE_DIR,      # New: Drop zones folder
    VAULT_DATA,
    VAULT_VIDEOS,
    PROXIES_DIR,
    EDITOR_DIR,
    TRANSLATED_DONE_DIR,
    SRT_DIR,
    VIDEO_DIR,
    ERROR_DIR,
    INBOX_DIR / "03_REMOTE_REVIEW" / "Classic",
    INBOX_DIR / "03_REMOTE_REVIEW" / "Modern_Look",
    INBOX_DIR / "03_REMOTE_REVIEW" / "Apple_TV",
]:
    _safe_mkdir(d)

# --- BINARIES ---
def get_binary(name, default):
    # 1. Check PATH
    path = shutil.which(name)
    if path:
        return path
    
    # 2. Check common Mac/Linux locations
    search_paths = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        str(Path(site.getuserbase()) / "bin")
    ]
    for sp in search_paths:
        bin_path = Path(sp) / name
        if bin_path.exists():
            return str(bin_path)
            
    return default

FFMPEG_BIN = get_binary("ffmpeg", "ffmpeg")
FFPROBE_BIN = get_binary("ffprobe", "ffprobe")

# --- STORAGE READINESS ---
_WRITE_PROBE_CACHE = {}
_WRITE_PROBE_TTL_SECONDS = 30.0

def critical_paths_ready(require_write: bool = False) -> bool:
    """
    Returns True if the minimum required filesystem paths are accessible.

    This is intentionally dynamic (it may become True after an external drive is mounted).
    """
    required = [INBOX_DIR, VAULT_DIR, DELIVERY_DIR]

    def _check_dir(p: Path) -> bool:
        try:
            target = p.resolve() if p.is_symlink() else p
        except Exception:
            target = p

        if not target.exists():
            return False
        if not target.is_dir():
            return False

        if require_write:
            now = time.monotonic()
            cache_key = str(target)
            cached = _WRITE_PROBE_CACHE.get(cache_key)
            if cached and (now - cached[0]) < _WRITE_PROBE_TTL_SECONDS:
                return bool(cached[1])

            writable = os.access(str(target), os.W_OK | os.X_OK)
            if writable:
                probe_path = target / f".omega_write_test.{os.getpid()}.{int(now * 1e9)}"
                try:
                    with open(probe_path, "wb") as f:
                        f.write(b"1")
                    writable = True
                except Exception:
                    writable = False
                finally:
                    try:
                        probe_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            _WRITE_PROBE_CACHE[cache_key] = (now, writable)
            return bool(writable)

        return True

    return all(_check_dir(p) for p in required)

def disk_space_available(min_gb: float = 50.0) -> tuple[bool, float]:
    """
    Check if sufficient disk space is available for batch processing.

    Returns (is_sufficient, available_gb).
    Checks the DELIVERY_DIR path (usually where output goes).
    """
    try:
        target = DELIVERY_DIR.resolve() if DELIVERY_DIR.is_symlink() else DELIVERY_DIR
        stat = shutil.disk_usage(str(target))
        available_gb = stat.free / (1024**3)
        return (available_gb >= min_gb, available_gb)
    except Exception as e:
        logger.warning("Could not check disk space: %s", e)
        return (False, 0.0)


# --- PROJECT-BASED FOLDER STRUCTURE ---
# Each program gets its own folder: 2_VAULT/{month}/{stem}/
# This is the agency-standard organization for media localization.

from datetime import datetime as _dt

def get_project_folder(stem: str, create: bool = True, reference_date: Optional[_dt] = None) -> Path:
    """
    Get or create the project folder for a given stem.
    
    Structure: 2_VAULT/YYYY-MM/{stem}/
    
    Args:
        stem: Canonical stem (e.g., "InTouch_W42")
        create: If True, create the folder if it doesn't exist
        reference_date: Optional date to determine month folder (defaults to now)
    
    Returns:
        Path to the project folder
    """
    # Determine month folder
    ref = reference_date or _dt.now()
    month = ref.strftime("%Y-%m")
    
    project_dir = VAULT_DIR / month / stem
    
    if create:
        _safe_mkdir(project_dir)
        _safe_mkdir(project_dir / "source")
        _safe_mkdir(project_dir / "data")
        _safe_mkdir(project_dir / "subtitles")
        _safe_mkdir(project_dir / "outputs")
    
    return project_dir


def find_project_folder(stem: str) -> Optional[Path]:
    """
    Find an existing project folder for a stem (searches all months).
    
    Returns the first match found, or None if not found.
    """
    if not VAULT_DIR.exists():
        return None
    
    # Search month folders in reverse order (most recent first)
    month_folders = sorted(
        [d for d in VAULT_DIR.iterdir() if d.is_dir() and len(d.name) == 7 and d.name[4] == '-'],
        reverse=True
    )
    
    for month_dir in month_folders:
        project_dir = month_dir / stem
        if project_dir.exists() and project_dir.is_dir():
            return project_dir
    
    return None


def get_project_file(stem: str, filename: str, subdir: str = "data") -> Path:
    """
    Get the path to a file within a project folder.
    
    Args:
        stem: Canonical stem
        filename: Filename (e.g., "skeleton.json")
        subdir: Subfolder within project ("source", "data", "subtitles", "outputs")
    
    Returns:
        Full path to the file
    """
    project_dir = find_project_folder(stem) or get_project_folder(stem)
    return project_dir / subdir / filename


def get_source_video(stem: str) -> Optional[Path]:
    """Find the source video for a stem (checks project folder first, then legacy)."""
    # Check project folder first
    project_dir = find_project_folder(stem)
    if project_dir:
        source_dir = project_dir / "source"
        if source_dir.exists():
            for ext in [".mp4", ".mov", ".mkv", ".avi"]:
                for video in source_dir.glob(f"*{ext}"):
                    return video
    
    # Fall back to legacy flat structure
    for ext in [".mp4", ".mov", ".mkv", ".avi"]:
        legacy = VAULT_VIDEOS / f"{stem}{ext}"
        if legacy.exists():
            return legacy
        # Also check without exact stem match
        for video in VAULT_VIDEOS.glob(f"{stem}*{ext}"):
            return video
    
    return None


def find_skeleton(stem: str) -> Optional[Path]:
    """
    Find the skeleton file for a given stem/job_id.

    Checks in order:
    1. Project folder: {project}/data/skeleton.json (new convention)
    2. Project folder: {project}/data/{stem}_SKELETON.json (migrated)
    3. Project folder: {project}/data/{stem}_SKELETON_DONE.json (migrated)
    4. Legacy: VAULT_DATA/{stem}_SKELETON.json
    5. Legacy: VAULT_DATA/{stem}_SKELETON_DONE.json

    This is the SINGLE source of truth for skeleton lookups.
    Returns Path if found, None otherwise.
    """
    # Check project folder first
    project_dir = find_project_folder(stem)
    if project_dir:
        data_dir = project_dir / "data"
        # New convention
        skel = data_dir / "skeleton.json"
        if skel.exists():
            return skel
        # Migrated files (kept original names)
        skel = data_dir / f"{stem}_SKELETON.json"
        if skel.exists():
            return skel
        skel_done = data_dir / f"{stem}_SKELETON_DONE.json"
        if skel_done.exists():
            return skel_done
    
    # Fall back to legacy flat structure
    skel = VAULT_DATA / f"{stem}_SKELETON.json"
    if skel.exists():
        return skel
    skel_done = VAULT_DATA / f"{stem}_SKELETON_DONE.json"
    if skel_done.exists():
        return skel_done
    return None

# --- SETTINGS ---

# Gemini Models
# ⚠️ CRITICAL: NEVER USE GEMINI 1.5. IT IS BANNED.
MODEL_TRANSLATOR = "gemini-3-pro-preview"  # High-reasoning translation
MODEL_EDITOR = "gemini-3-flash-preview"    # Fast, low-cost review/polish
MODEL_ASSISTANT = "gemini-3-flash-preview"  # Assistant UI
# Vertex AI requires "global" for preview models (gemini-3-*).
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global")

# --- CLOUD ARTIFACTS (GCS) ---
# Store per-job JSON artifacts (skeleton/termbook/translation/approved/checkpoints) in GCS.
# This enables a cloud-first translation/editor pipeline while keeping heavy video work local.
OMEGA_JOBS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", _DEFAULTS["gcs_bucket"])
OMEGA_JOBS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", _DEFAULTS["gcs_prefix"])
OMEGA_CLOUD_MUSIC_DETECT = os.environ.get("OMEGA_CLOUD_MUSIC_DETECT", "1").strip().lower() in {"1", "true", "yes", "on"}

# --- CLOUD SYNC SERVICE ---
OMEGA_CLOUD_SYNC_ENABLED = os.environ.get("OMEGA_CLOUD_SYNC_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
OMEGA_CLOUD_SYNC_POLL_SECONDS = float(os.environ.get("OMEGA_CLOUD_SYNC_POLL_SECONDS", "60"))
OMEGA_CLOUD_SYNC_BATCH_LIMIT = int(os.environ.get("OMEGA_CLOUD_SYNC_BATCH_LIMIT", "50"))
# Dead-man timeout for cloud jobs (minutes). Set to 0 to disable.
OMEGA_CLOUD_DEADMAN_MINUTES = int(os.environ.get("OMEGA_CLOUD_DEADMAN_MINUTES", "60"))

# --- STATION IDENTITY / SCOPE ---
# Unique identifier for this Mac/station. Used to prevent cross-station collisions.
# Default: sanitized hostname. Override with OMEGA_STATION_ID in .env/.omega_secrets.
_raw_station = os.environ.get("OMEGA_STATION_ID", "") or socket.gethostname()
OMEGA_STATION_ID = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(_raw_station).strip()).strip("-").lower() or "station"

# When true, this station will claim/process jobs that have no station_id assigned.
# Keep False for multi-station safety; set True temporarily to recover legacy jobs.
OMEGA_STATION_CLAIM_UNASSIGNED = os.environ.get("OMEGA_STATION_CLAIM_UNASSIGNED", "0").strip().lower() in {"1", "true", "yes", "on"}

# UI scope: "station" (default) shows only this station's jobs, "all" shows everything.
OMEGA_UI_SCOPE = os.environ.get("OMEGA_UI_SCOPE", "station").strip().lower()

# Optional: when set, the local manager will trigger a Cloud Run Job execution
# automatically after uploading job artifacts to GCS (no manual worker run).
OMEGA_CLOUD_RUN_JOB = os.environ.get("OMEGA_CLOUD_RUN_JOB", _DEFAULTS["cloud_run_job"]).strip()
# Cloud Run jobs run in a specific region; keep this separate from GEMINI_LOCATION.
OMEGA_CLOUD_RUN_REGION = os.environ.get("OMEGA_CLOUD_RUN_REGION", _DEFAULTS["gcs_region"]).strip() or _DEFAULTS["gcs_region"]
OMEGA_CLOUD_PROJECT = os.environ.get("OMEGA_CLOUD_PROJECT", _DEFAULTS["gcp_project"]).strip() or _DEFAULTS["gcp_project"]

# --- CLOUD PIPELINE TUNING (ADVANCED) ---
# Translation chunking
OMEGA_CLOUD_TRANSLATE_MAX_ATTEMPTS = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_MAX_ATTEMPTS", "6") or "6")
OMEGA_CLOUD_TRANSLATE_SPLIT_AFTER = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_SPLIT_AFTER", "2") or "2")
OMEGA_CLOUD_TRANSLATE_CHUNK_SIZE = int(os.environ.get("OMEGA_CLOUD_TRANSLATE_CHUNK_SIZE", "90") or "90")
OMEGA_CLOUD_CONTINUITY_SIZE = int(os.environ.get("OMEGA_CLOUD_CONTINUITY_SIZE", "8") or "8")

# Music detection
OMEGA_CLOUD_MUSIC_CHUNK_SIZE = int(os.environ.get("OMEGA_CLOUD_MUSIC_CHUNK_SIZE", "120") or "120")

# Editor & document brief
OMEGA_CLOUD_EDITOR_MAX_ATTEMPTS = int(os.environ.get("OMEGA_CLOUD_EDITOR_MAX_ATTEMPTS", "3") or "3")
OMEGA_CLOUD_DOC_BRIEF = os.environ.get("OMEGA_CLOUD_DOC_BRIEF", "1").strip().lower() in {"1", "true", "yes", "on"}
OMEGA_CLOUD_DOC_BRIEF_SEGMENTS = int(os.environ.get("OMEGA_CLOUD_DOC_BRIEF_SEGMENTS", "120") or "120")
OMEGA_CLOUD_DOC_BRIEF_CHARS = int(os.environ.get("OMEGA_CLOUD_DOC_BRIEF_CHARS", "12000") or "12000")

# --- ELEVENLABS (TRANSCRIPTION + DUBBING) ---
# API key for ElevenLabs Scribe v2 (get from https://elevenlabs.io)
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
# Additional keyterms for transcription accuracy (comma-separated, max 100 total)
# Built-in includes: Jesus, Christ, Jerusalem, Gospel, CBN, 700 Club, etc.
ELEVENLABS_KEYTERMS = os.environ.get("ELEVENLABS_KEYTERMS", "").strip()
# Enable speaker diarization - default True
ELEVENLABS_SPEAKER_DIARIZATION = os.environ.get("ELEVENLABS_SPEAKER_DIARIZATION", "1").strip().lower() in {"1", "true", "yes", "on"}
# Maximum speakers to detect (1-32, default: 32 = auto)
ELEVENLABS_MAX_SPEAKERS = int(os.environ.get("ELEVENLABS_MAX_SPEAKERS", "32") or "32")
# Enable audio event tagging (music, laughter, applause) - default True
ELEVENLABS_TAG_AUDIO_EVENTS = os.environ.get("ELEVENLABS_TAG_AUDIO_EVENTS", "1").strip().lower() in {"1", "true", "yes", "on"}
# Auto-detect language instead of assuming English - default False
ELEVENLABS_AUTO_LANGUAGE = os.environ.get("ELEVENLABS_AUTO_LANGUAGE", "0").strip().lower() in {"1", "true", "yes", "on"}
# Enable entity detection (names, locations, orgs) - default True for broadcast quality
ELEVENLABS_ENTITY_DETECTION = os.environ.get("ELEVENLABS_ENTITY_DETECTION", "1").strip().lower() in {"1", "true", "yes", "on"}
# Entity categories for detection (comma-separated or "all")
ELEVENLABS_ENTITY_CATEGORIES = os.environ.get("ELEVENLABS_ENTITY_CATEGORIES", "").strip()
# Diarization threshold: 0.0-0.4 (lower = more sensitive to speaker changes, 0.3 = balanced)
ELEVENLABS_DIARIZATION_THRESHOLD = float(os.environ.get("ELEVENLABS_DIARIZATION_THRESHOLD", "0.3") or "0.3")

# --- TRANSCRIBER SELECTION ---
# ElevenLabs Scribe v2 is required.
OMEGA_TRANSCRIBER = os.environ.get("OMEGA_TRANSCRIBER", "elevenlabs").strip().lower()
if OMEGA_TRANSCRIBER != "elevenlabs":
    logger.warning("OMEGA_TRANSCRIBER=%s is unsupported. ElevenLabs is required.", OMEGA_TRANSCRIBER)

# --- DEMUCS VOCAL EXTRACTION ---
# Enable Demucs to remove background music before transcription (requires M2 Mac)
# This helps prevent transcription of background lyrics
OMEGA_DEMUCS_ENABLED = os.environ.get("OMEGA_DEMUCS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
# Demucs model: "htdemucs" (fast, ~8-12 min/hour) or "htdemucs_ft" (slower, best quality)
OMEGA_DEMUCS_MODEL = os.environ.get("OMEGA_DEMUCS_MODEL", "htdemucs").strip()
# Device: "mps" (Apple Silicon GPU), "cpu", or "cuda" (Nvidia)
OMEGA_DEMUCS_DEVICE = os.environ.get("OMEGA_DEMUCS_DEVICE", "mps").strip()

# --- MULTIMODAL VISION (AZOTUS) ---
# Enable multimodal vision scan/danger zones. A 360p proxy is always generated when
# cloud translation is enabled; set this to 1 to also run vision scan + danger zones
# (avoid lower-thirds, on-screen text).
OMEGA_MULTIMODAL_ENABLED = os.environ.get("OMEGA_MULTIMODAL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

# Style Map
STYLE_MAP = {
    "Joyce Meyer": "RUV_BOX",
    "Praise": "OMEGA_MODERN", 
    "News": "RUV_BOX",
    "CBN": "RUV_BOX",
    "700": "RUV_BOX",
    "DEFAULT": "OMEGA_MODERN"
}

# Burn Method Map
# Maps internal style names to burn methods ("RuvBox" = Direct ASS, "Apple" = Overlay)
BURN_METHOD_MAP = {
    "Classic": "RuvBox",   # DIRECT: Use ASS Burn for Classic
    "RuvBox": "RuvBox",    # DIRECT: Use ASS Burn for RuvBox
    "Modern": "Default",
    # Folder / legacy aliases
    "Modern_Look": "Default",
    "OMEGA_MODERN": "Default",
    "Apple": "Apple",
    "Apple_TV": "Apple",
    "AppleTV_IS": "Apple",
    "DEFAULT": "RuvBox"
}

# --- DELIVERY PROFILES (MEDIA ENCODING) ---
# Select the appropriate profile when burning subtitles based on client requirements.
# Use dashboard dropdown or set DEFAULT_DELIVERY_PROFILE for automatic selection.

DELIVERY_PROFILES = {
    "broadcast_hevc": {
        "name": "Broadcast HEVC (Fast)",
        "encoder": "hevc_videotoolbox",
        "bitrate": "12M",
        "maxrate": "15M",
        "bufsize": "24M",
        "extra_args": ["-tag:v", "hvc1"],  # QuickTime/Apple compatibility
        "description": "Fast hardware encoding (9x), modern compatibility",
        "speed": "9x",
        "compatibility": "Modern (2017+)"
    },
    "broadcast_h264": {
        "name": "Broadcast H.264 (Universal)",
        "encoder": "libx264",
        "preset": "slow",
        "crf": "18",
        "maxrate": "18M",
        "bufsize": "36M",
        "extra_args": ["-profile:v", "high", "-level", "4.1"],
        "description": "Software encoding, plays on everything",
        "speed": "1x",
        "compatibility": "Universal"
    },
    "web": {
        "name": "Web Optimized",
        "encoder": "hevc_videotoolbox",
        "bitrate": "6M",
        "maxrate": "8M",
        "bufsize": "12M",
        "extra_args": ["-tag:v", "hvc1"],
        "description": "Smaller files for streaming/upload",
        "speed": "9x",
        "compatibility": "Modern"
    },
    "archive": {
        "name": "Archive (Master)",
        "encoder": "libx264",
        "preset": "veryslow",
        "crf": "16",
        "maxrate": "25M",
        "bufsize": "50M",
        "extra_args": ["-profile:v", "high"],
        "description": "Highest quality, for archival",
        "speed": "0.3x",
        "compatibility": "Universal"
    },
    "universal": {
        "name": "Universal (Safe Default)",
        "encoder": "libx264",
        "preset": "medium",
        "crf": "20",
        "maxrate": "12M",
        "bufsize": "24M",
        "extra_args": ["-profile:v", "main", "-level", "4.0"],
        "description": "Balanced speed/quality, maximum compatibility",
        "speed": "2x",
        "compatibility": "Maximum"
    }
}

# Default profile when no specific profile is selected
DEFAULT_DELIVERY_PROFILE = os.environ.get("OMEGA_DELIVERY_PROFILE", "broadcast_hevc").strip()

# --- CLIENT PATTERNS ---
# Auto-detect client from filename. Keys are lowercase patterns to match, values are display names.
# Matched in order, first match wins. Add your clients here.
CLIENT_PATTERNS = {
    "intouch": "In Touch",
    "in_touch": "In Touch",
    "timessquare": "Times Square Church",
    "times_square": "Times Square Church",
    "tsc": "Times Square Church",
    "charles_stanley": "Charles Stanley",
    "charlesstanley": "Charles Stanley",
    "benny_hinn": "Benny Hinn",
    "bennyhinn": "Benny Hinn",
    "joyce_meyer": "Joyce Meyer",
    "joycemeyer": "Joyce Meyer",
    "i2": "Omega TV",  # Internal production codes
    "gospel": "Gospel",
    # Add more patterns as needed
}

# --- CLIENT DEFAULTS ---
# Default turnaround time (in days) for each client
# Delivery template tokens:
#   {client} - Client name
#   {title} - Extracted from original filename
#   {date_YYYY_MM_DD} - 2024_12_28
#   {date_MM-DD-YY} - 12-28-24
#   {date_DD_month_YYYY} - 28_december_2024
CLIENT_DEFAULTS = {
    "In Touch": {
        "due_date_days": 7,
        "delivery_template": "InTouch_{title}_{date_YYYY_MM_DD}",
        "delivery_method": "folder",  # 'folder', 'email', 'ftp'
        "delivery_target": "4_DELIVERY/InTouch"
    },
    "Times Square Church": {
        "due_date_days": 5,
        "delivery_template": "TSC_{title}_{date_MM-DD-YY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/TSC"
    },
    "Charles Stanley": {
        "due_date_days": 7,
        "delivery_template": "CharlesStanley_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/CharlesStanley"
    },
    "Benny Hinn": {
        "due_date_days": 10,
        "delivery_template": "BennyHinn_{title}_{date_MM-DD-YY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/BennyHinn"
    },
    "Joyce Meyer": {
        "due_date_days": 7,
        "delivery_template": "JoyceMeyer_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/JoyceMeyer"
    },
    "Omega TV": {
        "due_date_days": 3,
        "delivery_template": "OmegaTV_{title}_{date_DD_month_YYYY}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/OmegaTV"
    },
    "Gospel": {
        "due_date_days": 7,
        "delivery_template": "Gospel_{date_YYYY_MM_DD}_{title}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY/Gospel"
    },
    "unknown": {
        "due_date_days": 7,
        "delivery_template": "{title}_{date_YYYY_MM_DD}",
        "delivery_method": "folder",
        "delivery_target": "4_DELIVERY"
    },
}

# --- LEGACY PUBLISH SETTINGS (for backwards compatibility) ---
PUBLISH_X264_PRESET = os.environ.get("OMEGA_X264_PRESET", "medium")
PUBLISH_VIDEO_BITRATE = os.environ.get("OMEGA_VIDEO_BITRATE", "15M")
PUBLISH_VIDEO_MAXRATE = os.environ.get("OMEGA_VIDEO_MAXRATE", "18M")
PUBLISH_VIDEO_BUFSIZE = os.environ.get("OMEGA_VIDEO_BUFSIZE", "30M")


# --- STALL DETECTION TIMEOUTS ---
# Time in seconds before a job is considered stalled/dead in a stage
OMEGA_STALL_TIMEOUTS = {
    "TRANSLATING": 1800.0,      # 30 minutes (Reduced from 90)
    "BURNING": 3600.0,          # 1 hour (Reduced from 6)
    "INGESTING": 1800.0,
    "REVIEWING": 172800.0,      # 48 hours for human review
}
