# Production Scorecard (Post-Hardening Snapshot)

## Scoring Rubric
- 10: deterministic, zero critical omissions, high reliability
- 7-9: production-capable with managed residual risk
- <7: fragile for unattended delivery

## Current Scores
1. Local System Reliability: 8.7/10
- Stronger due to per-job artifact locking and repaired publisher path.
- Remaining gap: full automated test suite not runnable here (`pytest` missing).

2. Cloud Translation Quality Controls: 8.5/10
- Prompt strengthened for omission/theology preservation.
- Added suspicious-short-output guard.
- Remaining gap: gold-set benchmark + periodic drift tests not yet automated.

3. Interservice Communication (Local <-> Cloud Sync): 8.8/10
- Cloud sync already had orphan recovery and DB circuit breaker.
- Added artifact lock coordination with manager finalize/burn.

4. Subtitle Finalization Integrity: 9.0/10
- No-truncation behavior implemented.
- Pre-burn coverage gate blocks silent text loss.
- Remaining gap: optional stricter linguistic QA heuristics for fragment boundary polish.

5. Overall Production Readiness: 8.8/10
- Now resilient enough for routine delivery with far lower omission risk.
- To reach 10/10: add gold-set evaluation, CI tests, and automated deploy checks.

## Quality Gates Now Active
1. No-loss line processing in finalizer.
2. Pre-burn coverage validation (`OMEGA_MIN_TEXT_COVERAGE`, default `0.995`).
3. Cross-process artifact locking on approved/SRT/video lifecycle.

## Immediate KPI Targets
1. Critical omission rate: 0 per delivered program.
2. Burn gate failure rate: <2% (and every failure actionable).
3. Manual hotfix reburns: reduced by >80%.
4. Cloud sync orphan misses: 0.
