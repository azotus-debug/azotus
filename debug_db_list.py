import sys
import os
import json
import sqlite3

# Direct sqlite access to avoid module issues
try:
    conn = sqlite3.connect('omega.db') # Try root first
    cursor = conn.cursor()
    
    print("--- RECENT TRACKS ---")
    # Join with programs to get title/original_filename
    query = """
    SELECT t.job_id, t.stage, t.status, t.updated_at, p.title 
    FROM tracks t 
    JOIN programs p ON t.program_id = p.id 
    ORDER BY t.created_at DESC 
    LIMIT 10
    """
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        for row in rows:
            print(f"Job: {row[0]} | Stage: {row[1]} | Status: {row[2]} | Title: {row[4]}")
    except Exception as e:
        print(f"Query 1 failed: {e}")
        # Try finding the other db path if this one is empty/wrong
        pass
        
    conn.close()

except Exception as e:
    print(f"Root DB failed: {e}")

# Try the other common location if root failed or returned nothing
# Based on previous find_by_name: cloud/review_portal/production.db is unlikely.
# But omega_manager usually creates omega.db in CWD.
