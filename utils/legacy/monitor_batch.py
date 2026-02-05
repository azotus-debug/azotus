#!/usr/bin/env python3
"""
Batch Processing Monitor for SubtitleWorkflow

Monitors multiple jobs in real-time, tracks progress through all stages,
measures performance, and generates a comprehensive report.

Usage:
    python3 utils/monitor_batch.py --jobs job1,job2,job3
    python3 utils/monitor_batch.py --watch-new 3  # Monitor next 3 new jobs
    python3 utils/monitor_batch.py --all  # Monitor all active jobs
"""

import sqlite3
import time
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.BASE_DIR / "production.db"
REPORT_DIR = config.BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)


class JobMonitor:
    """Monitor multiple jobs through the pipeline."""

    def __init__(self, job_ids=None, watch_count=None):
        self.job_ids = job_ids or []
        self.watch_count = watch_count
        self.start_time = datetime.now()
        self.job_data = {}
        self.stage_transitions = defaultdict(list)
        self.initial_job_count = 0

    def get_db_connection(self):
        """Get database connection."""
        return sqlite3.connect(str(DB_PATH))

    def get_active_jobs(self):
        """Get all jobs that are not completed or failed."""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT job_id, stage, status, progress, created_at
            FROM tracks
            WHERE stage NOT IN ('COMPLETED', 'FAILED', 'DEAD')
            ORDER BY created_at DESC
        """)

        jobs = cursor.fetchall()
        conn.close()
        return jobs

    def watch_for_new_jobs(self):
        """Wait for new jobs to appear and start monitoring them."""
        print(f"⏳ Waiting for {self.watch_count} new jobs to be ingested...")
        print("   Drop your video files into 1_INBOX/01_AUTO_PILOT/Classic/")
        print()

        conn = self.get_db_connection()
        cursor = conn.cursor()

        # Get baseline count
        cursor.execute("SELECT COUNT(*) FROM tracks")
        initial_count = cursor.fetchone()[0]
        self.initial_job_count = initial_count

        new_jobs = []
        last_count = initial_count

        while len(new_jobs) < self.watch_count:
            time.sleep(2)

            cursor.execute("SELECT COUNT(*) FROM tracks")
            current_count = cursor.fetchone()[0]

            if current_count > last_count:
                # New jobs detected
                cursor.execute("""
                    SELECT job_id, stage, created_at
                    FROM tracks
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (current_count - initial_count,))

                all_new = cursor.fetchall()
                new_jobs = [job[0] for job in all_new]

                print(f"✅ Detected {len(new_jobs)} new job(s):")
                for job in all_new:
                    print(f"   - {job[0]} (stage: {job[1]})")
                print()

                last_count = current_count

        conn.close()
        self.job_ids = new_jobs[:self.watch_count]
        print(f"🎯 Monitoring {len(self.job_ids)} jobs:")
        for job_id in self.job_ids:
            print(f"   - {job_id}")
        print()

    def get_job_status(self, job_id):
        """Get current status of a job."""
        conn = self.get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT job_id, stage, status, progress, created_at, updated_at, meta
            FROM tracks
            WHERE job_id = ?
        """, (job_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        meta = json.loads(row[6]) if row[6] else {}

        return {
            'job_id': row[0],
            'stage': row[1],
            'status': row[2],
            'progress': row[3] or 0,
            'created_at': row[4],
            'updated_at': row[5],
            'meta': meta
        }

    def update_job_data(self):
        """Update data for all monitored jobs."""
        for job_id in self.job_ids:
            current = self.get_job_status(job_id)

            if not current:
                continue

            # Initialize job tracking
            if job_id not in self.job_data:
                self.job_data[job_id] = {
                    'start_stage': current['stage'],
                    'start_time': datetime.now(),
                    'stage_history': [],
                    'current': current
                }

            prev = self.job_data[job_id]['current']

            # Detect stage transition
            if prev['stage'] != current['stage']:
                transition = {
                    'from': prev['stage'],
                    'to': current['stage'],
                    'timestamp': datetime.now(),
                    'duration': (datetime.now() - self.job_data[job_id].get('last_transition', self.job_data[job_id]['start_time'])).total_seconds()
                }

                self.job_data[job_id]['stage_history'].append(transition)
                self.job_data[job_id]['last_transition'] = datetime.now()
                self.stage_transitions[job_id].append(transition)

                # Print transition
                print(f"📊 {job_id[:20]}... {transition['from']} → {transition['to']} ({transition['duration']:.0f}s)")

            self.job_data[job_id]['current'] = current

    def print_status(self):
        """Print current status of all jobs."""
        print("\n" + "="*100)
        print(f"⏰ Elapsed: {(datetime.now() - self.start_time).total_seconds():.0f}s | Monitoring {len(self.job_ids)} jobs")
        print("="*100)

        stages = ['INGEST', 'TRANSCRIBED', 'TRANSLATING', 'TRANSLATING_CLOUD_SUBMITTED',
                  'REVIEWED', 'FINALIZED', 'BURNING', 'COMPLETED']

        for job_id in self.job_ids:
            if job_id not in self.job_data:
                continue

            data = self.job_data[job_id]
            current = data['current']

            # Calculate job runtime
            runtime = (datetime.now() - data['start_time']).total_seconds()
            runtime_str = f"{runtime/60:.1f}m" if runtime > 60 else f"{runtime:.0f}s"

            # Create progress bar
            stage_index = stages.index(current['stage']) if current['stage'] in stages else 0
            total_stages = len(stages)
            progress_pct = (stage_index / total_stages) * 100

            bar_length = 30
            filled = int((progress_pct / 100) * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)

            # Status icon
            if current['stage'] == 'COMPLETED':
                icon = "✅"
            elif current['stage'] in ['FAILED', 'DEAD']:
                icon = "❌"
            elif current['stage'] == 'BURNING':
                icon = "🔥"
            elif current['stage'].startswith('TRANSLATING'):
                icon = "🌐"
            elif current['stage'] == 'TRANSCRIBED':
                icon = "📝"
            else:
                icon = "⚙️"

            print(f"{icon} {job_id[:30]:<30} [{bar}] {progress_pct:>5.1f}% | {current['stage']:<25} | {runtime_str:>6}")
            print(f"   Status: {current['status'][:70]}")

        print("="*100 + "\n")

    def is_batch_complete(self):
        """Check if all jobs are complete or failed."""
        terminal_stages = ['COMPLETED', 'FAILED', 'DEAD', 'DELIVERED']

        for job_id in self.job_ids:
            if job_id not in self.job_data:
                return False

            current_stage = self.job_data[job_id]['current']['stage']
            if current_stage not in terminal_stages:
                return False

        return True

    def generate_report(self):
        """Generate comprehensive report."""
        report_time = datetime.now()
        report_file = REPORT_DIR / f"batch_report_{report_time.strftime('%Y%m%d_%H%M%S')}.txt"

        total_runtime = (report_time - self.start_time).total_seconds()

        lines = []
        lines.append("="*100)
        lines.append(f"BATCH PROCESSING REPORT - {report_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("="*100)
        lines.append("")
        lines.append(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Ended:   {report_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Total Runtime: {total_runtime/60:.1f} minutes ({total_runtime/3600:.2f} hours)")
        lines.append("")
        lines.append(f"Jobs Monitored: {len(self.job_ids)}")
        lines.append("")

        # Summary statistics
        completed = 0
        failed = 0
        other = 0

        for job_id in self.job_ids:
            if job_id not in self.job_data:
                continue

            stage = self.job_data[job_id]['current']['stage']
            if stage in ['COMPLETED', 'DELIVERED']:
                completed += 1
            elif stage in ['FAILED', 'DEAD']:
                failed += 1
            else:
                other += 1

        lines.append("SUMMARY:")
        lines.append(f"  ✅ Completed: {completed}/{len(self.job_ids)}")
        lines.append(f"  ❌ Failed:    {failed}/{len(self.job_ids)}")
        lines.append(f"  ⏳ Other:     {other}/{len(self.job_ids)}")
        lines.append("")

        if completed > 0:
            success_rate = (completed / len(self.job_ids)) * 100
            lines.append(f"  Success Rate: {success_rate:.1f}%")
            lines.append("")

        # Per-job details
        lines.append("="*100)
        lines.append("JOB DETAILS")
        lines.append("="*100)
        lines.append("")

        for job_id in self.job_ids:
            if job_id not in self.job_data:
                continue

            data = self.job_data[job_id]
            current = data['current']
            runtime = (report_time - data['start_time']).total_seconds()

            lines.append(f"Job: {job_id}")
            lines.append(f"  Final Stage: {current['stage']}")
            lines.append(f"  Final Status: {current['status']}")
            lines.append(f"  Progress: {current['progress']:.1f}%")
            lines.append(f"  Runtime: {runtime/60:.1f} minutes")
            lines.append("")

            # Stage timeline
            if data['stage_history']:
                lines.append("  Stage Timeline:")
                for i, transition in enumerate(data['stage_history'], 1):
                    lines.append(f"    {i}. {transition['from']} → {transition['to']} ({transition['duration']:.0f}s)")
                lines.append("")

            # Metadata
            meta = current.get('meta', {})
            if meta.get('last_error'):
                lines.append(f"  Last Error: {meta['last_error']}")
                lines.append("")

        # Performance analysis
        lines.append("="*100)
        lines.append("PERFORMANCE ANALYSIS")
        lines.append("="*100)
        lines.append("")

        # Average time per stage
        stage_times = defaultdict(list)
        for job_id, transitions in self.stage_transitions.items():
            for t in transitions:
                stage_times[t['from']].append(t['duration'])

        if stage_times:
            lines.append("Average Time Per Stage:")
            for stage, times in sorted(stage_times.items()):
                avg_time = sum(times) / len(times)
                lines.append(f"  {stage}: {avg_time/60:.1f} minutes (samples: {len(times)})")
            lines.append("")

        # Bottleneck detection
        lines.append("Bottleneck Analysis:")
        if stage_times:
            slowest_stage = max(stage_times.items(), key=lambda x: sum(x[1])/len(x[1]))
            avg_slowest = sum(slowest_stage[1]) / len(slowest_stage[1])
            lines.append(f"  Slowest Stage: {slowest_stage[0]} ({avg_slowest/60:.1f} minutes avg)")
        lines.append("")

        # Parallel processing check
        lines.append("Concurrency:")
        lines.append(f"  Jobs processed: {len(self.job_ids)}")
        if len(self.job_ids) > 1 and completed > 0:
            avg_runtime = sum((report_time - self.job_data[jid]['start_time']).total_seconds()
                            for jid in self.job_ids if jid in self.job_data) / len(self.job_ids)

            if avg_runtime > 0:
                efficiency = (avg_runtime * len(self.job_ids)) / total_runtime
                lines.append(f"  Parallel efficiency: {efficiency:.1f}x")
                lines.append(f"  (1.0 = sequential, {len(self.job_ids)}.0 = perfect parallel)")
        lines.append("")

        # Write report
        report_text = "\n".join(lines)
        report_file.write_text(report_text)

        # Print report
        print("\n" + report_text)
        print(f"\n📄 Report saved to: {report_file}")

        return report_file

    def monitor(self, interval=5, max_duration=14400):
        """
        Monitor jobs until complete or timeout.

        Args:
            interval: Seconds between status checks
            max_duration: Maximum monitoring duration in seconds (default 4 hours)
        """
        if self.watch_count:
            self.watch_for_new_jobs()

        if not self.job_ids:
            print("❌ No jobs to monitor!")
            return

        print(f"🚀 Starting batch monitor for {len(self.job_ids)} jobs")
        print(f"   Update interval: {interval}s")
        print(f"   Max duration: {max_duration/3600:.1f} hours")
        print()

        timeout = time.time() + max_duration

        try:
            while time.time() < timeout:
                self.update_job_data()
                self.print_status()

                if self.is_batch_complete():
                    print("✅ All jobs completed!")
                    break

                time.sleep(interval)

            if time.time() >= timeout:
                print("⏰ Monitoring timeout reached!")

        except KeyboardInterrupt:
            print("\n⚠️  Monitoring interrupted by user")

        finally:
            print("\n📊 Generating final report...\n")
            self.generate_report()


def main():
    parser = argparse.ArgumentParser(description='Monitor batch processing of subtitle jobs')
    parser.add_argument('--jobs', type=str, help='Comma-separated list of job IDs to monitor')
    parser.add_argument('--watch-new', type=int, help='Wait for and monitor N new jobs')
    parser.add_argument('--all', action='store_true', help='Monitor all active jobs')
    parser.add_argument('--interval', type=int, default=5, help='Update interval in seconds (default: 5)')
    parser.add_argument('--max-hours', type=float, default=4, help='Maximum monitoring duration in hours (default: 4)')

    args = parser.parse_args()

    job_ids = None
    watch_count = None

    if args.jobs:
        job_ids = [j.strip() for j in args.jobs.split(',')]
    elif args.watch_new:
        watch_count = args.watch_new
    elif args.all:
        # Get all active jobs
        monitor = JobMonitor()
        active = monitor.get_active_jobs()
        job_ids = [job[0] for job in active]
        if not job_ids:
            print("No active jobs to monitor")
            return
    else:
        parser.print_help()
        return

    monitor = JobMonitor(job_ids=job_ids, watch_count=watch_count)
    monitor.monitor(interval=args.interval, max_duration=int(args.max_hours * 3600))


if __name__ == '__main__':
    main()
