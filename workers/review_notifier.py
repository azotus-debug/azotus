"""
Review Notification System
==========================
Sends magic link emails to reviewers when translations are ready.
"""

import hashlib
import time
import os
import logging
from pathlib import Path
from typing import Optional

# Load .env file to ensure secrets are available
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not installed, rely on environment

logger = logging.getLogger(__name__)

# Configuration
REVIEW_PORTAL_URL = os.environ.get(
    "OMEGA_REVIEW_PORTAL_URL",
    "https://omega-review-283123700702.us-central1.run.app"
)
SECRET_KEY = os.environ.get("OMEGA_REVIEW_SECRET", "omega-review-secret-2024")
TOKEN_EXPIRY_HOURS = 72


def generate_review_token(job_id: str, expiry_hours: int = TOKEN_EXPIRY_HOURS) -> tuple[str, int]:
    """
    Generate a secure review token for a job.
    
    Returns:
        Tuple of (token, expiry_timestamp)
    """
    expiry_ts = int(time.time()) + (expiry_hours * 3600)
    payload = f"{job_id}:{expiry_ts}:{SECRET_KEY}"
    token = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return token, expiry_ts


def build_review_url(job_id: str, expiry_hours: int = TOKEN_EXPIRY_HOURS) -> str:
    """
    Build a magic link URL for reviewing a job.
    
    Example:
        https://omega-review.../review/I2252_Gospel?token=abc123&exp=1234567890
    """
    token, expiry_ts = generate_review_token(job_id, expiry_hours)
    return f"{REVIEW_PORTAL_URL}/review/{job_id}?token={token}&exp={expiry_ts}"


def send_review_notification(
    job_id: str,
    program_name: str,
    target_language: str,
    reviewer_email: str,
    quality_rating: Optional[float] = None
) -> bool:
    """
    Send email notification to reviewer with magic link.
    
    Args:
        job_id: The job identifier
        program_name: Human-readable program name
        target_language: Target language (e.g., "Polish")
        reviewer_email: Email address of reviewer
        quality_rating: Optional AI quality rating
    
    Returns:
        True if email sent successfully
    """
    try:
        from email_utils import send_email
    except ImportError:
        logger.error("   ❌ email_utils not available")
        return False
    
    review_url = build_review_url(job_id)
    
    # Build email content
    subject = f"🎬 Translation Ready for Review: {program_name} ({target_language})"
    
    quality_section = ""
    if quality_rating:
        quality_section = f"""
        <p style="background: #f0f9ff; padding: 10px; border-radius: 6px;">
            <strong>AI Quality Rating:</strong> {quality_rating}/10
        </p>
        """
    
    body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #333; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1a1d29, #252836); padding: 30px; border-radius: 12px 12px 0 0;">
            <h1 style="color: #f5a623; margin: 0;">🎬 Omega Review</h1>
        </div>
        
        <div style="background: #fff; padding: 30px; border: 1px solid #e5e7eb;">
            <h2 style="margin-top: 0;">Translation Ready for Review</h2>
            
            <table style="width: 100%; margin: 20px 0;">
                <tr>
                    <td style="padding: 8px 0; color: #6b7280;">Program:</td>
                    <td style="padding: 8px 0; font-weight: 600;">{program_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; color: #6b7280;">Language:</td>
                    <td style="padding: 8px 0; font-weight: 600;">{target_language}</td>
                </tr>
            </table>
            
            {quality_section}
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{review_url}" 
                   style="display: inline-block; background: #50e3c2; color: #000; padding: 14px 28px; 
                          text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                    Start Review →
                </a>
            </div>
            
            <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                This link will expire in {TOKEN_EXPIRY_HOURS} hours.
            </p>
        </div>
        
        <div style="background: #f9fafb; padding: 20px; border-radius: 0 0 12px 12px; text-align: center;">
            <p style="color: #9ca3af; font-size: 12px; margin: 0;">
                Omega TV Subtitle System
            </p>
        </div>
    </body>
    </html>
    """
    
    try:
        send_email(
            to_email=reviewer_email,
            subject=subject,
            body=body,
            html=True
        )
        logger.info(f"   📧 Review notification sent to {reviewer_email}")
        return True
    except Exception as e:
        logger.error(f"   ❌ Failed to send review notification: {e}")
        return False


def get_reviewer_for_language(language: str) -> Optional[str]:
    """
    Get the reviewer email for a specific language.
    
    This can be customized to look up reviewers from a database or config.
    For now, uses environment variables.
    """
    # Check for language-specific reviewer
    env_key = f"OMEGA_REVIEWER_{language.upper()}"
    reviewer = os.environ.get(env_key)
    
    if reviewer:
        return reviewer
    
    # Fall back to default reviewer
    return os.environ.get("OMEGA_REVIEWER_EMAIL")
