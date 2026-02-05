# Codex Verification Guide (Azotus)

## Quick Verification (Run This)

```bash
cd /Users/haukurhauksson/Azotus && python3 - <<'PY'
import os, py_compile, hashlib
cache_root = "/tmp/azotus_pycs"
os.makedirs(cache_root, exist_ok=True)
errors = []
for dirpath, dirnames, filenames in os.walk("."):
    dirnames[:] = [d for d in dirnames if d not in {"omega-frontend", ".git", "node_modules", "__pycache__", ".pycache"}]
    for name in filenames:
        if not name.endswith(".py"):
            continue
        path = os.path.join(dirpath, name)
        try:
            digest = hashlib.sha1(path.encode("utf-8")).hexdigest()
            cfile = os.path.join(cache_root, f"{digest}.pyc")
            py_compile.compile(path, cfile=cfile, doraise=True)
        except Exception as e:
            errors.append((path, e))
if errors:
    print("PY_COMPILE_ERRORS")
    for path, e in errors:
        print(f"{path}: {e}")
else:
    print("PY_COMPILE_OK")
PY
```

Frontend:
```bash
cd /Users/haukurhauksson/Azotus/omega-frontend && npm run lint && npm run build
```

## Pipeline Integrity Checks

- **Proxy generation** happens in `omega_manager._run_multimodal_pipeline()` and is uploaded to `gs://<bucket>/<prefix>/<job_id>/proxy_360p.mp4`.
- **Vertex/Gemini uses the proxy** in `omega_cloud_worker.GeminiTranslationPipeline._create_context_cache()`.
- **Cloud pipeline** is triggered in `omega_manager._run_translate_cloud()`.

## Station Safety Checks

- Station identity config in `config.py` (`OMEGA_STATION_ID`, `OMEGA_UI_SCOPE`).
- Job filtering in `omega_manager.process_jobs()` and `cloud_sync_service._sync_job()`.
- API filtering in `dashboard.py` (programs/tracks/deliveries).

## DB Enforcement

- `omega_db.py` requires `DB_TYPE=postgres` and raises otherwise.

## Expected Warnings (Non‑blocking)

Next.js may warn about `img` tags and `useEffect` dependency lists. These do not block build but should be cleaned up later.
