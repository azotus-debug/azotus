"""
Omega Review Portal
===================
A delightful web interface for reviewing and approving subtitle translations.

Endpoints:
- GET  /review/<job_id>?token=<token>  - View review interface
- GET  /api/job/<job_id>               - Get job data (JSON)
- POST /api/job/<job_id>/save          - Save draft
- POST /api/job/<job_id>/approve       - Approve and finalize
- GET  /api/audio/<job_id>/<index>     - Stream audio clip
"""

import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    # Try loading from parent directories (where main .env is)
    env_paths = [
        Path(__file__).parent.parent.parent / ".env",  # /SubtitleWorkflow/.env
        Path(__file__).parent / ".env",  # /cloud/review_portal/.env
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass  # dotenv not installed, rely on environment variables
from flask import Flask, render_template, jsonify, request, send_file, abort
from typing import Optional, Tuple

app = Flask(__name__)

# Configuration
GCS_BUCKET = os.environ.get("OMEGA_JOBS_BUCKET", "omega-jobs-subtitle-project")
GCS_PREFIX = os.environ.get("OMEGA_JOBS_PREFIX", "jobs")
SECRET_KEY = os.environ.get("OMEGA_REVIEW_SECRET", "omega-review-secret-2024")
TOKEN_EXPIRY_HOURS = 72

# Lazy GCS client initialization
_storage_client = None
_bucket = None


def get_bucket():
    """Get GCS bucket with lazy initialization."""
    global _storage_client, _bucket
    if _bucket is None:
        # Try to load credentials from service account file
        sa_file = Path(__file__).parent.parent.parent / "service_account.json"
        if sa_file.exists():
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(sa_file))
        
        from google.cloud import storage
        _storage_client = storage.Client()
        _bucket = _storage_client.bucket(GCS_BUCKET)
    return _bucket


# =============================================================================
# Token Management
# =============================================================================

def generate_token(job_id: str, expiry_hours: int = TOKEN_EXPIRY_HOURS) -> Tuple[str, int]:
    """Generate a secure review token for a job."""
    expiry_ts = int(time.time()) + (expiry_hours * 3600)
    payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    token = hashlib.sha256(payload.encode()).hexdigest()[:32]
    app.logger.info(f"🔑 Generated token for {job_id}: token={token[:8]}..., exp={expiry_ts}, secret_prefix={SECRET_KEY[:8]}...")
    return token, expiry_ts


def verify_token(job_id: str, token: str, expiry_ts: int) -> bool:
    """Verify a review token is valid and not expired."""
    current_time = time.time()
    
    # Debug logging
    app.logger.debug(f"🔐 Verifying token for {job_id}")
    app.logger.debug(f"   Received token: {token[:8] if token else 'None'}...")
    app.logger.debug(f"   Expiry timestamp: {expiry_ts}")
    app.logger.debug(f"   Current time: {int(current_time)}")
    app.logger.debug(f"   Secret prefix: {SECRET_KEY[:8]}...")
    
    if current_time > expiry_ts:
        app.logger.warning(f"❌ Token expired for {job_id}: now={int(current_time)} > exp={expiry_ts} (expired {int(current_time - expiry_ts)}s ago)")
        return False
    
    expected_payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    expected_token = hashlib.sha256(expected_payload.encode()).hexdigest()[:32]
    
    if token != expected_token:
        app.logger.warning(f"❌ Token mismatch for {job_id}:")
        app.logger.warning(f"   Received:  {token}")
        app.logger.warning(f"   Expected:  {expected_token}")
        return False
    
    app.logger.info(f"✅ Token valid for {job_id} (expires in {int(expiry_ts - current_time)}s)")
    return True


# =============================================================================
# GCS Helpers
# =============================================================================

def get_job_data(job_id: str) -> dict:
    """Fetch job data from GCS."""
    bucket = get_bucket()
    
    # Try multiple naming patterns for skeleton
    skeleton_patterns = [
        f"{GCS_PREFIX}/{job_id}/{job_id}_SKELETON.json",
        f"{GCS_PREFIX}/{job_id}/skeleton.json",
    ]
    
    skeleton_data = None
    for pattern in skeleton_patterns:
        skeleton_blob = bucket.blob(pattern)
        if skeleton_blob.exists():
            skeleton_data = json.loads(skeleton_blob.download_as_text())
            break
    
    if not skeleton_data:
        return None
    
    # Try multiple naming patterns for translation/approved
    translation_patterns = [
        f"{GCS_PREFIX}/{job_id}/{job_id}_APPROVED.json",
        f"{GCS_PREFIX}/{job_id}/approved.json",
        f"{GCS_PREFIX}/{job_id}/{job_id}_TRANSLATED.json",
        f"{GCS_PREFIX}/{job_id}/translation_draft.json",
    ]
    
    approved_data = skeleton_data  # Default to skeleton
    for pattern in translation_patterns:
        blob = bucket.blob(pattern)
        if blob.exists():
            approved_data = json.loads(blob.download_as_text())
            break
    
    # Get job metadata
    job_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/job.json")
    job_meta = json.loads(job_blob.download_as_text()) if job_blob.exists() else {}
    
    # Get editor report if exists
    report_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/editor_report.json")
    editor_report = json.loads(report_blob.download_as_text()) if report_blob.exists() else None
    
    # Handle different segment structures
    segments = approved_data.get("segments", [])
    if not segments and isinstance(approved_data, list):
        segments = approved_data
    
    return {
        "job_id": job_id,
        "program_name": job_meta.get("stem", job_id),
        "target_language": job_meta.get("target_lang", job_meta.get("target_language_code", "Unknown")),
        "skeleton": skeleton_data,
        "translation": approved_data if isinstance(approved_data, dict) else {"segments": approved_data},
        "editor_report": editor_report,
        "subtitle_count": len(segments),
        # Video preview (from Bunny Stream)
        "bunny_embed_url": job_meta.get("meta", {}).get("bunny_embed_url"),
        "has_video": bool(job_meta.get("meta", {}).get("bunny_video_id")),
    }


def save_draft(job_id: str, segments: list) -> bool:
    """Save draft edits to GCS."""
    draft_blob = get_bucket().blob(f"{GCS_PREFIX}/{job_id}/{job_id}_DRAFT.json")
    draft_data = {
        "segments": segments,
        "saved_at": datetime.utcnow().isoformat(),
        "status": "draft"
    }
    draft_blob.upload_from_string(json.dumps(draft_data, indent=2))
    return True


def _load_json_from_gcs(bucket, path: str) -> Optional[dict]:
    """Load JSON from GCS path, return None if not found."""
    try:
        blob = bucket.blob(path)
        if blob.exists():
            return json.loads(blob.download_as_text())
    except Exception as e:
        app.logger.warning(f"Failed to load {path}: {e}")
    return None


def _verify_request_token(job_id: str) -> bool:
    """
    Verify token from request headers or body.
    Returns True if valid, False otherwise.
    In dev mode, always returns True.
    """
    if os.environ.get("OMEGA_DEV_MODE") == "1":
        return True

    # Try to get token from headers first
    token = request.headers.get("X-Review-Token")
    exp_str = request.headers.get("X-Review-Exp")

    # Fallback to request body
    if not token:
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        exp_str = data.get("exp")

    if not token or not exp_str:
        return False

    try:
        exp = int(exp_str)
    except (ValueError, TypeError):
        return False

    return verify_token(job_id, token, exp)


def approve_job(job_id: str, segments: list, reviewer_name: str = "Reviewer") -> bool:
    """Approve and finalize the job, capturing corrections for learning."""
    bucket = get_bucket()

    # Save approved segments
    approved_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/{job_id}_REVIEWED.json")
    approved_data = {
        "segments": segments,
        "approved_at": datetime.utcnow().isoformat(),
        "approved_by": reviewer_name,
        "status": "approved"
    }
    approved_blob.upload_from_string(json.dumps(approved_data, indent=2))

    # Update job status
    status_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/review_status.json")
    status_blob.upload_from_string(json.dumps({
        "status": "approved",
        "approved_at": datetime.utcnow().isoformat(),
        "approved_by": reviewer_name
    }))

    # Capture corrections for learning (async-friendly)
    try:
        _capture_corrections_async(job_id, segments, reviewer_name, bucket)
    except Exception as e:
        # Don't fail the approval if correction capture fails
        app.logger.warning(f"Correction capture failed for {job_id}: {e}")

    return True


def _capture_corrections_async(job_id: str, reviewed_segments: list, reviewer_name: str, bucket):
    """
    Capture corrections made by reviewer for learning.
    Runs after approval, comparing original translation to reviewed version.
    """
    from correction_capture import capture_corrections, save_corrections_to_gcs

    job_prefix = f"{GCS_PREFIX}/{job_id}"

    # Load original translation (before review)
    translation_patterns = [
        f"{job_prefix}/{job_id}_APPROVED.json",
        f"{job_prefix}/approved.json",
        f"{job_prefix}/{job_id}_TRANSLATED.json",
        f"{job_prefix}/translation_draft.json",
    ]

    original_segments = None
    for pattern in translation_patterns:
        data = _load_json_from_gcs(bucket, pattern)
        if data:
            original_segments = data.get("segments", [])
            if original_segments:
                break

    if not original_segments:
        app.logger.info(f"No original translation found for {job_id}, skipping correction capture")
        return

    # Load job metadata
    job_meta = _load_json_from_gcs(bucket, f"{job_prefix}/job.json") or {}

    # Capture corrections
    report = capture_corrections(
        job_id=job_id,
        original_segments=original_segments,
        reviewed_segments=reviewed_segments,
        reviewer_name=reviewer_name,
        job_metadata=job_meta
    )

    if report.corrected_count > 0:
        save_corrections_to_gcs(report, bucket)
        app.logger.info(
            f"Captured {report.corrected_count} corrections for {job_id} "
            f"({report.correction_rate:.1%} correction rate)"
        )
    else:
        app.logger.info(f"No corrections found for {job_id} - translation approved as-is")


# =============================================================================
# Routes
# =============================================================================

@app.route("/")
def index():
    """Landing page."""
    return render_template("index.html")


@app.route("/review/<job_id>")
def review(job_id: str):
    """Main review interface."""
    token = request.args.get("token", "")
    expiry_str = request.args.get("exp", "")
    
    app.logger.info(f"📝 Review request: job={job_id}, token={token[:8] if token else 'None'}..., exp={expiry_str}")
    
    # For development, allow access without token
    if os.environ.get("OMEGA_DEV_MODE") == "1":
        app.logger.info("🔓 DEV MODE: Bypassing token verification")
        pass
    else:
        # Validate expiry parameter
        if not expiry_str:
            app.logger.warning(f"❌ Missing exp parameter for {job_id}")
            abort(401, "Missing 'exp' parameter in URL")
        
        try:
            expiry = int(expiry_str)
        except ValueError:
            app.logger.warning(f"❌ Invalid exp parameter for {job_id}: {expiry_str}")
            abort(401, "Invalid 'exp' parameter - must be a Unix timestamp")
        
        if not token:
            app.logger.warning(f"❌ Missing token for {job_id}")
            abort(401, "Missing 'token' parameter in URL")
        
        if not verify_token(job_id, token, expiry):
            abort(401, "Token expired or invalid. Please request a new review link.")
    
    job_data = get_job_data(job_id)
    if not job_data:
        abort(404, "Job not found")
    
    return render_template("review.html", job=job_data)


@app.route("/api/job/<job_id>")
def api_get_job(job_id: str):
    """API: Get job data."""
    job_data = get_job_data(job_id)
    if not job_data:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job_data)


@app.route("/api/job/<job_id>/save", methods=["POST"])
def api_save_draft(job_id: str):
    """API: Save draft."""
    # Verify token for write operations
    if not _verify_request_token(job_id):
        return jsonify({"error": "Invalid or expired token"}), 401

    data = request.get_json()
    segments = data.get("segments", [])

    if save_draft(job_id, segments):
        return jsonify({"success": True, "message": "Draft saved"})
    return jsonify({"error": "Failed to save"}), 500


@app.route("/api/job/<job_id>/approve", methods=["POST"])
def api_approve(job_id: str):
    """API: Approve and finalize."""
    # Verify token for write operations
    if not _verify_request_token(job_id):
        return jsonify({"error": "Invalid or expired token"}), 401

    data = request.get_json()
    segments = data.get("segments", [])
    reviewer = data.get("reviewer_name", "Anonymous Reviewer")

    if approve_job(job_id, segments, reviewer):
        return jsonify({
            "success": True,
            "message": "Approved! The video will be burned within 30 minutes."
        })
    return jsonify({"error": "Failed to approve"}), 500


# =============================================================================
# Review Portal 2.0: Simplified Review API
# =============================================================================

@app.route("/api/job/<job_id>/review-summary")
def get_review_summary_api(job_id: str):
    """
    Returns simplified review data for Review Portal 2.0.
    Only includes issues that need human review (confidence < 0.9).
    Auto-approves everything else.
    
    Query params:
        token: Review token
        exp: Token expiry timestamp
    """
    from validators import validate_segment_with_confidence, parse_timestamp
    
    token = request.args.get("token", "")
    exp = request.args.get("exp", "")
    
    # Verify token (unless in dev mode)
    if not os.environ.get("OMEGA_DEV_MODE") == "1":
        if token and exp:
            try:
                expiry = int(exp)
            except ValueError:
                return jsonify({"error": "Invalid expiry timestamp"}), 401
            if not verify_token(job_id, token, expiry):
                return jsonify({"error": "Token expired or invalid"}), 401
    
    # Load job data
    job_data = get_job_data(job_id)
    if not job_data:
        return jsonify({"error": "Job not found"}), 404
    
    # Get segments from translation data
    translation = job_data.get("translation", {})
    segments = translation.get("segments", [])
    if not segments:
        segments = job_data.get("skeleton", {}).get("segments", [])
    
    # Run validation with confidence scores
    issues = []
    auto_approved_count = 0
    
    for i, segment in enumerate(segments):
        validation = validate_segment_with_confidence(segment, i, segments)
        
        if validation["needs_review"]:
            issues.append({
                "id": f"issue_{i}",
                "segment_index": i,
                "type": validation["issue_type"],
                "message": validation["human_message"],
                "severity": validation["severity"],
                "confidence": validation["confidence"],
                "original_text": segment.get("source_text", ""),
                "current_text": segment.get("text", ""),
                "suggested_text": validation.get("suggestion", ""),
                "timestamp_start": parse_timestamp(segment.get("start", "0")),
                "timestamp_end": parse_timestamp(segment.get("end", "0"))
            })
        else:
            auto_approved_count += 1
    
    # Get metadata
    program_name = job_data.get("program_name", job_id)
    target_language = job_data.get("target_language", "Unknown")
    video_url = job_data.get("bunny_embed_url", "")
    
    # Estimate review time: ~15 seconds per issue (4 issues per minute)
    # Most issues are quick accept/skip decisions
    estimated_minutes = max(1, len(issues) // 4)
    
    return jsonify({
        "job_id": job_id,
        "program_name": program_name,
        "language": target_language,
        "total_segments": len(segments),
        "auto_approved": auto_approved_count,
        "needs_review": len(issues),
        "estimated_time": f"~{estimated_minutes} minute{'s' if estimated_minutes > 1 else ''}",
        "video_url": video_url,
        "issues": issues
    })


@app.route("/api/job/<job_id>/resolve-issue", methods=["POST"])
def resolve_issue(job_id: str):
    """
    Save reviewer's decision on an issue.
    
    Request body:
    {
        "issue_id": "issue_5",
        "action": "accept" | "edit" | "skip",
        "new_text": "Updated subtitle text"  # Required for accept/edit
    }
    
    Returns:
    {
        "success": true,
        "remaining_issues": 4
    }
    """
    data = request.get_json()
    issue_id = data.get("issue_id")
    action = data.get("action")  # "accept", "edit", "skip"
    new_text = data.get("new_text")
    
    if not issue_id or not action:
        return jsonify({"error": "issue_id and action required"}), 400
    
    # Load current job data
    job_data = get_job_data(job_id)
    if not job_data:
        return jsonify({"error": "Job not found"}), 404
    
    translation = job_data.get("translation", {})
    segments = translation.get("segments", [])
    if not segments:
        segments = job_data.get("skeleton", {}).get("segments", [])
    
    # Parse issue_id to get segment index
    try:
        segment_index = int(issue_id.replace("issue_", ""))
    except ValueError:
        return jsonify({"error": "Invalid issue_id format"}), 400
    
    if segment_index < 0 or segment_index >= len(segments):
        return jsonify({"error": "Segment index out of range"}), 400
    
    # Apply the action
    if action in ("accept", "edit") and new_text:
        segments[segment_index]["text"] = new_text
        segments[segment_index]["reviewed"] = True
        segments[segment_index]["review_action"] = action
    elif action == "skip":
        segments[segment_index]["reviewed"] = True
        segments[segment_index]["review_action"] = "skip"
    
    # Save draft
    save_draft(job_id, segments)
    
    # Count remaining issues (unreviewed segments that need review)
    from validators import validate_segment_with_confidence
    remaining = 0
    for i, seg in enumerate(segments):
        if not seg.get("reviewed"):
            validation = validate_segment_with_confidence(seg, i, segments)
            if validation["needs_review"]:
                remaining += 1
    
    return jsonify({
        "success": True,
        "remaining_issues": remaining,
        "action_applied": action
    })


@app.route("/api/job/<job_id>/approve-all", methods=["POST"])
def approve_all_reviewed(job_id: str):
    """
    Approve all reviewed segments and finalize the job.
    Call this after all issues have been resolved.
    """
    data = request.get_json() or {}
    reviewer_name = data.get("reviewer_name", "Reviewer")
    
    # Load current draft
    bucket = get_bucket()
    draft_blob = bucket.blob(f"{GCS_PREFIX}/{job_id}/{job_id}_DRAFT.json")
    
    if not draft_blob.exists():
        return jsonify({"error": "No draft found. Review issues first."}), 400
    
    draft_data = json.loads(draft_blob.download_as_text())
    segments = draft_data.get("segments", [])
    
    # Approve the job
    if approve_job(job_id, segments, reviewer_name):
        return jsonify({
            "success": True,
            "message": "All segments approved! Video will be burned within 30 minutes.",
            "segments_approved": len(segments)
        })
    
    return jsonify({"error": "Failed to approve"}), 500


@app.route("/api/audio/<job_id>/<int:index>")
def api_audio_clip(job_id: str, index: int):
    """API: Stream audio clip for a specific subtitle."""
    # Audio clips are stored as: jobs/{job_id}/audio_clips/clip_{index:04d}.mp3
    clip_name = f"{GCS_PREFIX}/{job_id}/audio_clips/clip_{index:04d}.mp3"
    clip_blob = get_bucket().blob(clip_name)
    
    if not clip_blob.exists():
        abort(404, "Audio clip not found")
    
    # Stream from GCS
    content = clip_blob.download_as_bytes()
    from io import BytesIO
    return send_file(
        BytesIO(content),
        mimetype="audio/mpeg",
        as_attachment=False
    )


# =============================================================================
# AI Assistant Routes
# =============================================================================

@app.route("/api/segment/suggest", methods=["POST"])
def suggest_improvement():
    """
    Get AI suggestion for improving a translation segment.

    Request body:
    {
        "source_text": "Hello world",
        "translated_text": "Halló heimur",
        "target_language": "is",
        "context": {}
    }

    Returns:
    {
        "suggestion": "...",
        "reasoning": "...",
        "confidence": 0.85,
        "available": true
    }
    """
    from ai_assistant import get_ai_assistant

    try:
        data = request.get_json()
        source_text = data.get("source_text", "")
        translated_text = data.get("translated_text", "")
        target_language = data.get("target_language", "is")
        context = data.get("context", {})

        assistant = get_ai_assistant()

        if not assistant.is_available():
            return jsonify({
                "available": False,
                "error": "AI assistant not configured (ANTHROPIC_API_KEY missing)"
            }), 503

        suggestion = assistant.suggest_improvement(
            source_text=source_text,
            translated_text=translated_text,
            target_language=target_language,
            context=context
        )

        if suggestion is None:
            return jsonify({
                "available": True,
                "error": "Failed to generate suggestion"
            }), 500

        return jsonify({
            "available": True,
            "suggestion": suggestion.suggestion,
            "reasoning": suggestion.reasoning,
            "confidence": suggestion.confidence
        })

    except Exception as e:
        app.logger.error(f"AI suggestion error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/job/<job_id>/analyze", methods=["POST"])
def analyze_quality(job_id):
    """
    Run full quality analysis on a job.

    Returns:
    {
        "score": 85.5,
        "accuracy": 90.0,
        "fluency": 85.0,
        "consistency": 82.0,
        "summary": "...",
        "available": true
    }
    """
    from ai_assistant import get_ai_assistant

    try:
        # Load job segments from GCS
        bucket = get_bucket()
        job_prefix = f"{GCS_PREFIX}/{job_id}"

        # Load skeleton (source) - try multiple naming patterns
        skeleton_patterns = [
            f"{job_prefix}/{job_id}_SKELETON.json",
            f"{job_prefix}/skeleton.json",
        ]
        skeleton_data = None
        for pattern in skeleton_patterns:
            skeleton_data = _load_json_from_gcs(bucket, pattern)
            if skeleton_data:
                break

        if not skeleton_data:
            return jsonify({"error": "Source segments not found"}), 404

        # Load translation - try multiple naming patterns
        translation_patterns = [
            f"{job_prefix}/{job_id}_APPROVED.json",
            f"{job_prefix}/approved.json",
            f"{job_prefix}/{job_id}_TRANSLATED.json",
            f"{job_prefix}/translation_draft.json",
        ]
        translation_data = None
        for pattern in translation_patterns:
            translation_data = _load_json_from_gcs(bucket, pattern)
            if translation_data:
                break

        if not translation_data:
            return jsonify({"error": "Translation not found"}), 404

        source_segments = skeleton_data.get("segments", [])
        translated_segments = translation_data.get("segments", [])

        # Get target language from job metadata
        job_meta = _load_json_from_gcs(bucket, f"{job_prefix}/job.json") or {}
        target_language = job_meta.get("target_lang") or job_meta.get("target_language_code", "is")

        assistant = get_ai_assistant()

        if not assistant.is_available():
            return jsonify({
                "available": False,
                "error": "AI assistant not configured"
            }), 503

        # Convert to Segment objects
        from ai_assistant import Segment
        source_segs = [Segment(
            start=s.get("start", ""),
            end=s.get("end", ""),
            text=s.get("text", ""),
            source_text=s.get("text", "")
        ) for s in source_segments]

        translated_segs = [Segment(
            start=s.get("start", ""),
            end=s.get("end", ""),
            text=s.get("text", "")
        ) for s in translated_segments]

        report = assistant.analyze_translation_quality(
            source_segments=source_segs,
            translated_segments=translated_segs,
            target_language=target_language
        )

        if report is None:
            return jsonify({
                "available": True,
                "error": "Analysis failed"
            }), 500

        return jsonify({
            "available": True,
            "score": report.score,
            "accuracy": report.accuracy_score,
            "fluency": report.fluency_score,
            "consistency": report.consistency_score,
            "summary": report.summary
        })

    except Exception as e:
        app.logger.error(f"Quality analysis error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/segment/validate", methods=["POST"])
def validate_segment():
    """
    Validate a single segment for quality issues.

    Request body:
    {
        "start": "0:00:02.500",
        "end": "0:00:05.000",
        "text": "Hello world"
    }

    Returns:
    {
        "issues": [
            {
                "type": "cps",
                "severity": "error",
                "message": "Reading speed too fast: 25 CPS"
            }
        ]
    }
    """
    from validators import get_validator

    try:
        data = request.get_json()
        validator = get_validator()
        issues = validator.validate_segment(data)

        return jsonify({
            "issues": [
                {
                    "type": issue.type,
                    "severity": issue.severity,
                    "message": issue.message,
                    "suggestion": issue.suggestion
                }
                for issue in issues
            ]
        })

    except Exception as e:
        app.logger.error(f"Validation error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/job/<job_id>/audio")
def get_job_audio(job_id: str):
    """
    Get audio URL for waveform display.
    Returns audio URL (from Bunny or GCS).
    """
    try:
        job_meta = _load_json_from_gcs(get_bucket(), f"{GCS_PREFIX}/{job_id}/job.json")
        if not job_meta:
            return jsonify({"error": "Job not found"}), 404

        # Check for Bunny audio URL in meta
        bunny_audio = job_meta.get("meta", {}).get("bunny_audio_url")
        if bunny_audio:
            return jsonify({"audio_url": bunny_audio})

        # Check for audio in Bunny video (extract from embed URL)
        bunny_video_id = job_meta.get("meta", {}).get("bunny_video_id")
        bunny_library_id = job_meta.get("meta", {}).get("bunny_library_id")
        if bunny_video_id and bunny_library_id:
            # Bunny Stream provides audio extraction via their CDN
            audio_url = f"https://vz-{bunny_library_id}.b-cdn.net/{bunny_video_id}/audio.mp3"
            return jsonify({"audio_url": audio_url})

        # Fallback: check for GCS audio file
        audio_blob = get_bucket().blob(f"{GCS_PREFIX}/{job_id}/audio.mp3")
        if audio_blob.exists():
            # Generate signed URL for temporary access
            url = audio_blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=1),
                method="GET"
            )
            return jsonify({"audio_url": url})

        return jsonify({"error": "No audio available"}), 404

    except Exception as e:
        app.logger.error(f"Audio fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/job/<job_id>/validate", methods=["GET"])
def validate_job(job_id):
    """
    Run full validation on all segments in a job.

    Returns:
    {
        "quality_score": 85.5,
        "error_count": 2,
        "warning_count": 5,
        "info_count": 3,
        "issues": [...]
    }
    """
    from validators import get_validator

    try:
        # Load segments from GCS - try multiple naming patterns
        bucket = get_bucket()
        job_prefix = f"{GCS_PREFIX}/{job_id}"

        translation_patterns = [
            f"{job_prefix}/{job_id}_APPROVED.json",
            f"{job_prefix}/approved.json",
            f"{job_prefix}/{job_id}_TRANSLATED.json",
            f"{job_prefix}/translation_draft.json",
        ]

        translation_data = None
        for pattern in translation_patterns:
            translation_data = _load_json_from_gcs(bucket, pattern)
            if translation_data:
                break

        if not translation_data:
            return jsonify({"error": "Translation not found"}), 404

        segments = translation_data.get("segments", [])

        validator = get_validator()
        results = validator.validate_all(segments)

        return jsonify({
            "quality_score": results["quality_score"],
            "error_count": results["error_count"],
            "warning_count": results["warning_count"],
            "info_count": results["info_count"],
            "total_segments": results["total_segments"],
            "issues": [
                {
                    "type": issue.type,
                    "severity": issue.severity,
                    "message": issue.message,
                    "segment_index": issue.segment_index,
                    "suggestion": issue.suggestion
                }
                for issue in results["issues"][:100]  # Limit to first 100 issues
            ]
        })

    except Exception as e:
        app.logger.error(f"Job validation error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# One-Click Fix Routes
# =============================================================================

@app.route("/api/job/<job_id>/fix/merge", methods=["POST"])
def fix_merge_segments(job_id: str):
    """
    Merge two adjacent segments to fix CPS issues.

    When a segment is too short with high CPS, merging with adjacent
    segment can give more time for the text.

    Request body:
    {
        "segment_index": 5,
        "direction": "up" | "down"  // merge with previous or next segment
    }

    Returns merged segment data for client-side update.
    """
    try:
        data = request.get_json()
        segment_index = data.get("segment_index")
        direction = data.get("direction", "down")

        if segment_index is None:
            return jsonify({"error": "segment_index required"}), 400

        # Load current translation
        bucket = get_bucket()
        job_prefix = f"{GCS_PREFIX}/{job_id}"

        translation_patterns = [
            f"{job_prefix}/{job_id}_DRAFT.json",
            f"{job_prefix}/{job_id}_APPROVED.json",
            f"{job_prefix}/approved.json",
            f"{job_prefix}/{job_id}_TRANSLATED.json",
        ]

        translation_data = None
        used_path = None
        for pattern in translation_patterns:
            translation_data = _load_json_from_gcs(bucket, pattern)
            if translation_data:
                used_path = pattern
                break

        if not translation_data:
            return jsonify({"error": "Translation not found"}), 404

        segments = translation_data.get("segments", [])

        # Validate indices
        if direction == "up":
            merge_index = segment_index - 1
        else:
            merge_index = segment_index + 1

        if merge_index < 0 or merge_index >= len(segments):
            return jsonify({"error": "Cannot merge - no adjacent segment"}), 400

        # Perform merge
        if direction == "up":
            # Merge current into previous
            first_idx, second_idx = merge_index, segment_index
        else:
            # Merge next into current
            first_idx, second_idx = segment_index, merge_index

        first_seg = segments[first_idx]
        second_seg = segments[second_idx]

        # Create merged segment
        merged = {
            "start": first_seg.get("start", ""),
            "end": second_seg.get("end", ""),
            "text": f"{first_seg.get('text', '')} {second_seg.get('text', '')}".strip()
        }

        # Preserve any extra fields from first segment
        for key in first_seg:
            if key not in merged:
                merged[key] = first_seg[key]

        # Return preview without saving (client will update UI then save)
        return jsonify({
            "success": True,
            "action": "merge",
            "merged_segment": merged,
            "merged_indices": [first_idx, second_idx],
            "keep_index": first_idx,
            "remove_index": second_idx
        })

    except Exception as e:
        app.logger.error(f"Merge fix error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/job/<job_id>/fix/extend", methods=["POST"])
def fix_extend_segment(job_id: str):
    """
    Extend segment timing to reduce CPS.

    When a segment has high CPS but there's a gap before/after,
    we can extend timing into the gap.

    Request body:
    {
        "segment_index": 5,
        "direction": "start" | "end",  // extend start earlier or end later
        "amount_ms": 500  // milliseconds to extend (default 500)
    }

    Returns updated segment timing.
    """
    try:
        data = request.get_json()
        segment_index = data.get("segment_index")
        direction = data.get("direction", "end")
        amount_ms = data.get("amount_ms", 500)

        if segment_index is None:
            return jsonify({"error": "segment_index required"}), 400

        # Load current translation
        bucket = get_bucket()
        job_prefix = f"{GCS_PREFIX}/{job_id}"

        translation_patterns = [
            f"{job_prefix}/{job_id}_DRAFT.json",
            f"{job_prefix}/{job_id}_APPROVED.json",
            f"{job_prefix}/approved.json",
            f"{job_prefix}/{job_id}_TRANSLATED.json",
        ]

        translation_data = None
        for pattern in translation_patterns:
            translation_data = _load_json_from_gcs(bucket, pattern)
            if translation_data:
                break

        if not translation_data:
            return jsonify({"error": "Translation not found"}), 404

        segments = translation_data.get("segments", [])

        if segment_index < 0 or segment_index >= len(segments):
            return jsonify({"error": "Invalid segment index"}), 400

        segment = segments[segment_index]

        # Parse current timing
        def parse_time(time_str):
            """Parse MM:SS.mmm or HH:MM:SS.mmm to milliseconds"""
            if not time_str:
                return 0
            parts = time_str.replace(',', '.').split(':')
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600000 + int(m) * 60000 + int(float(s) * 1000)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60000 + int(float(s) * 1000)
            else:
                return int(float(parts[0]) * 1000)

        def format_time(ms):
            """Format milliseconds to MM:SS.mmm"""
            total_sec = ms / 1000
            minutes = int(total_sec // 60)
            seconds = total_sec % 60
            return f"{minutes}:{seconds:06.3f}"

        start_ms = parse_time(segment.get("start", "0:00"))
        end_ms = parse_time(segment.get("end", "0:00"))

        # Check for adjacent segment constraints
        if direction == "start":
            # Check previous segment
            if segment_index > 0:
                prev_end = parse_time(segments[segment_index - 1].get("end", "0:00"))
                min_start = prev_end + 83  # 2 frames minimum gap
            else:
                min_start = 0

            new_start = max(min_start, start_ms - amount_ms)
            new_end = end_ms
        else:
            # Check next segment
            if segment_index < len(segments) - 1:
                next_start = parse_time(segments[segment_index + 1].get("start", "0:00"))
                max_end = next_start - 83  # 2 frames minimum gap
            else:
                max_end = end_ms + amount_ms + 10000  # No constraint, allow extension

            new_start = start_ms
            new_end = min(max_end, end_ms + amount_ms)

        # Calculate new CPS
        duration_sec = (new_end - new_start) / 1000
        text_len = len(segment.get("text", ""))
        new_cps = text_len / duration_sec if duration_sec > 0 else 999

        return jsonify({
            "success": True,
            "action": "extend",
            "segment_index": segment_index,
            "original_start": segment.get("start"),
            "original_end": segment.get("end"),
            "new_start": format_time(new_start),
            "new_end": format_time(new_end),
            "new_cps": round(new_cps, 1),
            "extension_ms": amount_ms
        })

    except Exception as e:
        app.logger.error(f"Extend fix error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/job/<job_id>/fix/shorten", methods=["POST"])
def fix_shorten_text(job_id: str):
    """
    Get AI suggestion to shorten text to reduce CPS.

    Request body:
    {
        "segment_index": 5,
        "target_cps": 20  // target CPS (default 20)
    }

    Returns AI suggestion for shorter text.
    """
    from ai_assistant import get_ai_assistant

    try:
        data = request.get_json()
        segment_index = data.get("segment_index")
        target_cps = data.get("target_cps", 20)

        if segment_index is None:
            return jsonify({"error": "segment_index required"}), 400

        # Load segments
        bucket = get_bucket()
        job_prefix = f"{GCS_PREFIX}/{job_id}"

        # Load translation
        translation_patterns = [
            f"{job_prefix}/{job_id}_DRAFT.json",
            f"{job_prefix}/{job_id}_APPROVED.json",
            f"{job_prefix}/approved.json",
            f"{job_prefix}/{job_id}_TRANSLATED.json",
        ]
        translation_data = None
        for pattern in translation_patterns:
            translation_data = _load_json_from_gcs(bucket, pattern)
            if translation_data:
                break

        # Load skeleton for source text
        skeleton_patterns = [
            f"{job_prefix}/{job_id}_SKELETON.json",
            f"{job_prefix}/skeleton.json",
        ]
        skeleton_data = None
        for pattern in skeleton_patterns:
            skeleton_data = _load_json_from_gcs(bucket, pattern)
            if skeleton_data:
                break

        if not translation_data:
            return jsonify({"error": "Translation not found"}), 404

        segments = translation_data.get("segments", [])
        source_segments = skeleton_data.get("segments", []) if skeleton_data else []

        if segment_index < 0 or segment_index >= len(segments):
            return jsonify({"error": "Invalid segment index"}), 400

        segment = segments[segment_index]
        source_text = source_segments[segment_index].get("text", "") if segment_index < len(source_segments) else ""
        current_text = segment.get("text", "")

        # Calculate duration and target length
        def parse_time(time_str):
            if not time_str:
                return 0
            parts = time_str.replace(',', '.').split(':')
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + float(s)
            else:
                return float(parts[0])

        start_sec = parse_time(segment.get("start", "0:00"))
        end_sec = parse_time(segment.get("end", "0:00"))
        duration = end_sec - start_sec

        target_chars = int(duration * target_cps)
        current_chars = len(current_text)

        # Get target language
        job_meta = _load_json_from_gcs(bucket, f"{job_prefix}/job.json") or {}
        target_language = job_meta.get("target_lang") or job_meta.get("target_language_code", "is")

        assistant = get_ai_assistant()
        if not assistant.is_available():
            return jsonify({
                "available": False,
                "error": "AI assistant not configured"
            }), 503

        # Create prompt for shortening
        lang_names = {
            "is": "Icelandic", "nl": "Dutch", "de": "German",
            "es": "Spanish", "fr": "French", "it": "Italian",
            "pt": "Portuguese", "no": "Norwegian", "sv": "Swedish", "da": "Danish"
        }
        lang_name = lang_names.get(target_language, target_language)

        prompt = f"""You are a subtitle editor. The following subtitle is too long for its duration.

Original English: {source_text}
Current {lang_name} translation: {current_text}

Current length: {current_chars} characters
Target length: {target_chars} characters (max)
Duration: {duration:.1f} seconds
Target CPS: {target_cps}

Please provide a shorter {lang_name} translation that:
1. Fits within {target_chars} characters
2. Preserves the essential meaning
3. Sounds natural in {lang_name}
4. Uses common short words over long formal ones

Respond in this exact format:
SHORTENED: [your shortened translation]
REASONING: [brief explanation of what you shortened]"""

        response = assistant.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )

        content = response.content[0].text
        shortened = assistant._extract_field(content, "SHORTENED")
        reasoning = assistant._extract_field(content, "REASONING")

        new_cps = len(shortened) / duration if duration > 0 else 0

        return jsonify({
            "success": True,
            "action": "shorten",
            "segment_index": segment_index,
            "original_text": current_text,
            "shortened_text": shortened,
            "reasoning": reasoning,
            "original_chars": current_chars,
            "new_chars": len(shortened),
            "target_chars": target_chars,
            "new_cps": round(new_cps, 1),
            "target_cps": target_cps
        })

    except Exception as e:
        app.logger.error(f"Shorten fix error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Correction Learning Routes
# =============================================================================

@app.route("/api/corrections/stats")
def corrections_stats():
    """
    Get correction statistics for all jobs.

    Query params:
    - lang: Filter by language code (optional)

    Returns:
    {
        "total_jobs": 25,
        "total_corrections": 150,
        "avg_correction_rate": 0.12,
        "by_type": {"terminology": 50, "style": 80, ...},
        "by_language": {"is": {...}, "nl": {...}},
        "top_term_candidates": [...]
    }
    """
    from correction_capture import get_correction_stats

    try:
        lang = request.args.get("lang")
        stats = get_correction_stats(bucket=get_bucket(), lang=lang)
        return jsonify(stats)

    except Exception as e:
        app.logger.error(f"Correction stats error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/corrections/<lang>/termbook")
def get_termbook_candidates(lang: str):
    """
    Get termbook candidates for a language.

    These are terminology corrections that have been made consistently
    across multiple jobs, indicating potential termbook entries.

    Returns:
    {
        "language": "is",
        "total_candidates": 15,
        "candidates": [
            {
                "wrong_term": "halló",
                "correct_term": "sæl",
                "occurrences": 5,
                "confidence": 0.85,
                "contexts": ["Hello...", "...]
            }
        ]
    }
    """
    try:
        bucket = get_bucket()
        termbook_path = f"corrections/{lang}/termbook_candidates.json"
        termbook_blob = bucket.blob(termbook_path)

        if not termbook_blob.exists():
            return jsonify({
                "language": lang,
                "total_candidates": 0,
                "candidates": [],
                "message": "No termbook candidates yet - corrections will be captured when reviewers approve jobs"
            })

        data = json.loads(termbook_blob.download_as_text())
        return jsonify(data)

    except Exception as e:
        app.logger.error(f"Termbook fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/corrections/<job_id>")
def get_job_corrections(job_id: str):
    """
    Get corrections captured for a specific job.

    Returns:
    {
        "job_id": "...",
        "corrected_count": 12,
        "correction_rate": 0.08,
        "corrections": [...],
        "term_candidates": [...]
    }
    """
    try:
        bucket = get_bucket()

        # Try to find the job's language by checking common patterns
        job_meta = _load_json_from_gcs(bucket, f"{GCS_PREFIX}/{job_id}/job.json")
        lang = "unknown"
        if job_meta:
            lang = job_meta.get("target_lang") or job_meta.get("target_language_code", "unknown")

        # Try to load corrections (check common languages if unknown)
        langs_to_try = [lang] if lang != "unknown" else ["is", "nl", "es", "de", "fr", "pt", "it"]

        for try_lang in langs_to_try:
            corrections_path = f"corrections/{try_lang}/{job_id}_corrections.json"
            corrections_blob = bucket.blob(corrections_path)

            if corrections_blob.exists():
                data = json.loads(corrections_blob.download_as_text())
                return jsonify(data)

        return jsonify({
            "job_id": job_id,
            "message": "No corrections found - job may not have been reviewed yet"
        }), 404

    except Exception as e:
        app.logger.error(f"Job corrections fetch error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Termbook Routes
# =============================================================================

@app.route("/api/termbook/<lang>")
def get_termbook(lang: str):
    """
    Get merged terminology for a language.

    Query params:
    - ministry: Ministry slug for ministry-specific terms (optional)

    Returns merged termbook from corrections + global + ministry.
    """
    from termbook import get_merged_termbook, list_termbooks

    try:
        ministry = request.args.get("ministry")
        terms = get_merged_termbook(lang, ministry=ministry, bucket=get_bucket())

        return jsonify({
            "language": lang,
            "ministry": ministry or "global",
            "term_count": len(terms),
            "terms": terms
        })

    except Exception as e:
        app.logger.error(f"Termbook fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/termbook/<lang>/<ministry>")
def get_ministry_termbook(lang: str, ministry: str):
    """
    Get terminology for a specific language/ministry.

    Returns only that specific termbook (not merged).
    """
    from termbook import load_termbook

    try:
        termbook = load_termbook(lang, ministry, bucket=get_bucket())

        if termbook is None:
            return jsonify({
                "language": lang,
                "ministry": ministry,
                "entries": [],
                "message": "Termbook not found - create one by adding terms"
            }), 404

        return jsonify({
            "language": lang,
            "ministry": ministry,
            "updated_at": termbook.updated_at,
            "entry_count": len(termbook.entries),
            "entries": [
                {
                    "source_term": e.source_term,
                    "target_term": e.target_term,
                    "source": e.source,
                    "confidence": e.confidence,
                    "notes": e.notes,
                    "examples": e.examples
                }
                for e in termbook.entries.values()
            ]
        })

    except Exception as e:
        app.logger.error(f"Termbook fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/termbook/<lang>/term", methods=["POST"])
def add_termbook_term(lang: str):
    """
    Add or update a term in the termbook.

    Request body:
    {
        "source_term": "Anointing",
        "target_term": "Smurning",
        "ministry": "global",  // optional, default 'global'
        "notes": "Religious context",  // optional
        "examples": ["The anointing of the Holy Spirit"]  // optional
    }
    """
    from termbook import add_term

    try:
        data = request.get_json()
        source_term = data.get("source_term", "").strip()
        target_term = data.get("target_term", "").strip()
        ministry = data.get("ministry", "global")
        notes = data.get("notes")
        examples = data.get("examples", [])

        if not source_term or not target_term:
            return jsonify({"error": "source_term and target_term are required"}), 400

        success = add_term(
            lang=lang,
            source_term=source_term,
            target_term=target_term,
            ministry=ministry,
            source="manual",
            notes=notes,
            examples=examples,
            bucket=get_bucket()
        )

        if success:
            return jsonify({
                "success": True,
                "message": f"Term '{source_term}' -> '{target_term}' added to {lang}/{ministry}"
            })

        return jsonify({"error": "Failed to save term"}), 500

    except Exception as e:
        app.logger.error(f"Add term error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/termbook/<lang>/term", methods=["DELETE"])
def delete_termbook_term(lang: str):
    """
    Remove a term from the termbook.

    Request body:
    {
        "source_term": "Anointing",
        "ministry": "global"  // optional
    }
    """
    from termbook import remove_term

    try:
        data = request.get_json()
        source_term = data.get("source_term", "").strip()
        ministry = data.get("ministry", "global")

        if not source_term:
            return jsonify({"error": "source_term is required"}), 400

        success = remove_term(
            lang=lang,
            source_term=source_term,
            ministry=ministry,
            bucket=get_bucket()
        )

        if success:
            return jsonify({
                "success": True,
                "message": f"Term '{source_term}' removed from {lang}/{ministry}"
            })

        return jsonify({"error": "Term not found"}), 404

    except Exception as e:
        app.logger.error(f"Delete term error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/termbooks")
def list_all_termbooks():
    """
    List all available termbooks by language.

    Returns:
    {
        "is": ["global", "benny_hinn", "in_touch"],
        "nl": ["global"],
        ...
    }
    """
    from termbook import list_termbooks

    try:
        termbooks = list_termbooks(bucket=get_bucket())
        return jsonify(termbooks)

    except Exception as e:
        app.logger.error(f"List termbooks error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/termbook/<lang>/import-corrections", methods=["POST"])
def import_corrections(lang: str):
    """
    Import high-confidence corrections into the termbook.

    This promotes auto-learned terminology from reviewer corrections
    into the termbook for use in future translations.

    Query params:
    - min_confidence: Minimum confidence threshold (default: 0.8)
    """
    from termbook import import_corrections_to_termbook

    try:
        min_confidence = float(request.args.get("min_confidence", 0.8))
        count = import_corrections_to_termbook(lang, min_confidence, bucket=get_bucket())

        return jsonify({
            "success": True,
            "imported_count": count,
            "message": f"Imported {count} terms from corrections for {lang}"
        })

    except Exception as e:
        app.logger.error(f"Import corrections error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    debug = os.environ.get("OMEGA_DEV_MODE") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
