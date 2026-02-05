#!/usr/bin/env python3
"""
SQLite to PostgreSQL Migration Script for Omega Subtitle Workflow.

This script migrates data from the SQLite production.db to a PostgreSQL database.
It handles schema differences, generates UUIDs where needed, and converts data types.

Usage:
    python scripts/migrate_sqlite_to_pg.py --dry-run
    python scripts/migrate_sqlite_to_pg.py --validate
    python scripts/migrate_sqlite_to_pg.py --batch-size 500
    python scripts/migrate_sqlite_to_pg.py --resume-from <id>

Environment Variables:
    DB_DSN / DATABASE_URL - PostgreSQL connection string
    SQLITE_DB_PATH - Path to SQLite database (default: production.db)

Author: Omega Subtitle Workflow Team
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Attempt to load psycopg2
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values, Json
except ImportError:
    print("ERROR: psycopg2 is required. Install with: pip install psycopg2-binary")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ----- Configuration -----

# Default paths
DEFAULT_SQLITE_PATH = Path(__file__).parent.parent / "production.db"

# Stage enum mappings (legacy -> normalized)
STAGE_MAPPING: Dict[str, str] = {
    # Standard stages (already normalized)
    "QUEUED": "queued",
    "INGEST": "ingest",
    "TRANSCRIBED": "transcribed",
    "TRANSLATING": "translating",
    "TRANSLATING_CLOUD_SUBMITTED": "translating",
    "CLOUD_TRANSLATING": "translating",
    "CLOUD_REVIEWING": "reviewing",
    "REVIEWED": "reviewed",
    "AWAITING_REVIEW": "reviewing",
    "FINALIZING": "finalizing",
    "FINALIZED": "finalized",
    "BURNING": "burning",
    "BURNED": "burned",
    "COMPLETED": "completed",
    "DELIVERED": "delivered",
    "ERROR": "error",
    "FAILED": "error",
    "DEAD": "error",
    # Empty or None
    "": "queued",
    None: "queued",
}

# Tables in migration order (respecting foreign key dependencies)
MIGRATION_ORDER: List[str] = [
    "system_state",      # Independent
    "programs",          # Root entity
    "jobs",              # Independent (legacy)
    "master_scripts",    # References programs
    "tracks",            # References programs, master_scripts
    "track_deliveries",  # References tracks
    "script_edits",      # References master_scripts, tracks
    "stage_transitions", # References tracks
    "deliveries",        # References jobs (legacy)
    "stations",          # Independent
    "cloud_sync_state",  # Independent
    "book_projects",     # Independent (Literati)
    "book_chapters",     # References book_projects
]


@dataclass
class MigrationStats:
    """Track migration statistics."""
    table: str
    total: int = 0
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    failed_ids: List[str] = field(default_factory=list)

    def success_rate(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.migrated / self.total) * 100


@dataclass
class MigrationResult:
    """Overall migration result."""
    stats: Dict[str, MigrationStats] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    def summary(self) -> str:
        """Generate migration summary."""
        lines = [
            "",
            "=" * 60,
            "MIGRATION SUMMARY",
            "=" * 60,
            f"Started:   {self.started_at.isoformat()}",
            f"Completed: {self.completed_at.isoformat() if self.completed_at else 'In Progress'}",
            "",
        ]

        for table, stat in self.stats.items():
            status = "done" if stat.failed == 0 else f"{stat.failed} FAILED"
            lines.append(f"  {table}: {stat.migrated}/{stat.total} ({status})")

        if self.errors:
            lines.append("")
            lines.append("ERRORS:")
            for err in self.errors[:10]:  # Show first 10
                lines.append(f"  - {err}")
            if len(self.errors) > 10:
                lines.append(f"  ... and {len(self.errors) - 10} more")

        lines.append("=" * 60)
        return "\n".join(lines)


# ----- Helper Functions -----

def get_pg_dsn() -> str:
    """Get PostgreSQL connection string from environment."""
    dsn = (
        os.getenv("DB_DSN")
        or os.getenv("DATABASE_URL")
        or os.getenv("CLOUD_SQL_DSN")
        or ""
    ).strip()
    if not dsn:
        raise RuntimeError(
            "Missing PostgreSQL DSN. Set one of: DB_DSN, DATABASE_URL, CLOUD_SQL_DSN"
        )
    return dsn


def get_sqlite_path() -> Path:
    """Get SQLite database path."""
    path = os.getenv("SQLITE_DB_PATH", str(DEFAULT_SQLITE_PATH))
    return Path(path)


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def convert_timestamp(val: Any) -> Optional[str]:
    """
    Convert various timestamp formats to ISO 8601 string.
    Handles: None, int/float (Unix timestamp), string (ISO or other formats).
    """
    if val is None:
        return None

    # Already a datetime
    if isinstance(val, datetime):
        return val.isoformat()

    # Unix timestamp (int or float)
    if isinstance(val, (int, float)):
        # Sanity check: should be a reasonable timestamp (year 2000-2100)
        if 946684800 < val < 4102444800:  # 2000-01-01 to 2100-01-01
            try:
                return datetime.fromtimestamp(val).isoformat()
            except (OSError, ValueError):
                pass
        # Might be milliseconds
        if val > 1000000000000:
            try:
                return datetime.fromtimestamp(val / 1000).isoformat()
            except (OSError, ValueError):
                pass
        return None

    # String timestamp
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None

        # Try ISO format first
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(val, fmt).isoformat()
            except ValueError:
                continue

        # Return as-is if it looks like a date
        if "20" in val[:4]:  # Starts with year 20xx
            return val

    return None


def convert_meta_to_jsonb(val: Any) -> Optional[Dict]:
    """
    Convert meta column from TEXT to JSONB-compatible dict.
    """
    if val is None:
        return None

    if isinstance(val, dict):
        return val

    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            # Return as wrapped object if not valid JSON
            return {"_raw": val}

    return None


def normalize_stage(stage: Optional[str]) -> str:
    """Convert legacy stage string to normalized enum value."""
    if stage is None:
        return "queued"
    stage = stage.strip().upper()
    return STAGE_MAPPING.get(stage, stage.lower())


def convert_boolean(val: Any) -> Optional[bool]:
    """Convert SQLite integer to PostgreSQL boolean."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "t")
    return bool(val)


# ----- Table Transformers -----

def transform_programs(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform programs table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    row["deleted_at"] = convert_timestamp(row.get("deleted_at"))
    return row


def transform_tracks(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform tracks table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["stage"] = normalize_stage(row.get("stage"))
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["output_override"] = convert_boolean(row.get("output_override"))
    row["pending_resync"] = convert_boolean(row.get("pending_resync"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    row["locked_at"] = convert_timestamp(row.get("locked_at"))
    row["override_timestamp"] = convert_timestamp(row.get("override_timestamp"))
    return row


def transform_master_scripts(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform master_scripts table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    row["locked_at"] = convert_timestamp(row.get("locked_at"))
    return row


def transform_jobs(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform jobs table row."""
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    row["retry_after"] = convert_timestamp(row.get("retry_after"))
    row["claimed_at"] = convert_timestamp(row.get("claimed_at"))
    return row


def transform_stage_transitions(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform stage_transitions table row."""
    # Note: id is auto-generated in PostgreSQL (SERIAL)
    row["from_stage"] = normalize_stage(row.get("from_stage")) if row.get("from_stage") else None
    row["to_stage"] = normalize_stage(row.get("to_stage"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    return row


def transform_track_deliveries(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform track_deliveries table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["delivered_at"] = convert_timestamp(row.get("delivered_at"))
    return row


def transform_script_edits(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform script_edits table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    return row


def transform_deliveries(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform deliveries table row."""
    # id is auto-generated in PostgreSQL
    row["delivered_at"] = convert_timestamp(row.get("delivered_at"))
    return row


def transform_stations(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform stations table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["config"] = convert_meta_to_jsonb(row.get("config"))
    row["last_heartbeat"] = convert_timestamp(row.get("last_heartbeat"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    return row


def transform_cloud_sync_state(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform cloud_sync_state table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    return row


def transform_book_projects(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform book_projects table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    return row


def transform_book_chapters(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform book_chapters table row."""
    row["id"] = row.get("id") or generate_uuid()
    row["meta"] = convert_meta_to_jsonb(row.get("meta"))
    row["created_at"] = convert_timestamp(row.get("created_at"))
    row["updated_at"] = convert_timestamp(row.get("updated_at"))
    row["locked_at"] = convert_timestamp(row.get("locked_at"))
    row["step1_completed_at"] = convert_timestamp(row.get("step1_completed_at"))
    row["step2_completed_at"] = convert_timestamp(row.get("step2_completed_at"))
    row["step3_completed_at"] = convert_timestamp(row.get("step3_completed_at"))
    return row


def transform_system_state(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform system_state table row (no changes needed)."""
    return row


# Transformer registry
TRANSFORMERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "programs": transform_programs,
    "tracks": transform_tracks,
    "master_scripts": transform_master_scripts,
    "jobs": transform_jobs,
    "stage_transitions": transform_stage_transitions,
    "track_deliveries": transform_track_deliveries,
    "script_edits": transform_script_edits,
    "deliveries": transform_deliveries,
    "stations": transform_stations,
    "cloud_sync_state": transform_cloud_sync_state,
    "book_projects": transform_book_projects,
    "book_chapters": transform_book_chapters,
    "system_state": transform_system_state,
}


# ----- PostgreSQL Schema Information -----

# Columns that are JSONB in PostgreSQL (need Json() wrapper)
JSONB_COLUMNS = {"meta", "config", "artifacts", "checkpoints", "artifact_hashes"}

# Columns that are auto-generated in PostgreSQL (skip in INSERT)
AUTO_COLUMNS = {
    "deliveries": ["id"],
    "stage_transitions": ["id"],
}


# ----- Migration Functions -----

def get_sqlite_tables(conn: sqlite3.Connection) -> List[str]:
    """Get list of tables in SQLite database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return [row[0] for row in cursor.fetchall()]


def get_table_columns(sqlite_conn: sqlite3.Connection, table: str) -> List[str]:
    """Get column names for a table in SQLite."""
    cursor = sqlite_conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def get_pg_table_columns(pg_conn, table: str) -> List[str]:
    """Get column names for a table in PostgreSQL."""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        return [row[0] for row in cur.fetchall()]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a SQLite table."""
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def fetch_rows(
    conn: sqlite3.Connection,
    table: str,
    batch_size: int,
    offset: int = 0,
    resume_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch rows from SQLite table in batches."""
    columns = get_table_columns(conn, table)

    # Build query with optional resume point
    query = f"SELECT * FROM {table}"
    params: List[Any] = []

    # Determine primary key for ordering/resuming
    pk = "id" if "id" in columns else (columns[0] if columns else None)

    if resume_id and pk:
        query += f" WHERE {pk} > ?"
        params.append(resume_id)

    if pk:
        query += f" ORDER BY {pk}"

    query += " LIMIT ? OFFSET ?"
    params.extend([batch_size, offset])

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    # Convert to list of dicts
    return [dict(zip(columns, row)) for row in rows]


def build_insert_sql(table: str, columns: List[str]) -> str:
    """Build PostgreSQL INSERT statement with ON CONFLICT handling."""
    # Skip auto-generated columns
    skip_cols = set(AUTO_COLUMNS.get(table, []))
    insert_cols = [c for c in columns if c not in skip_cols]

    col_list = ", ".join(f'"{c}"' for c in insert_cols)
    placeholders = ", ".join(["%s"] * len(insert_cols))

    # Determine conflict handling based on table
    if table in ("system_state",):
        conflict_col = "key"
    elif table in ("deliveries", "stage_transitions"):
        # These have auto-generated IDs, no conflict expected
        return f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
    elif "id" in insert_cols:
        conflict_col = "id"
    elif "file_stem" in insert_cols:
        conflict_col = "file_stem"
    else:
        # No conflict handling
        return f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'

    return f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT ("{conflict_col}") DO NOTHING'


def prepare_row_values(
    row: Dict[str, Any],
    columns: List[str],
    table: str,
) -> Tuple[Any, ...]:
    """Prepare row values for PostgreSQL INSERT."""
    skip_cols = set(AUTO_COLUMNS.get(table, []))
    values = []

    for col in columns:
        if col in skip_cols:
            continue

        val = row.get(col)

        # Wrap JSONB columns
        if col in JSONB_COLUMNS and val is not None:
            val = Json(val)

        values.append(val)

    return tuple(values)


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table: str,
    batch_size: int,
    dry_run: bool = False,
    resume_id: Optional[str] = None,
) -> MigrationStats:
    """Migrate a single table from SQLite to PostgreSQL."""
    stats = MigrationStats(table=table)

    # Get column information
    sqlite_cols = get_table_columns(sqlite_conn, table)
    if not sqlite_cols:
        logger.warning(f"Table {table} has no columns or doesn't exist in SQLite")
        return stats

    pg_cols = get_pg_table_columns(pg_conn, table)
    if not pg_cols:
        logger.warning(f"Table {table} doesn't exist in PostgreSQL - skipping")
        return stats

    # Find common columns (SQLite might have more/different columns)
    common_cols = [c for c in sqlite_cols if c in pg_cols]
    if not common_cols:
        logger.warning(f"No common columns between SQLite and PostgreSQL for table {table}")
        return stats

    # Get transformer
    transformer = TRANSFORMERS.get(table, lambda x: x)

    # Count total rows
    stats.total = count_rows(sqlite_conn, table)
    if stats.total == 0:
        logger.info(f"  {table}: Empty table, skipping")
        return stats

    # Build INSERT SQL
    insert_sql = build_insert_sql(table, common_cols)

    offset = 0
    batch_num = 0

    while offset < stats.total:
        batch_num += 1
        rows = fetch_rows(sqlite_conn, table, batch_size, offset, resume_id if batch_num == 1 else None)

        if not rows:
            break

        for row in rows:
            try:
                # Apply transformation
                transformed = transformer(row)

                # Prepare values
                values = prepare_row_values(transformed, common_cols, table)

                if dry_run:
                    stats.migrated += 1
                else:
                    with pg_conn.cursor() as cur:
                        cur.execute(insert_sql, values)
                    stats.migrated += 1

            except Exception as e:
                stats.failed += 1
                row_id = row.get("id") or row.get("file_stem") or row.get("key") or str(offset)
                stats.failed_ids.append(str(row_id))
                logger.error(f"    Failed to migrate {table} row {row_id}: {e}")

        if not dry_run:
            pg_conn.commit()

        offset += batch_size
        progress = min(100, int((offset / stats.total) * 100))
        status_char = "." if stats.failed == 0 else "!"
        print(f"\r  {table}: {stats.migrated}/{stats.total} ({progress}%) {status_char}", end="", flush=True)

    print()  # Newline after progress
    return stats


def validate_migration(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    tables: List[str],
) -> bool:
    """Validate migration by comparing counts and sample data."""
    logger.info("\nValidating migration...")
    all_valid = True

    for table in tables:
        # Count comparison
        sqlite_count = count_rows(sqlite_conn, table)

        with pg_conn.cursor() as cur:
            try:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                pg_count = cur.fetchone()[0]
            except Exception:
                pg_count = 0

        match = "match" if sqlite_count == pg_count else "MISMATCH"
        if sqlite_count != pg_count:
            all_valid = False

        logger.info(f"  {table}: SQLite={sqlite_count}, PostgreSQL={pg_count} ({match})")

    return all_valid


def rollback_migration(pg_conn, tables: List[str]) -> None:
    """
    Truncate PostgreSQL tables to allow re-migration.

    WARNING: This deletes all data in the specified tables!
    """
    logger.warning("ROLLBACK: Truncating PostgreSQL tables...")

    # Truncate in reverse order (respecting foreign keys)
    for table in reversed(tables):
        try:
            with pg_conn.cursor() as cur:
                cur.execute(f'TRUNCATE TABLE "{table}" CASCADE')
            logger.info(f"  Truncated: {table}")
        except Exception as e:
            logger.error(f"  Failed to truncate {table}: {e}")

    pg_conn.commit()
    logger.info("Rollback complete.")


def run_migration(
    dry_run: bool = False,
    validate: bool = False,
    batch_size: int = 100,
    resume_from: Optional[str] = None,
    tables_filter: Optional[List[str]] = None,
) -> MigrationResult:
    """
    Run the full migration from SQLite to PostgreSQL.

    Args:
        dry_run: If True, don't actually insert data
        validate: If True, validate migration after completion
        batch_size: Number of rows to process at a time
        resume_from: Resume from a specific record ID
        tables_filter: Only migrate these tables (None = all)

    Returns:
        MigrationResult with statistics and errors
    """
    result = MigrationResult()

    # Connect to databases
    sqlite_path = get_sqlite_path()
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    logger.info(f"SQLite source: {sqlite_path}")

    sqlite_conn = sqlite3.connect(str(sqlite_path), timeout=30.0)
    sqlite_conn.row_factory = sqlite3.Row

    pg_dsn = get_pg_dsn()
    logger.info(f"PostgreSQL target: {pg_dsn.split('@')[-1] if '@' in pg_dsn else 'configured'}")

    pg_conn = psycopg2.connect(pg_dsn)

    try:
        # Determine which tables to migrate
        available_tables = get_sqlite_tables(sqlite_conn)
        tables_to_migrate = [
            t for t in MIGRATION_ORDER
            if t in available_tables and (tables_filter is None or t in tables_filter)
        ]

        # Add any tables not in our predefined order
        for t in available_tables:
            if t not in tables_to_migrate and (tables_filter is None or t in tables_filter):
                tables_to_migrate.append(t)

        logger.info(f"\nTables to migrate: {', '.join(tables_to_migrate)}")

        if dry_run:
            logger.info("DRY RUN MODE - No data will be written")

        logger.info(f"Batch size: {batch_size}")
        if resume_from:
            logger.info(f"Resuming from ID: {resume_from}")

        logger.info("\n" + "=" * 60)
        logger.info("STARTING MIGRATION")
        logger.info("=" * 60 + "\n")

        # Migrate each table
        for table in tables_to_migrate:
            logger.info(f"Migrating {table}...")

            stats = migrate_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table=table,
                batch_size=batch_size,
                dry_run=dry_run,
                resume_id=resume_from if table == tables_to_migrate[0] else None,
            )

            result.stats[table] = stats

            if stats.failed > 0:
                result.errors.append(
                    f"{table}: {stats.failed} rows failed (IDs: {', '.join(stats.failed_ids[:5])}...)"
                )

        # Validate if requested
        if validate and not dry_run:
            if not validate_migration(sqlite_conn, pg_conn, tables_to_migrate):
                result.errors.append("Validation failed: row counts don't match")

        result.completed_at = datetime.now()

    finally:
        sqlite_conn.close()
        pg_conn.close()

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate Omega Subtitle Workflow data from SQLite to PostgreSQL"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without doing it",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="After migration, compare counts and sample data",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Migrate in batches of N records (default: 100)",
    )
    parser.add_argument(
        "--resume-from",
        type=str,
        default=None,
        help="Resume from a specific record ID",
    )
    parser.add_argument(
        "--tables",
        type=str,
        default=None,
        help="Comma-separated list of tables to migrate (default: all)",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Truncate PostgreSQL tables to allow re-migration (DESTRUCTIVE)",
    )

    args = parser.parse_args()

    # Handle rollback
    if args.rollback:
        confirm = input("WARNING: This will DELETE all data in PostgreSQL tables. Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            logger.info("Rollback cancelled.")
            return

        pg_conn = psycopg2.connect(get_pg_dsn())
        try:
            available_tables = MIGRATION_ORDER  # Use predefined order
            rollback_migration(pg_conn, available_tables)
        finally:
            pg_conn.close()
        return

    # Parse tables filter
    tables_filter = None
    if args.tables:
        tables_filter = [t.strip() for t in args.tables.split(",")]

    try:
        result = run_migration(
            dry_run=args.dry_run,
            validate=args.validate,
            batch_size=args.batch_size,
            resume_from=args.resume_from,
            tables_filter=tables_filter,
        )

        print(result.summary())

        # Exit with error code if there were failures
        if result.errors:
            sys.exit(1)

    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
