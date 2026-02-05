
from google.cloud import aiplatform
import config

project_id = config.OMEGA_CLOUD_PROJECT
location = config.GEMINI_LOCATION

print(f"Checking models in {project_id} / {location}...")

try:
    aiplatform.init(project=project_id, location=location)
    models = aiplatform.Model.list()
    # This lists custom models. We want foundation models.
    # Vertex AI SDK has a different way to list foundation models often involving just trying them or using Model Garden API.
    # But let's try the generative models.
    
    from vertexai.preview.generative_models import GenerativeModel
    import vertexai
    vertexai.init(project=project_id, location=location)

    models_to_test = []
    for name in [config.MODEL_TRANSLATOR, config.MODEL_EDITOR, config.MODEL_ASSISTANT]:
        if name and name not in models_to_test:
            models_to_test.append(name)

    for model_name in models_to_test:
        print(f"Testing {model_name}...")
        try:
            m = GenerativeModel(model_name)
            r = m.generate_content("Hi")
            if r and r.text:
                print(f"✅ {model_name} works!")
            else:
                print(f"⚠️ {model_name} response empty")
        except Exception as e:
            print(f"❌ {model_name} failed: {e}")


except Exception as e:
    print(f"Fatal error: {e}")
