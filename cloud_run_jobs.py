import json
from typing import Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession

from gcp_auth import ensure_google_application_credentials


def _resolve_project_id(explicit_project: Optional[str]) -> str:
    if explicit_project:
        return str(explicit_project).strip()
    _, project_id = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not project_id:
        raise RuntimeError("Could not resolve GCP project id (set OMEGA_CLOUD_PROJECT).")
    return project_id


def run_cloud_run_job(
    *,
    job_name: str,
    region: str,
    project: Optional[str],
    args: list[str],
) -> dict:
    """
    Triggers a Cloud Run Job execution via the Cloud Run v2 REST API.

    This avoids requiring `gcloud` on the local machine.
    """
    ensure_google_application_credentials()
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials)

    resolved_project = _resolve_project_id(project)
    job_name = str(job_name).strip()
    region = str(region).strip()
    if not job_name:
        raise ValueError("job_name is required")
    if not region:
        raise ValueError("region is required")

    url = f"https://run.googleapis.com/v2/projects/{resolved_project}/locations/{region}/jobs/{job_name}:run"
    body = {
        "overrides": {
            "containerOverrides": [
                {
                    "args": args,
                }
            ]
        }
    }

    resp = session.post(url, data=json.dumps(body), headers={"Content-Type": "application/json"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Cloud Run job execution failed ({resp.status_code}): {resp.text}")
    return resp.json()
