import os
import sys
import fcntl
import atexit
import signal
import time
from pathlib import Path

class ProcessLock:
    """
    Ensures only one instance of a script runs at a time using OS-level file locking (fcntl).
    Includes PID verification to detect stale locks or zombie processes.
    """
    def __init__(self, name):
        self.name = name
        self.lock_file = Path(f"/tmp/{name}.lock")
        self.fp = None

    def __enter__(self):
        try:
            # Open in append+read mode to avoid truncating existing PID if locked
            self.fp = open(self.lock_file, 'a+')
            self.fp.seek(0)
            
            # Try to acquire an exclusive lock
            try:
                fcntl.lockf(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, BlockingIOError):
                # Lock is held by another process. Check if it's stale.
                if self.is_lock_stale():
                    print(f"⚠️ Found stale lock for {self.name}. Breaking lock...")
                    self.fp.close()
                    os.remove(self.lock_file)
                    # Re-open and try again
                    self.fp = open(self.lock_file, 'a+')
                    fcntl.lockf(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    print(f"❌ ERROR: {self.name} is already running.")
                    sys.exit(1)
            
            # --- LOCK ACQUIRED ---
            # Now it's safe to truncate and write our PID
            self.fp.seek(0)
            self.fp.truncate()
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            
            # Register cleanup
            atexit.register(self.cleanup)
            signal.signal(signal.SIGTERM, self.handle_signal)
            signal.signal(signal.SIGINT, self.handle_signal)
            
            print(f"🔒 Acquired lock for {self.name} (PID: {os.getpid()})")
            return self
            
        except PermissionError:
             print(f"❌ ERROR: Permission denied for lock file '{self.lock_file}'.")
             sys.exit(1)

    def force_kill_existing(self):
        """Attempts to kill the process holding the lock."""
        try:
            self.fp = open(self.lock_file, 'r')
            content = self.fp.read().strip()
            self.fp.close()
            if content:
                pid = int(content)
                try:
                    os.kill(pid, 0)
                    print(f"🔪 Force Killing existing process {pid}...")
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(1) # Wait for death
                except OSError:
                    pass
        except Exception as e:
            print(f"⚠️ Failed to kill existing process: {e}")

    def is_lock_stale(self):
        """Checks if the lock is held by a dead process."""
        try:
            self.fp.seek(0)
            content = self.fp.read().strip()
            if not content:
                return True # Empty lock file is stale
                
            pid = int(content)
            try:
                os.kill(pid, 0) # Check if process exists
                return False # Process is alive
            except OSError:
                return True # Process is dead
        except Exception:
            return True # Read error, assume stale

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def handle_signal(self, signum, frame):
        print(f"\n🛑 Received signal {signum}. Cleaning up...")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        if self.fp:
            try:
                # Unlock and close
                fcntl.lockf(self.fp, fcntl.LOCK_UN)
                self.fp.close()
                # Only remove if we created it/held it
                if self.lock_file.exists():
                    os.remove(self.lock_file)
            except Exception:
                pass
            self.fp = None
