#!/usr/bin/env python3
"""
Omega Secrets Setup
===================
Run this ONCE to configure your API keys. They'll be saved permanently.

Usage:
    python3 setup_secrets.py
"""

import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"

def get_current_value(key: str) -> str:
    """Get current value from .env file."""
    if not ENV_FILE.exists():
        return ""
    content = ENV_FILE.read_text()
    match = re.search(rf'^{key}=(.*)$', content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""

def set_env_value(key: str, value: str):
    """Set a value in .env file."""
    content = ENV_FILE.read_text()
    pattern = rf'^{key}=.*$'
    replacement = f'{key}={value}'

    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        content += f'\n{replacement}\n'

    ENV_FILE.write_text(content)
    print(f"  Saved {key}")

def main():
    print("\n" + "="*60)
    print("OMEGA SECRETS SETUP")
    print("="*60)
    print("\nThis will configure your API keys permanently.\n")

    # Bunny CDN
    print("-" * 40)
    print("BUNNY CDN (for video preview in review portal)")
    print("-" * 40)
    current = get_current_value("BUNNY_API_KEY")
    if current and "PASTE" not in current:
        print(f"  Current: {current[:10]}... (already configured)")
        change = input("  Change it? [y/N]: ").strip().lower()
        if change != 'y':
            print("  Keeping existing value.")
        else:
            value = input("  Enter Bunny API Key: ").strip()
            if value:
                set_env_value("BUNNY_API_KEY", value)
    else:
        print("  Not configured.")
        print("  Get it from: https://bunny.net > Stream > Library 576409 > Settings")
        value = input("  Enter Bunny API Key: ").strip()
        if value:
            set_env_value("BUNNY_API_KEY", value)
        else:
            print("  Skipped.")

    print("\n" + "="*60)
    print("DONE")
    print("="*60)
    print("\nYour secrets are saved in .env")
    print("Run 'sh start_omega.sh' to restart with new config.\n")

if __name__ == "__main__":
    main()
