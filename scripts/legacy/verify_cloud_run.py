
import os
import json
import logging
from pathlib import Path
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request as AuthRequest

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Constants (from config.py / config/defaults.py analysis)
PROJECT_ID = "sermon-translator-system"
REGION = "us-central1"
JOB_NAME = "omega-cloud-worker"
SA_PATH = "service_account.json"

def get_access_token():
    """Get access token from service_account.json"""
    if not os.path.exists(SA_PATH):
        logger.error(f"Service account file not found at {SA_PATH}")
        return None
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SA_PATH,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(AuthRequest())
        return credentials.token
    except Exception as e:
        logger.error(f"Failed to get access token: {e}")
        return None

def fetch_job_details(token):
    """Fetch Cloud Run Job details via REST API"""
    url = f"https://run.googleapis.com/v2/projects/{PROJECT_ID}/locations/{REGION}/jobs/{JOB_NAME}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"API Request Failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Request Error: {e}")
        return None

def main():
    logger.info("Starting Cloud Run Config Verification...")
    
    token = get_access_token()
    if not token:
        logger.error("Could not obtain access token. Aborting.")
        return

    job_data = fetch_job_details(token)
    if job_data:
        logger.info("Successfully fetched job details.")
        print(json.dumps(job_data, indent=2))
        
        # Basic validation
        containers = job_data.get('template', {}).get('template', {}).get('containers', [])
        if containers:
            env_vars = containers[0].get('env', [])
            logger.info("\n--- Environment Variables ---")
            for var in env_vars:
                logger.info(f"{var.get('name')}: {var.get('value')}")
    else:
        logger.error("Failed to fetch job data.")

if __name__ == "__main__":
    main()
