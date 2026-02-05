
import os
import sys

# Ensure base dir is in path to import omega_db
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from dotenv import load_dotenv
load_dotenv()  # Load .env (DB params)

# Set Auth for Connector
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(BASE_DIR, "service_account.json")

import omega_db

def main():
    dump_file = os.path.join(BASE_DIR, "migration_dump.sql")
    if not os.path.exists(dump_file):
        print(f"❌ Dump file not found: {dump_file}")
        print("Run migrate_sqlite_to_pg.py first!")
        sys.exit(1)

    print("🔌 Connecting to Cloud SQL...")
    try:
        conn = omega_db._get_pg_connection()
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)
    
    # Init Schema First
    print("🏗️  Initializing Postgres Schema...")
    try:
        cur = conn.cursor()
        # Drop all known tables to ensure clean slate (and avoid old schema)
        tables = ["script_edits", "track_deliveries", "tracks", "master_scripts", "programs", "system_state", "deliveries", "jobs"]
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        conn.commit()

        omega_db.init_pg_schema(conn)
        print("✅ Schema Initialized.")
    except Exception as e:
        print(f"❌ Schema Init Failed: {e}")
        # Continue? Or Exit? If tables exist, it might persist.
        # But if fail, import likely fails.
        # But init_pg_schema uses IF NOT EXISTS.
        # If it fails, it's a connection or syntax error.
        sys.exit(1)
        
    cursor = conn.cursor()

    print(f"📖 Reading {dump_file}...")
    with open(dump_file, "r") as f:
        sql_script = f.read()

    # Split functionality is rudimentary. 
    # For robust import, we might need to better handle statement splitting 
    # if the dump contains complex logic.
    # But migrate_sqlite_to_pg.py produces separate INSERTs.
    
    # Actually, let's just use cursor.execute?
    # pg8000/psycopg2 might prefer one statement per execute.
    
    statements = sql_script.split(";\n")
    
    print(f"🚀 Importing {len(statements)} statements...")
    
    success = 0
    errors = 0
    
    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            cursor.execute(stmt)
            success += 1
        except Exception as e:
            print(f"⚠️ Error executing statement:\n{stmt[:200]}...\nError: {e}")
            # errors += 1
            # In PG, one error breaks transaction. Fail hard.
            sys.exit(1)
            
    conn.commit()
    conn.close()
    
    print("-" * 40)
    print(f"✅ Import Complete!")
    print(f"Success: {success}")
    print(f"Errors:  {errors}")

if __name__ == "__main__":
    main()
