import os
import json
import config
import logging
logger = logging.getLogger("OmegaDB")
import threading
from pathlib import Path
from datetime import datetime, timedelta

# CONFIG
_SCHEMA_READY = False

def _fetchone_dict(cursor):
    """Fetch one row as a dictionary."""
    row = cursor.fetchone()
    if row is None: return None
    if hasattr(row, 'keys'): return dict(row)
    desc = cursor.description
    if desc:
        return dict(zip([col[0] for col in desc], row))
    return row

def _fetchall_dicts(cursor):
    """Fetch all rows as dictionaries."""
    rows = cursor.fetchall()
    if not rows: return []
    # Check first row for Row-like capabilities
    if hasattr(rows[0], 'keys'):
        return [dict(row) for row in rows]
    desc = cursor.description
    if desc:
        cols = [col[0] for col in desc]
        return [dict(zip(cols, row)) for row in rows]
    return rows
_SCHEMA_LOCK = threading.Lock()


def ensure_schema():
    """Run migrations once per process to guarantee new columns/tables exist."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        try:
            conn = _connect()
            init_pg_schema(conn)
            conn.close()
            _SCHEMA_READY = True
        except Exception as e:
            print(f"⚠️ Schema migration skipped: {e}")



# DB Config - PostgreSQL only
_env_db_type = os.getenv("DB_TYPE", "postgres").lower()
if _env_db_type != "postgres":
    raise RuntimeError("DB_TYPE must be 'postgres'. SQLite is not supported.")
DB_TYPE = "postgres"

# PostgreSQL Pool (Lazy Init)
_PG_POOL = None

def _get_pg_connection():
    """Get a connection to Cloud SQL PostgreSQL using SQLAlchemy Pooling.
    
    Supports two connection methods:
    1. DB_DSN / DATABASE_URL - Direct connection string (e.g., postgresql://user:pass@host/db)
    2. DB_INSTANCE_CONNECTION_NAME - Cloud SQL connector (for Google Cloud)
    """
    from sqlalchemy import create_engine
    
    global _PG_POOL
    
    if _PG_POOL:
        return _PG_POOL.raw_connection()

    # Method 1: Direct DSN connection string
    dsn = (os.getenv("DB_DSN") or os.getenv("DATABASE_URL") or "").strip()
    if dsn:
        _PG_POOL = create_engine(
            dsn,
            pool_size=10,
            max_overflow=5,
            pool_timeout=60,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        return _PG_POOL.raw_connection()

    # Method 2: Cloud SQL connector
    import pg8000
    from google.cloud.sql.connector import Connector
    
    instance_connection_name = os.getenv("DB_INSTANCE_CONNECTION_NAME")
    db_user = os.getenv("DB_USER", "omega_user")
    db_pass = os.getenv("DB_PASS")
    db_name = os.getenv("DB_NAME", "omega_db")

    if not instance_connection_name or not db_pass:
        raise ValueError("Missing DB_DSN or DB_INSTANCE_CONNECTION_NAME/DB_PASS env vars for PostgreSQL")

    # Initialize connector
    connector = Connector()

    def getconn():
        conn = connector.connect(
            instance_connection_name,
            "pg8000",
            user=db_user,
            password=db_pass,
            db=db_name,
            ip_type="public"
        )
        return conn

    # Create SQLAlchemy engine with pooling
    _PG_POOL = create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        pool_size=10,
        max_overflow=5,
        pool_timeout=60,
        pool_recycle=1800,
    )
    
    return _PG_POOL.raw_connection()



def begin_transaction():
    """Start a database transaction. Returns connection object."""
    conn = _connect()
    # Don't use context manager - caller manages lifecycle
    return conn

def commit_transaction(conn):
    """Commit a transaction."""
    conn.commit()

def rollback_transaction(conn):
    """Rollback a transaction."""
    conn.rollback()

class OmegaCursor:
    def __init__(self, cursor, db_type):
        self.cursor = cursor
        self.db_type = db_type
    
    def execute(self, query, params=None):
        if self.db_type == 'postgres':
             # Convert ? to %s for Postgres
             original_query = query
             query = query.replace('?', '%s')
             # print(f"DEBUG SQL (PG): {original_query} -> {query} | Params: {params}") # Uncomment for deeper debug
        if params is None:
            return self.cursor.execute(query)
        return self.cursor.execute(query, params)
        
    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None: return None
        if hasattr(row, 'keys'): return dict(row)
        if self.cursor.description:
            cols = [d[0] for d in self.cursor.description]
            return dict(zip(cols, row))
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        if not rows: return []
        if hasattr(rows[0], 'keys'): return [dict(r) for r in rows]
        if self.cursor.description:
            cols = [d[0] for d in self.cursor.description]
            return [dict(zip(cols, r)) for r in rows]
        return rows

    def __getattr__(self, attr):
        return getattr(self.cursor, attr)
        
    def __iter__(self):
        # Allow iteration (for row in cursor)
        return iter(self.fetchall())


class OmegaConnection:
    def __init__(self, conn, db_type):
        self.conn = conn
        self.db_type = db_type

    def cursor(self):
        return OmegaCursor(self.conn.cursor(), self.db_type)
    
    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        return self.conn.close()

    def __getattr__(self, attr):
        return getattr(self.conn, attr)


def _connect():
    """Get a PostgreSQL database connection."""
    return OmegaConnection(_get_pg_connection(), "postgres")

def init_pg_schema(conn):
    """Initialize Postgres schema (Cloud SQL)."""
    c = conn.cursor()
    
    # helper for idempotency
    # In PG, CREATE TABLE IF NOT EXISTS works.
    
    # JOBS
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            file_stem TEXT PRIMARY KEY,
            stage TEXT,
            status TEXT,
            progress REAL,
            updated_at TIMESTAMP,
            meta TEXT,
            target_language TEXT DEFAULT 'is',
            program_profile TEXT DEFAULT 'standard',
            subtitle_style TEXT DEFAULT 'Classic',
            editor_report TEXT,
            client TEXT DEFAULT 'unknown',
            due_date DATE,
            failure_count INTEGER DEFAULT 0,
            suggested_fixes TEXT,
            asset_id TEXT,
            client_id TEXT,
            vault_path TEXT,
            priority INTEGER DEFAULT 0,
            retry_after TIMESTAMP,
            claimed_at TIMESTAMP,
            worker_id TEXT,
            station_id TEXT
        )
    ''')

    # DELIVERIES
    c.execute('''
        CREATE TABLE IF NOT EXISTS deliveries (
            id SERIAL PRIMARY KEY,
            job_stem TEXT,
            client TEXT,
            delivered_at TIMESTAMP,
            method TEXT,
            notes TEXT
        )
    ''')

    # SYSTEM_STATE
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    c.execute("INSERT INTO system_state (key, value) VALUES ('db_version', '0') ON CONFLICT DO NOTHING")

    # PROGRAMS
    c.execute('''
        CREATE TABLE IF NOT EXISTS programs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            original_filename TEXT,
            video_path TEXT,
            thumbnail_path TEXT,
            duration_seconds REAL,
            client TEXT,
            due_date TEXT,
            default_style TEXT DEFAULT 'Classic',
            status TEXT DEFAULT 'ACTIVE',
            deleted_at TIMESTAMP,
            deleted_original_filename TEXT,
            deleted_video_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT
        )
    ''')

    # MASTER_SCRIPTS
    c.execute('''
        CREATE TABLE IF NOT EXISTS master_scripts (
            id TEXT PRIMARY KEY,
            program_id TEXT NOT NULL,
            language_code TEXT NOT NULL,
            language_name TEXT,
            state TEXT DEFAULT 'draft',
            version INTEGER DEFAULT 1,
            locked_at TIMESTAMP,
            locked_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT,
            FOREIGN KEY (program_id) REFERENCES programs(id)
        )
    ''')

    # TRACKS
    c.execute('''
        CREATE TABLE IF NOT EXISTS tracks (
            id TEXT PRIMARY KEY,
            program_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'subtitle',
            language_code TEXT NOT NULL,
            language_name TEXT,
            stage TEXT DEFAULT 'QUEUED',
            state TEXT DEFAULT 'draft',
            status TEXT DEFAULT 'Pending',
            progress REAL DEFAULT 0.0,
            delivery_status TEXT DEFAULT 'PENDING',
            job_id TEXT,
            voice_id TEXT,
            rating REAL,
            output_path TEXT,
            depends_on TEXT,
            master_script_id TEXT,
            output_version TEXT DEFAULT '1.0',
            output_override INTEGER DEFAULT 0,
            override_reason TEXT,
            override_author TEXT,
            override_timestamp TIMESTAMP,
            pending_resync INTEGER DEFAULT 0,
            locked_at TIMESTAMP,
            locked_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT,
            FOREIGN KEY (program_id) REFERENCES programs(id),
            FOREIGN KEY (depends_on) REFERENCES tracks(id),
            FOREIGN KEY (master_script_id) REFERENCES master_scripts(id)
        )
    ''')

    # TRACK_DELIVERIES
    c.execute('''
        CREATE TABLE IF NOT EXISTS track_deliveries (
            id TEXT PRIMARY KEY,
            track_id TEXT NOT NULL,
            destination TEXT,
            recipient TEXT,
            delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    ''')
    
    # SCRIPT_EDITS
    c.execute('''
        CREATE TABLE IF NOT EXISTS script_edits (
            id TEXT PRIMARY KEY,
            master_script_id TEXT NOT NULL,
            track_id TEXT,
            change_type TEXT NOT NULL,
            summary TEXT,
            author TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT,
            FOREIGN KEY (master_script_id) REFERENCES master_scripts(id),
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    ''')

    # STATIONS - Remote processing nodes (Iceland, etc.)
    c.execute('''
        CREATE TABLE IF NOT EXISTS stations (
            id TEXT PRIMARY KEY,
            display_name TEXT,
            tailscale_ip TEXT,
            last_heartbeat TIMESTAMP,
            status TEXT DEFAULT 'offline',
            jobs_processed INTEGER DEFAULT 0,
            config TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # STAGE_TRANSITIONS - Audit log for track stage changes
    c.execute('''
        CREATE TABLE IF NOT EXISTS stage_transitions (
            id SERIAL PRIMARY KEY,
            track_id TEXT NOT NULL,
            job_stem TEXT NOT NULL,
            from_stage TEXT,
            to_stage TEXT NOT NULL,
            processing_step TEXT,
            worker_id TEXT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # =========================================================================
    # OMEGA LITERATI - Book Translation Tables
    # =========================================================================

    # BOOK_PROJECTS - Top-level book metadata
    c.execute('''
        CREATE TABLE IF NOT EXISTS book_projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            source_language TEXT DEFAULT 'en',
            target_language TEXT DEFAULT 'is',
            source_file_path TEXT,
            total_chapters INTEGER DEFAULT 0,
            stage TEXT DEFAULT 'UPLOADING',
            status TEXT DEFAULT 'Pending',
            progress REAL DEFAULT 0.0,
            glossary TEXT,
            character_bible TEXT,
            style_guide TEXT,
            translation_notes TEXT,
            client TEXT,
            due_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT
        )
    ''')
    
    # BOOK_CHAPTERS - Individual chapters with translation stages
    c.execute('''
        CREATE TABLE IF NOT EXISTS book_chapters (
            id TEXT PRIMARY KEY,
            book_id TEXT NOT NULL,
            chapter_number INTEGER NOT NULL,
            chapter_title TEXT,
            source_text TEXT,
            step1_translation TEXT,
            step1_completed_at TIMESTAMP,
            step2_theology_review TEXT,
            step2_completed_at TIMESTAMP,
            step3_polish TEXT,
            step3_completed_at TIMESTAMP,
            final_text TEXT,
            stage TEXT DEFAULT 'PENDING',
            status TEXT DEFAULT 'Pending',
            locked_at TIMESTAMP,
            locked_by TEXT,
            word_count INTEGER DEFAULT 0,
            translation_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            meta TEXT,
            FOREIGN KEY (book_id) REFERENCES book_projects(id)
        )
    ''')

    # INDEXES
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_program ON tracks(program_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_stage ON tracks(stage)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_programs_client ON programs(client)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_master_scripts_program_lang ON master_scripts(program_id, language_code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_script_edits_master ON script_edits(master_script_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stations_status ON stations(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_jobs_station ON jobs(station_id)')
    
    # Book indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_book_chapters_book ON book_chapters(book_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_book_chapters_stage ON book_chapters(stage)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_book_projects_stage ON book_projects(stage)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_book_projects_client ON book_projects(client)')

    # Stage transitions indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_stage_transitions_track_id ON stage_transitions(track_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_stage_transitions_created_at ON stage_transitions(created_at)')

    # MINISTRY PROFILES (Workflow Architecture)
    c.execute('''
        CREATE TABLE IF NOT EXISTS ministry_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            languages TEXT NOT NULL,
            default_delivery_id TEXT,
            terminology TEXT,
            style TEXT,
            watch_folder TEXT,
            workflow TEXT DEFAULT 'standard',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        )
    ''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_ministry_profiles_slug ON ministry_profiles(slug)')

    # DELIVERY PROFILES
    c.execute('''
        CREATE TABLE IF NOT EXISTS delivery_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            outputs TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_profiles_slug ON delivery_profiles(slug)')

    # DROPZONE RECIPES
    c.execute('''
        CREATE TABLE IF NOT EXISTS dropzone_recipes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder_name TEXT UNIQUE NOT NULL,
            ministry_id TEXT,
            languages TEXT NOT NULL,
            delivery_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_dropzone_recipes_folder ON dropzone_recipes(folder_name)')

    # ERROR LOG - For observability and debugging
    c.execute('''
        CREATE TABLE IF NOT EXISTS error_log (
            id SERIAL PRIMARY KEY,
            job_id TEXT,
            error_type TEXT NOT NULL,
            message TEXT NOT NULL,
            traceback TEXT,
            worker TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_error_log_job_id ON error_log(job_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_error_log_created_at ON error_log(created_at)')

    # =========================================================================
    # ADD MISSING COLUMNS (PostgreSQL schema drift fixes)
    # Legacy columns that may be missing in older deployments.
    # =========================================================================

    # Helper function to add column if it doesn't exist
    def add_column_if_missing(table: str, column: str, col_type: str, default: str = None):
        try:
            c.execute(f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}'
            """)
            if not c.fetchone():
                default_clause = f" DEFAULT {default}" if default else ""
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}")
                logger.info(f"Added missing column {table}.{column}")
        except Exception as e:
            # Column might already exist or other error
            pass

    # Jobs table - missing columns
    add_column_if_missing('jobs', 'cloud_job_triggered_at', 'TIMESTAMP')
    add_column_if_missing('jobs', 'cloud_job_name', 'TEXT')
    add_column_if_missing('jobs', 'burn_profile', 'TEXT', "'broadcast_hevc'")
    add_column_if_missing('jobs', 'finalizer_ran_at', 'TIMESTAMP')
    add_column_if_missing('jobs', 'skeleton_path', 'TEXT')
    add_column_if_missing('jobs', 'transcript_version', 'INTEGER', '1')

    # Tracks table - missing columns
    add_column_if_missing('tracks', 'claimed_by', 'TEXT')
    add_column_if_missing('tracks', 'claimed_at', 'TIMESTAMP')
    add_column_if_missing('tracks', 'review_deadline', 'TIMESTAMP')
    add_column_if_missing('tracks', 'cloud_job_id', 'TEXT')
    add_column_if_missing('tracks', 'cloud_job_triggered_at', 'TIMESTAMP')

    # COMMIT IS HANDLED BY CALLER (usually) but harmless to commit DDL
    conn.commit()


def _increment_version(cursor):
    """Bumps the db_version so the frontend knows to refetch."""
    try:
        cursor.execute("UPDATE system_state SET value = CAST(value AS INTEGER) + 1 WHERE key = 'db_version'")
    except Exception:
        pass

def _parse_meta_value(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {}


def _merge_job_meta(program_meta, track_meta, program_id, track_id, language_code):
    merged = {}
    if isinstance(program_meta, dict):
        merged.update(program_meta)
    if isinstance(track_meta, dict):
        merged.update(track_meta)
    if program_id and "program_id" not in merged:
        merged["program_id"] = program_id
    if track_id and "track_id" not in merged:
        merged["track_id"] = track_id
    if language_code and "target_language" not in merged:
        merged["target_language"] = language_code
    return merged


def _job_dict_from_track_row(row):
    program_meta = _parse_meta_value(row.get("program_meta"))
    track_meta = _parse_meta_value(row.get("track_meta"))
    merged_meta = _merge_job_meta(
        program_meta,
        track_meta,
        row.get("program_id"),
        row.get("track_id"),
        row.get("language_code"),
    )

    target_language = (
        track_meta.get("target_language")
        or row.get("language_code")
        or program_meta.get("target_language")
        or "is"
    )
    program_profile = (
        track_meta.get("program_profile")
        or program_meta.get("program_profile")
        or "standard"
    )
    subtitle_style = (
        track_meta.get("subtitle_style")
        or program_meta.get("subtitle_style")
        or row.get("default_style")
        or "Classic"
    )
    editor_report = track_meta.get("editor_report") or program_meta.get("editor_report")
    client = row.get("client") or program_meta.get("client") or "unknown"
    due_date = row.get("due_date") or program_meta.get("due_date")

    if row.get("original_filename") and "original_filename" not in merged_meta:
        merged_meta["original_filename"] = row.get("original_filename")

    return {
        "file_stem": row.get("file_stem"),
        "title": row.get("title"),
        "stage": row.get("stage"),
        "status": row.get("status"),
        "progress": row.get("progress"),
        "target_language": target_language,
        "program_profile": program_profile,
        "subtitle_style": subtitle_style,
        "editor_report": editor_report,
        "client": client,
        "due_date": due_date,
        "original_filename": row.get("original_filename"),
        "video_path": row.get("video_path"),
        "thumbnail_path": row.get("thumbnail_path"),
        "created_at": row.get("track_created_at") or row.get("program_created_at"),
        "updated_at": row.get("track_updated_at") or row.get("program_updated_at"),
        "meta": merged_meta,
        "program_id": row.get("program_id"),
        "track_id": row.get("track_id"),
    }


def get_job_via_track(file_stem):
    """MIGRATION HELPER: Fetch a job via tracks/programs."""
    conn = _connect()
    c = conn.cursor()
    c.execute(
        """
        SELECT
            t.id AS track_id,
            t.job_id AS file_stem,
            t.stage,
            t.status,
            t.progress,
            t.language_code,
            t.language_name,
            t.meta AS track_meta,
            t.created_at AS track_created_at,
            t.updated_at AS track_updated_at,
            p.id AS program_id,
            p.title,
            p.original_filename,
            p.video_path,
            p.thumbnail_path,
            p.client,
            p.due_date,
            p.default_style,
            p.meta AS program_meta,
            p.created_at AS program_created_at,
            p.updated_at AS program_updated_at
        FROM tracks t
        JOIN programs p ON t.program_id = p.id
        WHERE t.job_id = ?
          AND (p.status != 'DELETED' OR p.status IS NULL)
        """,
        (file_stem,),
    )
    row = _fetchone_dict(c)
    conn.close()
    if not row:
        return None
    return _job_dict_from_track_row(row)


def get_all_jobs_via_tracks(stages: list = None, limit: int = 100) -> list:
    """MIGRATION HELPER: Fetch jobs via tracks/programs.
    
    Args:
        stages: List of stages to filter by.
        limit: Max number of records to return.
    """
    conn = _connect()
    # Ensure row_factory is set (it usually is by _connect/OmegaConnection, but explicit doesn't hurt)
    c = conn.cursor()
    
    base_query = """
        SELECT
            t.id AS track_id,
            t.job_id AS file_stem,
            t.stage,
            t.status,
            t.progress,
            t.language_code,
            t.language_name,
            t.meta AS track_meta,
            t.created_at AS track_created_at,
            t.updated_at AS track_updated_at,
            p.id AS program_id,
            p.title,
            p.original_filename,
            p.video_path,
            p.thumbnail_path,
            p.client,
            p.due_date,
            p.default_style,
            p.meta AS program_meta,
            p.created_at AS program_created_at,
            p.updated_at AS program_updated_at
        FROM tracks t
        JOIN programs p ON t.program_id = p.id
        WHERE t.job_id IS NOT NULL AND t.job_id != ''
          AND (p.status != 'DELETED' OR p.status IS NULL)
    """
    params = []
    
    if stages:
        if isinstance(stages, str):
            stages = [stages]
        placeholders = ", ".join(["?"] * len(stages))
        base_query += f" AND t.stage IN ({placeholders})"
        params.extend(stages)

    base_query += " ORDER BY t.updated_at DESC"
    base_query += f" LIMIT {limit}"
    
    c.execute(base_query, tuple(params))
    rows = _fetchall_dicts(c)
    conn.close()
    return [_job_dict_from_track_row(row) for row in rows]


def update_job_via_track(
    file_stem,
    stage=None,
    status=None,
    progress=None,
    meta=None,
    target_language=None,
    program_profile=None,
    subtitle_style=None,
    editor_report=None,
    client=None,
    due_date=None,
    title=None,
):
    """MIGRATION HELPER: Update job via tracks/programs."""
    ensure_schema()
    track = get_track_by_job(file_stem)
    if not track and isinstance(meta, dict):
        track_id = meta.get("track_id")
        if track_id:
            track = get_track(track_id)
    if not track:
        logging.warning("update_job_via_track: no track for job_id=%s", file_stem)
        return False

    now = datetime.now().isoformat()

    def _normalize_timeline(value):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    def _append_stage_timeline(timeline, stage_value, now_value):
        if not stage_value:
            return timeline, False
        timeline = list(timeline)
        last = timeline[-1] if timeline else None
        if last and last.get("stage") == stage_value and not last.get("ended_at"):
            return timeline, False
        if last and not last.get("ended_at"):
            last["ended_at"] = now_value
        timeline.append({"stage": stage_value, "started_at": now_value, "ended_at": None})
        return timeline, True

    def _append_status_timeline(timeline, status_value, now_value, max_items=50):
        if not status_value:
            return timeline, False
        timeline = list(timeline)
        last = timeline[-1] if timeline else None
        if last and last.get("status") == status_value:
            return timeline, False
        timeline.append({"status": status_value, "at": now_value})
        if max_items and len(timeline) > max_items:
            timeline = timeline[-max_items:]
        return timeline, True

    existing_meta = track.get("meta") if isinstance(track.get("meta"), dict) else {}
    incoming_meta = meta if isinstance(meta, dict) else {}
    merged_meta = {**existing_meta, **incoming_meta}
    meta_changed = False

    if stage is not None:
        timeline = _normalize_timeline(merged_meta.get("stage_timeline"))
        timeline, changed = _append_stage_timeline(timeline, stage, now)
        if changed or (timeline and timeline != merged_meta.get("stage_timeline")):
            merged_meta["stage_timeline"] = timeline
            meta_changed = True

    if status is not None:
        timeline = _normalize_timeline(merged_meta.get("status_timeline"))
        if not timeline and track.get("status"):
            timeline.append({"status": track.get("status"), "at": now})
        timeline, changed = _append_status_timeline(timeline, status, now)
        if changed or (timeline and timeline != merged_meta.get("status_timeline")):
            merged_meta["status_timeline"] = timeline
            meta_changed = True

    incoming_cloud_stage = incoming_meta.get("cloud_stage") if incoming_meta else None
    if incoming_cloud_stage:
        timeline = _normalize_timeline(merged_meta.get("cloud_stage_timeline"))
        timeline, changed = _append_stage_timeline(timeline, str(incoming_cloud_stage), now)
        if changed or (timeline and timeline != merged_meta.get("cloud_stage_timeline")):
            merged_meta["cloud_stage_timeline"] = timeline
            meta_changed = True

    track_updates = {}
    if stage is not None:
        track_updates["stage"] = stage
    if status is not None:
        track_updates["status"] = status
    if progress is not None:
        track_updates["progress"] = progress

    if target_language is not None:
        track_updates["language_code"] = target_language
        try:
            from profiles import LANGUAGES
            lang_info = LANGUAGES.get(str(target_language).lower(), {})
            lang_name = lang_info.get("name")
            if lang_name:
                track_updates["language_name"] = lang_name
        except Exception:
            pass
        merged_meta["target_language"] = target_language
        meta_changed = True

    if program_profile is not None:
        merged_meta["program_profile"] = program_profile
        meta_changed = True

    if subtitle_style is not None:
        merged_meta["subtitle_style"] = subtitle_style
        meta_changed = True

    if editor_report is not None:
        merged_meta["editor_report"] = editor_report
        meta_changed = True
        # Extract rating from editor report and save to track.rating column
        try:
            report_data = json.loads(editor_report) if isinstance(editor_report, str) else editor_report
            if isinstance(report_data, dict) and "rating" in report_data:
                track_updates["rating"] = float(report_data["rating"])
        except Exception:
            pass

    if meta is not None or meta_changed:
        track_updates["meta"] = merged_meta

    # Extract final_output from meta and set as track.output_path
    final_output = merged_meta.get("final_output")
    if final_output and not track.get("output_path"):
        track_updates["output_path"] = str(final_output)

    if track_updates:
        update_track(track["id"], **track_updates)

    program_updates = {}
    if title is not None:
        program_updates["title"] = title
    if client is not None:
        program_updates["client"] = client
    if due_date is not None:
        program_updates["due_date"] = due_date
    if subtitle_style is not None:
        program_updates["default_style"] = subtitle_style

    if program_updates:
        update_program(track["program_id"], **program_updates)

    try:
        conn = _connect()
        c = conn.cursor()
        _increment_version(c)
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return True


def update(
    file_stem,
    stage=None,
    status=None,
    progress=None,
    meta=None,
    target_language=None,
    program_profile=None,
    subtitle_style=None,
    editor_report=None,
    client=None,
    due_date=None,
):
    """DEPRECATED: Use update_job_via_track()."""
    logging.warning("DEPRECATED: omega_db.update() called for %s", file_stem)
    return update_job_via_track(
        file_stem,
        stage=stage,
        status=status,
        progress=progress,
        meta=meta,
        target_language=target_language,
        program_profile=program_profile,
        subtitle_style=subtitle_style,
        editor_report=editor_report,
        client=client,
        due_date=due_date,
    )


def get_job(file_stem):
    """DEPRECATED: Use get_job_via_track()."""
    logging.warning("DEPRECATED: omega_db.get_job() called for %s", file_stem)
    return get_job_via_track(file_stem)

def get_all_jobs(stage: str = None):
    """DEPRECATED: Use get_all_jobs_via_tracks()."""
    logging.warning("DEPRECATED: omega_db.get_all_jobs() called")
    return get_all_jobs_via_tracks(stage)

def delete_job(file_stem):
    """Deep delete a job, including programs, tracks, and files on disk."""
    print(f"DEBUG: delete_job START for '{file_stem}'")
    if not file_stem or len(file_stem) < 2:
        print("DEBUG: file_stem too short")
        return
        return
    
    # 1. Database Cleanup
    try:
        conn = _connect()
        c = conn.cursor()

        # Legacy Jobs
        c.execute("DELETE FROM jobs WHERE file_stem=?", (file_stem,))
        print(f"DEBUG: {c.rowcount} legacy jobs deleted")

        # Modern Programs & Tracks
        # Find programs that match this file_stem (could be title, filename, or UUID)
        print(f"DEBUG: Searching programs for title='{file_stem}' or matches")
        c.execute(
            "SELECT id, title FROM programs WHERE id=? OR title=? OR original_filename LIKE ?",
            (file_stem, file_stem, f"{file_stem}%")
        )
        program_rows = c.fetchall()
        print(f"DEBUG: Found {len(program_rows)} programs to delete")
        
        for row in program_rows:
            try:
                p_id = row['id']
                
                # Delete tracks first
                c.execute("DELETE FROM tracks WHERE program_id=?", (p_id,))
                
                # Delete master scripts (orphan prevention)
                try:
                    c.execute("DELETE FROM master_scripts WHERE program_id=?", (p_id,))
                except Exception as e:
                    print(f"Warning: Failed to delete master_scripts for program {p_id}: {e}")

                # Delete programs
                c.execute("DELETE FROM programs WHERE id=?", (p_id,))
                
            except Exception as e:
                print(f"Error checking program deletion for row {row}: {e}")
            
        conn.commit()
        print(f"Deep delete successful for '{file_stem}'.")
    except Exception as e:
        print(f"Deep delete failed for '{file_stem}': {e}")
        # Don't re-raise, allow file cleanup to attempt to proceed
    finally:
        if conn: conn.close()

    # 2. Disk Cleanup
    _delete_files_from_disk(file_stem)

def _delete_files_from_disk(stem):
    """Remove all files matching the stem from critical folders."""
    if not stem: return
    
    # Folders to scrub
    folders = [
        config.INBOX_DIR,
        config.VAULT_DIR,
        config.VAULT_DIR / "Audio",
        config.VAULT_DIR / "Videos",
        config.PROXY_DIR,
        config.DELIVERY_DIR,
        config.ARCHIVE_DIR
    ]
    
    count = 0
    for folder in folders:
        if not folder.exists(): continue
        try:
            # Delete files starting with stem (case insensitive-ish by glob)
            # We iterate to be safe
            for f in folder.glob(f"{stem}*"):
                if f.is_file():
                    try:
                        f.unlink()
                        count += 1
                    except Exception as e:
                        print(f"Warning: Failed to delete file {f}: {e}")
                elif f.is_dir():
                    # Optional: Remove dir if it matches stem EXACTLY (like a job folder)
                    if f.name == stem:
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                        count += 1
        except Exception as e:
            print(f"Warning: Error during cleanup in {search_dir}: {e}")
    # print(f"Deleted {count} files for {stem}")


def log_delivery(job_stem, client, delivered_at, method, notes=""):
    """Log a delivery in the deliveries table."""
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        INSERT INTO deliveries (job_stem, client, delivered_at, method, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (job_stem, client, delivered_at, method, notes))
    conn.commit()
    conn.close()


def log_stage_transition(track_id, job_stem, from_stage, to_stage, processing_step=None, worker_id=None, reason=None):
    """Log a stage transition in the stage_transitions audit table.

    Args:
        track_id: The track ID (e.g., UUID of the track)
        job_stem: The job stem/file identifier
        from_stage: The previous stage (can be None for initial state)
        to_stage: The new stage (required)
        processing_step: Optional identifier for the processing step (e.g., 'transcribe', 'translate')
        worker_id: Optional identifier for the worker/process that triggered the transition
        reason: Optional reason/notes for the transition
    """
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        INSERT INTO stage_transitions (track_id, job_stem, from_stage, to_stage, processing_step, worker_id, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (track_id, job_stem, from_stage, to_stage, processing_step, worker_id, reason))
    conn.commit()
    conn.close()


def get_stage_transitions(track_id=None, job_stem=None, limit=100):
    """Get stage transition history.

    Args:
        track_id: Filter by track ID
        job_stem: Filter by job stem
        limit: Maximum number of records to return (default 100)

    Returns:
        List of stage transition records as dictionaries
    """
    conn = _connect()
    c = conn.cursor()

    if track_id:
        c.execute("SELECT * FROM stage_transitions WHERE track_id=? ORDER BY created_at DESC LIMIT ?", (track_id, limit))
    elif job_stem:
        c.execute("SELECT * FROM stage_transitions WHERE job_stem=? ORDER BY created_at DESC LIMIT ?", (job_stem, limit))
    else:
        c.execute("SELECT * FROM stage_transitions ORDER BY created_at DESC LIMIT ?", (limit,))

    rows = c.fetchall()
    conn.close()
    return rows if isinstance(rows, list) and rows and isinstance(rows[0], dict) else [dict(row) if hasattr(row, 'keys') else row for row in rows]


def record_error(job_id: str, error_type: str, message: str, traceback: str = None, worker: str = None):
    """Record an error for observability and debugging.

    Args:
        job_id: The job ID associated with this error (can be None for system errors)
        error_type: Category of error (e.g., 'transcription', 'translation', 'burn', 'system')
        message: Human-readable error message (truncated to 1000 chars)
        traceback: Full traceback string (optional)
        worker: Name of the worker that encountered the error (optional)
    """
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        INSERT INTO error_log (job_id, error_type, message, traceback, worker)
        VALUES (?, ?, ?, ?, ?)
    ''', (job_id, error_type, (message or "")[:1000], traceback, worker))
    conn.commit()
    conn.close()


def get_recent_errors(hours: int = 24, job_id: str = None, limit: int = 50) -> list:
    """Get recent errors from the error log.

    Args:
        hours: Look back this many hours (default 24)
        job_id: Filter by job ID (optional)
        limit: Maximum number of errors to return (default 50)

    Returns:
        List of error records as dictionaries
    """
    conn = _connect()
    c = conn.cursor()

    # Fixed syntax error (removed orphan else) and corrected for SQLite
    time_filter = f"datetime('now', '-{hours} hours')"

    if job_id:
        c.execute(f"""
            SELECT * FROM error_log
            WHERE job_id = ? AND created_at >= {time_filter}
            ORDER BY created_at DESC LIMIT ?
        """, (job_id, limit))
    else:
        c.execute(f"""
            SELECT * FROM error_log
            WHERE created_at >= {time_filter}
            ORDER BY created_at DESC LIMIT ?
        """, (limit,))

    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_deliveries(job_stem=None):
    """Get delivery history. If job_stem provided, filter to that job."""
    conn = _connect()
    c = conn.cursor()
    if job_stem:
        c.execute("SELECT * FROM deliveries WHERE job_stem=? ORDER BY delivered_at DESC", (job_stem,))
    else:
        c.execute("SELECT * FROM deliveries ORDER BY delivered_at DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_jobs_since(last_version_processed):
    """Placeholder for delta-fetching logic if needed later."""
    # For now, we use the version to trigger a full fetch of only metadata
    # but the goal is to only fetch the delta.
    return get_all_jobs_via_tracks()


# =============================================================================
# PROGRAMS & TRACKS (Localization Platform API)
# =============================================================================

import uuid

def _generate_id() -> str:
    """Generate a UUID for new records."""
    return str(uuid.uuid4())

# --- PROGRAMS ---

def create_program(
    title: str,
    original_filename: str = None,
    video_path: str = None,
    thumbnail_path: str = None,
    duration_seconds: float = None,
    client: str = None,
    due_date: str = None,
    default_style: str = 'Classic',
    meta: dict = None
) -> str:
    """Create a new program. Returns program ID."""
    program_id = _generate_id()
    now = datetime.now().isoformat()
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO programs (id, title, original_filename, video_path, thumbnail_path,
                                  duration_seconds, client, due_date, default_style, 
                                  created_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (program_id, title, original_filename, video_path, thumbnail_path,
              duration_seconds, client, due_date, default_style,
              now, now, json.dumps(meta or {})))
        conn.commit()
    finally:
        conn.close()
    
    return program_id


def _program_row_to_dict(row) -> dict:
    if not row:
        return None
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except Exception:
            result['meta'] = {}
    return result


def get_program(program_id: str, include_deleted: bool = False) -> dict:
    """Get a program by ID. Returns None if not found or deleted (unless include_deleted=True)."""
    conn = _connect()
    c = conn.cursor()
    if include_deleted:
        c.execute("SELECT * FROM programs WHERE id=?", (program_id,))
    else:
        c.execute("SELECT * FROM programs WHERE id=? AND (status != 'DELETED' OR status IS NULL)", (program_id,))
    row = c.fetchone()
    conn.close()

    return _program_row_to_dict(row)


def update_program(program_id: str, **kwargs) -> bool:
    """Update program fields. Returns True if updated."""
    if not kwargs:
        return False
    
    # Handle meta specially
    if 'meta' in kwargs and isinstance(kwargs['meta'], dict):
        # Merge with existing meta
        existing = get_program(program_id)
        if existing:
            existing_meta = existing.get('meta', {}) or {}
            existing_meta.update(kwargs['meta'])
            kwargs['meta'] = json.dumps(existing_meta)
    
    kwargs['updated_at'] = datetime.now().isoformat()
    
    set_clause = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [program_id]
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute(f"UPDATE programs SET {set_clause} WHERE id=?", values)
        conn.commit()
        success = c.rowcount > 0
    finally:
        conn.close()

    return success


def get_all_programs(client: str = None, limit: int = 100) -> list:
    """Get all programs, optionally filtered by client."""
    conn = _connect()
    c = conn.cursor()
    
    if client:
        c.execute("""
            SELECT * FROM programs 
            WHERE client=? AND (status != 'DELETED' OR status IS NULL)
            ORDER BY created_at DESC LIMIT ?
        """, (client, limit))
    else:
        c.execute("""
            SELECT * FROM programs 
            WHERE (status != 'DELETED' OR status IS NULL)
            ORDER BY created_at DESC LIMIT ?
        """, (limit,))
    
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


def get_program_by_video(video_path: str, include_deleted: bool = False) -> dict:
    """Find program by video path. Returns None if not found or deleted (unless include_deleted=True)."""
    if not video_path:
        return None
    conn = _connect()
    c = conn.cursor()
    if include_deleted:
        c.execute(
            "SELECT * FROM programs WHERE video_path=? ORDER BY updated_at DESC LIMIT 1",
            (video_path,),
        )
    else:
        c.execute(
            """
            SELECT * FROM programs
            WHERE video_path=? AND (status != 'DELETED' OR status IS NULL)
            ORDER BY updated_at DESC LIMIT 1
            """,
            (video_path,),
        )
    row = c.fetchone()
    conn.close()

    return _program_row_to_dict(row)


def get_program_by_original_filename(original_filename: str, include_deleted: bool = False) -> dict:
    """Find program by original filename. Returns None if not found or deleted (unless include_deleted=True)."""
    if not original_filename:
        return None
    conn = _connect()
    c = conn.cursor()
    if include_deleted:
        c.execute(
            "SELECT * FROM programs WHERE original_filename=? ORDER BY updated_at DESC LIMIT 1",
            (original_filename,),
        )
    else:
        c.execute(
            """
            SELECT * FROM programs
            WHERE original_filename=? AND (status != 'DELETED' OR status IS NULL)
            ORDER BY updated_at DESC LIMIT 1
            """,
            (original_filename,),
        )
    row = c.fetchone()
    conn.close()

    return _program_row_to_dict(row)


def get_program_by_title(title: str, include_deleted: bool = False) -> dict:
    """Find program by title. Returns None if not found or deleted (unless include_deleted=True)."""
    if not title:
        return None
    conn = _connect()
    c = conn.cursor()
    if include_deleted:
        c.execute(
            """
            SELECT * FROM programs
            WHERE LOWER(title)=LOWER(?)
            ORDER BY CASE WHEN status = 'DELETED' THEN 1 ELSE 0 END,
                     updated_at DESC
            LIMIT 1
            """,
            (title,),
        )
    else:
        c.execute(
            """
            SELECT * FROM programs
            WHERE LOWER(title)=LOWER(?) AND (status != 'DELETED' OR status IS NULL)
            ORDER BY updated_at DESC LIMIT 1
            """,
            (title,),
        )
    row = c.fetchone()
    conn.close()

    return _program_row_to_dict(row)


def get_deleted_program_by_video(video_path: str) -> dict:
    """Find a deleted program by video path."""
    if not video_path:
        return None
    conn = _connect()
    c = conn.cursor()
    c.execute(
        """
        SELECT * FROM programs
        WHERE status = 'DELETED' AND (video_path=? OR deleted_video_path=?)
        ORDER BY deleted_at DESC LIMIT 1
        """,
        (video_path, video_path),
    )
    row = c.fetchone()
    conn.close()
    return _program_row_to_dict(row)


def get_deleted_program_by_original_filename(original_filename: str) -> dict:
    """Find a deleted program by original filename."""
    if not original_filename:
        return None
    conn = _connect()
    c = conn.cursor()
    c.execute(
        """
        SELECT * FROM programs
        WHERE status = 'DELETED' AND (original_filename=? OR deleted_original_filename=?)
        ORDER BY deleted_at DESC LIMIT 1
        """,
        (original_filename, original_filename),
    )
    row = c.fetchone()
    conn.close()
    return _program_row_to_dict(row)


def get_deleted_program_by_title(title: str) -> dict:
    """Find a deleted program by title."""
    if not title:
        return None
    conn = _connect()
    c = conn.cursor()
    c.execute(
        """
        SELECT * FROM programs
        WHERE status = 'DELETED' AND title=?
        ORDER BY deleted_at DESC LIMIT 1
        """,
        (title,),
    )
    row = c.fetchone()
    conn.close()
    return _program_row_to_dict(row)


# --- TRACKS ---

def create_track(
    program_id: str,
    type: str = 'subtitle',
    language_code: str = 'is',
    language_name: str = None,
    stage: str = 'QUEUED',
    status: str = 'Pending',
    job_id: str = None,
    voice_id: str = None,
    depends_on: str = None,
    master_script_id: str = None,
    output_version: str = "1.0",
    pending_resync: bool = False,
    meta: dict = None
) -> str:
    """Create a new track for a program. Returns track ID."""
    ensure_schema()
    track_id = _generate_id()
    now = datetime.now().isoformat()
    
    # Auto-fill language name from profiles if not provided
    if not language_name:
        from profiles import LANGUAGES
        lang_info = LANGUAGES.get(language_code, {})
        language_name = lang_info.get('name', language_code.upper())

    if not master_script_id:
        master_script_id = ensure_master_script(
            program_id=program_id,
            language_code=language_code,
            language_name=language_name,
        )
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO tracks (id, program_id, type, language_code, language_name,
                               stage, status, progress, job_id, voice_id, depends_on,
                               master_script_id, output_version, output_override, pending_resync,
                               created_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        ''', (track_id, program_id, type, language_code, language_name,
              stage, status, job_id, voice_id, depends_on,
              master_script_id, output_version, int(bool(pending_resync)),
              now, now, json.dumps(meta or {})))
        conn.commit()
    finally:
        conn.close()
    
    # Sync to Cloud
    return track_id


def get_track(track_id: str) -> dict:
    """Get a track by ID."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


def get_tracks_for_program(program_id: str) -> list:
    """Get all tracks for a program."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE program_id=? ORDER BY created_at", (program_id,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


def update_track(track_id: str, **kwargs) -> bool:
    """Update track fields."""
    if not kwargs:
        return False
    ensure_schema()
    
    # Handle meta specially
    if 'meta' in kwargs and isinstance(kwargs['meta'], dict):
        existing = get_track(track_id)
        if existing:
            existing_meta = existing.get('meta', {}) or {}
            existing_meta.update(kwargs['meta'])
            kwargs['meta'] = json.dumps(existing_meta)
    
    kwargs['updated_at'] = datetime.now().isoformat()
    
    set_clause = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [track_id]
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute(f"UPDATE tracks SET {set_clause} WHERE id=?", values)
        conn.commit()
        success = c.rowcount > 0
    finally:
        conn.close()
    
    return success


def get_track_by_job(job_id: str) -> dict:
    """Find track by linked job ID."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM tracks WHERE job_id=?", (job_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    result = dict(row)
    if result.get('meta'):
        try:
            result['meta'] = json.loads(result['meta'])
        except:
            result['meta'] = {}
    return result


# --- LANGUAGE FORKING ---

def fork_language(program_id: str, language_code: str, track_type: str = "subtitle") -> dict:
    """
    Create a new language track for an existing program.

    This is THE function for adding new languages. It handles:
    1. Finding the program and validating it exists
    2. Checking for duplicate tracks
    3. Finding the skeleton file (checks multiple patterns)
    4. Generating a proper job_id
    5. Copying skeleton to new location
    6. Creating the track in database
    7. Uploading to GCS
    8. Triggering the cloud worker

    Args:
        program_id: UUID of the program
        language_code: ISO language code (e.g., 'nl', 'es', 'de')
        track_type: 'subtitle' or 'dub'

    Returns:
        dict with 'track_id', 'job_id', 'status'

    Raises:
        ValueError: If program not found, duplicate exists, or no skeleton
    """
    import shutil
    from gcs_jobs import new_job_id, GcsJobPaths
    from google.cloud import storage

    logger = logging.getLogger("omega_db.fork_language")
    language_code = language_code.lower().strip()

    # STEP 1: Get program
    program = get_program(program_id)
    if not program:
        raise ValueError(f"Program {program_id} not found")

    logger.info(f"Forking language {language_code} for program: {program.get('title', program_id)}")

    # STEP 2: Check for existing track in this language
    existing_tracks = get_tracks_for_program(program_id)
    for t in existing_tracks:
        if (t.get('language_code', '').lower() == language_code and
            t.get('type', 'subtitle').lower() == track_type.lower()):
            raise ValueError(f"{language_code} {track_type} track already exists (track_id: {t.get('id')})")

    # STEP 3: Find the skeleton file
    skeleton_path = _find_skeleton_for_program(program_id, existing_tracks, program)
    if not skeleton_path:
        raise ValueError(f"No skeleton found for program {program_id}. Has it been transcribed?")

    logger.info(f"Found skeleton: {skeleton_path}")

    # STEP 4: Generate new job_id
    original_stem = Path(program.get('original_filename', '')).stem
    if not original_stem:
        original_stem = program_id[:8]  # Fallback to UUID prefix
    job_id = new_job_id(f"{original_stem}_{language_code}")
    logger.info(f"Generated job_id: {job_id}")

    # STEP 5: Copy skeleton to new location
    new_skeleton_path = config.VAULT_DATA / f"{job_id}_SKELETON.json"
    try:
        shutil.copy2(skeleton_path, new_skeleton_path)
        logger.info(f"Copied skeleton to: {new_skeleton_path}")
    except Exception as e:
        raise ValueError(f"Failed to copy skeleton: {e}")

    # STEP 6: Create track in database
    now = datetime.now().isoformat()
    track_meta = {
        'skeleton_path': str(new_skeleton_path),
        'forked_from': str(skeleton_path),
        'forked_at': now,
        'original_filename': program.get('original_filename'),
        'target_language': language_code,
    }

    # Preserve vault_path from program if available
    program_meta = program.get('meta') or {}
    if isinstance(program_meta, str):
        try:
            program_meta = json.loads(program_meta)
        except:
            program_meta = {}
    if program_meta.get('vault_path'):
        track_meta['vault_path'] = program_meta['vault_path']
    if program_meta.get('station_id'):
        track_meta['station_id'] = program_meta['station_id']

    track_id = create_track(
        program_id=program_id,
        type=track_type,
        language_code=language_code,
        stage='TRANSCRIBED',
        status='Ready for Translation',
        job_id=job_id,
        meta=track_meta
    )
    logger.info(f"Created track: {track_id}")

    # STEP 7: Upload to GCS
    bucket_name = getattr(config, 'OMEGA_JOBS_BUCKET', None)
    prefix = getattr(config, 'OMEGA_JOBS_PREFIX', 'jobs')

    if bucket_name:
        try:
            client = storage.Client()
            bucket = client.bucket(bucket_name)
            paths = GcsJobPaths(bucket=bucket_name, prefix=prefix, job_id=job_id)

            # Upload skeleton
            blob = bucket.blob(paths.skeleton_blob)
            blob.upload_from_filename(str(new_skeleton_path))
            logger.info(f"Uploaded skeleton to GCS: {paths.skeleton_blob}")

            # Upload job.json metadata
            job_payload = {
                "id": job_id,
                "file_stem": original_stem,
                "target_language": language_code,
                "program_profile": "standard",
                "meta": track_meta,
                "created_at": now
            }
            job_blob = bucket.blob(paths.job_blob)
            job_blob.upload_from_string(
                json.dumps(job_payload, ensure_ascii=False, indent=2),
                content_type="application/json"
            )
            logger.info(f"Uploaded job.json to GCS: {paths.job_blob}")

            # Update track with upload timestamp
            update_track(track_id, meta={**track_meta, 'uploaded_at': now})

        except Exception as e:
            logger.error(f"GCS upload failed: {e}")
            # Track is created, it can still work - don't fail completely
            update_track(track_id, status=f"GCS upload failed: {e}")

    # STEP 8: Update stage to trigger cloud worker
    update_track(track_id, stage='TRANSLATING_CLOUD_SUBMITTED', status=f'Translating ({language_code})')

    logger.info(f"Fork complete: track={track_id}, job={job_id}")

    return {
        "track_id": track_id,
        "job_id": job_id,
        "status": "translating",
        "skeleton_path": str(new_skeleton_path)
    }


def _find_skeleton_for_program(program_id: str, existing_tracks: list, program: dict) -> Path:
    """
    Find the skeleton file for a program.
    Checks multiple patterns in order of preference.

    Returns Path if found, None otherwise.
    """
    VAULT_DATA = config.VAULT_DATA

    # Pattern 1: Check existing tracks for skeleton_path in meta
    for track in existing_tracks:
        meta = track.get('meta') or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}
        skel = meta.get('skeleton_path')
        if skel:
            path = Path(skel)
            if path.exists():
                return path

    # Pattern 2: Check by job_id patterns from existing tracks
    for track in existing_tracks:
        job_id = track.get('job_id')
        if job_id:
            for suffix in ['_SKELETON.json', '_SKELETON_DONE.json']:
                path = VAULT_DATA / f"{job_id}{suffix}"
                if path.exists():
                    return path

    # Pattern 3: Check by original filename stem
    original_filename = program.get('original_filename', '')
    if original_filename:
        stem = Path(original_filename).stem
        for suffix in ['_SKELETON.json', '_SKELETON_DONE.json']:
            path = VAULT_DATA / f"{stem}{suffix}"
            if path.exists():
                return path

    # Pattern 4: Check program meta for skeleton_path
    program_meta = program.get('meta') or {}
    if isinstance(program_meta, str):
        try:
            program_meta = json.loads(program_meta)
        except:
            program_meta = {}
    skel = program_meta.get('skeleton_path')
    if skel:
        path = Path(skel)
        if path.exists():
            return path

    return None


# --- MASTER SCRIPTS ---

def create_master_script(
    program_id: str,
    language_code: str,
    language_name: str = None,
    state: str = "draft",
    version: int = 1,
    locked_at: str = None,
    locked_by: str = None,
    meta: dict = None,
) -> str:
    """Create a master script for a program/language."""
    ensure_schema()
    script_id = _generate_id()
    now = datetime.now().isoformat()

    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO master_scripts (
                id, program_id, language_code, language_name,
                state, version, locked_at, locked_by, created_at, updated_at, meta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            script_id, program_id, language_code, language_name,
            state, int(version), locked_at, locked_by, now, now, json.dumps(meta or {})
        ))
        conn.commit()
    finally:
        conn.close()
    return script_id


def get_master_script(master_script_id: str) -> dict:
    """Get a master script by ID."""
    ensure_schema()
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM master_scripts WHERE id=?", (master_script_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("meta"):
        try:
            result["meta"] = json.loads(result["meta"])
        except Exception:
            result["meta"] = {}
    return result


def get_master_script_for_program(program_id: str, language_code: str) -> dict:
    """Get the master script for a program + language."""
    ensure_schema()
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM master_scripts WHERE program_id=? AND language_code=?",
        (program_id, language_code),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    if result.get("meta"):
        try:
            result["meta"] = json.loads(result["meta"])
        except Exception:
            result["meta"] = {}
    return result


def ensure_master_script(program_id: str, language_code: str, language_name: str = None) -> str:
    """Create the master script if missing, return its ID."""
    existing = get_master_script_for_program(program_id, language_code)
    if existing:
        return existing.get("id")
    return create_master_script(
        program_id=program_id,
        language_code=language_code,
        language_name=language_name,
        state="draft",
        version=1,
    )


def update_master_script(master_script_id: str, **kwargs) -> bool:
    """Update master script fields."""
    if not kwargs:
        return False
    ensure_schema()

    if 'meta' in kwargs and isinstance(kwargs['meta'], dict):
        existing = get_master_script(master_script_id)
        if existing:
            existing_meta = existing.get('meta', {}) or {}
            existing_meta.update(kwargs['meta'])
            kwargs['meta'] = json.dumps(existing_meta)

    kwargs['updated_at'] = datetime.now().isoformat()
    set_clause = ', '.join(f"{k}=?" for k in kwargs.keys())
    values = list(kwargs.values()) + [master_script_id]

    conn = _connect()
    c = conn.cursor()
    try:
        c.execute(f"UPDATE master_scripts SET {set_clause} WHERE id=?", values)
        conn.commit()
        success = c.rowcount > 0
    finally:
        conn.close()

    return success


def log_script_edit(
    master_script_id: str,
    change_type: str,
    track_id: str = None,
    summary: str = None,
    author: str = None,
    meta: dict = None,
) -> str:
    """Log a classified script edit."""
    ensure_schema()
    edit_id = _generate_id()
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO script_edits (
                id, master_script_id, track_id, change_type, summary, author, created_at, meta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            edit_id,
            master_script_id,
            track_id,
            change_type,
            summary,
            author,
            datetime.now().isoformat(),
            json.dumps(meta or {}),
        ))
        conn.commit()
    finally:
        conn.close()
    return edit_id


def get_active_tracks(limit: int = 50) -> list:
    """Get all tracks that are in progress (not COMPLETE/COMPLETED or DELIVERED)."""
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        SELECT t.*, p.title as program_title 
        FROM tracks t 
        JOIN programs p ON t.program_id = p.id
        WHERE t.stage NOT IN ('COMPLETE', 'COMPLETED', 'DELIVERED')
        ORDER BY t.updated_at DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except:
                r['meta'] = {}
        results.append(r)
    return results


def get_all_tracks(limit: int = None) -> list:
    """Get all tracks, optionally limited."""
    ensure_schema()
    conn = _connect()
    c = conn.cursor()
    if limit:
        c.execute("SELECT * FROM tracks ORDER BY updated_at DESC LIMIT ?", (limit,))
    else:
        c.execute("SELECT * FROM tracks ORDER BY updated_at DESC")
    rows = c.fetchall()
    conn.close()

    results = []
    for row in rows:
        r = dict(row)
        if r.get('meta'):
            try:
                r['meta'] = json.loads(r['meta'])
            except Exception:
                r['meta'] = {}
        results.append(r)
    return results


# --- TRACK DELIVERIES ---

def record_track_delivery(
    track_id: str,
    destination: str,
    recipient: str = None,
    notes: str = None,
    lock_master: bool = True,
    locked_by: str = None,
    clear_pending_resync: bool = True,
) -> str:
    """Record that a track was delivered. Returns delivery ID."""
    ensure_schema()
    delivery_id = _generate_id()
    now = datetime.now().isoformat()
    
    conn = _connect()
    c = conn.cursor()
    try:
        c.execute('''
            INSERT INTO track_deliveries (id, track_id, destination, recipient, delivered_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (delivery_id, track_id, destination, recipient, now, notes))
        
        # Update delivery_status (keep stage as COMPLETE for production clarity)
        # Update delivery_status (keep stage as COMPLETE for production clarity)
        # Fix: Only update locked_at if lock_master is True (implying final delivery)
        if clear_pending_resync:
            if lock_master:
                c.execute(
                    "UPDATE tracks SET delivery_status='DELIVERED', pending_resync=0, locked_at=?, locked_by=?, updated_at=? WHERE id=?",
                    (now, locked_by or "system", now, track_id),
                )
            else:
                c.execute(
                    "UPDATE tracks SET delivery_status='DELIVERED', pending_resync=0, updated_at=? WHERE id=?",
                    (now, track_id),
                )
        else:
            if lock_master:
                c.execute(
                    "UPDATE tracks SET delivery_status='DELIVERED', locked_at=?, locked_by=?, updated_at=? WHERE id=?",
                    (now, locked_by or "system", now, track_id),
                )
            else:
                c.execute(
                    "UPDATE tracks SET delivery_status='DELIVERED', updated_at=? WHERE id=?",
                    (now, track_id),
                )
        
        conn.commit()
    finally:
        conn.close()

    if lock_master:
        track = get_track(track_id)
        master_script_id = track.get("master_script_id") if track else None
        if master_script_id:
            master = get_master_script(master_script_id)
            if master and not master.get("locked_at"):
                update_master_script(
                    master_script_id,
                    state="locked",
                    locked_at=now,
                    locked_by=locked_by or "system",
                )
    
    # Final sync check
    return delivery_id


def get_deliveries_for_track(track_id: str) -> list:
    """Get all delivery records for a track."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM track_deliveries WHERE track_id=? ORDER BY delivered_at DESC", (track_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_deliveries(days: int = 7, limit: int = 100) -> list:
    """Get recent deliveries with program/track info."""
    conn = _connect()
    c = conn.cursor()
    c.execute('''
        SELECT d.*, t.language_code, t.type as track_type, p.title as program_title
        FROM track_deliveries d
        JOIN tracks t ON d.track_id = t.id
        JOIN programs p ON t.program_id = p.id
        WHERE d.delivered_at >= datetime('now', ?)
        ORDER BY d.delivered_at DESC
        LIMIT ?
    ''', (f'-{days} days', limit))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# =============================================================================
# STATION MANAGEMENT (Iceland CDN, etc.)
# =============================================================================

def register_station(
    station_id: str,
    display_name: str = None,
    tailscale_ip: str = None,
    config: dict = None
) -> bool:
    """
    Register or update a remote processing station.
    Called on station startup and periodically during heartbeat.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config_json = json.dumps(config) if config else None
    
    try:
        c.execute('''
            INSERT INTO stations (id, display_name, tailscale_ip, config, last_heartbeat, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'online', %s)
            ON CONFLICT (id) DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, stations.display_name),
                tailscale_ip = COALESCE(EXCLUDED.tailscale_ip, stations.tailscale_ip),
                config = COALESCE(EXCLUDED.config, stations.config),
                last_heartbeat = EXCLUDED.last_heartbeat,
                status = 'online',
                updated_at = EXCLUDED.updated_at
        ''', (station_id, display_name, tailscale_ip, config_json, now, now))
        
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Station registration failed: {e}")
        return False
    finally:
        conn.close()


def station_heartbeat(station_id: str, status: str = "online") -> bool:
    """
    Update station heartbeat timestamp and status.
    Called every 30s from station daemon.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        c.execute(
            "UPDATE stations SET last_heartbeat=?, status=?, updated_at=? WHERE id=?",
            (now, status, now, station_id)
        )
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Station heartbeat failed: {e}")
        return False
    finally:
        conn.close()


def get_station(station_id: str) -> dict:
    """Get station details by ID."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM stations WHERE id=?", (station_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        result = dict(row) if hasattr(row, 'keys') else row
        if result.get('config') and isinstance(result['config'], str):
            try:
                result['config'] = json.loads(result['config'])
            except:
                pass
        return result
    return None


def get_all_stations() -> list:
    """Get all registered stations."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM stations ORDER BY display_name")
    rows = c.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        r = dict(row) if hasattr(row, 'keys') else row
        if r.get('config') and isinstance(r['config'], str):
            try:
                r['config'] = json.loads(r['config'])
            except:
                pass
        results.append(r)
    return results


def cleanup_dead_stations(timeout_minutes: int = 5) -> int:
    """
    Reset jobs claimed by stations that haven't sent a heartbeat in `timeout_minutes`.
    Returns number of jobs released.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now()
    cutoff = (now - timedelta(minutes=timeout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Find dead stations
        c.execute("SELECT id FROM stations WHERE last_heartbeat < ?", (cutoff,))
        rows = c.fetchall()
        dead_stations = [row[0] if isinstance(row, tuple) else row['id'] for row in rows]
        
        released_count = 0
        if dead_stations:
            # Release their jobs
            placeholders = ','.join(['?'] * len(dead_stations))
            c.execute(f'''
                UPDATE jobs 
                SET station_id = NULL, status = 'Released (Station Offline)', claimed_at = NULL
                WHERE station_id IN ({placeholders})
                AND (stage != 'COMPLETED')
            ''', dead_stations)
            released_count = c.rowcount
            
            # Mark stations as offline
            c.execute(f'''
                UPDATE stations 
                SET status = 'offline' 
                WHERE id IN ({placeholders})
            ''', dead_stations)
            
            logging.warning(f"💀 Cleanup: Detected {len(dead_stations)} dead stations. Released {released_count} stuck jobs.")
            
        conn.commit()
        return released_count
    except Exception as e:
        logging.error(f"Failed to cleanup dead stations: {e}")
        return 0
    finally:
        conn.close()


def claim_next_job(station_id: str, stage: str = "PENDING_DELIVERY") -> dict:
    """
    Atomically claim the next available job for a station.
    Uses FOR UPDATE SKIP LOCKED in PostgreSQL for safe concurrency.
    
    Returns the claimed job or None if no jobs available.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Atomic claim with row locking
        c.execute('''
            UPDATE jobs SET 
                station_id = %s,
                claimed_at = %s,
                status = 'Claimed by ' || %s
            WHERE file_stem = (
                SELECT file_stem FROM jobs 
                WHERE stage = %s 
                AND (station_id IS NULL OR station_id = '')
                AND (retry_after IS NULL OR retry_after < NOW())
                ORDER BY priority DESC, updated_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
        ''', (station_id, now, station_id, stage))
        row = c.fetchone()
        
        conn.commit()
        
        if row:
            result = dict(row) if hasattr(row, 'keys') else row
            if result.get('meta') and isinstance(result['meta'], str):
                try:
                    result['meta'] = json.loads(result['meta'])
                except:
                    pass
            return result
        return None
        
    except Exception as e:
        logging.error(f"Job claim failed: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def release_job(file_stem: str, error: str = None) -> bool:
    """
    Release a claimed job back to the queue.
    Used when station fails to process or shuts down.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        if error:
            c.execute('''
                UPDATE jobs SET 
                    station_id = NULL,
                    claimed_at = NULL,
                    status = ?,
                    updated_at = ?
                WHERE file_stem = ?
            ''', (f"Released: {error[:100]}", now, file_stem))
        else:
            c.execute('''
                UPDATE jobs SET 
                    station_id = NULL,
                    claimed_at = NULL,
                    status = 'Released - awaiting retry',
                    updated_at = ?
                WHERE file_stem = ?
            ''', (now, file_stem))
        
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Job release failed: {e}")
        return False
    finally:
        conn.close()


def complete_station_job(file_stem: str, station_id: str, delivery_path: str = None) -> bool:
    """
    Mark a job as completed by a station.
    Updates job stage and increments station's jobs_processed counter.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Update job
        c.execute('''
            UPDATE jobs SET 
                stage = 'DELIVERED',
                status = 'Delivered',
                progress = 100.0,
                updated_at = ?
            WHERE file_stem = ? AND station_id = ?
        ''', (now, file_stem, station_id))
        
        # Increment station counter
        c.execute('''
            UPDATE stations SET 
                jobs_processed = jobs_processed + 1,
                updated_at = ?
            WHERE id = ?
        ''', (now, station_id))
        
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Job completion failed: {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# OMEGA LITERATI - Book Translation CRUD Functions
# =============================================================================

def create_book_project(
    title: str,
    author: str = None,
    source_language: str = "en",
    target_language: str = "is",
    source_file_path: str = None,
    client: str = None,
    due_date: str = None,
    glossary: dict = None,
    character_bible: dict = None,
    style_guide: str = None,
    meta: dict = None
) -> dict:
    """
    Create a new book project.
    Returns the created book project dict.
    """
    import uuid
    book_id = f"book_{uuid.uuid4().hex[:12]}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = _connect()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT INTO book_projects (
                id, title, author, source_language, target_language,
                source_file_path, stage, status, progress, glossary,
                character_bible, style_guide,
                client, due_date, created_at, updated_at, meta
            ) VALUES (?, ?, ?, ?, ?, ?, 'UPLOADING', 'Pending', 0.0, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            book_id, title, author, source_language, target_language,
            source_file_path,
            json.dumps(glossary) if glossary else None,
            json.dumps(character_bible) if character_bible else None,
            style_guide,
            client, due_date, now, now,
            json.dumps(meta) if meta else None
        ))
        conn.commit()
        
        return get_book_project(book_id)
    except Exception as e:
        logging.error(f"Failed to create book project: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def get_book_project(book_id: str) -> dict:
    """Fetch a single book project by ID."""
    conn = _connect()
    c = conn.cursor()
    
    try:
        c.execute("SELECT * FROM book_projects WHERE id = ?", (book_id,))
        row = c.fetchone()
        if row:
            result = dict(row) if hasattr(row, 'keys') else row
            # Parse JSON fields
            for field in ['glossary', 'character_bible', 'style_guide', 'meta']:
                if result.get(field) and isinstance(result[field], str):
                    try:
                        result[field] = json.loads(result[field])
                    except:
                        pass
            return result
        return None
    finally:
        conn.close()


def get_all_book_projects(client: str = None, stage: str = None) -> list:
    """Get all book projects, optionally filtered by client or stage."""
    conn = _connect()
    c = conn.cursor()
    
    try:
        query = "SELECT * FROM book_projects"
        params = []
        conditions = []
        
        if client:
            conditions.append("client = ?")
            params.append(client)
        if stage:
            conditions.append("stage = ?")
            params.append(stage)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC"
        
        c.execute(query, params)
        rows = c.fetchall()
        
        results = []
        for row in rows:
            result = dict(row) if hasattr(row, 'keys') else row
            for field in ['glossary', 'character_bible', 'style_guide', 'meta']:
                if result.get(field) and isinstance(result[field], str):
                    try:
                        result[field] = json.loads(result[field])
                    except:
                        pass
            results.append(result)
        return results
    finally:
        conn.close()


def update_book_project(
    book_id: str,
    title: str = None,
    author: str = None,
    stage: str = None,
    status: str = None,
    progress: float = None,
    total_chapters: int = None,
    glossary: dict = None,
    character_bible: dict = None,
    style_guide: str = None,
    translation_notes: str = None,
    meta: dict = None
) -> bool:
    """Update a book project's fields."""
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    updates = ["updated_at = ?"]
    params = [now]
    
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if author is not None:
        updates.append("author = ?")
        params.append(author)
    if stage is not None:
        updates.append("stage = ?")
        params.append(stage)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if progress is not None:
        updates.append("progress = ?")
        params.append(progress)
    if total_chapters is not None:
        updates.append("total_chapters = ?")
        params.append(total_chapters)
    if glossary is not None:
        updates.append("glossary = ?")
        params.append(json.dumps(glossary))
    if character_bible is not None:
        updates.append("character_bible = ?")
        params.append(json.dumps(character_bible))
    if style_guide is not None:
        updates.append("style_guide = ?")
        params.append(style_guide)
    if translation_notes is not None:
        updates.append("translation_notes = ?")
        params.append(translation_notes)
    if meta is not None:
        updates.append("meta = ?")
        params.append(json.dumps(meta))
    
    params.append(book_id)
    
    try:
        c.execute(
            f"UPDATE book_projects SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to update book project: {e}")
        return False
    finally:
        conn.close()


def add_book_chapter(
    book_id: str,
    chapter_number: int,
    chapter_title: str = None,
    source_text: str = None,
    word_count: int = None,
    meta: dict = None
) -> dict:
    """
    Add a chapter to a book project.
    Returns the created chapter dict.
    """
    import uuid
    chapter_id = f"ch_{uuid.uuid4().hex[:12]}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate word count if not provided
    if word_count is None and source_text:
        word_count = len(source_text.split())
    
    conn = _connect()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT INTO book_chapters (
                id, book_id, chapter_number, chapter_title, source_text,
                stage, status, word_count, created_at, updated_at, meta
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', 'Pending', ?, ?, ?, ?)
        ''', (
            chapter_id, book_id, chapter_number, chapter_title, source_text,
            word_count or 0, now, now,
            json.dumps(meta) if meta else None
        ))
        conn.commit()
        
        # Update book's total chapters count
        c.execute('''
            UPDATE book_projects SET 
                total_chapters = (SELECT COUNT(*) FROM book_chapters WHERE book_id = ?),
                updated_at = ?
            WHERE id = ?
        ''', (book_id, now, book_id))
        conn.commit()
        
        return get_book_chapter(chapter_id)
    except Exception as e:
        logging.error(f"Failed to add book chapter: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def get_book_chapter(chapter_id: str) -> dict:
    """Fetch a single book chapter by ID."""
    conn = _connect()
    c = conn.cursor()
    
    try:
        c.execute("SELECT * FROM book_chapters WHERE id = ?", (chapter_id,))
        row = c.fetchone()
        if row:
            result = dict(row) if hasattr(row, 'keys') else row
            if result.get('meta') and isinstance(result['meta'], str):
                try:
                    result['meta'] = json.loads(result['meta'])
                except:
                    pass
            return result
        return None
    finally:
        conn.close()


def get_book_chapters(book_id: str, stage: str = None) -> list:
    """Get all chapters for a book, optionally filtered by stage."""
    conn = _connect()
    c = conn.cursor()
    
    try:
        query = "SELECT * FROM book_chapters WHERE book_id = ?"
        params = [book_id]
        
        if stage:
            query += " AND stage = ?"
            params.append(stage)
            
        query += " ORDER BY chapter_number ASC"
        
        c.execute(query, params)
        rows = c.fetchall()
        
        results = []
        for row in rows:
            result = dict(row) if hasattr(row, 'keys') else row
            if result.get('meta') and isinstance(result['meta'], str):
                try:
                    result['meta'] = json.loads(result['meta'])
                except:
                    pass
            results.append(result)
        return results
    finally:
        conn.close()


def get_book_with_chapters(book_id: str) -> dict:
    """
    Get a book project with all its chapters.
    Returns book dict with 'chapters' list.
    """
    book = get_book_project(book_id)
    if book:
        book['chapters'] = get_book_chapters(book_id)
    return book


def update_chapter_translation(
    chapter_id: str,
    step1_translation: str = None,
    step2_theology_review: str = None,
    step3_polish: str = None,
    final_text: str = None,
    stage: str = None,
    status: str = None,
    translation_notes: str = None,
    step: int = None,
    translation_text: str = None,
    error: str = None
) -> bool:
    """
    Update a chapter's translation fields.
    Automatically sets completed_at timestamps when content is provided.
    Supports explicit column args OR generic step/text args.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    updates = ["updated_at = ?"]
    params = [now]
    
    # Map generic step argument to specific columns
    if step == 1 and translation_text is not None:
        step1_translation = translation_text
    elif step == 2 and translation_text is not None:
        step2_theology_review = translation_text
    elif step == 3 and translation_text is not None:
        step3_polish = translation_text
        
    if step1_translation is not None:
        updates.append("step1_translation = ?")
        params.append(step1_translation)
        updates.append("step1_completed_at = ?")
        params.append(now)
    if step2_theology_review is not None:
        updates.append("step2_theology_review = ?")
        params.append(step2_theology_review)
        updates.append("step2_completed_at = ?")
        params.append(now)
    if step3_polish is not None:
        updates.append("step3_polish = ?")
        params.append(step3_polish)
        updates.append("step3_completed_at = ?")
        params.append(now)
    if final_text is not None:
        updates.append("final_text = ?")
        params.append(final_text)
    if stage is not None:
        updates.append("stage = ?")
        params.append(stage)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if translation_notes is not None:
        updates.append("translation_notes = ?")
        params.append(translation_notes)
    
    params.append(chapter_id)
    
    try:
        c.execute(
            f"UPDATE book_chapters SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to update chapter translation: {e}")
        return False
    finally:
        conn.close()


def lock_chapter(chapter_id: str, locked_by: str) -> bool:
    """
    Lock a chapter to prevent concurrent editing/translation.
    Returns False if already locked by someone else.
    """
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Check if already locked
        c.execute("SELECT locked_at, locked_by FROM book_chapters WHERE id = ?", (chapter_id,))
        row = c.fetchone()
        if row:
            result = dict(row) if hasattr(row, 'keys') else row
            if result.get('locked_at') and result.get('locked_by') != locked_by:
                return False  # Already locked by someone else
        
        c.execute('''
            UPDATE book_chapters SET 
                locked_at = ?,
                locked_by = ?,
                updated_at = ?
            WHERE id = ?
        ''', (now, locked_by, now, chapter_id))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to lock chapter: {e}")
        return False
    finally:
        conn.close()


def unlock_chapter(chapter_id: str) -> bool:
    """Unlock a chapter for editing."""
    conn = _connect()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        c.execute('''
            UPDATE book_chapters SET 
                locked_at = NULL,
                locked_by = NULL,
                updated_at = ?
            WHERE id = ?
        ''', (now, chapter_id))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logging.error(f"Failed to unlock chapter: {e}")
        return False
    finally:
        conn.close()


def delete_book_project(book_id: str) -> bool:
    """
    Delete a book project and all its chapters.
    Use with caution!
    """
    conn = _connect()
    c = conn.cursor()
    
    try:
        # Delete chapters first
        c.execute("DELETE FROM book_chapters WHERE book_id = ?", (book_id,))
        # Then delete the book
        c.execute("DELETE FROM book_projects WHERE id = ?", (book_id,))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Failed to delete book project: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_next_pending_chapter(book_id: str, for_step: int = 1) -> dict:
    """
    Get the next chapter that needs processing for the specified step.
    Step 1: Literary translation (needs source_text, no step1_translation)
    Step 2: Theology review (needs step1_translation, no step2_theology_review)
    Step 3: Polish (needs step2_theology_review, no step3_polish)
    """
    conn = _connect()
    c = conn.cursor()
    
    try:
        if for_step == 1:
            c.execute('''
                SELECT * FROM book_chapters 
                WHERE book_id = ? 
                AND source_text IS NOT NULL 
                AND step1_translation IS NULL
                AND locked_at IS NULL
                ORDER BY chapter_number ASC
                LIMIT 1
            ''', (book_id,))
        elif for_step == 2:
            c.execute('''
                SELECT * FROM book_chapters 
                WHERE book_id = ? 
                AND step1_translation IS NOT NULL 
                AND step2_theology_review IS NULL
                AND locked_at IS NULL
                ORDER BY chapter_number ASC
                LIMIT 1
            ''', (book_id,))
        elif for_step == 3:
            c.execute('''
                SELECT * FROM book_chapters 
                WHERE book_id = ? 
                AND step2_theology_review IS NOT NULL 
                AND step3_polish IS NULL
                AND locked_at IS NULL
                ORDER BY chapter_number ASC
                LIMIT 1
            ''', (book_id,))
        else:
            return None
        
        row = c.fetchone()
        if row:
            result = dict(row) if hasattr(row, 'keys') else row
            if result.get('meta') and isinstance(result['meta'], str):
                try:
                    result['meta'] = json.loads(result['meta'])
                except:
                    pass
            return result
        return None
    finally:
        conn.close()

# =========================================================================
# CLOUD SYNC HELPERS (Phase 1)
# =========================================================================

def get_sync_state(artifact_id: str, artifact_type: str):
    """Get the sync state for a specific artifact."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM cloud_sync_state WHERE id=? AND artifact_type=?",
            (artifact_id, artifact_type)
        )
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def create_sync_state(artifact_id: str, artifact_type: str, artifact_path: str, local_path: str = None):
    """Create initial sync state record."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO cloud_sync_state
            (id, artifact_type, artifact_path, state, local_path, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (artifact_id, artifact_type, artifact_path, local_path))
        conn.commit()
    finally:
        conn.close()


def update_sync_state(artifact_id: str, artifact_type: str, state: str,
                      local_path: str = None, error_message: str = None,
                      artifact_path: str = None):
    """Update sync state with new status."""
    conn = _connect()
    try:
        updates = ["state = ?", "updated_at = CURRENT_TIMESTAMP", "attempt_count = attempt_count + 1"]
        params = [state]

        if local_path:
            updates.append("local_path = ?")
            params.append(local_path)

        if artifact_path:
            updates.append("artifact_path = ?")
            params.append(artifact_path)

        if error_message:
            updates.append("error_message = ?")
            params.append(error_message)

        updates.append("last_attempt_at = CURRENT_TIMESTAMP")
        params.extend([artifact_id, artifact_type])

        query = f"UPDATE cloud_sync_state SET {', '.join(updates)} WHERE id=? AND artifact_type=?"
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def get_pending_syncs(artifact_type: str = None) -> list:
    """Get all sync operations that need processing."""
    conn = _connect()
    try:
        c = conn.cursor()
        if artifact_type:
            c.execute(
                "SELECT * FROM cloud_sync_state WHERE artifact_type=? AND state IN ('PENDING', 'DOWNLOADING', 'DOWNLOADED', 'FILE_SAVED') ORDER BY created_at",
                (artifact_type,)
            )
        else:
            c.execute(
                "SELECT * FROM cloud_sync_state WHERE state IN ('PENDING', 'DOWNLOADING', 'DOWNLOADED', 'FILE_SAVED') ORDER BY created_at"
            )
        return [dict(row) for row in c.fetchall()]
    finally:
        conn.close()


# =============================================================================
# MINISTRY PROFILES (Workflow Architecture)
# =============================================================================

def create_ministry_profile(
    name: str,
    slug: str,
    languages: list = None,
    default_delivery_id: str = None,
    terminology: dict = None,
    style: dict = None,
    watch_folder: str = None,
    workflow: str = 'standard'
) -> str:
    """Create a new ministry profile. Returns profile ID."""
    profile_id = _generate_id()
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO ministry_profiles
            (id, name, slug, languages, default_delivery_id, terminology, style, watch_folder, workflow, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            profile_id,
            name,
            slug,
            json.dumps(languages or []),
            default_delivery_id,
            json.dumps(terminology) if terminology else None,
            json.dumps(style) if style else None,
            watch_folder,
            workflow
        ))
        conn.commit()
    finally:
        conn.close()
    return profile_id

def get_ministry_profile(profile_id: str = None, slug: str = None) -> dict:
    """Get a ministry profile by ID or slug. Returns None if not found."""
    conn = _connect()
    try:
        c = conn.cursor()
        if profile_id:
            c.execute("SELECT * FROM ministry_profiles WHERE id=?", (profile_id,))
        elif slug:
            c.execute("SELECT * FROM ministry_profiles WHERE slug=?", (slug,))
        else:
            return None
        row = c.fetchone()
        if row:
            result = dict(row)
            # Parse JSON fields
            if result.get('languages'):
                try:
                    result['languages'] = json.loads(result['languages'])
                except:
                    result['languages'] = []
            if result.get('terminology'):
                try:
                    result['terminology'] = json.loads(result['terminology'])
                except:
                    result['terminology'] = {}
            if result.get('style'):
                try:
                    result['style'] = json.loads(result['style'])
                except:
                    result['style'] = {}
            return result
        return None
    finally:
        conn.close()

def list_ministry_profiles() -> list:
    """List all ministry profiles."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM ministry_profiles ORDER BY name")
        results = []
        for row in c.fetchall():
            result = dict(row)
            if result.get('languages'):
                try:
                    result['languages'] = json.loads(result['languages'])
                except:
                    result['languages'] = []
            if result.get('terminology'):
                try:
                    result['terminology'] = json.loads(result['terminology'])
                except:
                    result['terminology'] = {}
            if result.get('style'):
                try:
                    result['style'] = json.loads(result['style'])
                except:
                    result['style'] = {}
            results.append(result)
        return results
    finally:
        conn.close()

def update_ministry_profile(profile_id: str, **kwargs) -> bool:
    """Update a ministry profile. Returns True if updated."""
    if not kwargs:
        return False
    conn = _connect()
    try:
        updates = []
        params = []
        for key, value in kwargs.items():
            if key in ('languages', 'terminology', 'style') and value is not None:
                updates.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in ('name', 'slug', 'default_delivery_id', 'watch_folder', 'workflow'):
                updates.append(f"{key} = ?")
                params.append(value)
        if not updates:
            return False
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(profile_id)
        query = f"UPDATE ministry_profiles SET {', '.join(updates)} WHERE id = ?"
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        return True
    finally:
        conn.close()

def delete_ministry_profile(profile_id: str) -> bool:
    """Delete a ministry profile. Returns True if deleted."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM ministry_profiles WHERE id=?", (profile_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# =============================================================================
# DELIVERY PROFILES (Workflow Architecture)
# =============================================================================

def create_delivery_profile(
    name: str,
    slug: str,
    outputs: list = None
) -> str:
    """Create a new delivery profile. Returns profile ID."""
    profile_id = _generate_id()
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO delivery_profiles
            (id, name, slug, outputs, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            profile_id,
            name,
            slug,
            json.dumps(outputs or [])
        ))
        conn.commit()
    finally:
        conn.close()
    return profile_id

def get_delivery_profile(profile_id: str = None, slug: str = None) -> dict:
    """Get a delivery profile by ID or slug. Returns None if not found."""
    conn = _connect()
    try:
        c = conn.cursor()
        if profile_id:
            c.execute("SELECT * FROM delivery_profiles WHERE id=?", (profile_id,))
        elif slug:
            c.execute("SELECT * FROM delivery_profiles WHERE slug=?", (slug,))
        else:
            return None
        row = c.fetchone()
        if row:
            result = dict(row)
            if result.get('outputs'):
                try:
                    result['outputs'] = json.loads(result['outputs'])
                except:
                    result['outputs'] = []
            return result
        return None
    finally:
        conn.close()

def list_delivery_profiles() -> list:
    """List all delivery profiles."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM delivery_profiles ORDER BY name")
        results = []
        for row in c.fetchall():
            result = dict(row)
            if result.get('outputs'):
                try:
                    result['outputs'] = json.loads(result['outputs'])
                except:
                    result['outputs'] = []
            results.append(result)
        return results
    finally:
        conn.close()

def update_delivery_profile(profile_id: str, **kwargs) -> bool:
    """Update a delivery profile. Returns True if updated."""
    allowed = {'name', 'slug', 'outputs'}
    updates = []
    values = []
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            updates.append(f"{key} = ?")
            if key == 'outputs' and isinstance(value, list):
                values.append(json.dumps(value))
            else:
                values.append(value)

    if not updates:
        return False

    conn = _connect()
    try:
        c = conn.cursor()
        query = f"UPDATE delivery_profiles SET {', '.join(updates)} WHERE id = ?"
        values.append(profile_id)
        c.execute(query, values)
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_delivery_profile(profile_id: str) -> bool:
    """Delete a delivery profile. Returns True if deleted."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM delivery_profiles WHERE id=?", (profile_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# =============================================================================
# DROPZONE RECIPES (Workflow Architecture)
# =============================================================================

def create_dropzone_recipe(
    name: str,
    folder_name: str,
    ministry_id: str,
    languages: list,
    delivery_id: str
) -> str:
    """Create a new dropzone recipe. Returns recipe ID."""
    recipe_id = _generate_id()
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO dropzone_recipes
            (id, name, folder_name, ministry_id, languages, delivery_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            recipe_id,
            name,
            folder_name,
            ministry_id,
            json.dumps(languages),
            delivery_id
        ))
        conn.commit()
    finally:
        conn.close()
    return recipe_id

def get_dropzone_recipe(recipe_id: str = None, folder_name: str = None) -> dict:
    """Get a dropzone recipe by ID or folder name. Returns None if not found."""
    conn = _connect()
    try:
        c = conn.cursor()
        if recipe_id:
            c.execute("SELECT * FROM dropzone_recipes WHERE id=?", (recipe_id,))
        elif folder_name:
            c.execute("SELECT * FROM dropzone_recipes WHERE folder_name=?", (folder_name,))
        else:
            return None
        row = c.fetchone()
        if row:
            result = dict(row)
            if result.get('languages'):
                try:
                    result['languages'] = json.loads(result['languages'])
                except:
                    result['languages'] = []
            return result
        return None
    finally:
        conn.close()

def list_dropzone_recipes() -> list:
    """List all dropzone recipes."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM dropzone_recipes ORDER BY name")
        results = []
        for row in c.fetchall():
            result = dict(row)
            if result.get('languages'):
                try:
                    result['languages'] = json.loads(result['languages'])
                except:
                    result['languages'] = []
            results.append(result)
        return results
    finally:
        conn.close()

def update_dropzone_recipe(recipe_id: str, **kwargs) -> bool:
    """Update a dropzone recipe. Returns True if updated."""
    allowed = {'name', 'folder_name', 'ministry_id', 'languages', 'delivery_id'}
    updates = []
    values = []
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            updates.append(f"{key} = ?")
            if key == 'languages' and isinstance(value, list):
                values.append(json.dumps(value))
            else:
                values.append(value)

    if not updates:
        return False

    conn = _connect()
    try:
        c = conn.cursor()
        query = f"UPDATE dropzone_recipes SET {', '.join(updates)} WHERE id = ?"
        values.append(recipe_id)
        c.execute(query, values)
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_dropzone_recipe(recipe_id: str) -> bool:
    """Delete a dropzone recipe. Returns True if deleted."""
    conn = _connect()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM dropzone_recipes WHERE id=?", (recipe_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# =============================================================================
# AUTO-INITIALIZE SCHEMA AT MODULE LOAD
# =============================================================================

def _ensure_schema():
    """Ensure database schema exists at module initialization."""
    try:
        conn = _connect()
        init_pg_schema(conn)
        conn.close()
        logger.info("PostgreSQL schema initialized/verified")
    except Exception as e:
        logger.warning(f"PostgreSQL schema init: {e}")


# Run schema initialization when module is imported
_ensure_schema()
