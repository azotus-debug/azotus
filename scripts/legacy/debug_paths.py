import os
import sys
from pathlib import Path
import config
import omega_db

print(f"CWD: {os.getcwd()}")
print(f"Config BASE_DIR: {config.BASE_DIR}")
print(f"Config DB_PATH: {config.DB_PATH}")
print(f"OmegaDB DB_PATH: {omega_db.DB_PATH}")
print(f"Resolved DB_PATH: {omega_db.DB_PATH.resolve() if omega_db.DB_PATH.exists() else 'DOES NOT EXIST'}")

# Check if there is another .db file nearby
print("Scanning for .db files in CWD:")
for f in Path(".").glob("*.db"):
    print(f" - {f} ({f.stat().st_size} bytes, modified: {f.stat().st_mtime})")
