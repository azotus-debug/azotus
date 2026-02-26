#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_FILE="${1:-}"

generate_report() {
  echo "generated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "repo_root=$ROOT_DIR"
  echo "branch=$(git -C "$ROOT_DIR" branch --show-current)"
  echo "commit=$(git -C "$ROOT_DIR" rev-parse --short HEAD)"
  echo "total_changes=$(git -C "$ROOT_DIR" status --porcelain=1 | wc -l | awk '{print $1}')"
  echo
  echo "top_level_distribution:"
  git -C "$ROOT_DIR" status --porcelain=1 | awk '
    {
      line=substr($0,4)
      split(line,parts," -> ")
      path=parts[length(parts)]
      split(path,a,"/")
      top=(a[1]==""?"(root)":a[1])
      c[top]++
    }
    END{
      for(k in c) printf "%s %d\n",k,c[k]
    }' | sort -k2,2nr
  echo
  echo "sample_entries:"
  git -C "$ROOT_DIR" status --porcelain=1 | sed -n '1,80p'
}

if [ -n "$OUT_FILE" ]; then
  generate_report >"$OUT_FILE"
  echo "Drift report written: $OUT_FILE"
else
  generate_report
fi

