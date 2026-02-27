# Autopilot Status

## Scope
Hardening sprint focused on deterministic artifacts, no-loss subtitles, and pre-burn safety gates.

## Completed
1. Added cross-process per-job artifact lock:
- `/Users/haukurhauksson/Azotus/artifact_lock.py`
- Used by finalize/burn in `/Users/haukurhauksson/Azotus/omega_manager.py`
- Used by approved JSON sync in `/Users/haukurhauksson/Azotus/cloud_sync_service.py`

2. Removed text-loss behavior in finalizer:
- Updated `/Users/haukurhauksson/Azotus/workers/finalizer.py`
- `split_into_balanced_lines()` now preserves all content.
- Overflow is folded instead of truncated.

3. Added pre-burn text coverage gate:
- Added `compute_srt_text_coverage()` in `/Users/haukurhauksson/Azotus/workers/finalizer.py`
- Wired gate into `/Users/haukurhauksson/Azotus/omega_manager.py` `_run_burn()`.
- Burn now fails fast if SRT coverage drops below `OMEGA_MIN_TEXT_COVERAGE` (default `0.995`).

4. Repaired publisher runtime path:
- Replaced malformed `publish()` body in `/Users/haukurhauksson/Azotus/workers/publisher.py`
- Function now has a deterministic, valid implementation.

5. Strengthened translation quality instructions:
- Tightened omission/theology rules in `/Users/haukurhauksson/Azotus/omega_cloud_worker.py`
- Added translation-side omission guard for suspiciously short outputs.
- Strengthened chunk editor priority for omission detection.

6. Expanded default theology glossary:
- Added core theology terms in `/Users/haukurhauksson/Azotus/profiles.py` (`standard` profile).

## Validation Run
- Syntax check passed:
  - `python3 -m py_compile artifact_lock.py workers/finalizer.py workers/publisher.py omega_manager.py cloud_sync_service.py omega_cloud_worker.py profiles.py`
- Coverage gate sample passed on known job:
  - `coverage_ratio=1.0`, `missing_tokens=0`.

## Operational Notes
- `pytest` is not installed in this environment (`command not found`).
- Existing repository contains unrelated in-progress changes outside this sprint; none were reverted.

## Next Recommended Runbook
1. Restart manager/sync services to load patched code paths.
2. Process one full 30-minute program.
3. Verify `qa_coverage` in job meta and confirm no missing-token failures.
4. If clean, keep `OMEGA_MIN_TEXT_COVERAGE=0.995`; otherwise temporarily lower to `0.99` and inspect misses.
