import os
from pathlib import Path

import config


def ensure_google_application_credentials() -> bool:
    """
    Best-effort helper for local development.

    In Cloud Run, prefer Workload Identity / attached service accounts instead of
    mounting JSON keys.
    """
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return True

    default_path = Path(config.BASE_DIR) / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True

    return False

