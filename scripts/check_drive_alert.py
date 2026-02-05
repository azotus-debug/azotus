import sys
import time
from email_utils import send_email

def alert_missing_drive(minutes_waited):
    subject = "🚨 CRITICAL: SSD Missing on Production Mac"
    body = f"""
    OMEGA SYSTEM ALERT
    ------------------
    The external drive '/Volumes/Extreme SSD' has been missing for {minutes_waited} minutes.
    
    The Omega Manager processes cannot start without this drive.
    
    Action Required:
    1. Check if the SSD is plugged in.
    2. Check if it's mounted.
    3. Run 'diskutil list' in terminal.
    
    System is pausing until drive reappears.
    """
    import os
    reviewer = os.environ.get("OMEGA_REVIEWER_EMAIL", "hawk1982@me.com")
    
    try:
        send_email(subject=subject, body=body, to_addrs=reviewer)
        print(f"📧 Alert sent: {subject}")
    except Exception as e:
        print(f"❌ Failed to send alert: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        minutes = sys.argv[1]
    else:
        minutes = "Unknown"
    alert_missing_drive(minutes)
