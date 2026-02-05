import omega_db
import sqlite3
import os
from datetime import datetime

TEST_DB = "smoke_test.db"

def setup():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    
    # Monkey-patch config to use test DB
    original_connect = omega_db._connect
    omega_db.DB_PATH = TEST_DB
    
    # Initialize schema
    omega_db.init_db()
    
    # Insert test data
    conn = omega_db._connect()
    c = conn.cursor()
    
    # Create Program
    c.execute("INSERT INTO programs (id, title) VALUES (?, ?)", ("prog1", "Test Program"))
    
    # Create Master Script
    c.execute("""
        INSERT INTO master_scripts (id, program_id, language_code, state, version)
        VALUES (?, ?, ?, ?, ?)
    """, ("master1", "prog1", "en", "approved", 1))
    
    # Create Track (Simulating pending resync)
    c.execute("""
        INSERT INTO tracks (id, program_id, master_script_id, type, language_code, stage, pending_resync)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("track1", "prog1", "master1", "subtitle", "en", "COMPLETE", 1))
    
    conn.commit()
    conn.close()
    
    return original_connect

def test_provisional_delivery():
    print("🧪 Testing Provisional Delivery...")
    
    # Action: Deliver with clear_pending_resync=False (Provisional)
    omega_db.record_track_delivery(
        track_id="track1",
        destination="folder",
        notes="Provisional Test",
        lock_master=False,
        locked_by=None,
        clear_pending_resync=False
    )
    
    # Verify
    conn = omega_db._connect()
    row = conn.execute("SELECT pending_resync, locked_at, delivery_status FROM tracks WHERE id='track1'").fetchone()
    conn.close()
    
    pending_resync = row[0]
    locked_at = row[1]
    status = row[2]
    
    if pending_resync == 1 and locked_at is None and status == 'DELIVERED':
        print("✅ PASS: Pending Sync preserved, Master unlocked.")
    else:
        print(f"❌ FAIL: Resync={pending_resync}, Locked={locked_at}, Status={status}")
        exit(1)

def test_final_delivery():
    print("🧪 Testing Final Delivery...")
    
    # Action: Deliver with clear_pending_resync=True (Final)
    omega_db.record_track_delivery(
        track_id="track1",
        destination="folder",
        notes="Final Delivery",
        lock_master=True,
        locked_by="tester",
        clear_pending_resync=True
    )
    
    # Verify
    conn = omega_db._connect()
    row = conn.execute("SELECT pending_resync, locked_at, locked_by FROM tracks WHERE id='track1'").fetchone()
    conn.close()
    
    pending_resync = row[0]
    locked_at = row[1]
    locked_by = row[2]
    
    if pending_resync == 0 and locked_at is not None and locked_by == "tester":
        print("✅ PASS: Resync cleared, Master locked.")
    else:
        print(f"❌ FAIL: Resync={pending_resync}, Locked={locked_at}")
        exit(1)

def main():
    print("Running Smoke Tests...")
    try:
        setup()
        test_provisional_delivery()
        test_final_delivery()
        print("\n✨ All smoke tests passed!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

if __name__ == "__main__":
    main()
