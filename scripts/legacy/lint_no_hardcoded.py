#!/usr/bin/env python3
"""
Pre-commit hook: Detect hardcoded project/bucket names.

Hardcoded values like 'sermon-translator-system' or 'omega-jobs-subtitle-project'
should be centralized in config/defaults.py.

Usage:
    python scripts/lint_no_hardcoded.py file1.py file2.py ...
"""
import sys
import re

# Values that should be centralized
HARDCODED_VALUES = [
    'sermon-translator-system',
    'omega-jobs-subtitle-project',
    'omega-sql-prod',
]

# Files allowed to contain these values
ALLOWED_PATTERNS = [
    'config/defaults.py',
    'config.py',  # During migration
    '.env',
    'lint_',
    'test_',
    '__pycache__',
]


def is_allowed(filepath: str) -> bool:
    """Check if file is allowed to contain hardcoded values."""
    return any(pattern in filepath for pattern in ALLOWED_PATTERNS)


def check_file(filepath: str) -> list[tuple[int, str, str]]:
    """Check a file for hardcoded values."""
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue

                for value in HARDCODED_VALUES:
                    if value in line:
                        violations.append((line_num, line.strip(), value))
    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lint_no_hardcoded.py <file1.py> [file2.py ...]")
        return 0

    failed = False
    for filepath in sys.argv[1:]:
        if not filepath.endswith('.py'):
            continue

        if is_allowed(filepath):
            continue

        violations = check_file(filepath)
        for line_num, line, value in violations:
            print(f"{filepath}:{line_num}: hardcoded value '{value}'")
            print(f"  {line}")
            print(f"  Fix: Import from config/defaults.py instead")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
