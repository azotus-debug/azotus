#!/usr/bin/env python3
"""
Omega Station - Remote Processing Node
======================================
Runs on remote Mac Minis (Iceland, future stations).
Watches INBOX, claims jobs, processes, delivers via FTP.

Usage:
    python omega_station.py --config /path/to/station.json
    python omega_station.py --config /path/to/station.json --db-type sqlite --test-mode

Environment Variables:
    DB_TYPE                     - 'postgres' or 'sqlite' (default: postgres)
    DB_INSTANCE_CONNECTION_NAME - Cloud SQL instance for postgres
    DB_PASS                     - Database password
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import omega_db
from workers.remote_review import generate_proxy

# Configure logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "station.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("OmegaStation")


# =============================================================================
# CONFIGURATION
# =============================================================================

class StationConfig:
    """Load and validate station configuration."""
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._load()
    
    def _load(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"Station config not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            data = json.load(f)
        
        # Required fields
        self.station_id = data.get("station_id")
        if not self.station_id:
            raise ValueError("station_id is required in config")
        
        # Optional with defaults
        self.display_name = data.get("display_name", self.station_id)
        self.tailscale_ip = data.get("tailscale_ip")
        
        # Watch folders
        folders = data.get("watch_folders", {})
        self.inbox_dir = Path(folders.get("INBOX", "./INBOX"))
        self.processing_dir = Path(folders.get("PROCESSING", "./PROCESSING"))
        self.archive_dir = Path(folders.get("ARCHIVE", "./ARCHIVE"))
        self.proxy_dir = Path(folders.get("PROXY", "./PROXY"))
        
        # Delivery config
        self.delivery = data.get("delivery", {"type": "folder", "path": "./OUTBOX"})
        
        # Proxy settings
        proxy = data.get("proxy", {})
        self.proxy_enabled = proxy.get("enabled", True)
        self.proxy_resolution = proxy.get("resolution", "854:480")
        self.proxy_crf = proxy.get("crf", "28")
        
        # Timing
        self.poll_interval = data.get("poll_interval", 10)  # seconds
        self.heartbeat_interval = data.get("heartbeat_interval", 30)  # seconds
        
        # File extensions to watch
        self.extensions = set(data.get("extensions", [
            ".mp4", ".mov", ".mkv", ".mpg", ".mpeg", ".mxf", ".avi"
        ]))
        
        # Store raw config for DB
        self._raw = data
    
    def ensure_directories(self):
        """Create all required directories."""
        for d in [self.inbox_dir, self.processing_dir, self.archive_dir, self.proxy_dir]:
            d.mkdir(parents=True, exist_ok=True)
            logger.info(f"   📁 {d}")


# =============================================================================
# FILE STABILITY CHECK (from omega_manager.py)
# =============================================================================

STABILITY_CHECKS = int(os.environ.get("OMEGA_STABILITY_CHECKS", "3"))
STABILITY_DELAY = float(os.environ.get("OMEGA_STABILITY_DELAY", "1.0"))
MIN_AGE_SECONDS = float(os.environ.get("OMEGA_MIN_AGE", "3.0"))


def is_stable_file(path: Path) -> bool:
    """
    Check if file has stopped growing (safe to ingest).
    Prevents ingesting files that are still being copied.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    
    # Must be at least MIN_AGE_SECONDS old
    if MIN_AGE_SECONDS and (time.time() - stat.st_mtime) < MIN_AGE_SECONDS:
        return False
    
    # Check size stability
    size = stat.st_size
    for _ in range(STABILITY_CHECKS - 1):
        time.sleep(max(0.1, STABILITY_DELAY))
        try:
            if path.stat().st_size != size:
                return False  # Still growing
        except FileNotFoundError:
            return False
    
    return True


# =============================================================================
# DELIVERY HANDLERS
# =============================================================================

def deliver_folder(source: Path, dest_dir: Path, filename: str = None) -> bool:
    """Simple copy to folder (for testing or local delivery)."""
    import shutil
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    dest_name = filename or source.name
    dest_path = dest_dir / dest_name
    
    try:
        shutil.copy2(source, dest_path)
        logger.info(f"   ✅ Delivered to folder: {dest_path}")
        return True
    except Exception as e:
        logger.error(f"   ❌ Folder delivery failed: {e}")
        return False


def deliver_ftp(source: Path, config: dict, filename: str = None) -> bool:
    """Upload to FTP server."""
    import ftplib
    
    host = config.get("host")
    user = config.get("user")
    password = config.get("password", os.environ.get("FTP_PASSWORD", ""))
    remote_dir = config.get("remote_dir", "/")
    
    if not all([host, user, password]):
        logger.error("   ❌ FTP config incomplete (need host, user, password)")
        return False
    
    dest_name = filename or source.name
    
    for attempt in range(3):
        try:
            logger.info(f"   📤 FTP upload attempt {attempt + 1}/3: {dest_name}")
            with ftplib.FTP(host, timeout=30) as ftp:
                ftp.login(user, password)
                ftp.cwd(remote_dir)
                with open(source, "rb") as f:
                    ftp.storbinary(f"STOR {dest_name}", f)
            logger.info(f"   ✅ FTP upload complete: {dest_name}")
            return True
        except Exception as e:
            logger.warning(f"   ⚠️ FTP attempt {attempt + 1} failed: {e}")
            time.sleep(2 ** attempt)
    
    logger.error(f"   ❌ FTP upload failed after 3 attempts")
    return False


def deliver(source: Path, config: dict, stem: str) -> bool:
    """Deliver file using configured method."""
    delivery_type = config.get("type", "folder")
    
    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = source.suffix
    filename = f"{stem}_{timestamp}{ext}"
    
    if delivery_type == "folder":
        return deliver_folder(source, config.get("path", "./OUTBOX"), filename)
    elif delivery_type == "ftp":
        return deliver_ftp(source, config, filename)
    else:
        logger.error(f"   ❌ Unknown delivery type: {delivery_type}")
        return False


# =============================================================================
# OMEGA STATION DAEMON
# =============================================================================

class OmegaStation:
    """
    Remote processing station daemon.
    Watches INBOX, claims jobs, processes, delivers.
    """
    
    def __init__(self, config: StationConfig, test_mode: bool = False):
        self.config = config
        self.test_mode = test_mode
        self.running = False
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.active_jobs = set()
        self._lock = threading.Lock()
    
    def start(self):
        """Start the station daemon."""
        logger.info("=" * 60)
        logger.info(f"🚀 Starting Omega Station: {self.config.display_name}")
        logger.info(f"   Station ID: {self.config.station_id}")
        logger.info(f"   Test Mode: {self.test_mode}")
        logger.info("=" * 60)
        
        # Ensure directories exist
        logger.info("📁 Ensuring directories...")
        self.config.ensure_directories()
        
        # Register with database
        logger.info("📡 Registering with database...")
        omega_db.register_station(
            station_id=self.config.station_id,
            display_name=self.config.display_name,
            tailscale_ip=self.config.tailscale_ip,
            config=self.config._raw
        )
        logger.info(f"   ✅ Registered as: {self.config.station_id}")
        
        # Start heartbeat thread
        self.running = True
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        logger.info(f"   💓 Heartbeat started (every {self.config.heartbeat_interval}s)")
        
        # Main loop
        logger.info(f"👀 Watching: {self.config.inbox_dir}")
        logger.info(f"⏱️  Poll interval: {self.config.poll_interval}s")
        logger.info("-" * 60)
        
        try:
            while self.running:
                self._poll_inbox()
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            logger.info("\n🛑 Shutdown requested...")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the station daemon."""
        self.running = False
        omega_db.station_heartbeat(self.config.station_id, "offline")
        self.executor.shutdown(wait=True)
        logger.info("👋 Station stopped")
    
    def _heartbeat_loop(self):
        """Send heartbeat to database every N seconds."""
        while self.running:
            try:
                with self._lock:
                    status = "processing" if self.active_jobs else "online"
                omega_db.station_heartbeat(self.config.station_id, status)
            except Exception as e:
                logger.warning(f"💔 Heartbeat failed: {e}")
            time.sleep(self.config.heartbeat_interval)
    
    def _poll_inbox(self):
        """Scan INBOX for new video files."""
        try:
            for file_path in self.config.inbox_dir.iterdir():
                # Skip hidden files
                if file_path.name.startswith("."):
                    continue
                
                # Check extension
                if file_path.suffix.lower() not in self.config.extensions:
                    continue
                
                # Check if already processing
                stem = file_path.stem
                with self._lock:
                    if stem in self.active_jobs:
                        continue
                
                # Check stability
                if not is_stable_file(file_path):
                    logger.debug(f"   ⏳ File not stable yet: {file_path.name}")
                    continue
                
                # Process this file
                logger.info(f"📥 Found: {file_path.name}")
                with self._lock:
                    self.active_jobs.add(stem)
                
                self.executor.submit(self._process_file, file_path)
                
        except Exception as e:
            logger.error(f"❌ Poll error: {e}")
    
    def _process_file(self, source_path: Path):
        """Process a single video file."""
        stem = source_path.stem
        
        try:
            logger.info(f"🔧 Processing: {stem}")
            
            # 1. Move to PROCESSING
            processing_path = self.config.processing_dir / source_path.name
            source_path.rename(processing_path)
            logger.info(f"   📦 Moved to PROCESSING")
            
            # 2. Generate proxy if enabled
            proxy_path = None
            if self.config.proxy_enabled:
                logger.info(f"   🎬 Generating proxy...")
                try:
                    proxy_path = generate_proxy(processing_path, stem)
                    logger.info(f"   ✅ Proxy: {proxy_path.name}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Proxy generation failed: {e}")
            
            # 3. Deliver (for now, just deliver the original file)
            # In full implementation, this would deliver the subtitle file
            logger.info(f"   📤 Delivering...")
            success = deliver(processing_path, self.config.delivery, stem)
            
            if success:
                # 4. Move to ARCHIVE
                archive_path = self.config.archive_dir / source_path.name
                processing_path.rename(archive_path)
                logger.info(f"   📁 Archived: {archive_path.name}")
                logger.info(f"✅ Completed: {stem}")
            else:
                logger.error(f"❌ Delivery failed: {stem}")
                # Move back to INBOX for retry
                processing_path.rename(source_path)
        
        except Exception as e:
            logger.error(f"❌ Processing error for {stem}: {e}")
            # Try to move back to INBOX
            try:
                processing_path = self.config.processing_dir / source_path.name
                if processing_path.exists():
                    processing_path.rename(source_path)
            except:
                pass
        
        finally:
            with self._lock:
                self.active_jobs.discard(stem)


# =============================================================================
# SIGNAL HANDLERS
# =============================================================================

_station: Optional[OmegaStation] = None


def handle_signal(signum, frame):
    """Handle shutdown signals gracefully."""
    global _station
    if _station:
        logger.info(f"Received signal {signum}, shutting down...")
        _station.stop()
    sys.exit(0)


# =============================================================================
# MAIN
# =============================================================================

def main():
    global _station
    
    parser = argparse.ArgumentParser(description="Omega Station - Remote Processing Node")
    parser.add_argument("--config", "-c", required=True, help="Path to station.json config file")
    parser.add_argument("--db-type", choices=["postgres", "sqlite"], default="postgres", 
                        help="Database type (default: postgres)")
    parser.add_argument("--test-mode", action="store_true", help="Run in test mode (more logging)")
    
    args = parser.parse_args()
    
    # Set DB type
    os.environ["DB_TYPE"] = args.db_type
    
    # Load config
    try:
        config = StationConfig(Path(args.config))
    except Exception as e:
        print(f"❌ Config error: {e}")
        sys.exit(1)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # Create and start station
    _station = OmegaStation(config, test_mode=args.test_mode)
    _station.start()


if __name__ == "__main__":
    main()
