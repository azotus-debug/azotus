import logging
import json
import omega_db

# Re-export parse_meta from omega_db if needed or reimplement simple wrapper
def parse_meta(raw):
    """
    Parses metadata field from DB (JSON/dict/string) into a dictionary.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    try:
        return dict(raw)
    except Exception:
        return {}

logger = logging.getLogger("cloud_manager.db")

def get_engine():
    """
    Returns the SQLAlchemy engine from omega_db.
    Used by main.py ONLY if it needs direct engine access, 
    but based on review main.py mostly calls db.* functions.
    """
    # omega_db manages engine internally but exposes methods.
    # If main.py expects get_engine to verify connection, we can return it.
    # omega_db._PG_POOL or omega_db.get_engine (if exposed or we add it)
    # Since omega_db doesn't publicly expose get_engine used by legacy code easily,
    # and main.py doesn't seem to use it, we'll return None or a proxy if needed.
    # Reviewing main.py: It DOES NOT call get_engine().
    return None

def fetch_jobs(*, stages=None, limit=100) -> list:
    """
    Fetch jobs (tracks) from omega_db matching criteria.
    Maps back to legacy 'job' dictionary format.
    """
    try:
        return omega_db.get_all_jobs_via_tracks(stages=stages, limit=limit)
    except Exception as e:
        logger.error(f"fetch_jobs failed: {e}")
        return []

def update_job(
    *,
    stem: str,
    stage: str = None,
    status: str = None,
    progress: float = None,
    meta_updates: dict = None,
    expected_stage: str = None,
) -> bool:
    """
    Update a job (track).
    Supports 'expected_stage' for optimistic locking simulation.
    """
    if not stem:
        return False
        
    # Optimistic locking check
    if expected_stage:
        current = omega_db.get_job_via_track(stem)
        if not current:
            return False
        if current.get('stage') != expected_stage:
            return False

    return omega_db.update_job_via_track(
        file_stem=stem,
        stage=stage,
        status=status,
        progress=progress,
        meta=meta_updates
    )

def insert_job(
    *,
    stem: str,
    stage: str = "UPLOADED",
    status: str = "Uploaded",
    meta: dict = None,
    target_language: str = "is",
    program_profile: str = None,
) -> bool:
    """
    Create a new job (Program + Track).
    """
    if not stem:
        return False
    
    meta = meta or {}
    
    # Check for existence
    existing = omega_db.get_job_via_track(stem)
    if existing:
        return False
        
    try:
        # Create Program
        program_id = omega_db.create_program(
            title=stem,
            original_filename=meta.get("original_filename", stem),
            meta=meta
        )
        
        # Ensure Master Script
        ms_id = omega_db.ensure_master_script(
            program_id=program_id,
            language_code=target_language
        )
        
        # Create Track
        # Using 'stem' as job_id to maintain legacy compatibility
        track_id = omega_db.create_track(
            program_id=program_id,
            type='subtitle',
            language_code=target_language,
            language_name=None, # Will auto-resolve if not needed
            stage=stage,
            status=status,
            job_id=stem, 
            master_script_id=ms_id,
            meta=meta
        )
        return True
    except Exception as e:
        logger.error(f"insert_job failed: {e}")
        return False
