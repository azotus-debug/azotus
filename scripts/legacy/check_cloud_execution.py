
import os
import logging
import google.auth
from google.auth.transport.requests import Request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = "sermon-translator-system"
REGION = "us-central1"
JOB_NAME = "omega-cloud-worker"
SA_PATH = "service_account.json"

def check_execution():
    logger.info("Checking Cloud Run Executions...")

    if not os.path.exists(SA_PATH):
        logger.error("Service account file not found.")
        return

    credentials, project = google.auth.load_credentials_from_file(SA_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())

    import requests
    headers = {"Authorization": f"Bearer {credentials.token}"}
    
    # List executions (sorted by createTime desc)
    url = f"https://run.googleapis.com/v2/projects/{PROJECT_ID}/locations/{REGION}/jobs/{JOB_NAME}/executions"
    
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        logger.error(f"Failed to list executions: {resp.text}")
        return

    executions = resp.json().get("executions", [])
    if not executions:
        logger.info("No executions found.")
        return

    # Get latest
    latest = executions[0]
    name = latest['name']
    create_time = latest['createTime']
    status = "UNKNOWN"
    
    # Check conditions
    conditions = latest.get("terminalCondition", {})
    state = conditions.get("state", "RUNNING") # CONDITION_SUCCEEDED, CONDITION_FAILED
    
    logger.info(f"Latest Execution: {name}")
    logger.info(f"Created: {create_time}")
    logger.info(f"State: {state}")
    
    # If running, check task count status
    if state not in ["CONDITION_SUCCEEDED", "CONDITION_FAILED"]:
         logger.info("Status: STILL RUNNING")
    
    if state == "CONDITION_FAILED":
        logger.error(f"Execution Failed: {conditions.get('message', 'No message')}")

if __name__ == "__main__":
    check_execution()
