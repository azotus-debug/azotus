# Claude Code Handoff: Flask → FastAPI Route Migration

## Mission

Port all `/api/v2/*` routes from `dashboard.py` (Flask) into FastAPI router files under a new `routers/` directory. The existing `api_main.py` and `models.py` are already in place. Your job is purely mechanical: take each Flask route, rewrite it as an async FastAPI endpoint with Pydantic models and SQLAlchemy async sessions.

**Do NOT touch `omega_db.py`, `omega_manager.py`, or any worker scripts.** Those are consumed by background processes and stay as-is.

---

## Project Layout

```
/Users/haukurhauksson/Azotus/
├── api_main.py           ← FastAPI entry point (mount your routers here)
├── models.py             ← SQLAlchemy 2.0 ORM models (already complete)
├── dashboard.py          ← Flask server (5,759 lines, 101 routes — SOURCE OF TRUTH)
├── omega_db.py           ← Raw SQL database layer (DO NOT MODIFY)
├── config.py             ← All config, paths, env vars
├── routers/              ← CREATE THIS DIRECTORY, put router files here
│   ├── __init__.py
│   ├── programs.py
│   ├── tracks.py
│   ├── books.py
│   ├── ops.py
│   ├── settings.py
│   ├── health.py
│   ├── editor.py
│   └── legacy.py
└── db.py                 ← CREATE THIS FILE: async SQLAlchemy session factory
```

---

## Step 1: Create `db.py` (Async Session Factory)

The database connects via `DB_DSN` or `DATABASE_URL` env var (PostgreSQL connection string). See `omega_db.py` lines 118-143 for the exact env var names.

```python
# db.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Convert sync DSN to async (postgresql:// → postgresql+asyncpg://)
_sync_dsn = (os.getenv("DB_DSN") or os.getenv("DATABASE_URL") or "").strip()
if _sync_dsn.startswith("postgresql://"):
    DATABASE_URL = _sync_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _sync_dsn.startswith("postgres://"):
    DATABASE_URL = _sync_dsn.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    DATABASE_URL = _sync_dsn  # Fallback

engine = create_async_engine(DATABASE_URL, pool_size=10, max_overflow=5, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

**Dependency:** `pip install asyncpg` (already installed).

---

## Step 2: Create Router Files

### Pattern to follow for every route:

**Flask (in `dashboard.py`):**
```python
@app.route('/api/v2/programs', methods=['GET'])
@admin_required
def api_v2_programs():
    try:
        conn = omega_db._connect()
        c = conn.cursor()
        c.execute("SELECT * FROM programs WHERE status != 'DELETED' ORDER BY updated_at DESC")
        programs = omega_db._fetchall_dicts(c)
        # ... compute track_completion, needs_attention ...
        return jsonify(programs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

**FastAPI (in `routers/programs.py`):**
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db import get_db
from models import Program, Track

router = APIRouter(prefix="/api/v2", tags=["Programs"])

@router.get("/programs")
async def list_programs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Program).where(Program.status != "DELETED").order_by(Program.updated_at.desc())
    )
    programs = result.scalars().all()
    # ... same business logic ...
    return programs
```

### Admin auth

In Flask, `@admin_required` checks `request.remote_addr` for loopback or validates `X-Omega-Admin-Token` header. Replicate this as a FastAPI dependency:

```python
# In routers/__init__.py or a shared deps.py
from fastapi import Request, HTTPException
import secrets, os

_ADMIN_TOKEN_ENV = "OMEGA_ADMIN_TOKEN"

def admin_required(request: Request):
    remote = request.client.host
    if remote in ("127.0.0.1", "::1", "localhost"):
        return True
    configured = (os.environ.get(_ADMIN_TOKEN_ENV) or "").strip()
    if not configured:
        raise HTTPException(status_code=403, detail="Admin access required")
    provided = request.headers.get("X-Omega-Admin-Token", "")
    if not provided or not secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=403, detail="Admin access required")
    return True
```

---

## Step 3: Mount Routers in `api_main.py`

Add this to the bottom of `api_main.py` (before the `if __name__` block):

```python
from routers import programs, tracks, books, ops, settings, health, editor, legacy
app.include_router(programs.router)
app.include_router(tracks.router)
app.include_router(books.router)
app.include_router(ops.router)
app.include_router(settings.router)
app.include_router(health.router)
app.include_router(editor.router)
app.include_router(legacy.router)
```

---

## Route Inventory (Exact Lines in `dashboard.py`)

### `routers/health.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET | `/health` | 433 |
| GET | `/healthz` | 448 |
| GET | `/ready` | 453 |
| GET | `/api/health` | 641 |
| GET | `/api/v2/health/diagnose` | 805 |
| POST | `/api/v2/health/fix` | 954 |
| GET | `/api/v2/health/stuck` | 1056 |
| POST | `/api/v2/health/notify-dead` | 1154 |
| GET | `/metrics` | 1313 |

### `routers/programs.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET | `/api/v2/programs` | 2603 |
| GET | `/api/v2/programs/<id>` | 2636 |
| DELETE | `/api/v2/programs/<id>` | 2659 |
| POST | `/api/v2/programs` | 2810 |
| GET | `/api/v2/programs/staged` | 2837 |
| POST | `/api/v2/programs/<id>/configure` | 2869 |
| POST | `/api/v2/programs/<id>/start` | 2933 |
| GET | `/api/v2/programs/<id>/tracks` | 3231 |
| POST | `/api/v2/programs/<id>/tracks` | 3241 |
| GET | `/api/v2/thumbnails/<id>` | 3550 |

### `routers/tracks.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| POST | `/api/v2/tracks` | 5094 |
| POST | `/api/v2/fork-language` | 3356 |
| GET | `/api/v2/tracks/<id>` | 3409 |
| PUT | `/api/v2/tracks/<id>` | 3445 |
| GET | `/api/v2/tracks/active` | 3460 |
| GET | `/api/v2/jobs/<id>/logs` | 3471 |
| POST | `/api/v2/tracks/<id>/reveal` | 3485 |
| POST | `/api/v2/tracks/<id>/deliver` | 3518 |
| POST | `/api/v2/tracks/<id>/send-to-review` | 3569 |
| POST | `/api/v2/tracks/<id>/approve` | 3587 |
| POST | `/api/v2/tracks/<id>/override` | 3645 |
| POST | `/api/v2/tracks/<id>/lock` | 3676 |
| POST | `/api/v2/tracks/<id>/unlock` | 3706 |
| POST | `/api/v2/tracks/<id>/send-review` | 3793 |
| GET | `/api/v2/tracks/<id>/review-status` | 3849 |
| GET | `/api/v2/tracks/<id>/open-editor` | 3866 |
| POST | `/api/v2/tracks/<id>/start-dub` | 3882 |
| POST | `/api/v2/tracks/<id>/reject` | 3963 |
| POST | `/api/v2/tracks/<id>/retry` | 3983 |
| POST | `/api/v2/tracks/<id>/finalize` | 4063 |
| POST | `/api/v2/tracks/<id>/burn` | 4128 |

### `routers/books.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| POST | `/api/v2/books/upload` | 4635 |
| GET | `/api/v2/books` | 4723 |
| GET | `/api/v2/books/<id>` | 4738 |
| POST | `/api/v2/books` | 4752 |
| PATCH | `/api/v2/books/<id>` | 4792 |
| POST | `/api/v2/books/<id>/chapters` | 4814 |
| GET | `/api/v2/chapters/<id>` | 4847 |
| PATCH | `/api/v2/chapters/<id>` | 4861 |
| POST | `/api/v2/chapters/<id>/lock` | 4902 |
| POST | `/api/v2/chapters/<id>/unlock` | 4928 |
| POST | `/api/v2/books/<id>/translate` | 4944 |
| POST | `/api/v2/chapters/<id>/translate` | 4963 |
| DELETE | `/api/v2/books/<id>` | 4977 |

### `routers/ops.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET | `/api/v2/weekly_grid` | 4998 |
| GET | `/api/v2/ops/summary` | 5151 |
| GET | `/api/v2/ops/queue` | 5238 |
| POST | `/api/v2/ops/actions` | 5341 |
| GET | `/api/v2/ops/deliveries` | 5539 |
| GET | `/api/v2/cloud/status` | 5616 |
| GET | `/api/v2/pipeline/stats` | 4179 |

### `routers/settings.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET | `/api/v2/languages` | 4226 |
| GET | `/api/v2/voices` | 4247 |
| GET | `/api/v2/settings` | 4267 |
| PATCH | `/api/v2/settings` | 4278 |
| GET | `/api/v2/ministries` | 3056 |
| POST | `/api/v2/ministries` | 3067 |
| PUT | `/api/v2/ministries/<id>` | 3092 |
| DELETE | `/api/v2/ministries/<id>` | 3105 |
| GET | `/api/v2/deliveries/profiles` | 3117 |
| POST | `/api/v2/deliveries/profiles` | 3128 |
| PUT | `/api/v2/deliveries/profiles/<id>` | 3148 |
| DELETE | `/api/v2/deliveries/profiles/<id>` | 3161 |
| GET | `/api/v2/dropzones` | 3173 |
| POST | `/api/v2/dropzones` | 3184 |
| PUT | `/api/v2/dropzones/<id>` | 3206 |
| DELETE | `/api/v2/dropzones/<id>` | 3219 |
| GET | `/api/v2/deliveries` | 3538 |
| POST | `/api/v2/deliveries` | 4485 |

### `routers/editor.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET/POST | `/api/editor/<job_id>` | 2240 |
| POST | `/api/assistant/chat` | 2183 |
| GET | `/api/surgical/segments` | 2066 |
| POST | `/api/surgical/save` | 2134 |
| GET | `/api/stream/<job_id>` | 2500 |

### `routers/legacy.py`
| Method | Path | Flask Line |
|--------|------|-----------|
| GET | `/api/jobs` | 476 |
| GET | `/api/jobs_grouped` | 481 |
| POST | `/api/mark_delivered` | 519 |
| POST | `/api/delete_program` | 539 |
| GET | `/api/deliveries` | 630 |
| GET | `/api/encoding_status` | 1196 |
| GET | `/api/logs` | 1253 |
| GET | `/api/output/<stem>` | 1273 |
| POST | `/api/action/fork` | 1346 |
| POST | `/api/action/dub` | 1402 |
| POST | `/api/action` | 1446 |
| POST | `/api/smart_upload` | 1803 |
| GET | `/api/events` | 4421 |
| POST | `/api/upload` | 4562 |

---

## SQLAlchemy Models Available

The following models are defined in `models.py` and map directly to the PostgreSQL tables:

- `Program` — programs table
- `Track` — tracks table (FK → programs, master_scripts)
- `MasterScript` — master_scripts table (FK → programs)
- `ErrorLog` — error_log table
- `Station` — stations table
- `DeliveryProfile` — delivery_profiles table

**Models NOT yet created** (you'll need to add these to `models.py`):
- `TrackDelivery` — track_deliveries table (FK → tracks)
- `ScriptEdit` — script_edits table (FK → master_scripts, tracks)
- `StageTransition` — stage_transitions table
- `BookProject` — book_projects table
- `BookChapter` — book_chapters table (FK → book_projects)
- `MinistryProfile` — ministry_profiles table
- `DropzoneRecipe` — dropzone_recipes table

Refer to `omega_db.py` lines 300-575 for the exact column definitions.

---

## Critical Rules

1. **Preserve exact URL paths and HTTP methods.** The Next.js frontend relies on them.
2. **Use Pydantic response models** — don't return raw dicts.
3. **Use `async def`** for all handlers.
4. **Use SQLAlchemy async sessions** from `db.py`, not raw SQL.
5. **Don't import `omega_db`** in any router. The whole point is replacing it.
6. **Some routes shell out to worker scripts** (e.g., `/tracks/<id>/burn` calls `subs_render_overlay.py`). Keep those `subprocess.run()` calls as-is — wrap them in `asyncio.to_thread()` to avoid blocking the event loop.
7. **Some routes read/write files** (e.g., editor, upload). Keep those filesystem operations but wrap in `asyncio.to_thread()`.
8. **Install `asyncpg`** if not already installed: `pip install asyncpg`.
9. **Test each router** by running `uvicorn api_main:socket_app --port 8001` and curling the endpoints.

---

## Environment Variables for DB Connection

```bash
# Option 1: Direct connection string (local dev)
DB_DSN=postgresql://omega_user:PASSWORD@localhost:5432/omega_db

# Option 2: Cloud SQL connector (production)
DB_INSTANCE_CONNECTION_NAME=project:region:instance
DB_USER=omega_user
DB_PASS=PASSWORD
DB_NAME=omega_db
```

Check `.env` or `.omega_secrets` in the project root for the actual values.

---

## Verification

After porting all routes:
1. `uvicorn api_main:socket_app --host 0.0.0.0 --port 8001`
2. `curl http://localhost:8001/api/v2/programs` → should return the same JSON as `curl http://localhost:8080/api/v2/programs`
3. `curl http://localhost:8001/api/v2/pipeline/stats` → should return pipeline stats
4. `curl http://localhost:8001/docs` → FastAPI auto-generated Swagger UI should list all endpoints
