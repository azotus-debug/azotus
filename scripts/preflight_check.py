#!/usr/bin/env python3
"""
Omega Pre-Flight Check - Comprehensive System Validation
==========================================================
Validates ALL critical dependencies before starting the manager.

Run manually: python3 preflight_check.py
Integrated into: start_omega.sh

Exit Codes:
  0 = All checks passed
  1 = Critical failure (blocks startup)
  2 = Warning (non-critical, startup continues)

This is the SINGLE SOURCE OF TRUTH for system validation.
"""

import os
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import config

# Try to load GCP credentials
try:
    from gcp_auth import ensure_google_application_credentials
    ensure_google_application_credentials()
except ImportError:
    pass

# Results tracking
RESULTS = {"passed": [], "warnings": [], "failed": []}
DETAILS = {}  # Detailed info for JSON output


def log_pass(name: str, detail: str = ""):
    RESULTS["passed"].append(name)
    DETAILS[name] = {"status": "pass", "detail": detail}
    print(f"  \u2705 {name}" + (f": {detail}" if detail else ""))


def log_warn(name: str, detail: str = ""):
    RESULTS["warnings"].append(name)
    DETAILS[name] = {"status": "warn", "detail": detail}
    print(f"  \u26a0\ufe0f  {name}: {detail}")


def log_fail(name: str, detail: str = ""):
    RESULTS["failed"].append(name)
    DETAILS[name] = {"status": "fail", "detail": detail}
    print(f"  \u274c {name}: {detail}")


# ===========================================================================
# CHECK: Database Connectivity (PostgreSQL Required)
# ===========================================================================
def check_database():
    print("\n\U0001f5c4\ufe0f  Checking Database...")

    db_type = os.getenv("DB_TYPE", "postgres").lower()

    if db_type != "postgres":
        log_fail("Database Type", f"DB_TYPE={db_type} is not supported. Set DB_TYPE=postgres in .env")
        return False

    if db_type == "postgres":
        try:
            import omega_db
            conn = omega_db._connect()
            c = conn.cursor()

            # Test basic query
            c.execute("SELECT 1")
            result = c.fetchone()

            if result:
                log_pass("PostgreSQL Connection", "connected successfully")
            else:
                log_fail("PostgreSQL Connection", "query returned no results")
                return False

            # Check critical tables exist
            required_tables = ["jobs", "tracks", "programs", "master_scripts", "cloud_sync_state"]
            c.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
            """)
            existing_tables = {row[0] if isinstance(row, tuple) else row.get('table_name', row)
                             for row in c.fetchall()}

            missing_tables = [t for t in required_tables if t not in existing_tables]
            if missing_tables:
                log_warn("PostgreSQL Tables", f"missing: {', '.join(missing_tables)}")
            else:
                log_pass("PostgreSQL Tables", f"all {len(required_tables)} critical tables exist")

            conn.close()
            return True

        except ImportError as e:
            log_fail("PostgreSQL", f"missing dependency: {e}")
            return False
        except Exception as e:
            log_fail("PostgreSQL", str(e)[:100])
            return False

    return True  # PostgreSQL check passed


# ===========================================================================
# CHECK: File System Paths & Symlinks
# ===========================================================================
def check_paths():
    print("\n\U0001f4c1 Checking File System...")

    critical_paths = [
        ("INBOX", config.INBOX_DIR),
        ("VAULT", config.VAULT_DIR),
        ("DELIVERY", config.DELIVERY_DIR),
        ("VIDEO_DIR", config.VIDEO_DIR),
        ("SRT_DIR", config.SRT_DIR),
        ("VAULT_DATA", config.VAULT_DATA),
        ("VAULT_VIDEOS", config.VAULT_VIDEOS),
    ]

    all_ok = True
    for name, path in critical_paths:
        # Check if it's a symlink
        if path.is_symlink():
            target = path.resolve()
            if target.exists():
                # Check writability
                test_file = target / ".preflight_test"
                try:
                    test_file.touch()
                    test_file.unlink()
                    log_pass(f"{name} (symlink)", f"-> {target} (writable)")
                except Exception as e:
                    log_fail(f"{name} (symlink)", f"not writable: {e}")
                    all_ok = False
            else:
                log_fail(f"{name} (symlink)", f"dangling symlink -> {target}")
                all_ok = False
        elif path.exists():
            # Check writability
            test_file = path / ".preflight_test"
            try:
                test_file.touch()
                test_file.unlink()
                log_pass(name, "exists & writable")
            except Exception as e:
                log_fail(name, f"not writable: {e}")
                all_ok = False
        else:
            log_fail(name, "does not exist")
            all_ok = False

    return all_ok


# ===========================================================================
# CHECK: FFmpeg Capabilities
# ===========================================================================
def check_ffmpeg():
    print("\n\U0001f3ac Checking FFmpeg...")
    ffmpeg_bin = getattr(config, "FFMPEG_BIN", "ffmpeg")

    # Check if ffmpeg exists
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version_line = result.stdout.split("\n")[0]
            log_pass("FFmpeg", version_line[:60])
        else:
            log_fail("FFmpeg", "command failed")
            return False
    except FileNotFoundError:
        log_fail("FFmpeg", f"not found at {ffmpeg_bin}")
        return False
    except Exception as e:
        log_fail("FFmpeg", str(e))
        return False

    # Check for hardware encoders
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-encoders"],
            capture_output=True, text=True, timeout=10
        )
        encoders = result.stdout

        if "hevc_videotoolbox" in encoders:
            log_pass("HEVC Encoder", "hevc_videotoolbox available (GPU)")
        elif "libx265" in encoders:
            log_warn("HEVC Encoder", "using libx265 (CPU, slower)")
        else:
            log_warn("HEVC Encoder", "no HEVC encoder found")

        if "h264_videotoolbox" in encoders:
            log_pass("H.264 Encoder", "h264_videotoolbox available (GPU)")
        elif "libx264" in encoders:
            log_pass("H.264 Encoder", "libx264 available (CPU)")
        else:
            log_warn("H.264 Encoder", "no H.264 encoder found")

    except Exception:
        log_warn("Encoder Check", "could not verify encoders")

    return True


# ===========================================================================
# CHECK: Google Cloud Storage
# ===========================================================================
def check_gcs():
    print("\n\u2601\ufe0f  Checking Google Cloud Storage...")
    bucket_name = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")

    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        # Try to list a single blob (lightweight check)
        blobs = list(bucket.list_blobs(max_results=1, prefix="jobs/"))
        log_pass("GCS Connection", f"bucket '{bucket_name}' accessible")

        # Check if we can write (optional)
        try:
            test_blob = bucket.blob("preflight_test.txt")
            test_blob.upload_from_string("preflight test")
            test_blob.delete()
            log_pass("GCS Write", "write/delete permissions verified")
        except Exception as e:
            log_warn("GCS Write", f"write test failed: {str(e)[:50]}")

        return True

    except ImportError:
        log_fail("GCS", "google-cloud-storage not installed")
        return False
    except Exception as e:
        log_fail("GCS", str(e)[:100])
        return False


# ===========================================================================
# CHECK: Cloud Run Job Configuration
# ===========================================================================
def check_cloud_run():
    print("\n\U0001f680 Checking Cloud Run Job...")

    job_name = os.environ.get("OMEGA_CLOUD_RUN_JOB", "omega-cloud-worker")
    region = os.environ.get("OMEGA_CLOUD_RUN_REGION", "us-central1")
    project = os.environ.get("OMEGA_CLOUD_PROJECT", "sermon-translator-system")

    if not job_name:
        log_warn("Cloud Run Job", "OMEGA_CLOUD_RUN_JOB not set; cloud translation disabled")
        return True

    try:
        # Use gcloud to check job exists
        result = subprocess.run(
            ["gcloud", "run", "jobs", "describe", job_name,
             "--region", region, "--project", project,
             "--format", "json"],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            log_fail("Cloud Run Job", f"job '{job_name}' not found in {region}")
            return False

        job_config = json.loads(result.stdout)
        log_pass("Cloud Run Job", f"{job_name} exists in {region}")

        # Check for OMEGA_CLOUD_PIPELINE=1 in environment
        env_vars = {}
        try:
            containers = job_config.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            if containers:
                for env in containers[0].get("env", []):
                    env_vars[env.get("name")] = env.get("value")
        except Exception:
            pass

        if env_vars.get("OMEGA_CLOUD_PIPELINE") == "1":
            log_pass("Cloud Pipeline Flag", "OMEGA_CLOUD_PIPELINE=1 is set")
        else:
            log_fail("Cloud Pipeline Flag", "OMEGA_CLOUD_PIPELINE=1 NOT set in Cloud Run (using legacy runner!)")
            print("         Fix with: gcloud run jobs update omega-cloud-worker --region=us-central1 --update-env-vars='OMEGA_CLOUD_PIPELINE=1'")
            return False

        # Check required env vars
        required_vars = ["GOOGLE_CLOUD_PROJECT", "OMEGA_JOBS_BUCKET"]
        missing_vars = [v for v in required_vars if not env_vars.get(v)]
        if missing_vars:
            log_warn("Cloud Run Env", f"missing vars: {', '.join(missing_vars)}")

        # Check memory/CPU
        try:
            resources = containers[0].get("resources", {}).get("limits", {})
            memory = resources.get("memory", "unknown")
            cpu = resources.get("cpu", "unknown")
            log_pass("Cloud Run Resources", f"memory={memory}, cpu={cpu}")
        except Exception:
            pass

        return True

    except FileNotFoundError:
        log_warn("Cloud Run Check", "gcloud CLI not found; skipping Cloud Run verification")
        return True
    except subprocess.TimeoutExpired:
        log_warn("Cloud Run Check", "gcloud command timed out")
        return True
    except Exception as e:
        log_fail("Cloud Run Check", str(e)[:100])
        return False


# ===========================================================================
# CHECK: ElevenLabs API
# ===========================================================================
def check_elevenlabs():
    print("\n\U0001f3a4 Checking ElevenLabs Scribe...")

    transcriber = os.environ.get("OMEGA_TRANSCRIBER", "elevenlabs").strip().lower()
    if transcriber != "elevenlabs":
        log_fail("Transcriber", f"OMEGA_TRANSCRIBER={transcriber} is not allowed (ElevenLabs only)")
        return False

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        log_fail("ElevenLabs", "ELEVENLABS_API_KEY not set; transcription will fail")
        return False

    # Validate API key with a simple API call
    try:
        import requests
        resp = requests.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": api_key},
            timeout=10
        )
        if resp.status_code == 200:
            user_data = resp.json()
            subscription = user_data.get("subscription", {}).get("tier", "unknown")
            log_pass("ElevenLabs API", f"authenticated (tier: {subscription})")
            return True
        elif resp.status_code == 401:
            log_fail("ElevenLabs API", "invalid API key (401 Unauthorized)")
            return False
        else:
            log_warn("ElevenLabs API", f"key configured but validation returned {resp.status_code}")
            return True
    except ImportError:
        log_pass("ElevenLabs", f"API key configured (ends ...{api_key[-4:]})")
        return True
    except Exception as e:
        log_warn("ElevenLabs API", f"validation failed: {str(e)[:50]}")
        return True


# ===========================================================================
# CHECK: Vertex AI (Gemini)
# ===========================================================================
def check_vertex():
    print("\n\U0001f9e0 Checking Vertex AI (Gemini)...")

    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel

        project = config.OMEGA_CLOUD_PROJECT
        location = config.GEMINI_LOCATION

        vertexai.init(project=project, location=location)
        model = GenerativeModel(config.MODEL_TRANSLATOR)

        # Quick test call
        response = model.generate_content(
            "Say 'OK' if you can hear me.",
            generation_config={"max_output_tokens": 10}
        )
        if response and response.text:
            log_pass("Vertex AI", f"Gemini responding (project={project}, location={location})")
            return True
        else:
            log_fail("Vertex AI", "No response from model")
            return False

    except ImportError:
        log_fail("Vertex AI", "vertexai SDK not installed")
        return False
    except Exception as e:
        error_msg = str(e)[:100]
        if "403" in error_msg or "permission" in error_msg.lower():
            log_fail("Vertex AI", f"permission denied: {error_msg}")
        else:
            log_fail("Vertex AI", error_msg)
        return False


# ===========================================================================
# CHECK: Service Account Permissions
# ===========================================================================
def check_service_account():
    print("\n\U0001f510 Checking Service Account...")

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")

    if not creds_path:
        # Try to find service account file
        possible_paths = [
            Path(__file__).parent / "service_account.json",
            Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
        ]
        for p in possible_paths:
            if p.exists():
                creds_path = str(p)
                break

    if not creds_path:
        log_warn("Service Account", "GOOGLE_APPLICATION_CREDENTIALS not set")
        return True

    creds_file = Path(creds_path)
    if not creds_file.exists():
        log_fail("Service Account", f"file not found: {creds_path}")
        return False

    try:
        with open(creds_file) as f:
            creds = json.load(f)

        sa_email = creds.get("client_email", "unknown")
        project = creds.get("project_id", "unknown")
        log_pass("Service Account", f"{sa_email} (project: {project})")
        return True

    except Exception as e:
        log_fail("Service Account", f"invalid JSON: {e}")
        return False


# ===========================================================================
# CHECK: Disk Space
# ===========================================================================
def check_disk_space():
    print("\n\U0001f4be Checking Disk Space...")

    try:
        import shutil

        # Check delivery directory
        target = config.DELIVERY_DIR.resolve() if config.DELIVERY_DIR.is_symlink() else config.DELIVERY_DIR
        if target.exists():
            stat = shutil.disk_usage(str(target))
            available_gb = stat.free / (1024**3)
            total_gb = stat.total / (1024**3)

            if available_gb < 20:
                log_fail("Disk Space", f"{available_gb:.1f}GB free (need at least 20GB)")
                return False
            elif available_gb < 50:
                log_warn("Disk Space", f"{available_gb:.1f}GB free (recommend 50GB+)")
            else:
                log_pass("Disk Space", f"{available_gb:.1f}GB free of {total_gb:.0f}GB")
            return True
        else:
            log_warn("Disk Space", "could not check (DELIVERY_DIR not accessible)")
            return True

    except Exception as e:
        log_warn("Disk Space", f"check failed: {e}")
        return True


# ===========================================================================
# CHECK: Environment Variables Summary
# ===========================================================================
def check_env_summary():
    print("\n\U0001f4cb Environment Summary...")

    critical_vars = {
        "DB_TYPE": os.getenv("DB_TYPE", "postgres"),
        "OMEGA_TRANSCRIBER": os.getenv("OMEGA_TRANSCRIBER", "elevenlabs"),
        "OMEGA_CLOUD_RUN_JOB": os.getenv("OMEGA_CLOUD_RUN_JOB", "(not set)"),
        "OMEGA_CLOUD_PROJECT": os.getenv("OMEGA_CLOUD_PROJECT", "sermon-translator-system"),
        "OMEGA_JOBS_BUCKET": os.getenv("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project"),
    }

    for var, value in critical_vars.items():
        if value and value != "(not set)":
            print(f"  \u2022 {var}={value}")
        else:
            print(f"  \u2022 {var}=(not set)")

    return True


# ===========================================================================
# MAIN
# ===========================================================================
def run_preflight(json_output: bool = False):
    print("=" * 60)
    print("\U0001f680 OMEGA PRE-FLIGHT CHECK")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Run all checks
    checks = [
        ("Database", check_database),
        ("File System", check_paths),
        ("FFmpeg", check_ffmpeg),
        ("GCS", check_gcs),
        ("Cloud Run", check_cloud_run),
        ("ElevenLabs", check_elevenlabs),
        ("Vertex AI", check_vertex),
        ("Service Account", check_service_account),
        ("Disk Space", check_disk_space),
        ("Environment", check_env_summary),
    ]

    for name, check_fn in checks:
        try:
            check_fn()
        except Exception as e:
            log_fail(name, f"Unexpected error: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("\U0001f4ca SUMMARY")
    print("=" * 60)
    print(f"  \u2705 Passed:   {len(RESULTS['passed'])}")
    print(f"  \u26a0\ufe0f  Warnings: {len(RESULTS['warnings'])}")
    print(f"  \u274c Failed:   {len(RESULTS['failed'])}")

    # JSON output for programmatic use
    if json_output:
        output = {
            "timestamp": datetime.now().isoformat(),
            "passed": len(RESULTS["passed"]),
            "warnings": len(RESULTS["warnings"]),
            "failed": len(RESULTS["failed"]),
            "details": DETAILS,
            "status": "fail" if RESULTS["failed"] else ("warn" if RESULTS["warnings"] else "pass")
        }
        print("\n" + json.dumps(output, indent=2))

    if RESULTS["failed"]:
        print("\n\U0001f6d1 CRITICAL FAILURES - System will not work reliably:")
        for f in RESULTS["failed"]:
            print(f"     - {f}")
        print("\nFix the above issues and re-run preflight.")
        return 1

    if RESULTS["warnings"]:
        print("\n\u26a0\ufe0f  Warnings (non-critical):")
        for w in RESULTS["warnings"]:
            print(f"     - {w}")
        print("\n\u2705 Pre-flight complete with warnings. System can start.")
        return 2

    print("\n\u2705 Pre-flight complete. All systems GO!")
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Omega Pre-Flight Check")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--quick", action="store_true", help="Skip slow checks (Vertex AI test call)")
    args = parser.parse_args()

    exit_code = run_preflight(json_output=args.json)
    sys.exit(exit_code)
