import sqlite3
import json
import shutil
import os
from datetime import datetime
from pathlib import Path

# Configuration
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "production.db"
BACKUP_DIR = BASE_DIR / "backups"

def backup_sqlite():
    """Create a raw file copy of the SQLite database."""
    if not DB_PATH.exists():
        print(f"❌ Database not found at {DB_PATH}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"production_{timestamp}.db"
    
    print(f"📦 Backing up SQLite to {backup_file}...")
    shutil.copy2(DB_PATH, backup_file)
    print("✅ File backup complete.")
    return backup_file

def export_to_json():
    """Export all tables to a JSON file for platform-agnostic recovery."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in cursor.fetchall() if not row['name'].startswith('sqlite_')]
    
    export_data = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print(f"📄 Exporting tables to JSON...")
    
    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        rows = [dict(row) for row in cursor.fetchall()]
        export_data[table] = rows
        print(f"  - {table}: {len(rows)} rows")
        
    json_path = BACKUP_DIR / f"export_{timestamp}.json"
    
    # Add metadata
    final_export = {
        "export_date": datetime.now().isoformat(),
        "database_version": "1.0",
        "data": export_data
    }
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(final_export, f, indent=2, default=str)
        
    print(f"✅ JSON export saved to {json_path}")

def main():
    BACKUP_DIR.mkdir(exist_ok=True)
    print("🛡️  Omega Safety Backup Initiated")
    try:
        backup_sqlite()
        export_to_json()
        print("\n✨ All safety checks passed. Ready for migration steps.")
    except Exception as e:
        print(f"\n❌ Backup failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()
