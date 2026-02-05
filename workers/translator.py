import os
import json
import time
import random
import datetime
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Iterable
from google.cloud import storage
import vertexai
from vertexai.preview import caching
from vertexai.generative_models import GenerativeModel, Part, Content, SafetySetting, HarmCategory, HarmBlockThreshold, GenerationConfig
import config
import omega_db
import system_health

logger = logging.getLogger("OmegaManager.Translator")

# --- CONFIG ---
PROJECT_ID = config.OMEGA_CLOUD_PROJECT
BUCKET_NAME = "audio-hq-sermon-translator-55"
LOCATION = config.GEMINI_LOCATION
MAX_WORKERS = 3
BATCH_SIZE = 60

SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=HarmBlockThreshold.BLOCK_NONE),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_NONE),
]

# Icelandic Glossary
GLOSSARY_IS = {
    "I AM": "ÉG ER", "Lord": "Drottinn", "Savior": "Frelsari", "Holy Spirit": "Heilagur Andi",
    "Grace": "Náð", "Salvation": "Frelsi", "Gospel": "Fagnaðarerindið", "Sin": "Synd",
    "Repentance": "Iðrun", "Covenant": "Sáttmáli", "Anointing": "Smurning", "Revival": "Vakning",
    "Harvest": "Uppskera", "Gentiles": "Heiðingjar", "Evangelist": "Trúboði",
    "Partners": "Bakhjarlar", "Gospel Campaign": "Trúboðsátak", "Sponsored by": "Kostaður af",
    "In Touch": "In Touch", "Shake the Nations": "Shake the Nations"
}

# Dutch Glossary - Comprehensive theological and broadcast terms
GLOSSARY_NL = {
    # Divine names/titles
    "I AM": "IK BEN",
    "the Lord": "de Heer",
    "Lord": "Heer",
    "Lord Jesus": "Here Jezus",
    "Jesus Christ": "Jezus Christus",
    "God the Father": "God de Vader",
    "Holy Spirit": "Heilige Geest",
    "the Holy Spirit": "de Heilige Geest",
    "Savior": "Verlosser",
    "Redeemer": "Verlosser",
    "Lamb of God": "Lam van God",
    "Son of Man": "Mensenzoon",
    "Son of God": "Zoon van God",
    "Messiah": "Messias",
    "Christ": "Christus",
    "Almighty": "Almachtige",

    # Theological concepts
    "salvation": "verlossing",
    "grace": "genade",
    "faith": "geloof",
    "redemption": "verlossing",
    "righteousness": "gerechtigheid",
    "sanctification": "heiliging",
    "justification": "rechtvaardiging",
    "repentance": "bekering",
    "forgiveness": "vergeving",
    "atonement": "verzoening",
    "covenant": "verbond",
    "resurrection": "opstanding",
    "eternal life": "eeuwig leven",
    "born again": "wedergeboren",
    "baptism": "doop",
    "communion": "avondmaal",
    "sin": "zonde",
    "sins": "zonden",
    "sinner": "zondaar",
    "sinners": "zondaren",
    "repent": "bekeren",
    "confess": "belijden",
    "believe": "geloven",
    "blessing": "zegen",
    "blessed": "gezegend",
    "anoint": "zalven",
    "anointing": "zalving",
    "anointed": "gezalfd",
    "revival": "opwekking",
    "harvest": "oogst",
    "kingdom": "koninkrijk",
    "Kingdom of God": "Koninkrijk van God",
    "Kingdom of Heaven": "Koninkrijk der Hemelen",

    # Church/community terms
    "congregation": "gemeente",
    "church": "kerk",
    "fellowship": "gemeenschap",
    "worship": "aanbidding",
    "praise": "lofprijs",
    "prayer": "gebed",
    "pray": "bidden",
    "sermon": "preek",
    "preach": "prediken",
    "preacher": "prediker",
    "pastor": "predikant",
    "ministry": "bediening",
    "testimony": "getuigenis",
    "witness": "getuige",
    "disciple": "discipel",
    "disciples": "discipelen",
    "apostle": "apostel",
    "apostles": "apostelen",
    "prophet": "profeet",
    "prophets": "profeten",
    "evangelist": "evangelist",

    # Scripture references
    "Scripture": "Schrift",
    "Scriptures": "Schriften",
    "the Word": "het Woord",
    "the Word of God": "het Woord van God",
    "Gospel": "Evangelie",
    "Old Testament": "Oude Testament",
    "New Testament": "Nieuwe Testament",
    "Bible": "Bijbel",
    "verse": "vers",
    "chapter": "hoofdstuk",

    # Common phrases
    "in Jesus' name": "in Jezus' naam",
    "Amen": "Amen",
    "Hallelujah": "Halleluja",
    "Glory to God": "Eer aan God",
    "Praise the Lord": "Prijs de Heer",
    "God bless you": "God zegene u",
    "peace be with you": "vrede zij met u",

    # Program names (keep English)
    "In Touch": "In Touch",
    "Shake the Nations": "Shake the Nations",
    "700 Club": "700 Club",
    "CBN": "CBN",
}

# Legacy alias for backwards compatibility
GLOSSARY = GLOSSARY_IS


def get_glossary_for_language(lang_code: str) -> dict:
    """Returns the appropriate glossary for the given language code."""
    lang = lang_code.lower().strip()
    if lang in ("is", "icelandic"):
        return GLOSSARY_IS
    elif lang in ("nl", "dutch"):
        return GLOSSARY_NL
    else:
        # Return empty dict for unsupported languages
        return {}

_CHECKPOINT_VERSION = 1


def _slugify(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "default"
    allowed = []
    for ch in value:
        if ch.isalnum() or ch in {"-", "_", "."}:
            allowed.append(ch)
        else:
            allowed.append("_")
    collapsed = "".join(allowed)
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed.strip("_.") or "default"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{int(time.time() * 1e9)}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _checkpoint_path(stem: str, target_language_code: str, program_profile: str) -> Path:
    safe_stem = _slugify(stem)
    safe_lang = _slugify(target_language_code.lower())
    safe_profile = _slugify(program_profile)
    filename = f"{safe_stem}.{safe_lang}.{safe_profile}.translate_checkpoint.json"

    candidates = [
        config.VAULT_DATA / "checkpoints",
        config.BASE_DIR / "checkpoints",
    ]
    for checkpoint_dir in candidates:
        try:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            return checkpoint_dir / filename
        except Exception:
            continue

    # Last resort: return a path under BASE_DIR even if mkdir fails; caller will handle write failures.
    return config.BASE_DIR / filename


def _load_checkpoint(
    checkpoint_path: Path,
    *,
    stem: str,
    target_language_code: str,
    program_profile: str,
    source_count: int,
) -> Dict[str, Any]:
    if not checkpoint_path.exists():
        return {
            "version": _CHECKPOINT_VERSION,
            "stem": stem,
            "target_language_code": target_language_code.lower(),
            "program_profile": program_profile,
            "source_count": int(source_count),
            "translated": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    try:
        data = _read_json(checkpoint_path)
    except Exception:
        bad_path = checkpoint_path.with_suffix(
            checkpoint_path.suffix + f".corrupt.{int(time.time())}"
        )
        try:
            checkpoint_path.replace(bad_path)
        except Exception:
            pass
        return {
            "version": _CHECKPOINT_VERSION,
            "stem": stem,
            "target_language_code": target_language_code.lower(),
            "program_profile": program_profile,
            "source_count": int(source_count),
            "translated": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    translated = data.get("translated")
    if not isinstance(translated, dict):
        translated = {}

    normalized_translated: Dict[str, str] = {}
    for k, v in translated.items():
        if v is None:
            continue
        key = str(k)
        if isinstance(v, str):
            normalized_translated[key] = v
        else:
            try:
                normalized_translated[key] = str(v)
            except Exception:
                continue

    expected = {
        "version": _CHECKPOINT_VERSION,
        "stem": stem,
        "target_language_code": target_language_code.lower(),
        "program_profile": program_profile,
        "source_count": int(source_count),
    }

    mismatch = False
    for key, value in expected.items():
        if data.get(key) != value:
            mismatch = True
            break

    if mismatch:
        mismatch_path = checkpoint_path.with_suffix(
            checkpoint_path.suffix + f".mismatch.{int(time.time())}"
        )
        try:
            checkpoint_path.replace(mismatch_path)
        except Exception:
            pass
        return {
            **expected,
            "translated": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    data["translated"] = normalized_translated
    data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return data


def _translation_progress(translated_count: int, total_count: int) -> float:
    if total_count <= 0:
        return 40.0
    ratio = max(0.0, min(1.0, translated_count / total_count))
    return 40.0 + ratio * 15.0


def _clean_model_json(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        # Strip the first fence line (``` or ```json) and the final fence if present.
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _sleep_backoff(attempt: int, *, base: float = 1.8, cap_seconds: float = 60.0) -> None:
    # Jitter helps avoid thundering herds on retries.
    delay = min(cap_seconds, base ** max(1, attempt))
    delay += random.uniform(0.0, 0.6)
    time.sleep(delay)


def _iter_input_ids(batch: Iterable[dict]) -> list[int]:
    ids: list[int] = []
    for seg in batch:
        seg_id = seg.get("id")
        if seg_id is None:
            raise ValueError("Segment missing 'id'")
        try:
            ids.append(int(seg_id))
        except Exception as exc:
            raise ValueError(f"Invalid segment id: {seg_id!r}") from exc
    return ids


def _translate_batch_once(
    model: GenerativeModel,
    batch: list[dict],
    *,
    target_language: str,
    program_profile: str,
    audio_context: Optional[Part] = None,
) -> list[dict]:
    terminology_note = ""
    if target_language.lower() in {"icelandic", "is"}:
        terminology_note = '    - Terminology: "Pastor" -> "Prestur".\n'

    prompt = f"""
    TRANSLATE these segments to {target_language} (Profile: {program_profile}).
    Return ONLY JSON.

    BROADCAST CAPS:
    - Do NOT output ALL CAPS sentences.
    - If the source text is ALL CAPS, convert to natural sentence case.
    - Preserve acronyms/initialisms (e.g., USA, TV, I-690) and required theological titles (e.g., ÉG ER / YO SOY).

    ASR CLEANUP:
    - Fix obvious speech-to-text errors in the SOURCE when the intended word is clear.
    - If uncertain, keep the original wording.
    
    MUSIC:
    - Only output (MUSIC) for pure singing/lyrics or instrumental with no speech.
    - If speech is present over music, translate the speech and do NOT output (MUSIC).
{terminology_note}

    INPUT:
    {json.dumps(batch, ensure_ascii=False)}
    """

    generation_config = GenerationConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
            },
        },
        temperature=0.3,
    )

    contents = [prompt]
    if audio_context:
        contents.append(audio_context)

    response = model.generate_content(
        contents,
        generation_config=generation_config,
        safety_settings=SAFETY_SETTINGS,
    )

    cleaned = _clean_model_json(getattr(response, "text", "") or "")
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Model response is not a JSON array")

    input_ids = _iter_input_ids(batch)
    expected_ids = set(input_ids)
    result_map: Dict[int, str] = {}

    for item in parsed:
        if not isinstance(item, dict):
            continue
        seg_id = item.get("id")
        text = item.get("text")
        try:
            seg_id_int = int(seg_id)
        except Exception:
            continue
        if seg_id_int not in expected_ids:
            continue
        if not isinstance(text, str):
            continue
        result_map[seg_id_int] = text

    missing = [seg_id for seg_id in input_ids if seg_id not in result_map]
    if missing:
        raise ValueError(f"Missing IDs in model response: {missing[:8]}")

    return [{"id": seg_id, "text": result_map[seg_id]} for seg_id in input_ids]


def translate_batch_with_cache(
    model: GenerativeModel,
    batch: list[dict],
    target_language: str,
    program_profile: str = "standard",
    *,
    max_attempts: int = 6,
    split_after_attempts: int = 2,
    _depth: int = 0,
    audio_context: Optional[Part] = None,
) -> list[dict]:
    """
    Translates a batch of segments using the cached context.

    Stability features:
    - Robust retries with jittered backoff.
    - Automatic batch splitting when the model returns malformed/partial JSON.
    """
    if not batch:
        return []

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _translate_batch_once(
                model,
                batch,
                target_language=target_language,
                program_profile=program_profile,
                audio_context=audio_context,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            if len(batch) > 1 and attempt >= split_after_attempts:
                break
            logger.warning("   ⚠️ Batch parse/validation retry %s/%s: %s", attempt, max_attempts, exc)
            _sleep_backoff(attempt)
        except Exception as exc:
            last_exc = exc
            logger.warning("   ⚠️ Batch retry %s/%s: %s", attempt, max_attempts, exc)
            _sleep_backoff(attempt)

    if len(batch) <= 1:
        raise last_exc or RuntimeError("Batch translation failed")

    # Split batch to reduce response size / schema failure rate.
    mid = max(1, len(batch) // 2)
    logger.warning(
        "   🔪 Splitting batch (%s items) at depth %s after repeated failures: %s",
        len(batch),
        _depth,
        last_exc,
    )
    left = translate_batch_with_cache(
        model,
        batch[:mid],
        target_language,
        program_profile,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
        audio_context=audio_context,
    )
    right = translate_batch_with_cache(
        model,
        batch[mid:],
        target_language,
        program_profile,
        max_attempts=max_attempts,
        split_after_attempts=split_after_attempts,
        _depth=_depth + 1,
        audio_context=audio_context,
    )
    return left + right


def ensure_credentials() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"): return True
    default_path = config.BASE_DIR / "service_account.json"
    if default_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(default_path)
        return True
    return False

def upload_to_gcs(local_path: Path, destination_name: str) -> Optional[str]:
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(destination_name)
        if not blob.exists():
            logger.info(f"☁️ Uploading audio: {local_path.name}...")
            blob.upload_from_filename(str(local_path))
        return f"gs://{BUCKET_NAME}/{destination_name}"
    except Exception as e:
        logger.error(f"GCS Upload Failed: {e}")
        return None

import profiles

def create_context_cache(gcs_uri: str, stem: str, target_language: str = "Icelandic", program_profile: str = "standard") -> Optional[str]:
    logger.info(f"⚡️ Creating Context Cache ({config.MODEL_TRANSLATOR}) for {stem} in {target_language} (Profile: {program_profile})...")
    
    # Map full language name to code if needed, but profiles expects code (is, es)
    # The manager should pass the code (e.g. "is", "es")
    # If target_language is "Icelandic", map to "is"
    lang_map = {
        "icelandic": "is",
        "english": "en",
        "spanish": "es",
        "french": "fr",
        "german": "de",
        "portuguese": "pt",
        "italian": "it",
        "dutch": "nl",
    }
    lang_code = lang_map.get(target_language.lower(), target_language.lower())
    
    system_instruction = profiles.get_system_instruction(lang_code, program_profile)
    
    mime_type = "audio/wav" if gcs_uri.endswith(".wav") else "audio/mpeg"
    
    try:
        # Create Cache
        cached_content = caching.CachedContent.create(
            model_name=config.MODEL_TRANSLATOR,
            system_instruction=system_instruction,
            contents=[
                Content(role="user", parts=[
                    Part.from_uri(mime_type=mime_type, uri=gcs_uri)
                ])
            ],
            ttl=datetime.timedelta(minutes=60)
        )
        logger.info(f"✅ Cache Active! ID: {cached_content.name}")
        return cached_content.name
    except Exception as e:
        return cached_content.name
    except Exception as e:
        logger.warning(f"Cache Creation Failed (will fallback): {e}")
        return None

def translate(transcription_path: Path, target_language_code: str = "is", program_profile: str = "standard"):
    """
    Translates a skeleton transcription using a Gemini cached-audio context.

    Stability features:
    - On-disk checkpointing to resume after crashes/restarts.
    - Automatic batch splitting when the model returns malformed/partial JSON.
    """
    if not ensure_credentials():
        raise Exception("Google Credentials not found")

    stem = transcription_path.stem.replace("_SKELETON_DONE", "").replace("_SKELETON", "")
    program_profile = (program_profile or "standard").strip() or "standard"
    target_language_code = (target_language_code or "is").strip().lower() or "is"
    
    # Map code to name for logging/cache creation if needed, 
    # but we primarily use code now.
    lang_map = {
        "is": "Icelandic",
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "pt": "Portuguese",
        "it": "Italian",
        "nl": "Dutch",
    }
    target_language = lang_map.get(target_language_code, "Icelandic")

    # Load Data
    wrapper = _read_json(transcription_path)
    full_data = wrapper.get("segments", wrapper) if isinstance(wrapper, dict) else wrapper
    if not isinstance(full_data, list):
        raise ValueError(f"Invalid transcription format: expected list, got {type(full_data).__name__}")

    output_path = config.EDITOR_DIR / f"{stem}_{target_language_code.upper()}.json"
    if output_path.exists():
        try:
            existing = _read_json(output_path)
            if isinstance(existing, dict) and "source_data" in existing and "translated_data" in existing:
                logger.info("✅ Translation output already exists: %s", output_path.name)
                return output_path
        except Exception:
            pass

    checkpoint_path = _checkpoint_path(stem, target_language_code, program_profile)
    checkpoint = _load_checkpoint(
        checkpoint_path,
        stem=stem,
        target_language_code=target_language_code,
        program_profile=program_profile,
        source_count=len(full_data),
    )
    translated_map: Dict[str, str] = dict(checkpoint.get("translated") or {})

    input_ids = _iter_input_ids(full_data)
    total_count = len(input_ids)

    # Determine which segments still need translation.
    to_translate: list[dict] = []
    for seg in full_data:
        seg_id = str(seg.get("id"))
        existing_text = translated_map.get(seg_id)
        if not isinstance(existing_text, str) or not existing_text.strip():
            to_translate.append(seg)

    # If already complete (e.g., after a crash), just (re)emit the editor payload.
    if not to_translate:
        translated_segments = [{"id": seg_id, "text": translated_map[str(seg_id)]} for seg_id in input_ids]
        payload = {"source_data": full_data, "translated_data": translated_segments}
        _atomic_write_json(output_path, payload)
        logger.info("👤 Sent to Chief Editor (resumed): %s", output_path.name)
        return output_path

    audio_path = config.VAULT_DIR / "Audio" / f"{stem}.wav"
    if not audio_path.exists():
        # Fallback to old location
        audio_path = config.VAULT_DATA / f"{stem}.mp3"
    if not audio_path.exists():
        raise Exception(f"Audio file not found for {stem}")

    max_attempts = int(os.environ.get("OMEGA_TRANSLATE_MAX_ATTEMPTS", "6") or 6)
    split_after_attempts = int(os.environ.get("OMEGA_TRANSLATE_SPLIT_AFTER", "2") or 2)
    base_batch_size = int(os.environ.get("OMEGA_TRANSLATE_BATCH_SIZE", str(BATCH_SIZE)) or BATCH_SIZE)
    batch_size = max(1, min(base_batch_size, 200))

    # Init & Cache (only when we actually need to translate new segments).
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    gcs_uri = upload_to_gcs(audio_path, f"audio_cache/{_slugify(stem)}{audio_path.suffix}")
    if not gcs_uri:
        raise Exception("GCS Upload failed")

    cache_name: Optional[str] = None
    existing_cache_name = checkpoint.get("cache_name")
    existing_cache_model = checkpoint.get("cache_model")
    if (
        isinstance(existing_cache_name, str)
        and existing_cache_name.strip()
        and (not existing_cache_model or existing_cache_model == config.MODEL_TRANSLATOR)
    ):
        cache_name = existing_cache_name.strip()

    if cache_name:
        try:
            # Instantiate Model from existing Cache
            model = GenerativeModel.from_cached_content(
                cached_content=caching.CachedContent(cached_content_name=cache_name)
            )
            logger.info("♻️ Reusing existing context cache: %s", cache_name)
        except Exception:
            cache_name = None

    audio_context: Optional[Part] = None
    if not cache_name:
        # Prepare system instruction for fallback
        lang_map_full = {
            "icelandic": "is", "english": "en", "spanish": "es",
            "french": "fr", "german": "de", "portuguese": "pt", "italian": "it",
            "dutch": "nl"
        }
        l_code = lang_map_full.get(target_language.lower(), target_language.lower())
        sys_inst = profiles.get_system_instruction(l_code, program_profile)
        
        cache_name = create_context_cache(gcs_uri, stem, target_language, program_profile=program_profile)
        
        if cache_name:
            checkpoint["cache_name"] = cache_name
            checkpoint["cache_model"] = config.MODEL_TRANSLATOR
            checkpoint["cache_created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _atomic_write_json(checkpoint_path, checkpoint)

            model = GenerativeModel.from_cached_content(
                cached_content=caching.CachedContent(cached_content_name=cache_name)
            )
        else:
            # FALLBACK: No cache (e.g. content too short or error)
            # Use standard model and pass audio context in every request
            logger.warning("⚠️ Using Per-Request Audio Context (No Cache)")
            model = GenerativeModel(config.MODEL_TRANSLATOR, system_instruction=sys_inst)
            mime_type = "audio/wav" if gcs_uri.endswith(".wav") else "audio/mpeg"
            audio_context = Part.from_uri(mime_type=mime_type, uri=gcs_uri)

    success = False
    try:
        completed_before = sum(1 for seg_id in input_ids if str(seg_id) in translated_map)
        omega_db.update_job_via_track(
            stem,
            progress=_translation_progress(completed_before, total_count),
            meta={"translation_checkpoint": str(checkpoint_path)},
        )

        logger.info(
            "🧠 Translating %s: %s/%s already cached; batch_size=%s; profile=%s",
            stem,
            completed_before,
            total_count,
            batch_size,
            program_profile,
        )

        for offset in range(0, len(to_translate), batch_size):
            system_health.update_heartbeat("omega_manager")
            batch = to_translate[offset : offset + batch_size]
            translated_batch = translate_batch_with_cache(
                model,
                batch,
                target_language,
                program_profile,
                max_attempts=max_attempts,
                split_after_attempts=split_after_attempts,
                audio_context=audio_context,
            )
            for item in translated_batch:
                translated_map[str(item["id"])] = item["text"]

            checkpoint["translated"] = translated_map
            checkpoint["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            checkpoint["translated_count"] = sum(
                1 for seg_id in input_ids if str(seg_id) in translated_map
            )
            _atomic_write_json(checkpoint_path, checkpoint)

            translated_count = int(checkpoint["translated_count"])
            omega_db.update_job_via_track(
                stem,
                progress=_translation_progress(translated_count, total_count),
                status=f"Translating ({translated_count}/{total_count})",
                meta={"translation_checkpoint": str(checkpoint_path)},
            )

        # Reassemble in original order and emit editor payload.
        missing_final = [seg_id for seg_id in input_ids if str(seg_id) not in translated_map]
        if missing_final:
            raise RuntimeError(f"Translation incomplete; missing {len(missing_final)} segments")

        translated_segments = [{"id": seg_id, "text": translated_map[str(seg_id)]} for seg_id in input_ids]
        payload = {"source_data": full_data, "translated_data": translated_segments}
        _atomic_write_json(output_path, payload)
        logger.info("👤 Sent to Chief Editor: %s", output_path.name)

        checkpoint["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        checkpoint["output_path"] = str(output_path)
        checkpoint["complete"] = True
        _atomic_write_json(checkpoint_path, checkpoint)

        success = True
        return output_path
    finally:
        # Cleanup (only on success; failed runs keep resources to maximize resume speed).
        if not success:
            logger.warning("🧯 Translation did not complete; keeping cloud cache/blob for retry.")
        else:
            logger.info("🧹 Cleanup Crew: Removing cloud resources...")
            try:
                if cache_name:
                    caching.CachedContent(name=cache_name).delete()
            except Exception:
                pass
            try:
                if gcs_uri:
                    blob_name = f"audio_cache/{_slugify(stem)}{audio_path.suffix}"
                    storage.Client().bucket(BUCKET_NAME).blob(blob_name).delete()
            except Exception:
                pass
