# Azotus Subtitle Workflow (Clean)

This is the cleaned, stable hybrid system:
- Local Mac handles ingest + burn
- Cloud handles AI translation/edit/polish
- Cloud SQL Postgres is the single source of truth
- Programs/Tracks UI is the canonical interface

See `docs/RUNBOOK.md` for setup and operations.

Runtime requirements:
- PM2 is required for local process supervision (`./scripts/install_pm2.sh`).
- Optional override: set `OMEGA_PM2_BIN=/absolute/path/to/pm2` (for local installs).
- Start stack: `OMEGA_NO_TAIL=1 ./start_omega.sh`
- Stop stack: `./stop_all.sh`
