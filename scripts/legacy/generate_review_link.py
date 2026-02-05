#!/usr/bin/env python3
"""
Generate Magic Review Link
==========================
Creates a secure review link that allows external reviewers to access
the Omega Review Portal for a specific job.

Usage:
    python scripts/generate_review_link.py <job_id> [--hours 72]
    
Example:
    python scripts/generate_review_link.py cbnjd011326cc_nl-20260114T183000Z
"""

import sys
import os
import hashlib
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Configuration
SECRET_KEY = os.environ.get("OMEGA_REVIEW_SECRET", "omega-review-secret-2024")
DEFAULT_HOURS = 72

# Cloud Run URL (update after deployment)
CLOUD_RUN_URL = os.environ.get(
    "OMEGA_REVIEW_PORTAL_URL", 
    "https://omega-review-y5r67tzxwa-ew.a.run.app"  # europe-west1 example
)


def generate_token(job_id: str, expiry_hours: int = DEFAULT_HOURS) -> tuple[str, int]:
    """Generate a secure review token for a job."""
    expiry_ts = int(time.time()) + (expiry_hours * 3600)
    payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    token = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return token, expiry_ts


def generate_review_link(job_id: str, hours: int = DEFAULT_HOURS, base_url: str = None) -> str:
    """Generate a full review URL with token."""
    if base_url is None:
        base_url = CLOUD_RUN_URL
    
    token, expiry_ts = generate_token(job_id, hours)
    
    return f"{base_url}/review/{job_id}?token={token}&exp={expiry_ts}"


def verify_token(job_id: str, token: str, expiry_ts: int) -> bool:
    """Verify a review token is valid and not expired."""
    if time.time() > expiry_ts:
        return False
    expected_payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    expected_token = hashlib.sha256(expected_payload.encode()).hexdigest()[:32]
    return token == expected_token


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ Error: job_id required")
        print("\nAvailable jobs (run to update list):")
        print("  python scripts/generate_review_link.py --list")
        sys.exit(1)
    
    job_id = sys.argv[1]
    
    # Handle --list option
    if job_id == "--list":
        print("📋 Listing recent jobs from database...")
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            import omega_db
            tracks = omega_db.get_active_tracks(limit=10)
            if tracks:
                for t in tracks:
                    print(f"  {t.get('job_id', 'N/A'):<40} {t.get('stage', 'N/A'):<15} {t.get('language_code', 'N/A')}")
            else:
                print("  No active tracks found")
        except Exception as e:
            print(f"  Error: {e}")
        sys.exit(0)
    
    # Parse options
    hours = DEFAULT_HOURS
    if "--hours" in sys.argv:
        idx = sys.argv.index("--hours")
        if idx + 1 < len(sys.argv):
            hours = int(sys.argv[idx + 1])
    
    # Generate the link
    print(f"\n🔑 Generating review link for: {job_id}")
    print(f"   Secret prefix: {SECRET_KEY[:8]}...")
    print(f"   Expiry: {hours} hours")
    
    url = generate_review_link(job_id, hours)
    token, expiry_ts = generate_token(job_id, hours)
    
    print(f"\n✅ Review Link Generated:")
    print(f"\n   {url}")
    
    # Verify it works
    print(f"\n🔐 Verification:")
    is_valid = verify_token(job_id, token, expiry_ts)
    print(f"   Token valid: {is_valid}")
    print(f"   Expires: {time.ctime(expiry_ts)}")
    
    # Test local portal
    local_url = generate_review_link(job_id, hours, "http://localhost:5002")
    print(f"\n🏠 Local test URL:")
    print(f"   {local_url}")
    
    print(f"\n📋 To test locally:")
    print(f"   cd cloud/review_portal && python main.py")
    print(f"   Then open the local URL above")
    

if __name__ == "__main__":
    main()
