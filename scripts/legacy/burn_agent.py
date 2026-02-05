#!/usr/bin/env python3
"""
Omega Professional Burn Agent v2
================================

A rock-solid, production-grade burn agent that runs 24/7 without babysitting.

Features:
- Retry logic with exponential backoff
- Heartbeat every 30 seconds to cloud manager
- Graceful shutdown on SIGTERM/SIGINT
- Automatic disk cleanup after successful burn
- Disk space check before claiming jobs
- Structured logging with per-job context
- Statistics tracking

Usage:
    python burn_agent.py [--config CONFIG_FILE]

Environment Variables:
    OMEGA_CLOUD_MANAGER_URL     - Cloud Manager URL (required)
    OMEGA_CLOUD_MANAGER_TOKEN   - Auth token
    OMEGA_BURN_AGENT_ID         - Agent identifier (default: hostname)
    OMEGA_BURN_POLL_SECONDS     - Poll interval (default: 10)
    OMEGA_BURN_MODE             - "publisher" or "basic" (default: publisher)
    OMEGA_BURN_OUTPUT_DIR       - Output directory for burned videos
    OMEGA_BURN_CACHE_DIR        - Cache directory for downloads
    OMEGA_BURN_MIN_DISK_GB      - Minimum disk space required (default: 10)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from google.cloud import storage

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

import config  # noqa: E402

try:
    from workers import publisher  # noqa: E402
except Exception:
    publisher = None


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class BurnAgentConfig:
    """Configuration for the burn agent."""
    manager_url: str
    token: str = ""
    agent_id: str = ""
    
    poll_seconds: float = 10.0
    heartbeat_seconds: float = 30.0
    max_retries: int = 3
    retry_base_delay: float = 2.0  # Exponential backoff base
    request_timeout: int = 60
    
    min_disk_gb: float = 10.0
    max_cache_age_hours: float = 24.0
    cleanup_on_success: bool = True
    
    cache_dir: Path = field(default_factory=lambda: Path(config.DELIVERY_DIR) / "BURN_CACHE")
    output_dir: Path = field(default_factory=lambda: Path(config.DELIVERY_DIR) / "VIDEO")
    
    burn_mode: str = "publisher"  # "publisher" or "basic"
    allow_fallback: bool = True
    
    log_level: str = "INFO"
    log_json: bool = False
    
    def __post_init__(self):
        if not self.agent_id:
            self.agent_id = socket.gethostname() or "burn-agent"
        if isinstance(self.cache_dir, str):
            self.cache_dir = Path(self.cache_dir)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
    
    @classmethod
    def from_env(cls) -> BurnAgentConfig:
        """Load configuration from environment variables."""
        return cls(
            manager_url=(os.environ.get("OMEGA_CLOUD_MANAGER_URL") or "").rstrip("/"),
            token=(os.environ.get("OMEGA_CLOUD_MANAGER_TOKEN") or 
                   os.environ.get("OMEGA_BURN_AGENT_TOKEN") or ""),
            agent_id=(os.environ.get("OMEGA_BURN_AGENT_ID") or ""),
            poll_seconds=float(os.environ.get("OMEGA_BURN_POLL_SECONDS", "10")),
            heartbeat_seconds=float(os.environ.get("OMEGA_BURN_HEARTBEAT_SECONDS", "30")),
            max_retries=int(os.environ.get("OMEGA_BURN_MAX_RETRIES", "3")),
            min_disk_gb=float(os.environ.get("OMEGA_BURN_MIN_DISK_GB", "10")),
            cache_dir=Path(os.environ.get("OMEGA_BURN_CACHE_DIR") or 
                          (Path(config.DELIVERY_DIR) / "BURN_CACHE")),
            output_dir=Path(os.environ.get("OMEGA_BURN_OUTPUT_DIR") or 
                           (Path(config.DELIVERY_DIR) / "VIDEO")),
            burn_mode=os.environ.get("OMEGA_BURN_MODE", "publisher"),
            allow_fallback=os.environ.get("OMEGA_BURN_ALLOW_FALLBACK", "1").lower() in {"1", "true", "yes"},
            cleanup_on_success=os.environ.get("OMEGA_BURN_CLEANUP", "1").lower() in {"1", "true", "yes"},
            log_level=os.environ.get("OMEGA_BURN_LOG_LEVEL", "INFO"),
            log_json=os.environ.get("OMEGA_BURN_LOG_JSON", "0").lower() in {"1", "true", "yes"},
        )
    
    @classmethod
    def from_file(cls, path: Path) -> BurnAgentConfig:
        """Load configuration from YAML or JSON file."""
        import yaml  # Optional dependency
        
        with open(path) as f:
            if path.suffix in {".yaml", ".yml"}:
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
        
        # Expand environment variables in string values
        for key, value in data.items():
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                data[key] = os.environ.get(env_var, "")
        
        return cls(**data)
    
    def validate(self) -> None:
        """Validate configuration. Raises ValueError if invalid."""
        errors = []
        
        if not self.manager_url:
            errors.append("manager_url is required (set OMEGA_CLOUD_MANAGER_URL)")
        elif not self.manager_url.startswith(("http://", "https://")):
            errors.append(f"manager_url must be http:// or https://: {self.manager_url}")
        
        if self.poll_seconds < 1:
            errors.append(f"poll_seconds must be >= 1: {self.poll_seconds}")
        
        if self.min_disk_gb < 1:
            errors.append(f"min_disk_gb must be >= 1: {self.min_disk_gb}")
        
        if self.burn_mode not in {"publisher", "basic"}:
            errors.append(f"burn_mode must be 'publisher' or 'basic': {self.burn_mode}")
        
        if errors:
            raise ValueError("Configuration errors:\n  - " + "\n  - ".join(errors))


@dataclass
class AgentStats:
    """Statistics for the burn agent."""
    jobs_claimed: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    bytes_downloaded: int = 0
    bytes_uploaded: int = 0
    total_burn_time_seconds: float = 0.0
    last_job_at: Optional[datetime] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "jobs_claimed": self.jobs_claimed,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "success_rate": round(self.jobs_completed / max(1, self.jobs_claimed) * 100, 1),
            "avg_burn_time_seconds": round(self.total_burn_time_seconds / max(1, self.jobs_completed), 1),
            "uptime_seconds": (datetime.utcnow() - self.started_at).total_seconds(),
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_uploaded": self.bytes_uploaded,
        }


# =============================================================================
# Logging
# =============================================================================

class ContextLogger:
    """Logger with job context support."""
    
    def __init__(self, name: str, level: str = "INFO", use_json: bool = False):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper()))
        self.use_json = use_json
        self._context: Dict[str, Any] = {}
        
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            if use_json:
                handler.setFormatter(logging.Formatter("%(message)s"))
            else:
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s [%(name)s] %(message)s"
                ))
            self.logger.addHandler(handler)
    
    def set_context(self, **kwargs):
        self._context.update(kwargs)
    
    def clear_context(self):
        self._context.clear()
    
    def _log(self, level: str, msg: str, **kwargs):
        if self.use_json:
            data = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": level,
                "message": msg,
                **self._context,
                **kwargs,
            }
            self.logger.log(getattr(logging, level), json.dumps(data))
        else:
            context = " ".join(f"{k}={v}" for k, v in {**self._context, **kwargs}.items())
            full_msg = f"{msg} | {context}" if context else msg
            self.logger.log(getattr(logging, level), full_msg)
    
    def info(self, msg: str, **kwargs): self._log("INFO", msg, **kwargs)
    def warning(self, msg: str, **kwargs): self._log("WARNING", msg, **kwargs)
    def error(self, msg: str, **kwargs): self._log("ERROR", msg, **kwargs)
    def debug(self, msg: str, **kwargs): self._log("DEBUG", msg, **kwargs)


# =============================================================================
# Download Utilities
# =============================================================================

def parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """Parse gs:// URI into (bucket, blob_name)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a gs:// URI: {uri}")
    parts = uri[5:].split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid gs:// URI: {uri}")
    return parts[0], parts[1]


def download_with_retry(
    client: storage.Client,
    uri: str,
    dest_path: Path,
    max_retries: int = 3,
    base_delay: float = 2.0,
    logger: Optional[ContextLogger] = None,
) -> int:
    """
    Download a file with exponential backoff retry.
    
    Returns the number of bytes downloaded.
    """
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            if uri.startswith("gs://"):
                bucket_name, blob_name = parse_gcs_uri(uri)
                blob = client.bucket(bucket_name).blob(blob_name)
                blob.download_to_filename(str(dest_path))
                return dest_path.stat().st_size
            
            elif uri.startswith(("http://", "https://")):
                with requests.get(uri, stream=True, timeout=600) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                return dest_path.stat().st_size
            
            else:
                raise ValueError(f"Unsupported URI scheme: {uri}")
                
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                if logger:
                    logger.warning(f"Download failed, retrying in {delay}s", 
                                   attempt=attempt + 1, error=str(e))
                time.sleep(delay)
            else:
                raise
    
    raise last_error


def upload_with_retry(
    client: storage.Client,
    local_path: Path,
    bucket_name: str,
    blob_name: str,
    max_retries: int = 3,
    base_delay: float = 2.0,
    logger: Optional[ContextLogger] = None,
) -> str:
    """Upload a file with exponential backoff retry. Returns gs:// URI."""
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            client.bucket(bucket_name).blob(blob_name).upload_from_filename(str(local_path))
            return f"gs://{bucket_name}/{blob_name}"
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                if logger:
                    logger.warning(f"Upload failed, retrying in {delay}s",
                                   attempt=attempt + 1, error=str(e))
                time.sleep(delay)
            else:
                raise
    
    raise last_error


# =============================================================================
# Disk Management
# =============================================================================

def get_free_disk_gb(path: Path) -> float:
    """Get free disk space in GB for the given path."""
    stat = shutil.disk_usage(path if path.exists() else path.parent)
    return stat.free / (1024 ** 3)


def cleanup_old_cache_files(cache_dir: Path, max_age_hours: float, logger: ContextLogger) -> int:
    """Remove cache files older than max_age_hours. Returns count of files removed."""
    if not cache_dir.exists():
        return 0
    
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    removed = 0
    
    for path in cache_dir.iterdir():
        if path.is_file():
            mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
            if mtime < cutoff:
                try:
                    path.unlink()
                    removed += 1
                    logger.debug(f"Cleaned up old cache file: {path.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove cache file: {path.name}", error=str(e))
    
    return removed


# =============================================================================
# Burn Agent
# =============================================================================

class BurnAgent:
    """Professional-grade burn agent."""
    
    VERSION = "2.0.0"
    
    def __init__(self, cfg: BurnAgentConfig):
        self.cfg = cfg
        self.stats = AgentStats()
        self.logger = ContextLogger(
            "OmegaBurnAgent",
            level=cfg.log_level,
            use_json=cfg.log_json,
        )
        
        self._shutdown_requested = False
        self._current_job: Optional[Dict] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._storage_client: Optional[storage.Client] = None
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal gracefully."""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self._shutdown_requested = True
    
    @property
    def storage_client(self) -> storage.Client:
        if self._storage_client is None:
            self._storage_client = storage.Client()
        return self._storage_client
    
    def _auth_headers(self) -> Dict[str, str]:
        if not self.cfg.token:
            return {}
        return {"Authorization": f"Bearer {self.cfg.token}"}
    
    # -------------------------------------------------------------------------
    # API Calls
    # -------------------------------------------------------------------------
    
    def _claim_job(self) -> Optional[Dict]:
        """Claim a job from the cloud manager."""
        resp = requests.post(
            f"{self.cfg.manager_url}/api/burn/claim",
            headers=self._auth_headers(),
            json={"agent_id": self.cfg.agent_id},
            timeout=self.cfg.request_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("job")
    
    def _report_complete(self, stem: str, output_gcs_uri: Optional[str], output_path: Path) -> None:
        """Report job completion to cloud manager."""
        resp = requests.post(
            f"{self.cfg.manager_url}/api/burn/complete",
            headers=self._auth_headers(),
            json={
                "file_stem": stem,
                "agent_id": self.cfg.agent_id,
                "output_gcs_uri": output_gcs_uri,
                "output_path": str(output_path),
            },
            timeout=self.cfg.request_timeout,
        )
        resp.raise_for_status()
    
    def _report_fail(self, stem: str, error: str) -> None:
        """Report job failure to cloud manager."""
        try:
            resp = requests.post(
                f"{self.cfg.manager_url}/api/burn/fail",
                headers=self._auth_headers(),
                json={
                    "file_stem": stem,
                    "agent_id": self.cfg.agent_id,
                    "error": error,
                },
                timeout=self.cfg.request_timeout,
            )
            resp.raise_for_status()
        except Exception as e:
            self.logger.error(f"Failed to report failure to manager", error=str(e))
    
    def _send_heartbeat(self) -> None:
        """Send heartbeat to cloud manager."""
        try:
            resp = requests.post(
                f"{self.cfg.manager_url}/api/burn/heartbeat",
                headers=self._auth_headers(),
                json={
                    "agent_id": self.cfg.agent_id,
                    "version": self.VERSION,
                    "current_job": self._current_job.get("file_stem") if self._current_job else None,
                    "stats": self.stats.to_dict(),
                },
                timeout=10,
            )
            # Ignore 404 if endpoint doesn't exist yet
            if resp.status_code not in {200, 404}:
                resp.raise_for_status()
        except Exception as e:
            self.logger.debug(f"Heartbeat failed", error=str(e))
    
    # -------------------------------------------------------------------------
    # Burn Logic
    # -------------------------------------------------------------------------
    
    def _burn_with_publisher(self, video_path: Path, srt_path: Path, job: Dict) -> Path:
        """Burn using the publisher module (Omega styles)."""
        if not publisher:
            raise RuntimeError("Publisher module not available")
        
        style = job.get("subtitle_style") or "Classic"
        delivery_profile = job.get("delivery_profile")
        
        output = publisher.publish(video_path, srt_path, 
                                   subtitle_style=style,
                                   delivery_profile=delivery_profile)
        return Path(output)
    
    def _burn_with_ffmpeg(self, video_path: Path, srt_path: Path, output_path: Path, job: Dict) -> Path:
        """
        Burn using FFmpeg with delivery profile support.
        
        Supports professional broadcast formats (Netflix, YouTube, TV, etc.)
        via the delivery_profile field in the job.
        """
        from delivery_profiles import get_delivery_profile, DeliveryProfile
        
        # Get delivery profile from job or config
        profile_name = (
            job.get("delivery_profile") or 
            os.environ.get("OMEGA_BURN_DELIVERY_PROFILE") or 
            "source"
        )
        
        profile = get_delivery_profile(profile_name)
        self.logger.info(
            f"Using delivery profile",
            profile=profile_name,
            resolution=profile.resolution or "source",
            codec=profile.video_codec,
        )
        
        # Build FFmpeg command
        cmd = profile.build_ffmpeg_args(
            str(video_path),
            str(srt_path),
            str(output_path),
        )
        
        # Replace 'ffmpeg' with configured path
        cmd[0] = config.FFMPEG_BIN
        
        self.logger.info(f"Running FFmpeg burn", profile=profile_name)
        self.logger.debug(f"FFmpeg command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            # Extract useful error info
            stderr_tail = result.stderr[-1000:] if result.stderr else "No stderr"
            raise RuntimeError(
                f"FFmpeg failed (exit {result.returncode}):\n{stderr_tail}"
            )
        
        return output_path
    
    def _burn_job(self, job: Dict) -> Tuple[Path, int, int]:
        """
        Burn a job.
        
        Returns:
            (output_path, bytes_downloaded, bytes_uploaded)
        """
        stem = job.get("file_stem") or "unknown"
        video_uri = job.get("video_gcs_uri")
        srt_uri = job.get("srt_gcs_uri")
        
        if not video_uri or not srt_uri:
            raise RuntimeError("Job missing video_gcs_uri or srt_gcs_uri")
        
        # Ensure directories exist
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Download files
        video_path = self.cfg.cache_dir / Path(video_uri.split("/")[-1]).name
        srt_path = self.cfg.cache_dir / Path(srt_uri.split("/")[-1]).name
        
        self.logger.info(f"Downloading video", uri=video_uri)
        bytes_video = download_with_retry(
            self.storage_client, video_uri, video_path,
            max_retries=self.cfg.max_retries,
            base_delay=self.cfg.retry_base_delay,
            logger=self.logger,
        )
        
        self.logger.info(f"Downloading SRT", uri=srt_uri)
        bytes_srt = download_with_retry(
            self.storage_client, srt_uri, srt_path,
            max_retries=self.cfg.max_retries,
            base_delay=self.cfg.retry_base_delay,
            logger=self.logger,
        )
        
        bytes_downloaded = bytes_video + bytes_srt
        
        # Burn
        output_path = None
        if self.cfg.burn_mode == "publisher":
            try:
                output_path = self._burn_with_publisher(video_path, srt_path, job)
            except Exception as e:
                if self.cfg.allow_fallback:
                    self.logger.warning(f"Publisher failed, falling back to FFmpeg", error=str(e))
                else:
                    raise
        
        if output_path is None:
            output_path = self.cfg.output_dir / f"{stem}_SUBBED.mp4"
            output_path = self._burn_with_ffmpeg(video_path, srt_path, output_path, job)
        
        # Upload if configured
        bytes_uploaded = 0
        delivery_bucket = os.environ.get("OMEGA_DELIVERY_BUCKET", "").strip()
        if delivery_bucket:
            prefix = os.environ.get("OMEGA_DELIVERY_PREFIX", "deliveries").strip("/")
            blob_name = f"{prefix}/{output_path.name}" if prefix else output_path.name
            
            self.logger.info(f"Uploading output", bucket=delivery_bucket, blob=blob_name)
            upload_with_retry(
                self.storage_client, output_path, delivery_bucket, blob_name,
                max_retries=self.cfg.max_retries,
                base_delay=self.cfg.retry_base_delay,
                logger=self.logger,
            )
            bytes_uploaded = output_path.stat().st_size
        
        # Cleanup cache
        if self.cfg.cleanup_on_success:
            for path in [video_path, srt_path]:
                try:
                    path.unlink()
                    self.logger.debug(f"Cleaned up cache file", path=str(path))
                except Exception:
                    pass
        
        return output_path, bytes_downloaded, bytes_uploaded
    
    # -------------------------------------------------------------------------
    # Main Loop
    # -------------------------------------------------------------------------
    
    def _heartbeat_loop(self):
        """Background thread for sending heartbeats."""
        while not self._shutdown_requested:
            self._send_heartbeat()
            time.sleep(self.cfg.heartbeat_seconds)
    
    def _start_heartbeat_thread(self):
        """Start the heartbeat background thread."""
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
    
    def run_once(self, dry_run: bool = False) -> bool:
        """Run a single job. Returns True if a job was processed."""
        # Check disk space
        free_gb = get_free_disk_gb(self.cfg.output_dir)
        if free_gb < self.cfg.min_disk_gb:
            self.logger.warning(
                f"Low disk space, skipping claim",
                free_gb=round(free_gb, 1),
                required_gb=self.cfg.min_disk_gb,
            )
            return False
        
        # Claim job
        job = self._claim_job()
        if not job:
            return False
        
        stem = job.get("file_stem") or "unknown"
        self._current_job = job
        self.stats.jobs_claimed += 1
        self.logger.set_context(job=stem)
        self.logger.info(f"Claimed job")
        
        if dry_run:
            self.logger.info(f"Dry run - skipping actual burn")
            self.logger.clear_context()
            self._current_job = None
            return True
        
        start_time = time.time()
        
        try:
            output_path, bytes_down, bytes_up = self._burn_job(job)
            
            burn_time = time.time() - start_time
            self.stats.jobs_completed += 1
            self.stats.total_burn_time_seconds += burn_time
            self.stats.bytes_downloaded += bytes_down
            self.stats.bytes_uploaded += bytes_up
            self.stats.last_job_at = datetime.utcnow()
            
            # Report success
            output_gcs_uri = None
            if bytes_up > 0:
                bucket = os.environ.get("OMEGA_DELIVERY_BUCKET", "")
                prefix = os.environ.get("OMEGA_DELIVERY_PREFIX", "deliveries").strip("/")
                blob = f"{prefix}/{output_path.name}" if prefix else output_path.name
                output_gcs_uri = f"gs://{bucket}/{blob}"
            
            self._report_complete(stem, output_gcs_uri, output_path)
            
            self.logger.info(
                f"Completed job",
                burn_time_s=round(burn_time, 1),
                output=str(output_path),
            )
            
        except Exception as e:
            self.stats.jobs_failed += 1
            self.logger.error(f"Job failed", error=str(e))
            self._report_fail(stem, str(e))
        
        finally:
            self.logger.clear_context()
            self._current_job = None
        
        return True
    
    def run(self):
        """Run the main loop."""
        self.logger.info(
            f"Starting Omega Burn Agent",
            version=self.VERSION,
            agent_id=self.cfg.agent_id,
            manager_url=self.cfg.manager_url,
            burn_mode=self.cfg.burn_mode,
            poll_seconds=self.cfg.poll_seconds,
        )
        
        # Clean up old cache files on startup
        removed = cleanup_old_cache_files(
            self.cfg.cache_dir,
            self.cfg.max_cache_age_hours,
            self.logger,
        )
        if removed > 0:
            self.logger.info(f"Cleaned up {removed} old cache files")
        
        # Start heartbeat thread
        self._start_heartbeat_thread()
        
        # Main loop
        while not self._shutdown_requested:
            try:
                had_job = self.run_once()
                if not had_job:
                    time.sleep(self.cfg.poll_seconds)
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Network error in main loop", error=str(e))
                time.sleep(self.cfg.poll_seconds * 2)  # Longer backoff on network errors
            except Exception as e:
                self.logger.error(f"Unexpected error in main loop", error=str(e))
                time.sleep(self.cfg.poll_seconds)
        
        self.logger.info(
            f"Shutdown complete",
            **self.stats.to_dict(),
        )


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Omega Professional Burn Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c",
        type=Path,
        help="Path to config file (YAML or JSON)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single job and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Claim a job but don't actually burn (for testing)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate configuration and exit",
    )
    args = parser.parse_args()
    
    # Load configuration
    if args.config and args.config.exists():
        cfg = BurnAgentConfig.from_file(args.config)
    else:
        cfg = BurnAgentConfig.from_env()
    
    # Validate
    try:
        cfg.validate()
    except ValueError as e:
        print(f"Configuration error:\n{e}", file=sys.stderr)
        sys.exit(1)
    
    if args.validate:
        print("Configuration is valid:")
        print(f"  manager_url: {cfg.manager_url}")
        print(f"  agent_id: {cfg.agent_id}")
        print(f"  burn_mode: {cfg.burn_mode}")
        print(f"  output_dir: {cfg.output_dir}")
        sys.exit(0)
    
    # Run
    agent = BurnAgent(cfg)
    
    if args.once:
        agent.run_once(dry_run=args.dry_run)
    else:
        agent.run()


if __name__ == "__main__":
    main()
