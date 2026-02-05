#!/usr/bin/env python3
"""
Pre-commit hook: Block bare except:pass patterns.

These patterns silently swallow errors, making debugging impossible.
Always log exceptions or handle them explicitly.

Usage:
    python scripts/lint_no_bare_except.py file1.py file2.py ...
"""
import sys
import re

# Patterns that indicate silent failure
PATTERNS = [
    r'except\s*:\s*pass',           # except: pass
    r'except\s+Exception\s*:\s*pass',  # except Exception: pass
    r'except\s+.*:\s*pass\s*$',     # except SomeError: pass
]

COMBINED_PATTERN = '|'.join(PATTERNS)


def check_file(filepath: str) -> list[tuple[int, str]]:
    """Check a file for bare except:pass patterns."""
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                if re.search(COMBINED_PATTERN, line):
                    violations.append((line_num, line.strip()))
    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}", file=sys.stderr)
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lint_no_bare_except.py <file1.py> [file2.py ...]")
        return 0

    failed = False
    for filepath in sys.argv[1:]:
        if not filepath.endswith('.py'):
            continue

        violations = check_file(filepath)
        for line_num, line in violations:
            print(f"{filepath}:{line_num}: bare except:pass found")
            print(f"  {line}")
            print(f"  Fix: Add logging - except Exception as e: logger.warning(f'...: {{e}}')")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
