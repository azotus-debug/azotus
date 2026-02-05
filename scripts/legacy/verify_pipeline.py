import os
import sys
import argparse
import logging
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OmegaVerifier")

def check_anthropic():
    """Verifies Anthropic API access and Model ID."""
    print("\n🔎 Verifying Anthropic (Claude)...")
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("❌ ANTHROPIC_API_KEY is missing!")
            return False
            
        client = anthropic.Anthropic(api_key=api_key)
        model = config.OMEGA_CLAUDE_MODEL
        print(f"   Target Model: {model}")
        
        client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Ping"}]
        )
        print("✅ Anthropic API Configured Correctly")
        return True
    except Exception as e:
        print(f"❌ Anthropic Check Failed: {e}")
        return False

def check_gemini():
    """Verifies Gemini API access."""
    print("\n🔎 Verifying Google Vertex AI (Gemini)...")
    try:
        # Check if gcp_auth exists
        if os.path.exists("gcp_auth.py"):
            import gcp_auth
            gcp_auth.ensure_google_application_credentials()
            
        import vertexai
        from vertexai.generative_models import GenerativeModel
        
        project_id = config.OMEGA_CLOUD_PROJECT
        location = config.GEMINI_LOCATION
        
        vertexai.init(project=project_id, location=location)
        model = GenerativeModel(config.MODEL_TRANSLATOR)
        response = model.generate_content("Say hi")
        if response.text:
            print("✅ Gemini API Configured Correctly")
            return True
        else:
            print("⚠️ Gemini response empty")
            return False
    except Exception as e:
        print(f"❌ Gemini Check Failed: {e}")
        return False

def check_gcs_access():
    """Verifies GCS Bucket Access."""
    print("\n🔎 Verifying GCS Access...")
    try:
        # Check if gcp_auth exists
        if os.path.exists("gcp_auth.py"):
            try:
                import gcp_auth
                gcp_auth.ensure_google_application_credentials()
            except ImportError:
                pass

        from google.cloud import storage
        client = storage.Client()
        bucket_name = config.OMEGA_JOBS_BUCKET
        bucket = client.get_bucket(bucket_name)
        print(f"✅ Access to bucket '{bucket_name}' confirmed")
        return True
    except Exception as e:
        print(f"❌ GCS Check Failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Omega Pipeline Verification Tool")
    parser.add_argument("--component", choices=["all", "anthropic", "gemini", "gcs"], default="all")
    args = parser.parse_args()
    
    print("🚀 Starting Omega Pipeline Verification...")
    
    success = True
    if args.component in ["all", "anthropic"]:
        if not check_anthropic(): success = False
    if args.component in ["all", "gemini"]:
        if not check_gemini(): success = False
    if args.component in ["all", "gcs"]:
        if not check_gcs_access(): success = False
        
    if success:
        print("\n✨ All Checks Passed.")
        sys.exit(0)
    else:
        print("\n⚠️ Some checks failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
