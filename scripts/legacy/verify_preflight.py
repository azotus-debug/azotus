import os
import sys
import json
import logging
import importlib.util

# 1. Setup Logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def check_requirements():
    """Verify all imports required by the Cloud Worker resolve."""
    required_modules = [
        "google.cloud.storage",
        "vertexai",
        "anthropic",
        "tenacity",
        "pydantic"
    ]
    logger.info("📋 Step 1: Verifying Module Dependencies...")
    missing = []
    for mod in required_modules:
        try:
            __import__(mod)
            logger.info(f"   ✅ {mod} is installed")
        except ImportError:
            logger.error(f"   ❌ {mod} is MISSING")
            missing.append(mod)
    return not missing

def check_path_integrity():
    """Verify that the project structure is valid for the Cloud Worker."""
    logger.info("📋 Step 2: Verifying Path Integrity...")
    critical_files = [
        "omega_cloud_worker.py",
        "config.py",
        "providers/anthropic_claude.py",
        "cloud/requirements.txt",
        "Dockerfile"
    ]
    all_ok = True
    for f in critical_files:
        if os.path.exists(f):
            logger.info(f"   ✅ {f} exists")
        else:
            logger.error(f"   ❌ {f} IS MISSING")
            all_ok = False
            
    # Test internal import logic (Mimic what the worker does)
    try:
        sys.path.append(os.getcwd())
        from providers.anthropic_claude import is_claude_available, polish_with_claude
        logger.info("   ✅ Provider Import: providers.anthropic_claude imports successfully")
    except Exception as e:
        logger.error(f"   ❌ Provider Import FAILED: {e}")
        all_ok = False
        
    return all_ok

def check_logic_dry_run():
    """Perform a dry-run of the Claude polish logic with mock data."""
    logger.info("📋 Step 3: Performing Claude Logic Dry-Run...")
    
    import config
    from providers.anthropic_claude import polish_with_claude
    
    # Check for API Key
    if not config.ANTHROPIC_API_KEY:
        logger.warning("   ⚠️ ANTHROPIC_API_KEY not set in config.py")
        return False
        
    # Mock Data
    source_segments = [{"text": "Hello world", "start": 0, "end": 1}]
    draft_segments = [{"text": "Halló heimur", "start": 0, "end": 1}]
    
    try:
        logger.info(f"   📡 Calling Claude ({config.OMEGA_CLAUDE_MODEL}) with 1 segment...")
        result = polish_with_claude(
            source_segments=source_segments,
            draft_segments=draft_segments,
            target_language_code="is",
            target_language_name="Icelandic",
            bible_version="Biblían",
            god_address="formal",
            program_profile="standard",
            glossary={},
            max_fixes=1
        )
        
        logger.info(f"   ✅ Claude API Success!")
        logger.info(f"   📊 Result: {json.dumps(result, indent=2)}")
        return True
    except Exception as e:
        logger.error(f"   ❌ Claude Logic FAILED: {e}")
        return False

def check_dockerfile_sync():
    """Ensure the Dockerfile is in sync with the current project structure."""
    logger.info("📋 Step 4: Verifying Dockerfile Synchronization...")
    if not os.path.exists("Dockerfile"):
        return False
        
    with open("Dockerfile", "r") as f:
        content = f.read()
        
    required_copies = [
        "COPY providers/ /app/providers/",
        "COPY cloud/requirements.txt /app/requirements.txt",
        "COPY config.py /app/config.py"
    ]
    
    all_ok = True
    for line in required_copies:
        if line in content:
            logger.info(f"   ✅ Dockerfile contains: {line}")
        else:
            logger.error(f"   ❌ Dockerfile MISSING: {line}")
            all_ok = False
    return all_ok

def run_all_tests():
    print("\n🚀 Starting Professional Pre-Flight Verification Suite\n" + "="*50)
    
    results = [
        ("Dependencies", check_requirements()),
        ("Path Integrity", check_path_integrity()),
        ("Claude Dry-Run", check_logic_dry_run()),
        ("Docker Sync", check_dockerfile_sync())
    ]
    
    print("\n" + "="*50 + "\n🏁 FINAL REPORT\n")
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name:<20}: {status}")
        if not passed:
            all_passed = False
            
    if all_passed:
        print("\n🎉 ALL CHECKS PASSED. Ready for Deployment.")
        sys.exit(0)
    else:
        print("\n⚠️ SOME CHECKS FAILED. Do not deploy.")
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()
