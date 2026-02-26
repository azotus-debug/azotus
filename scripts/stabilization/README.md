# Stabilization Scripts

These scripts are part of the freeze program and are intended for repeatable evidence collection.

## Scripts
- `capture_baseline.sh`: captures git/runtime/health snapshots into `docs/stabilization/artifacts/<timestamp>/`.
- `smoke_gate.sh`: hard pass/fail checks for API health, frontend asset delivery, and non-empty/parity-checked program lists across API and frontend proxy.
- `library_integrity_gate.sh`: verifies active program ID parity across DB, backend API, and frontend proxy.
- `drift_report.sh`: creates a drift summary from `git status --porcelain`.
- `restart_gate.sh`: executes repeated `start_omega` + smoke + baseline capture loops for G2.
- `release_gate.sh`: one-command sign-off run (smoke -> library-integrity -> restart loops -> library-integrity -> baseline -> drift) with a single summary file.
- `soak_gate.sh`: runs hourly release-gate checks for a fixed duration (default 24 hours) and writes a rolling soak summary.

## Usage
```bash
./scripts/stabilization/capture_baseline.sh
./scripts/stabilization/smoke_gate.sh
./scripts/stabilization/library_integrity_gate.sh
./scripts/stabilization/drift_report.sh
OMEGA_RESTART_LOOPS=10 ./scripts/stabilization/restart_gate.sh
OMEGA_RELEASE_GATE_RESTART_LOOPS=3 ./scripts/stabilization/release_gate.sh
OMEGA_SOAK_HOURS=24 OMEGA_SOAK_INTERVAL_SECONDS=3600 ./scripts/stabilization/soak_gate.sh
```

## Optional Environment Overrides
- `OMEGA_SMOKE_API_URL` (default `http://127.0.0.1:8001`)
- `OMEGA_SMOKE_FRONTEND_URL` (default `http://127.0.0.1:3000`)
- `OMEGA_SMOKE_MIN_PROGRAMS` (default `1`)
- `OMEGA_SMOKE_RETRIES` (default `3`)
- `OMEGA_SMOKE_RETRY_DELAY_SECONDS` (default `2`)
- `OMEGA_LIBRARY_LIMIT` (default `5000`, max programs fetched/compared in integrity gate)
- `OMEGA_LIBRARY_RETRIES` (default `3`)
- `OMEGA_LIBRARY_RETRY_DELAY_SECONDS` (default `2`)
- `OMEGA_LIBRARY_REPORT_FILE` (optional path for key-value integrity summary output)
- `OMEGA_RESTART_LOOPS` (default `10`, for `restart_gate.sh`)
- `OMEGA_RESTART_WAIT_SECONDS` (default `60`, readiness wait before smoke in `restart_gate.sh`)
- `OMEGA_RESTART_OUT_DIR` (optional explicit output directory for `restart_gate.sh`)
- `OMEGA_RESTART_FORCE_START` (default `0`, passed to `start_omega.sh` as `OMEGA_FORCE_START` during restart loops)
- `OMEGA_RESTART_SKIP_PREFLIGHT` (default `1`, passed to `start_omega.sh` as `OMEGA_SKIP_PREFLIGHT` during restart loops)
- `INCLUDE_PIPELINE_AUDIT` (default `0`, set `1` to include `pipeline_audit.py` in baseline capture)
- `PIPELINE_AUDIT_TIMEOUT_SECONDS` (default `120`)
- `OMEGA_BASELINE_OUT_DIR` (optional explicit output directory for `capture_baseline.sh`)
- `OMEGA_RELEASE_GATE_RESTART_LOOPS` (default `3`, restart iterations inside `release_gate.sh`)
- `OMEGA_RELEASE_GATE_SKIP_START` (default `0`; set `1` to skip `start_omega.sh` preflight in `release_gate.sh`)
- `OMEGA_RELEASE_GATE_WAIT_SECONDS` (default `60`, readiness wait after preflight start in `release_gate.sh`)
- `OMEGA_RELEASE_GATE_FORCE_START` (default `1`, passed to both preflight start and restart loops as `OMEGA_FORCE_START`)
- `OMEGA_RELEASE_GATE_SKIP_PREFLIGHT` (default `1`, passed to both preflight start and restart loops as `OMEGA_SKIP_PREFLIGHT`)
- `OMEGA_SOAK_HOURS` (default `24`, number of hourly soak iterations)
- `OMEGA_SOAK_INTERVAL_SECONDS` (default `3600`, wait between iterations)
- `OMEGA_SOAK_RELEASE_RESTART_LOOPS` (default `1`, release-gate restart loops per soak iteration)
- `OMEGA_SOAK_OUT_DIR` (optional explicit output directory for soak artifacts/summary)

## Expected Workflow
1. Start stack: `OMEGA_NO_TAIL=1 ./start_omega.sh`
2. Run release gate: `./scripts/stabilization/release_gate.sh`
3. For long-run confidence, start soak gate: `OMEGA_SOAK_HOURS=24 OMEGA_SOAK_INTERVAL_SECONDS=3600 ./scripts/stabilization/soak_gate.sh`
4. Attach `release_gate_summary.txt` / `soak_summary.txt` and related artifact directories to stabilization review.
