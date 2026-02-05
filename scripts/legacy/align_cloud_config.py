
import os
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Constants
PROJECT_ID = "sermon-translator-system"
REGION = "us-central1"
JOB_NAME = "omega-cloud-worker"
SA_PATH = "service_account.json"
GCLOUD_CONFIG_DIR = os.environ.get("CLOUDSDK_CONFIG", "/tmp/gcloud-config")

# Variables to REMOVE to ensure parity with local config
VARS_TO_REMOVE = {
    "OMEGA_CLAUDE_POLISH",
    "OMEGA_CLAUDE_MODEL",
    "ANTHROPIC_API_KEY",
}

def run(cmd: list[str], env: dict) -> bool:
    try:
        subprocess.run(cmd, check=True, env=env)
        return True
    except subprocess.CalledProcessError as exc:
        logger.error(f"Command failed: {' '.join(cmd)}")
        logger.error(str(exc))
        return False

def main():
    logger.info("Starting Cloud Run Alignment (gcloud)...")

    if not os.path.exists(SA_PATH):
        logger.error(f"Service account file not found at {SA_PATH}")
        return

    env = os.environ.copy()
    env["CLOUDSDK_CONFIG"] = GCLOUD_CONFIG_DIR

    auth_cmd = [
        "gcloud", "auth", "activate-service-account",
        "--key-file", SA_PATH,
        "--project", PROJECT_ID,
    ]
    update_cmd = [
        "gcloud", "run", "jobs", "update", JOB_NAME,
        "--region", REGION,
        "--remove-env-vars", ",".join(sorted(VARS_TO_REMOVE)),
    ]

    logger.info(f"Using CLOUDSDK_CONFIG={GCLOUD_CONFIG_DIR}")
    if not run(auth_cmd, env):
        return
    run(update_cmd, env)

if __name__ == "__main__":
    main()
