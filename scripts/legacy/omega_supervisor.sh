#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "$REPO_DIR"

export OMEGA_NO_TAIL=1

./start_omega.sh

# Keep the supervisor alive so launchd doesn't thrash.
while true; do
  sleep 300
done
