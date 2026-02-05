
import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Force Gemini Location to global (as it is in Cloud Run)
os.environ["GEMINI_LOCATION"] = "global"

# Import the worker
import omega_cloud_worker

if __name__ == "__main__":
    job_id = "new_system-20260204T174454520018Z"
    print(f"🚀 Reproducing Cloud Run execution for Job ID: {job_id}")
    print("---------------------------------------------------------------")
    
    # Simulate command line arguments
    sys.argv = ["omega_cloud_worker.py", "--job-id", job_id]
    
    try:
        omega_cloud_worker.main()
    except SystemExit as e:
        print(f"Exit code: {e.code}")
    except Exception as e:
        print(f"CRITICAL FAILURE: {e}")
        import traceback
        traceback.print_exc()
