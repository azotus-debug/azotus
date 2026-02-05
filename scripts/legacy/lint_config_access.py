#!/usr/bin/env python3
"""
Pre-commit hook: Enforce config access through config.py only.

Direct os.environ.get() calls outside config.py lead to:
- Inconsistent defaults
- Hard to audit what config is used where
- Difficult to test with different configs

Usage:
    python scripts/lint_config_access.py file1.py file2.py ...
"""
import sys
import re

# Files allowed to use os.environ directly
ALLOWED_PATTERNS = [
    'config.py',
    'config/',
    'preflight',
    'setup.py',
    'lint_',  # Allow lint scripts themselves
]

# Pattern to detect direct environ access
ENVIRON_PATTERN = r'os\.environ\.get\s*\(|os\.environ\['


def is_allowed(filepath: str) -> bool:
    """Check if file is allowed to access os.environ directly."""
    return any(pattern in filepath for pattern in ALLOWED_PATTERNS)


def check_file(filepath: str) -> list[tuple[int, str]]:
    """Check a file for direct os.environ access."""
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue

                if re.search(ENVIRON_PATTERN, line):
                    violations.append((line_num, line.strip()))
    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lint_config_access.py <file1.py> [file2.py ...]")
        return 0

    failed = False
    for filepath in sys.argv[1:]:
        if not filepath.endswith('.py'):
            continue

        if is_allowed(filepath):
            continue

        violations = check_file(filepath)
        for line_num, line in violations:
            print(f"{filepath}:{line_num}: direct os.environ access")
            print(f"  {line}")
            print(f"  Fix: Use 'import config' and access 'config.VARIABLE_NAME' instead")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
