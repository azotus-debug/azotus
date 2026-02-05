import sys
import os

print("--- OMEGA SYSTEM AUDIT ---")
print("1. Checking Python Path...")
print(sys.path)

print("\n2. Checking Imports...")
modules = [
    "config",
    "omega_db",
    "omega_manager",
    "dashboard",
    "workers.transcriber",
    "workers.translator",
    "workers.editor",
    "workers.finalizer",
    "workers.publisher",
    "workers.dubber",
    "workers.forker",
    "providers.openai_tts",
    "profiles",
    "email_utils",
    "gcp_auth",
    "cloud_run_jobs",
    "gcs_jobs"
]

failed = []
for m in modules:
    try:
        __import__(m)
        print(f"✅ {m}")
    except Exception as e:
        print(f"❌ {m}: {e}")
        failed.append(m)

print("\n3. Checking Directory Structure...")
import config
required_dirs = [
    config.BASE_DIR / "1_INBOX",
    config.BASE_DIR / "2_VAULT",
    config.BASE_DIR / "3_EDITOR",
    config.BASE_DIR / "4_DELIVERY",
    config.BASE_DIR / "jobs", # New jobs dir
]
for d in required_dirs:
    if d.exists():
        print(f"✅ Dir: {d.name}")
    else:
        print(f"❌ Dir Missing: {d.name} ({d})")

if failed:
    print(f"\nCRITICAL: {len(failed)} Checks Failed!")
    sys.exit(1)
else:
    print("\nALL SYSTEMS GO.")
    sys.exit(0)
