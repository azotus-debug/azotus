import sqlite3
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Try to import supabase, but handle if not installed
try:
    from supabase import create_client, Client
except ImportError:
    print("❌ 'supabase' package not installed. Please run: pip install supabase")
    sys.exit(1)

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
    print("Please add them to your .env file.")
    sys.exit(1)

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Local DB path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "production.db")

def migrate_table(table_name: str, sqlite_conn: sqlite3.Connection):
    print(f"📦 Migrating table: {table_name}...")
    
    # Fetch all rows from SQLite
    cursor = sqlite_conn.cursor()
    try:
        cursor.execute(f"SELECT * FROM {table_name}")
    except sqlite3.OperationalError:
        print(f"   ⚠️ Table {table_name} not found in SQLite, skipping.")
        return

    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    
    if not rows:
        print(f"   ℹ️ No data in {table_name}.")
        return

    # Prepare batch upsert
    batch_size = 100
    total_rows = len(rows)
    print(f"   Found {total_rows} rows. Syncing...")

    for i in range(0, total_rows, batch_size):
        batch = rows[i:i+batch_size]
        data_batch = []
        
        for row in batch:
            row_dict = dict(zip(columns, row))
            
            # Data cleaning for Postgres compatibility
            cleaned_row = {}
            for k, v in row_dict.items():
                # Convert empty strings to None (NULL) if expected
                if v == "":
                    cleaned_row[k] = None
                # Parse JSON fields if they are strings
                elif k == 'meta' and isinstance(v, str):
                    try:
                        cleaned_row[k] = json.loads(v)
                    except json.JSONDecodeError:
                        cleaned_row[k] = {}
                # Boolean conversion (SQLite 0/1 -> Python bool)
                elif k in ['output_override', 'pending_resync', 'provisional'] and v is not None:
                     cleaned_row[k] = bool(v)
                # Fix: Handle 0.0 in timestamp fields
                elif v == "0.0" or v == 0.0:
                    cleaned_row[k] = None
                # Fix: Handle Unix Timestamps (float/str) -> ISO
                elif k in ['claimed_at', 'retry_after', 'updated_at', 'created_at', 'locked_at', 'override_timestamp'] and v is not None:
                     try:
                         # Try parsing as float timestamp if it looks like one
                         val_float = float(v)
                         if val_float > 1000000000: # heuristic: plausible unix time
                             cleaned_row[k] = datetime.fromtimestamp(val_float).isoformat()
                         else:
                             cleaned_row[k] = v
                     except (ValueError, TypeError):
                         cleaned_row[k] = v
                else:
                    cleaned_row[k] = v
            
            data_batch.append(cleaned_row)
        
        # Upsert to Supabase
        try:
            supabase.table(table_name).upsert(data_batch).execute()
            print(f"   ✓ Batch {i//batch_size + 1} synced ({len(data_batch)} rows)")
        except Exception as e:
            print(f"   ❌ Batch failed: {e}")
            # Optional: detailed error printing
            # print(data_batch[0]) 

def main():
    print("🚀 Starting Migration to Supabase...")
    
    if not os.path.exists(DB_PATH):
        print(f"❌ SQLite database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    
    # Tables to migrate in order of dependencies
    tables = [
        "programs",
        "master_scripts",
        "tracks",
        "jobs",
        "track_deliveries",
        "script_edits"
    ]
    
    for table in tables:
        migrate_table(table, conn)
        
    conn.close()
    print("\n✅ Migration complete!")

if __name__ == "__main__":
    main()
