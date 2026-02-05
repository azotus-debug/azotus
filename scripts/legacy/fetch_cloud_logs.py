
import logging
import google.auth
from google.auth.transport.requests import Request
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = "sermon-translator-system"
JOB_NAME = "omega-cloud-worker"
SA_PATH = "service_account.json"

def fetch_logs():
    logger.info("Fetching Cloud Run Logs...")

    # Load credentials with Logging scope
    credentials, project = google.auth.load_credentials_from_file(
        SA_PATH, 
        scopes=["https://www.googleapis.com/auth/logging.read", "https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())

    import requests
    
    # Calculate timestamp for last 15 minutes
    start_time = (datetime.utcnow() - timedelta(minutes=15)).isoformat() + "Z"

    url = "https://logging.googleapis.com/v2/entries:list"
    headers = {"Authorization": f"Bearer {credentials.token}"}
    
    # Filter for our Cloud Run job
    filter_str = f"""
    resource.type="cloud_run_job"
    resource.labels.job_name="{JOB_NAME}"
    timestamp >= "{start_time}"
    """
    
    payload = {
        "projectIds": [PROJECT_ID],
        "filter": filter_str,
        "orderBy": "timestamp desc",
        "pageSize": 20
    }
    
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.error(f"Failed to fetch logs: {resp.text}")
        return

    entries = resp.json().get("entries", [])
    if not entries:
        logger.info("No log entries found.")
        return

    print("-" * 60)
    for entry in reversed(entries): # Show oldest first
        ts = entry.get("timestamp")
        payload = entry.get("textPayload") or entry.get("jsonPayload")
        severity = entry.get("severity", "INFO")
        print(f"[{ts}] [{severity}] {payload}")
    print("-" * 60)

if __name__ == "__main__":
    fetch_logs()
