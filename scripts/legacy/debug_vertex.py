
import os
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import vertexai
from vertexai.generative_models import GenerativeModel, HarmCategory, HarmBlockThreshold, SafetySetting

def debug_vertex():
    print("🧠 Debugging Vertex AI...")
    
    # Force global if not set
    config.GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global")
    
    print(f"Project: {config.OMEGA_CLOUD_PROJECT}")
    print(f"Location: {config.GEMINI_LOCATION}")
    print(f"Model: {config.MODEL_TRANSLATOR}")
    
    try:
        vertexai.init(project=config.OMEGA_CLOUD_PROJECT, location=config.GEMINI_LOCATION)
        model = GenerativeModel(config.MODEL_TRANSLATOR)
        
        print("\nSending request...")
        # Use simple prompt, disable safety filters to see if that's the blocker
        safety_settings = [
            SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
            SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
            SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
            SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
        ]

        response = model.generate_content(
            "Say 'OK' if you can hear me.", 
            generation_config={"max_output_tokens": 100},
            safety_settings=safety_settings
        )
        
        print("\n--- Response Object ---")
        print(response)
        
        if response.text:
            print(f"\n✅ Success! Text: {response.text}")
        else:
            print("\n❌ No text in response.")
            
    except Exception as e:
        print(f"\n❌ Exception: {e}")

if __name__ == "__main__":
    debug_vertex()
