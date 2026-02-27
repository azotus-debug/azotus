#!/usr/bin/env python3
"""
Batch re-finalize and burn InTouch Ministries jobs:
i2251, i2601, i2602, i2603, i2604

Usage:
    python batch_intouch_delivery.py              # finalize + burn all
    python batch_intouch_delivery.py --finalize   # finalize only (generate SRTs)
    python batch_intouch_delivery.py --burn       # burn only (requires SRTs already exist)
"""

from __future__ import annotations

import sys
import os
import shutil
import argparse
import logging
from pathlib import Path
from typing import Optional

# Ensure Azotus is on the path
sys.path.insert(0, str(Path(__file__).parent))

import config
from workers import finalizer, publisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_intouch")

# ── Job definitions ──────────────────────────────────────────────────────────
ARCHIVE_BASE = Path("/Volumes/Extreme SSD/Omega_Work_Archive_20260121")
OMEGA_WORK = Path("/Volumes/Extreme SSD/Omega_Work")

JOBS = [
    {
        "name": "I2251",
        "approved_json": OMEGA_WORK / "3_TRANSLATED_DONE" / "i2251_gospel-20260121T232859923656Z_APPROVED.json",
        "source_video": OMEGA_WORK / "2_VAULT" / "Videos" / "I2251_Gospel.mp4",
        "language": "is",
    },
    {
        "name": "I2601",
        "approved_json": ARCHIVE_BASE / "3_TRANSLATED_DONE" / "i2601_iceland_timing_fix-20260102T215354703499Z_APPROVED.json",
        "source_video": ARCHIVE_BASE / "2_VAULT" / "2025-12" / "I2601_IntlUK_h264-1080p2997-aac" / "source" / "I2601_IntlUK_h264-1080p2997-aac.mp4",
        "language": "is",
    },
    {
        "name": "I2602",
        "approved_json": ARCHIVE_BASE / "3_TRANSLATED_DONE" / "i2602_intluk_h264-1080p2997-aac-20251225T002956Z_APPROVED.json",
        "source_video": ARCHIVE_BASE / "2_VAULT" / "2025-12" / "I2602_IntlUK_h264-1080p2997-aac" / "source" / "I2602_IntlUK_h264-1080p2997-aac.mp4",
        "language": "is",
    },
    {
        "name": "I2603",
        "approved_json": ARCHIVE_BASE / "3_TRANSLATED_DONE" / "i2603_is-20260112T173211856762Z_APPROVED.json",
        "source_video": ARCHIVE_BASE / "2_VAULT" / "2025-12" / "I2603_IntlUK_h264-1080p2997-aac" / "source" / "I2603_IntlUK_h264-1080p2997-aac.mp4",
        "language": "is",
    },
    {
        "name": "I2604",
        "approved_json": ARCHIVE_BASE / "3_TRANSLATED_DONE" / "i2604_is-20260113T233124399526Z_APPROVED.json",
        "source_video": ARCHIVE_BASE / "2_VAULT" / "2025-12" / "I2604_IntlUK_h264-1080p2997-aac" / "source" / "I2604_IntlUK_h264-1080p2997-aac.mp4",
        "language": "is",
    },
]

# ── Output directory for this batch ──────────────────────────────────────────
BATCH_OUTPUT = config.DELIVERY_DIR / "InTouch_Batch_2026-02-08"


def verify_files():
    """Verify all source files exist before starting."""
    all_ok = True
    for job in JOBS:
        if not job["approved_json"].exists():
            logger.error(f"❌ Missing approved JSON: {job['approved_json']}")
            all_ok = False
        if not job["source_video"].exists():
            logger.error(f"❌ Missing source video: {job['source_video']}")
            all_ok = False
        else:
            size = job["source_video"].stat().st_size
            if size < 1_000_000:
                logger.warning(f"⚠️  Source video suspiciously small ({size} bytes): {job['source_video']}")
    return all_ok


def run_finalize(job: dict) -> Path | None:
    """Run the updated finalizer to generate SRT from approved JSON."""
    logger.info(f"\n{'='*60}")
    logger.info(f"🎬 FINALIZING: {job['name']}")
    logger.info(f"   JSON: {job['approved_json'].name}")
    logger.info(f"   Video: {job['source_video'].name}")
    logger.info(f"{'='*60}")

    try:
        # Copy approved JSON to the working directory so finalizer can find it
        working_json = config.TRANSLATED_DONE_DIR / job["approved_json"].name
        if not working_json.exists():
            shutil.copy2(job["approved_json"], working_json)
            logger.info(f"   📋 Copied approved JSON to {working_json}")

        srt_path, normalized_path = finalizer.finalize(
            approved_path=working_json,
            target_language=job["language"],
            video_path=job["source_video"],
            apply_scene_snap=True,
        )
        logger.info(f"   ✅ SRT generated: {srt_path}")

        # Copy SRT to batch output
        BATCH_OUTPUT.mkdir(parents=True, exist_ok=True)
        srt_dest = BATCH_OUTPUT / f"{job['name']}_IS.srt"
        shutil.copy2(srt_path, srt_dest)
        logger.info(f"   📁 SRT copied to: {srt_dest}")

        return srt_path
    except Exception as e:
        logger.error(f"   ❌ FINALIZE FAILED for {job['name']}: {e}")
        import traceback
        traceback.print_exc()
        return None


def run_burn(job: dict, srt_path: Path) -> Path | None:
    """Burn subtitles into source video."""
    logger.info(f"\n{'='*60}")
    logger.info(f"🔥 BURNING: {job['name']}")
    logger.info(f"   SRT: {srt_path.name}")
    logger.info(f"   Video: {job['source_video'].name}")
    logger.info(f"{'='*60}")

    try:
        output_path = publisher.publish(
            video_path=job["source_video"],
            srt_path=srt_path,
            subtitle_style="Classic",
        )
        logger.info(f"   ✅ Burned video: {output_path}")

        # Copy burned video to batch output
        BATCH_OUTPUT.mkdir(parents=True, exist_ok=True)
        video_dest = BATCH_OUTPUT / f"{job['name']}_IS_SUBBED.mp4"
        if output_path and Path(output_path).exists():
            shutil.copy2(output_path, video_dest)
            logger.info(f"   📁 Video copied to: {video_dest}")

        return output_path
    except Exception as e:
        logger.error(f"   ❌ BURN FAILED for {job['name']}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(description="Batch InTouch delivery")
    parser.add_argument("--finalize", action="store_true", help="Only run finalization (JSON → SRT)")
    parser.add_argument("--burn", action="store_true", help="Only run burn (requires SRTs)")
    parser.add_argument("--jobs", nargs="+", help="Only process specific jobs (e.g., I2251 I2601)")
    args = parser.parse_args()

    # Default: do both if neither flag specified
    do_finalize = args.finalize or (not args.finalize and not args.burn)
    do_burn = args.burn or (not args.finalize and not args.burn)

    jobs = JOBS
    if args.jobs:
        names = [j.upper() for j in args.jobs]
        jobs = [j for j in JOBS if j["name"] in names]
        if not jobs:
            logger.error(f"No matching jobs found for: {args.jobs}")
            sys.exit(1)

    logger.info(f"📦 InTouch Batch Delivery")
    logger.info(f"   Jobs: {[j['name'] for j in jobs]}")
    logger.info(f"   Finalize: {do_finalize} | Burn: {do_burn}")
    logger.info(f"   Output: {BATCH_OUTPUT}")

    if not verify_files():
        logger.error("❌ File verification failed. Fix missing files first.")
        sys.exit(1)

    logger.info("✅ All files verified\n")

    results = {}
    for job in jobs:
        srt_path = None

        if do_finalize:
            srt_path = run_finalize(job)
            if not srt_path:
                results[job["name"]] = "FINALIZE_FAILED"
                continue

        if do_burn:
            if not srt_path:
                # Find existing SRT
                stem = job["approved_json"].stem.replace("_APPROVED", "")
                srt_path = config.DELIVERY_DIR / "SRT" / f"{stem}.srt"
                if not srt_path.exists():
                    # Try alternate location
                    srt_path = config.TRANSLATED_DONE_DIR / f"{stem}.srt"
                if not srt_path.exists():
                    logger.error(f"   ❌ No SRT found for {job['name']} — run --finalize first")
                    results[job["name"]] = "NO_SRT"
                    continue

            output = run_burn(job, srt_path)
            if not output:
                results[job["name"]] = "BURN_FAILED"
                continue

        results[job["name"]] = "OK"

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"📊 BATCH RESULTS")
    logger.info(f"{'='*60}")
    for name, status in results.items():
        icon = "✅" if status == "OK" else "❌"
        logger.info(f"   {icon} {name}: {status}")

    logger.info(f"\n📁 Delivery folder: {BATCH_OUTPUT}")
    if BATCH_OUTPUT.exists():
        for f in sorted(BATCH_OUTPUT.iterdir()):
            size_mb = f.stat().st_size / (1024 * 1024)
            logger.info(f"   📄 {f.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
