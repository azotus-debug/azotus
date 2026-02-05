"""
omega_db_pg.py - PostgreSQL Database Connection Module for Omega TV

This module provides PostgreSQL database connectivity with connection pooling,
retry logic, and graceful error handling. It can be used alongside omega_db.py
for PostgreSQL-specific operations.

Connection Methods (in priority order):
1. OMEGA_PG_CONNECTION_STRING - Direct connection string
2. OMEGA_CLOUD_SQL_INSTANCE - Cloud SQL Auth Proxy connection
3. Individual params - OMEGA_PG_HOST, OMEGA_PG_PORT, OMEGA_PG_DATABASE, OMEGA_PG_USER, OMEGA_PG_PASSWORD
4. Legacy: DB_DSN / DATABASE_URL / CLOUD_SQL_DSN

Environment Variables:
- OMEGA_PG_CONNECTION_STRING: Full PostgreSQL connection string
- OMEGA_CLOUD_SQL_INSTANCE: Cloud SQL instance name (project:region:instance)
- OMEGA_CLOUD_SQL_SOCKET_DIR: Socket directory for Cloud SQL proxy (default: /cloudsql)
- OMEGA_PG_HOST: PostgreSQL host
- OMEGA_PG_PORT: PostgreSQL port (default: 5432)
- OMEGA_PG_DATABASE: Database name (default: omega_db)
- OMEGA_PG_USER: Database user (default: omega_user)
- OMEGA_PG_PASSWORD: Database password
- OMEGA_PG_POOL_MIN: Minimum pool connections (default: 2)
- OMEGA_PG_POOL_MAX: Maximum pool connections (default: 10)
- OMEGA_PG_TIMEOUT: Connection timeout in seconds (default: 30)
- OMEGA_PG_RETRY_ATTEMPTS: Number of retry attempts (default: 3)
- OMEGA_PG_RETRY_DELAY: Base delay between retries in seconds (default: 1.0)
- OMEGA_LOCATIONS: Allowed processing locations (default: iceland,virginia)
- OMEGA_PG_CUTOVER_LANGS / OMEGA_CUTOVER_LANGS: Languages for cutover (optional)

Usage:
    from omega_db_pg import get_connection, fetch_all, transaction

    # Simple query
    rows = fetch_all("SELECT * FROM jobs WHERE stage = %s", ("COMPLETED",))

    # Transaction
    with transaction() as conn:
        execute("INSERT INTO jobs (file_stem, stage) VALUES (%s, %s)", ("test", "QUEUED"), conn=conn)
        execute("UPDATE programs SET updated_at = NOW() WHERE id = %s", (program_id,), conn=conn)
"""

import json
import os
import time
import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2 import pool, OperationalError, InterfaceError
from psycopg2.extras import RealDictCursor, Json, register_default_json, register_default_jsonb
from psycopg2.extensions import connection as PgConnection

# Configure logging
logger = logging.getLogger("omega_db_pg")

# ============================================================================
# Connection Pool Configuration
# ============================================================================

_POOL_MIN_CONN = int(os.getenv("OMEGA_PG_POOL_MIN", os.getenv("PG_POOL_MIN", "2")))
_POOL_MAX_CONN = int(os.getenv("OMEGA_PG_POOL_MAX", os.getenv("PG_POOL_MAX", "10")))
_CONNECTION_TIMEOUT = int(os.getenv("OMEGA_PG_TIMEOUT", "30"))
_RETRY_ATTEMPTS = int(os.getenv("OMEGA_PG_RETRY_ATTEMPTS", "3"))
_RETRY_DELAY = float(os.getenv("OMEGA_PG_RETRY_DELAY", "1.0"))

# Processing configuration (for claim/lease operations)
LEASE_MINUTES_DEFAULT = 30
PROCESSING_STEPS = [
    "ingest",
    "transcribe",
    "translate_submit",
    "translate_cloud",
    "review",
    "finalize",
    "burn",
    "deliver",
]

# Thread-safe pool management
_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

# Legacy compatibility alias
_POOL = None
_POOL_LOCK = _pool_lock


# ============================================================================
# Helper Functions
# ============================================================================

def _parse_csv(value: str) -> List[str]:
    """Parse comma-separated values into a list."""
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def _get_locations() -> List[str]:
    """Get allowed processing locations from environment."""
    raw = os.getenv("OMEGA_LOCATIONS", "iceland,virginia")
    return _parse_csv(raw)


def _get_cutover_languages() -> Optional[List[str]]:
    """Get languages that have been cut over to PostgreSQL."""
    raw = os.getenv("OMEGA_PG_CUTOVER_LANGS") or os.getenv("OMEGA_CUTOVER_LANGS") or ""
    langs = _parse_csv(raw)
    return langs or None


def _normalize_location(location_id: str) -> str:
    """Normalize and validate location ID."""
    location_id = (location_id or "").strip().lower()
    if not location_id:
        raise ValueError("location_id is required")
    return location_id


def _validate_location(location_id: str) -> None:
    """Validate that location_id is in the allowed list."""
    allowed = set(_get_locations())
    if location_id not in allowed:
        raise ValueError(f"Unknown location_id '{location_id}'. Allowed: {sorted(allowed)}")


def _normalize_language_codes(language_codes: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Normalize language codes to lowercase."""
    if not language_codes:
        return None
    return [code.strip().lower() for code in language_codes if code and code.strip()]


def _validate_processing_step(step: str) -> None:
    """Validate that a processing step is known."""
    if step not in PROCESSING_STEPS:
        raise ValueError(f"Invalid processing_step '{step}'. Allowed: {PROCESSING_STEPS}")


def _configure_connection(conn) -> None:
    """Configure a PostgreSQL connection with JSON handling."""
    register_default_json(conn, loads=json.loads)
    register_default_jsonb(conn, loads=json.loads)


# ============================================================================
# Connection Management
# ============================================================================

def _get_dsn() -> str:
    """
    Get PostgreSQL DSN (connection string) from environment.

    Checks multiple environment variables in order of preference:
    1. OMEGA_PG_CONNECTION_STRING (new)
    2. DB_DSN (legacy)
    3. DATABASE_URL (legacy)
    4. CLOUD_SQL_DSN (legacy)

    If none are set, attempts to build from individual parameters.

    Returns:
        PostgreSQL connection string.

    Raises:
        RuntimeError: If no connection configuration is found.
    """
    # Check for direct connection strings
    dsn = (
        os.getenv("OMEGA_PG_CONNECTION_STRING")
        or os.getenv("DB_DSN")
        or os.getenv("DATABASE_URL")
        or os.getenv("CLOUD_SQL_DSN")
        or ""
    ).strip()

    if dsn:
        logger.debug("Using connection string from environment")
        return dsn

    # Check for Cloud SQL Auth Proxy
    cloud_sql_instance = os.getenv("OMEGA_CLOUD_SQL_INSTANCE", "").strip()
    if cloud_sql_instance:
        socket_dir = os.getenv("OMEGA_CLOUD_SQL_SOCKET_DIR", "/cloudsql")
        socket_path = f"{socket_dir}/{cloud_sql_instance}"
        host = socket_path
        port = ""  # Unix socket
    else:
        # Check for individual parameters
        host = os.getenv("OMEGA_PG_HOST", "").strip()
        port = os.getenv("OMEGA_PG_PORT", "5432")

    if host:
        database = os.getenv("OMEGA_PG_DATABASE", "omega_db")
        user = os.getenv("OMEGA_PG_USER", "omega_user")
        password = os.getenv("OMEGA_PG_PASSWORD", "")

        # Build connection string
        if cloud_sql_instance:
            # Unix socket connection
            dsn = f"postgresql://{user}:{password}@/{database}?host={host}"
        else:
            # TCP connection
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"

        logger.debug("Built connection string from individual parameters")
        return dsn

    raise RuntimeError(
        "Missing PostgreSQL connection configuration. Set one of:\n"
        "  - OMEGA_PG_CONNECTION_STRING (e.g., postgresql://user:pass@host:5432/dbname)\n"
        "  - OMEGA_CLOUD_SQL_INSTANCE (e.g., project:region:instance)\n"
        "  - OMEGA_PG_HOST + OMEGA_PG_DATABASE + OMEGA_PG_USER + OMEGA_PG_PASSWORD\n"
        "  - DB_DSN / DATABASE_URL (legacy)"
    )


def _init_pool() -> pool.ThreadedConnectionPool:
    """
    Initialize the connection pool.

    Returns:
        ThreadedConnectionPool instance.

    Raises:
        OperationalError: If connection to database fails.
    """
    global _pool, _POOL

    with _pool_lock:
        if _pool is not None:
            return _pool

        dsn = _get_dsn()
        logger.info("Initializing PostgreSQL connection pool (min=%d, max=%d)",
                    _POOL_MIN_CONN, _POOL_MAX_CONN)

        try:
            _pool = pool.ThreadedConnectionPool(
                _POOL_MIN_CONN,
                _POOL_MAX_CONN,
                dsn=dsn
            )
            _POOL = _pool  # noqa: F841 - Legacy compatibility alias
            logger.info("PostgreSQL connection pool initialized successfully")
            return _pool
        except OperationalError as e:
            logger.error("Failed to initialize connection pool: %s", e)
            raise


def _get_pool() -> pool.ThreadedConnectionPool:
    """Get the connection pool, initializing if necessary."""
    global _pool
    if _pool is None:
        return _init_pool()
    return _pool


def get_connection(autocommit: bool = False) -> PgConnection:
    """
    Get a connection from the pool.

    Args:
        autocommit: If True, set connection to autocommit mode.

    Returns:
        PostgreSQL connection object.

    Raises:
        OperationalError: If unable to get a connection after retries.

    Note:
        Caller is responsible for returning the connection via release_connection()
        or using the connection as a context manager.
    """
    last_error = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            conn = _get_pool().getconn()
            if conn is None:
                raise OperationalError("Connection pool returned None")

            # Configure JSON handling
            _configure_connection(conn)

            # Test the connection is alive
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

            if autocommit:
                conn.autocommit = True

            logger.debug("Acquired connection from pool (attempt %d)", attempt + 1)
            return conn

        except (OperationalError, InterfaceError) as e:
            last_error = e
            logger.warning("Connection attempt %d failed: %s", attempt + 1, e)

            # Try to close bad connection
            if 'conn' in locals() and conn is not None:
                try:
                    _get_pool().putconn(conn, close=True)
                except Exception:
                    pass

            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_DELAY * (attempt + 1))  # Exponential backoff

    logger.error("Failed to get connection after %d attempts", _RETRY_ATTEMPTS)
    raise OperationalError(f"Failed to get connection after {_RETRY_ATTEMPTS} attempts: {last_error}")


def release_connection(conn: PgConnection, close: bool = False) -> None:
    """
    Return a connection to the pool.

    Args:
        conn: Connection to return.
        close: If True, close the connection instead of returning to pool.
    """
    if conn is None:
        return

    try:
        _get_pool().putconn(conn, close=close)
        logger.debug("Released connection to pool (close=%s)", close)
    except Exception as e:
        logger.warning("Error releasing connection: %s", e)


@contextmanager
def _get_conn():
    """
    Legacy context manager for getting a connection.

    This is kept for backward compatibility with existing code.
    Prefer using transaction() for new code.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ============================================================================
# Query Functions
# ============================================================================

def execute(
    query: str,
    params: Optional[Tuple] = None,
    conn: Optional[PgConnection] = None,
    returning: bool = False
) -> Optional[Any]:
    """
    Execute a query.

    Args:
        query: SQL query to execute.
        params: Query parameters (optional).
        conn: Existing connection to use (optional). If None, gets a new one.
        returning: If True, return the result of the query (for RETURNING clauses).

    Returns:
        If returning=True, returns fetchone() result. Otherwise None.

    Raises:
        Exception: On database error.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            result = cur.fetchone() if returning else None

        if own_conn:
            conn.commit()

        return result

    except Exception as e:
        if own_conn:
            conn.rollback()
        logger.error("Execute failed: %s | Query: %s | Params: %s", e, query, params)
        raise

    finally:
        if own_conn:
            release_connection(conn)


def execute_many(
    query: str,
    params_list: List[Tuple],
    conn: Optional[PgConnection] = None
) -> None:
    """
    Execute a query with multiple parameter sets.

    Args:
        query: SQL query to execute.
        params_list: List of parameter tuples.
        conn: Existing connection to use (optional). If None, gets a new one.

    Raises:
        Exception: On database error.
    """
    if not params_list:
        return

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.executemany(query, params_list)

        if own_conn:
            conn.commit()

        logger.debug("Executed batch query with %d rows", len(params_list))

    except Exception as e:
        if own_conn:
            conn.rollback()
        logger.error("Execute many failed: %s | Query: %s", e, query)
        raise

    finally:
        if own_conn:
            release_connection(conn)


def fetch_one(
    query: str,
    params: Optional[Tuple] = None,
    conn: Optional[PgConnection] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single row as a dictionary.

    Args:
        query: SQL query to execute.
        params: Query parameters (optional).
        conn: Existing connection to use (optional). If None, gets a new one.

    Returns:
        Row as dictionary, or None if no results.

    Raises:
        Exception: On database error.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    except Exception as e:
        logger.error("Fetch one failed: %s | Query: %s | Params: %s", e, query, params)
        raise

    finally:
        if own_conn:
            release_connection(conn)


def fetch_all(
    query: str,
    params: Optional[Tuple] = None,
    conn: Optional[PgConnection] = None
) -> List[Dict[str, Any]]:
    """
    Fetch all rows as a list of dictionaries.

    Args:
        query: SQL query to execute.
        params: Query parameters (optional).
        conn: Existing connection to use (optional). If None, gets a new one.

    Returns:
        List of rows as dictionaries.

    Raises:
        Exception: On database error.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    except Exception as e:
        logger.error("Fetch all failed: %s | Query: %s | Params: %s", e, query, params)
        raise

    finally:
        if own_conn:
            release_connection(conn)


@contextmanager
def transaction():
    """
    Context manager for atomic transactions.

    Usage:
        with transaction() as conn:
            execute("INSERT INTO ...", params, conn=conn)
            execute("UPDATE ...", params, conn=conn)
            # Commits on exit, rolls back on exception

    Yields:
        PostgreSQL connection object with an active transaction.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
        logger.debug("Transaction committed")
    except Exception as e:
        conn.rollback()
        logger.warning("Transaction rolled back: %s", e)
        raise
    finally:
        release_connection(conn)


# ============================================================================
# Health Check and Pool Management
# ============================================================================

def health_check() -> Dict[str, Any]:
    """
    Check database connectivity and measure latency.

    Returns:
        Dict with:
            - connected: bool - Whether database is reachable
            - latency_ms: float - Round-trip time in milliseconds
            - pool_min: int - Minimum pool size (if connected)
            - pool_max: int - Maximum pool size (if connected)
            - error: str - Error message (if not connected)
    """
    start = time.perf_counter()

    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

            elapsed = (time.perf_counter() - start) * 1000

            return {
                "connected": True,
                "latency_ms": round(elapsed, 2),
                "pool_min": _POOL_MIN_CONN,
                "pool_max": _POOL_MAX_CONN,
            }

        finally:
            release_connection(conn)

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        logger.error("Health check failed: %s", e)
        return {
            "connected": False,
            "latency_ms": round(elapsed, 2),
            "error": str(e)
        }


def close_pool() -> None:
    """
    Close all connections in the pool.

    Call this during application shutdown.
    """
    global _pool, _POOL

    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                logger.info("Connection pool closed")
            except Exception as e:
                logger.warning("Error closing pool: %s", e)
            finally:
                _pool = None
                _POOL = None  # noqa: F841 - Reset legacy alias


def is_configured() -> bool:
    """
    Check if PostgreSQL connection is configured.

    Returns:
        True if environment variables for PostgreSQL are set.
    """
    return bool(
        os.getenv("OMEGA_PG_CONNECTION_STRING") or
        os.getenv("OMEGA_CLOUD_SQL_INSTANCE") or
        os.getenv("OMEGA_PG_HOST") or
        os.getenv("DB_DSN") or
        os.getenv("DATABASE_URL") or
        os.getenv("CLOUD_SQL_DSN")
    )


# ============================================================================
# Convenience Aliases
# ============================================================================

query = fetch_all
query_one = fetch_one


# ============================================================================
# Track Management Functions (Legacy Compatibility)
# ============================================================================

def get_track(track_id: str) -> Optional[Dict[str, Any]]:
    """Get a track by ID."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM tracks WHERE id = %s", (track_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def record_event(track_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Record an event for a track."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events (track_id, event_type, payload)
                VALUES (%s, %s, %s)
                """,
                (track_id, event_type, Json(payload or {})),
            )


def claim_next_track(
    location_id: str,
    worker_id: str,
    lease_minutes: int = LEASE_MINUTES_DEFAULT,
    processing_step: str = "ingest",
    language_codes: Optional[Sequence[str]] = None,
    stage: str = "queued",
) -> Optional[Dict[str, Any]]:
    """
    Claim the next available track for processing.

    Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent claiming.

    Args:
        location_id: Processing location identifier.
        worker_id: Worker identifier claiming the track.
        lease_minutes: Lease duration in minutes.
        processing_step: Current processing step.
        language_codes: Filter by language codes (optional).
        stage: Stage to claim from (default: queued).

    Returns:
        Claimed track as dictionary, or None if no track available.
    """
    location_id = _normalize_location(location_id)
    _validate_location(location_id)
    language_codes = _normalize_language_codes(language_codes) or _get_cutover_languages()
    _validate_processing_step(processing_step)

    where_clauses = [
        "stage = %s",
        "(lease_until IS NULL OR lease_until < now())",
        "(storage_location IS NULL OR storage_location = %s OR %s = ANY(eligible_locations))",
    ]
    params: List[Any] = [stage, location_id, location_id]

    if language_codes:
        where_clauses.append("language_code = ANY(%s)")
        params.append(list(language_codes))

    where_sql = " AND ".join(where_clauses)

    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id
                FROM tracks
                WHERE {where_sql}
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                params,
            )
            row = cur.fetchone()
            if not row:
                return None

            track_id = row["id"]
            cur.execute(
                """
                UPDATE tracks
                SET stage = 'processing',
                    processing_step = %s,
                    claimed_at = now(),
                    location_id = %s,
                    worker_id = %s,
                    lease_until = now() + make_interval(mins => %s),
                    heartbeat_at = now(),
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (processing_step, location_id, worker_id, lease_minutes, track_id),
            )
            result = cur.fetchone()
            return dict(result) if result else None


def heartbeat_track(track_id: str, worker_id: str, lease_minutes: int = LEASE_MINUTES_DEFAULT) -> bool:
    """
    Update heartbeat and extend lease for a track.

    Args:
        track_id: Track to update.
        worker_id: Worker ID (must match current claim).
        lease_minutes: New lease duration.

    Returns:
        True if heartbeat was recorded, False if track not owned by worker.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tracks
                SET heartbeat_at = now(),
                    lease_until = now() + make_interval(mins => %s),
                    updated_at = now()
                WHERE id = %s AND worker_id = %s
                RETURNING id
                """,
                (lease_minutes, track_id, worker_id),
            )
            return cur.fetchone() is not None


def release_lease(
    track_id: str,
    worker_id: str,
    next_stage: Optional[str] = None,
    processing_step: Optional[str] = None,
    status: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    """
    Release a lease on a track, optionally updating its state.

    Args:
        track_id: Track to release.
        worker_id: Worker ID (must match current claim).
        next_stage: New stage to set (optional).
        processing_step: New processing step (optional).
        status: New status (optional).
        error: Error message (optional).

    Returns:
        True if lease was released, False if track not owned by worker.
    """
    fields: Dict[str, Any] = {
        "lease_until": None,
        "heartbeat_at": None,
    }
    if next_stage is not None:
        fields["stage"] = next_stage
    if processing_step is not None:
        _validate_processing_step(processing_step)
        fields["processing_step"] = processing_step
    if status is not None:
        fields["status"] = status
    if error is not None:
        fields["error"] = error

    return update_track_fields(track_id, worker_id, fields)


def update_track_fields(
    track_id: str,
    worker_id: Optional[str],
    fields: Dict[str, Any],
) -> bool:
    """
    Update arbitrary fields on a track.

    Args:
        track_id: Track to update.
        worker_id: Worker ID for ownership check (optional).
        fields: Dictionary of field names to values.

    Returns:
        True if track was updated, False if not found or not owned.

    Raises:
        ValueError: If an unknown field is specified.
    """
    if not fields:
        return False

    allowed = {
        "stage",
        "processing_step",
        "status",
        "storage_location",
        "eligible_locations",
        "location_id",
        "worker_id",
        "claimed_at",
        "lease_until",
        "heartbeat_at",
        "completed_at",
        "error",
        "retry_count",
        "artifacts",
        "checkpoints",
        "artifact_hashes",
    }

    updates = []
    params: List[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Field '{key}' is not allowed for update")
        if key == "processing_step" and value is not None:
            _validate_processing_step(value)
        if key in {"artifacts", "checkpoints", "artifact_hashes"}:
            updates.append(f"{key} = %s")
            params.append(Json(value or {}))
        else:
            updates.append(f"{key} = %s")
            params.append(value)

    updates.append("updated_at = now()")

    where = "id = %s"
    params.append(track_id)
    if worker_id:
        where += " AND worker_id = %s"
        params.append(worker_id)

    sql = f"UPDATE tracks SET {', '.join(updates)} WHERE {where} RETURNING id"

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() is not None


# ============================================================================
# Module Entry Point (for CLI testing)
# ============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not is_configured():
        print("PostgreSQL not configured. Set OMEGA_PG_* environment variables.")
        print("\nSupported configurations:")
        print("  1. OMEGA_PG_CONNECTION_STRING (full connection string)")
        print("  2. OMEGA_CLOUD_SQL_INSTANCE (Cloud SQL Auth Proxy)")
        print("  3. OMEGA_PG_HOST + OMEGA_PG_DATABASE + OMEGA_PG_USER + OMEGA_PG_PASSWORD")
        print("  4. DB_DSN or DATABASE_URL (legacy)")
        sys.exit(1)

    print("Testing PostgreSQL connection...")
    result = health_check()

    if result["connected"]:
        print(f"Connected successfully! Latency: {result['latency_ms']}ms")
        print(f"Pool configuration: min={result.get('pool_min')}, max={result.get('pool_max')}")

        # Try a simple query
        try:
            rows = fetch_all(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' LIMIT 10"
            )
            print(f"\nFound {len(rows)} tables in public schema:")
            for row in rows:
                print(f"  - {row['table_name']}")
        except Exception as e:
            print(f"Query failed: {e}")

        close_pool()
        print("\nConnection pool closed.")
    else:
        print(f"Connection failed: {result.get('error', 'Unknown error')}")
        sys.exit(1)
