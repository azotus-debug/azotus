-- ===========================================================================
-- OMEGA TV SUBTITLE WORKFLOW - Cloud SQL (PostgreSQL) Schema
-- ===========================================================================
--
-- Purpose: Complete PostgreSQL schema for the Omega TV subtitle production
-- system, designed for Google Cloud SQL.
--
-- Usage:
--   psql -h <cloud-sql-ip> -U omega_user -d omega_db -f cloud_sql_schema.sql
--   OR run via Cloud SQL console query editor
--
-- Created: 2026-02-01
-- Last Updated: 2026-02-01
-- ===========================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- For gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- For text search optimization (optional)

-- ===========================================================================
-- ENUM TYPES
-- ===========================================================================
-- Define PostgreSQL ENUM types for constrained values

-- job_stage_enum: Core pipeline stages for job processing
-- Maps to state_machine.py JobStage enum
CREATE TYPE job_stage_enum AS ENUM (
    'queued',       -- Job created, waiting for processing
    'processing',   -- Active work in progress (see processing_step for details)
    'reviewing',    -- Human review required
    'finalizing',   -- Generating final outputs (SRT, burned video)
    'delivered',    -- Successfully completed
    'failed'        -- Terminal error state
);

COMMENT ON TYPE job_stage_enum IS 'Core pipeline stages for track processing. Maps to state_machine.py JobStage enum.';

-- processing_step_enum: Sub-steps within the PROCESSING stage
-- Provides granular visibility into what the system is doing
CREATE TYPE processing_step_enum AS ENUM (
    'ingest',           -- Moving file, extracting audio
    'transcribe',       -- ElevenLabs transcription
    'translate_submit', -- Submitting to cloud translation
    'translate_cloud',  -- Cloud translation in progress
    'music_detect',     -- Detecting music segments
    'edit',             -- AI editing/review
    'polish',           -- Claude polish pass
    'burn',             -- FFmpeg video burning
    'deliver'           -- Final delivery/upload
);

COMMENT ON TYPE processing_step_enum IS 'Sub-steps within PROCESSING stage. Provides granular visibility into active work.';

-- track_type_enum: Type of output track
CREATE TYPE track_type_enum AS ENUM (
    'subtitle',     -- Subtitle track (SRT/ASS output)
    'dub'           -- Dubbed audio track (future)
);

COMMENT ON TYPE track_type_enum IS 'Type of output track: subtitle or dub.';

-- master_state_enum: State of a master script
CREATE TYPE master_state_enum AS ENUM (
    'draft',        -- Work in progress
    'approved',     -- Reviewed and approved
    'locked'        -- Locked for delivery, changes create new version
);

COMMENT ON TYPE master_state_enum IS 'State of master script content lifecycle.';

-- delivery_status_enum: Track delivery status
CREATE TYPE delivery_status_enum AS ENUM (
    'pending',          -- Not yet delivered
    'sent_for_review',  -- Sent to external reviewer
    'delivered'         -- Successfully delivered to client
);

COMMENT ON TYPE delivery_status_enum IS 'Track delivery status for client handoff.';


-- ===========================================================================
-- TABLE: programs
-- ===========================================================================
-- Master video records - the source content being localized
-- Each program can have multiple language tracks

CREATE TABLE IF NOT EXISTS programs (
    -- Primary key: UUID for globally unique identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Core metadata
    title TEXT NOT NULL,
    original_filename TEXT,
    video_path TEXT,                    -- Path to source video in vault
    thumbnail_path TEXT,                -- Path to generated thumbnail
    duration_seconds FLOAT8,            -- Video duration in seconds

    -- Client and scheduling
    client TEXT NOT NULL DEFAULT 'unknown',
    due_date TIMESTAMPTZ,               -- Delivery deadline

    -- Defaults for new tracks
    default_style TEXT DEFAULT 'Classic',  -- Default subtitle style

    -- Status tracking
    status TEXT DEFAULT 'ACTIVE',       -- ACTIVE, ARCHIVED, DELETED
    deleted_at TIMESTAMPTZ,             -- Soft delete timestamp
    deleted_original_filename TEXT,     -- Preserved filename on delete
    deleted_video_path TEXT,            -- Preserved path on delete

    -- Extensible metadata (JSONB for flexible schema)
    meta JSONB DEFAULT '{}'::jsonb,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE programs IS 'Master video records - source content being localized. Each program can have multiple language tracks.';
COMMENT ON COLUMN programs.id IS 'Globally unique program identifier (UUID)';
COMMENT ON COLUMN programs.title IS 'Human-readable program title';
COMMENT ON COLUMN programs.video_path IS 'Path to source video file in vault storage';
COMMENT ON COLUMN programs.meta IS 'Extensible JSONB metadata (e.g., asset_id, client_id, genre)';


-- ===========================================================================
-- TABLE: master_scripts
-- ===========================================================================
-- Source of truth transcripts for each program/language combination
-- Contains the canonical text that tracks are rendered from

CREATE TABLE IF NOT EXISTS master_scripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    program_id UUID NOT NULL REFERENCES programs(id) ON DELETE CASCADE,

    -- Script identification
    language_code TEXT NOT NULL,        -- ISO 639-1 code (e.g., 'en', 'is', 'nl')
    language_name TEXT,                 -- Human-readable name (e.g., 'Icelandic')

    -- Versioning for optimistic locking
    version INTEGER NOT NULL DEFAULT 1,

    -- Content state
    state master_state_enum DEFAULT 'draft',

    -- Content storage
    content JSONB DEFAULT '{"segments": []}'::jsonb,  -- Transcript segments

    -- Locking mechanism
    locked_at TIMESTAMPTZ,
    locked_by TEXT,                     -- User/worker that locked

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Extensible metadata
    meta JSONB DEFAULT '{}'::jsonb,

    -- Unique constraint: one master script per program/language
    UNIQUE(program_id, language_code)
);

COMMENT ON TABLE master_scripts IS 'Source of truth transcripts. Contains canonical text that tracks are rendered from.';
COMMENT ON COLUMN master_scripts.version IS 'Version number for optimistic locking and change tracking';
COMMENT ON COLUMN master_scripts.content IS 'JSONB containing transcript segments with timing and text';
COMMENT ON COLUMN master_scripts.state IS 'Content lifecycle state: draft, approved, or locked';


-- ===========================================================================
-- TABLE: tracks
-- ===========================================================================
-- Per-language output tracks for each program
-- This is the main work queue table that workers poll

CREATE TABLE IF NOT EXISTS tracks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    program_id UUID NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    master_script_id UUID REFERENCES master_scripts(id),
    depends_on UUID REFERENCES tracks(id),  -- For dubs depending on subtitles

    -- Track identification
    language_code TEXT NOT NULL,        -- ISO 639-1 code
    language_name TEXT,                 -- Human-readable name
    type track_type_enum NOT NULL DEFAULT 'subtitle',

    -- Pipeline state (new simplified model)
    stage job_stage_enum NOT NULL DEFAULT 'queued',
    processing_step processing_step_enum,  -- Current step when stage=processing
    status TEXT DEFAULT 'Pending',      -- Human-readable status message
    progress FLOAT8 DEFAULT 0.0,        -- 0.0 to 100.0

    -- Version for optimistic locking
    version INTEGER NOT NULL DEFAULT 1,

    -- Worker claiming (distributed locking)
    claimed_by TEXT,                    -- Worker ID that claimed this track
    claimed_at TIMESTAMPTZ,             -- When the claim was made
    lease_until TIMESTAMPTZ,            -- Claim expires after this time
    heartbeat_at TIMESTAMPTZ,           -- Last heartbeat from worker
    location_id TEXT,                   -- Processing location (iceland, virginia)
    worker_id TEXT,                     -- Specific worker instance ID

    -- Storage and location hints
    storage_location TEXT,              -- Where artifacts are stored
    eligible_locations TEXT[] DEFAULT ARRAY[]::text[],  -- Where this can be processed

    -- Artifact paths
    skeleton_path TEXT,                 -- Path to _SKELETON.json
    approved_path TEXT,                 -- Path to _APPROVED.json
    srt_path TEXT,                      -- Path to final .srt file
    video_path TEXT,                    -- Path to burned video output
    output_path TEXT,                   -- Generic output path

    -- Delivery tracking
    delivery_status delivery_status_enum DEFAULT 'pending',

    -- Quality and ratings
    rating FLOAT8,                      -- Quality rating (0-10)
    voice_id TEXT,                      -- For dub tracks: TTS voice ID

    -- Override mechanism (for cosmetic fixes without master change)
    output_version TEXT DEFAULT '1.0',  -- Major.Minor version string
    output_override BOOLEAN DEFAULT FALSE,
    override_reason TEXT,
    override_author TEXT,
    override_timestamp TIMESTAMPTZ,
    pending_resync BOOLEAN DEFAULT FALSE,  -- Dub needs to sync with updated master

    -- Error handling
    error TEXT,                         -- Last error message
    retry_count INTEGER DEFAULT 0,

    -- Locking
    locked_at TIMESTAMPTZ,
    locked_by TEXT,

    -- Legacy compatibility
    job_id TEXT,                        -- Legacy job identifier
    state TEXT DEFAULT 'draft',         -- Legacy state field

    -- Extensible metadata
    meta JSONB DEFAULT '{}'::jsonb,

    -- Artifact storage (for distributed systems)
    artifacts JSONB DEFAULT '{}'::jsonb,     -- Artifact registry
    checkpoints JSONB DEFAULT '{}'::jsonb,   -- Processing checkpoints
    artifact_hashes JSONB DEFAULT '{}'::jsonb,  -- Content hashes for verification

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,

    -- Unique constraint: one track per program/language/type
    UNIQUE(program_id, language_code, type)
);

COMMENT ON TABLE tracks IS 'Per-language output tracks. Main work queue table that workers poll for jobs.';
COMMENT ON COLUMN tracks.stage IS 'Current pipeline stage (queued, processing, reviewing, finalizing, delivered, failed)';
COMMENT ON COLUMN tracks.processing_step IS 'Current sub-step when stage is processing';
COMMENT ON COLUMN tracks.version IS 'Version for optimistic locking - increment on updates';
COMMENT ON COLUMN tracks.claimed_by IS 'Worker ID holding the claim. NULL means unclaimed.';
COMMENT ON COLUMN tracks.claimed_at IS 'Timestamp when claim was acquired. Used for stale claim detection.';
COMMENT ON COLUMN tracks.lease_until IS 'Claim expires after this timestamp. Workers must renew via heartbeat.';
COMMENT ON COLUMN tracks.meta IS 'Extensible JSONB for additional track metadata';


-- ===========================================================================
-- TABLE: stage_transitions
-- ===========================================================================
-- Audit log for all track stage changes
-- Provides full traceability of the processing pipeline

CREATE TABLE IF NOT EXISTS stage_transitions (
    id BIGSERIAL PRIMARY KEY,

    -- Track identification
    track_id UUID NOT NULL,             -- May reference deleted tracks, so no FK
    job_stem TEXT NOT NULL,             -- Legacy job identifier for compatibility

    -- Transition details
    from_stage TEXT,                    -- Previous stage (NULL for initial)
    to_stage TEXT NOT NULL,             -- New stage
    processing_step TEXT,               -- Processing step if applicable

    -- Attribution
    worker_id TEXT,                     -- Worker that triggered transition
    reason TEXT,                        -- Human-readable reason/notes

    -- Metadata
    meta JSONB DEFAULT '{}'::jsonb,     -- Additional transition context

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE stage_transitions IS 'Audit log for track stage changes. Provides full traceability of processing.';
COMMENT ON COLUMN stage_transitions.track_id IS 'Track that transitioned. Not a FK to preserve history.';
COMMENT ON COLUMN stage_transitions.from_stage IS 'Previous stage. NULL for initial creation.';
COMMENT ON COLUMN stage_transitions.to_stage IS 'New stage after transition.';
COMMENT ON COLUMN stage_transitions.processing_step IS 'Processing step identifier if transitioning within processing stage.';


-- ===========================================================================
-- TABLE: track_deliveries
-- ===========================================================================
-- Records of where track outputs were sent

CREATE TABLE IF NOT EXISTS track_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    track_id UUID NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    -- Delivery details
    destination TEXT,                   -- Where it was sent (e.g., 'FTP', 'S3', 'Email')
    recipient TEXT,                     -- Who received it
    delivered_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT,

    -- Extensible metadata
    meta JSONB DEFAULT '{}'::jsonb
);

COMMENT ON TABLE track_deliveries IS 'Records of where track outputs were delivered.';


-- ===========================================================================
-- TABLE: script_edits
-- ===========================================================================
-- Change classification log for master script edits

CREATE TABLE IF NOT EXISTS script_edits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    master_script_id UUID NOT NULL REFERENCES master_scripts(id) ON DELETE CASCADE,
    track_id UUID REFERENCES tracks(id),  -- Optional: if edit affects specific track

    -- Change classification
    change_type TEXT NOT NULL,          -- formatting_only, text_change_minor, text_change_material, output_override
    summary TEXT,                       -- Human-readable change description
    author TEXT,                        -- Who made the change

    -- Extensible metadata
    meta JSONB DEFAULT '{}'::jsonb,

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE script_edits IS 'Change classification log for master script edits.';
COMMENT ON COLUMN script_edits.change_type IS 'Classification: formatting_only, text_change_minor, text_change_material, output_override';


-- ===========================================================================
-- TABLE: events
-- ===========================================================================
-- Generic event log for track activity (alternative to stage_transitions)

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Relationships
    track_id UUID REFERENCES tracks(id) ON DELETE CASCADE,

    -- Event details
    event_type TEXT NOT NULL,           -- E.g., 'claimed', 'heartbeat', 'error', 'progress'
    payload JSONB,                      -- Event-specific data

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE events IS 'Generic event log for track activity and system events.';


-- ===========================================================================
-- TABLE: stations
-- ===========================================================================
-- Remote processing nodes (Iceland, Virginia, etc.)

CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,

    -- Display info
    display_name TEXT,

    -- Network
    tailscale_ip TEXT,                  -- Tailscale mesh network IP

    -- Status
    status TEXT DEFAULT 'offline',      -- online, offline, degraded
    last_heartbeat TIMESTAMPTZ,
    jobs_processed INTEGER DEFAULT 0,

    -- Configuration
    config JSONB DEFAULT '{}'::jsonb,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE stations IS 'Remote processing nodes for distributed video burning.';


-- ===========================================================================
-- TABLE: cloud_sync_state
-- ===========================================================================
-- Idempotent file syncing state machine

CREATE TABLE IF NOT EXISTS cloud_sync_state (
    id TEXT PRIMARY KEY,                -- Artifact identifier (stem or job_id)

    -- Artifact details
    artifact_type TEXT NOT NULL,        -- 'approved_json', 'delivered_video', etc.
    artifact_path TEXT NOT NULL,        -- GCS path

    -- State machine
    state TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING, DOWNLOADING, DOWNLOADED, FILE_SAVED, SYNCED
    local_path TEXT,                    -- Local filesystem path after download

    -- Retry tracking
    last_attempt_at TIMESTAMPTZ,
    attempt_count INTEGER DEFAULT 0,
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE cloud_sync_state IS 'State machine for idempotent cloud file syncing.';
COMMENT ON COLUMN cloud_sync_state.state IS 'Sync state: PENDING, DOWNLOADING, DOWNLOADED, FILE_SAVED, SYNCED';


-- ===========================================================================
-- TABLE: jobs (Legacy compatibility)
-- ===========================================================================
-- Legacy job table for backward compatibility during migration

CREATE TABLE IF NOT EXISTS jobs (
    file_stem TEXT PRIMARY KEY,

    -- Status
    stage TEXT,
    status TEXT,
    progress FLOAT8,

    -- Language and style
    target_language TEXT DEFAULT 'is',
    program_profile TEXT DEFAULT 'standard',
    subtitle_style TEXT DEFAULT 'Classic',

    -- Quality
    editor_report TEXT,
    suggested_fixes TEXT,

    -- Client info
    client TEXT DEFAULT 'unknown',
    due_date DATE,
    asset_id TEXT,
    client_id TEXT,
    vault_path TEXT,

    -- Error handling
    failure_count INTEGER DEFAULT 0,
    retry_after TIMESTAMPTZ,

    -- Worker info
    claimed_at TIMESTAMPTZ,
    worker_id TEXT,
    station_id TEXT,
    priority INTEGER DEFAULT 0,

    -- Extensible metadata
    meta JSONB,

    -- Timestamps
    updated_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE jobs IS 'Legacy job table for backward compatibility. New code should use tracks table.';


-- ===========================================================================
-- TABLE: deliveries (Legacy compatibility)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS deliveries (
    id BIGSERIAL PRIMARY KEY,
    job_stem TEXT,
    client TEXT,
    delivered_at TIMESTAMPTZ,
    method TEXT,
    notes TEXT
);

COMMENT ON TABLE deliveries IS 'Legacy deliveries table. New code should use track_deliveries.';


-- ===========================================================================
-- TABLE: system_state
-- ===========================================================================
-- Global system state and configuration

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Initialize version
INSERT INTO system_state (key, value) VALUES ('db_version', '1')
ON CONFLICT (key) DO NOTHING;

INSERT INTO system_state (key, value) VALUES ('schema_created_at', now()::text)
ON CONFLICT (key) DO NOTHING;

COMMENT ON TABLE system_state IS 'Global system state and configuration key-value store.';


-- ===========================================================================
-- INDEXES
-- ===========================================================================

-- Programs indexes
CREATE INDEX IF NOT EXISTS idx_programs_client ON programs(client);
CREATE INDEX IF NOT EXISTS idx_programs_status ON programs(status);
CREATE INDEX IF NOT EXISTS idx_programs_due_date ON programs(due_date);
CREATE INDEX IF NOT EXISTS idx_programs_created_at ON programs(created_at DESC);

-- Master scripts indexes
CREATE INDEX IF NOT EXISTS idx_master_scripts_program ON master_scripts(program_id);
CREATE INDEX IF NOT EXISTS idx_master_scripts_language ON master_scripts(language_code);
CREATE INDEX IF NOT EXISTS idx_master_scripts_state ON master_scripts(state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_master_scripts_program_lang ON master_scripts(program_id, language_code);

-- Tracks indexes (critical for worker polling)
CREATE INDEX IF NOT EXISTS idx_tracks_program ON tracks(program_id);
CREATE INDEX IF NOT EXISTS idx_tracks_stage ON tracks(stage);
CREATE INDEX IF NOT EXISTS idx_tracks_processing_step ON tracks(processing_step);
CREATE INDEX IF NOT EXISTS idx_tracks_language ON tracks(language_code);
CREATE INDEX IF NOT EXISTS idx_tracks_claimed_at ON tracks(claimed_at);
CREATE INDEX IF NOT EXISTS idx_tracks_delivery_status ON tracks(delivery_status);
CREATE INDEX IF NOT EXISTS idx_tracks_created_at ON tracks(created_at);
CREATE INDEX IF NOT EXISTS idx_tracks_updated_at ON tracks(updated_at DESC);

-- Composite indexes for worker claim queries
CREATE INDEX IF NOT EXISTS idx_tracks_stage_lease ON tracks(stage, lease_until);
CREATE INDEX IF NOT EXISTS idx_tracks_stage_claimed ON tracks(stage, claimed_at) WHERE claimed_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_tracks_unclaimed_queued ON tracks(created_at)
    WHERE stage = 'queued' AND (lease_until IS NULL OR lease_until < now());

-- Storage location indexes
CREATE INDEX IF NOT EXISTS idx_tracks_storage_location ON tracks(storage_location);
CREATE INDEX IF NOT EXISTS idx_tracks_eligible_locations ON tracks USING GIN(eligible_locations);

-- Stage transitions indexes (critical for audit queries)
CREATE INDEX IF NOT EXISTS idx_stage_transitions_track_id ON stage_transitions(track_id);
CREATE INDEX IF NOT EXISTS idx_stage_transitions_created_at ON stage_transitions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stage_transitions_job_stem ON stage_transitions(job_stem);
CREATE INDEX IF NOT EXISTS idx_stage_transitions_to_stage ON stage_transitions(to_stage);

-- Events indexes
CREATE INDEX IF NOT EXISTS idx_events_track ON events(track_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

-- Script edits indexes
CREATE INDEX IF NOT EXISTS idx_script_edits_master ON script_edits(master_script_id);
CREATE INDEX IF NOT EXISTS idx_script_edits_track ON script_edits(track_id);
CREATE INDEX IF NOT EXISTS idx_script_edits_created_at ON script_edits(created_at DESC);

-- Stations indexes
CREATE INDEX IF NOT EXISTS idx_stations_status ON stations(status);
CREATE INDEX IF NOT EXISTS idx_stations_heartbeat ON stations(last_heartbeat DESC);

-- Cloud sync state indexes
CREATE INDEX IF NOT EXISTS idx_cloud_sync_state_state ON cloud_sync_state(state);
CREATE INDEX IF NOT EXISTS idx_cloud_sync_state_artifact ON cloud_sync_state(artifact_type);
CREATE INDEX IF NOT EXISTS idx_cloud_sync_state_pending ON cloud_sync_state(created_at)
    WHERE state IN ('PENDING', 'DOWNLOADING', 'DOWNLOADED', 'FILE_SAVED');

-- Legacy jobs indexes
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(stage);
CREATE INDEX IF NOT EXISTS idx_jobs_station ON jobs(station_id);
CREATE INDEX IF NOT EXISTS idx_jobs_client ON jobs(client);


-- ===========================================================================
-- TRIGGERS: Auto-update updated_at timestamps
-- ===========================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply to all tables with updated_at
DROP TRIGGER IF EXISTS update_programs_updated_at ON programs;
CREATE TRIGGER update_programs_updated_at BEFORE UPDATE ON programs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tracks_updated_at ON tracks;
CREATE TRIGGER update_tracks_updated_at BEFORE UPDATE ON tracks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_master_scripts_updated_at ON master_scripts;
CREATE TRIGGER update_master_scripts_updated_at BEFORE UPDATE ON master_scripts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_stations_updated_at ON stations;
CREATE TRIGGER update_stations_updated_at BEFORE UPDATE ON stations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_cloud_sync_state_updated_at ON cloud_sync_state;
CREATE TRIGGER update_cloud_sync_state_updated_at BEFORE UPDATE ON cloud_sync_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ===========================================================================
-- FUNCTIONS: Stage transition logging
-- ===========================================================================

CREATE OR REPLACE FUNCTION log_track_stage_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Only log if stage actually changed
    IF OLD.stage IS DISTINCT FROM NEW.stage OR OLD.processing_step IS DISTINCT FROM NEW.processing_step THEN
        INSERT INTO stage_transitions (
            track_id,
            job_stem,
            from_stage,
            to_stage,
            processing_step,
            worker_id
        ) VALUES (
            NEW.id,
            COALESCE(NEW.job_id, NEW.id::text),
            OLD.stage::text,
            NEW.stage::text,
            NEW.processing_step::text,
            NEW.worker_id
        );
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS track_stage_audit ON tracks;
CREATE TRIGGER track_stage_audit AFTER UPDATE ON tracks
    FOR EACH ROW EXECUTE FUNCTION log_track_stage_change();


-- ===========================================================================
-- FUNCTIONS: Optimistic locking helper
-- ===========================================================================

CREATE OR REPLACE FUNCTION claim_track_atomic(
    p_track_id UUID,
    p_worker_id TEXT,
    p_expected_version INTEGER,
    p_lease_minutes INTEGER DEFAULT 30
) RETURNS BOOLEAN AS $$
DECLARE
    v_claimed BOOLEAN := FALSE;
BEGIN
    UPDATE tracks
    SET
        claimed_by = p_worker_id,
        claimed_at = now(),
        lease_until = now() + make_interval(mins => p_lease_minutes),
        worker_id = p_worker_id,
        version = version + 1,
        stage = 'processing'
    WHERE id = p_track_id
      AND version = p_expected_version
      AND (claimed_by IS NULL OR lease_until < now());

    GET DIAGNOSTICS v_claimed = ROW_COUNT;
    RETURN v_claimed > 0;
END;
$$ language 'plpgsql';

COMMENT ON FUNCTION claim_track_atomic IS 'Atomically claim a track with optimistic locking. Returns TRUE if claim succeeded.';


-- ===========================================================================
-- VIEWS: Useful query shortcuts
-- ===========================================================================

-- Active tracks awaiting processing
CREATE OR REPLACE VIEW v_pending_tracks AS
SELECT
    t.id,
    t.program_id,
    p.title as program_title,
    t.language_code,
    t.stage,
    t.processing_step,
    t.created_at,
    t.claimed_by,
    t.lease_until
FROM tracks t
JOIN programs p ON t.program_id = p.id
WHERE t.stage = 'queued'
  AND (t.lease_until IS NULL OR t.lease_until < now())
ORDER BY t.created_at;

COMMENT ON VIEW v_pending_tracks IS 'Tracks waiting to be claimed by workers.';

-- Track processing summary
CREATE OR REPLACE VIEW v_track_summary AS
SELECT
    stage::text,
    processing_step::text,
    language_code,
    COUNT(*) as count,
    MIN(created_at) as oldest,
    MAX(created_at) as newest
FROM tracks
WHERE stage NOT IN ('delivered', 'failed')
GROUP BY stage, processing_step, language_code
ORDER BY stage, processing_step, language_code;

COMMENT ON VIEW v_track_summary IS 'Summary of tracks by stage and language.';


-- ===========================================================================
-- GRANTS (adjust for your Cloud SQL user)
-- ===========================================================================

-- Example grants (uncomment and adjust as needed):
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO omega_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO omega_user;
-- GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO omega_user;


-- ===========================================================================
-- FINAL NOTES
-- ===========================================================================
--
-- Key design decisions:
-- 1. UUID primary keys for distributed system compatibility
-- 2. JSONB for flexible metadata storage
-- 3. ENUM types for constrained values with type safety
-- 4. Composite indexes for worker claim queries (critical for performance)
-- 5. Automatic stage transition auditing via trigger
-- 6. Optimistic locking via version column
-- 7. Soft delete pattern for programs
-- 8. Legacy tables preserved for migration compatibility
--
-- For migrations, see: scripts/migrate_sqlite_to_pg.py
-- For schema updates, increment db_version in system_state
--
-- ===========================================================================
