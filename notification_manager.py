import os
import logging
from typing import Optional
from datetime import datetime
from email_utils import send_email

logger = logging.getLogger("OmegaNotifications")

class NotificationManager:
    """
    Handles notifications defined in the Master Script Spec.
    Triggers:
    - Review request sent
    - Provisional delivery issued
    - Final delivery issued
    - Revision requested
    - Revision approved/rendering
    - Revision delivered
    - Output-only override delivered
    """

    @staticmethod
    def _get_operator_email() -> str:
        # Fallback to reviewer email if operator specific env not set
        return os.environ.get("OMEGA_OPERATOR_EMAIL", os.environ.get("OMEGA_REVIEWER_EMAIL", "")).strip()

    @staticmethod
    def notify_review_request(
        reviewer_email: str,
        program_title: str,
        review_url: str,
        due_date: str = None
    ) -> bool:
        """
        Notify reviewer that a script is ready for review.
        """
        subject = f"Review Request: {program_title}"
        body = (
            f"Hello,\n\n"
            f"The Master Script for '{program_title}' is ready for your review.\n"
            f"Please review and approve by: {due_date or 'ASAP'}.\n\n"
            f"Review Link: {review_url}\n\n"
            f"Thank you,\nOmega System"
        )
        return send_email(subject=subject, body=body, to_addrs=reviewer_email)

    @staticmethod
    def notify_provisional_delivery(
        client_email: str,
        program_title: str,
        download_link: str,
        version: str
    ) -> bool:
        """
        Notify client of provisional delivery (watermarked/pre-lock).
        """
        subject = f"Provisional Delivery: {program_title} (v{version})"
        body = (
            f"Hello,\n\n"
            f"A provisional version (v{version}) of '{program_title}' is available.\n"
            f"Note: This is a provisional delivery and may be subject to further quality control.\n\n"
            f"Download: {download_link}\n\n"
            f"Best,\nOmega Team"
        )
        return send_email(subject=subject, body=body, to_addrs=client_email)

    @staticmethod
    def notify_final_delivery(
        client_email: str,
        program_title: str,
        download_link: str,
        version: str
    ) -> bool:
        """
        Notify client of final delivery (Master Locked).
        """
        subject = f"Final Delivery: {program_title} (v{version})"
        body = (
            f"Hello,\n\n"
            f"The final version (v{version}) of '{program_title}' has been delivered.\n"
            f"This master script is now LOCKED.\n\n"
            f"Download: {download_link}\n\n"
            f"Best,\nOmega Team"
        )
        return send_email(subject=subject, body=body, to_addrs=client_email)

    @staticmethod
    def notify_revision_requested(
        program_title: str,
        requested_by: str,
        reason: str
    ) -> bool:
        """
        Alert Operator that a revision has been requested (unlocks Master).
        """
        operator_email = NotificationManager._get_operator_email()
        if not operator_email:
            logger.warning("No operator email configured for revision alert.")
            return False

        subject = f"ACTION REQUIRED: Revision Requested for {program_title}"
        body = (
            f"Alert,\n\n"
            f"A revision has been requested for the locked master '{program_title}'.\n"
            f"Requested By: {requested_by}\n"
            f"Reason: {reason}\n\n"
            f"The Master Script must be unlocked and version incremented.\n\n"
            f"System"
        )
        return send_email(subject=subject, body=body, to_addrs=operator_email)

    @staticmethod
    def notify_revision_started(
        client_email: str,
        program_title: str,
        eta: str = None
    ) -> bool:
        """
        Notify client that their revision request is approved and processing.
        """
        subject = f"Revision Started: {program_title}"
        body = (
            f"Hello,\n\n"
            f"We have started working on the requested revision for '{program_title}'.\n"
            f"Estimated delivery: {eta or 'Coming soon'}.\n\n"
            f"Best,\nOmega Team"
        )
        return send_email(subject=subject, body=body, to_addrs=client_email)

    @staticmethod
    def notify_revision_delivered(
        client_email: str,
        program_title: str,
        download_link: str,
        version: str
    ) -> bool:
        """
        Notify client of delivered revision.
        """
        subject = f"Revision Delivered: {program_title} (v{version})"
        body = (
            f"Hello,\n\n"
            f"The revised version (v{version}) of '{program_title}' is ready.\n\n"
            f"Download: {download_link}\n\n"
            f"Best,\nOmega Team"
        )
        return send_email(subject=subject, body=body, to_addrs=client_email)

    @staticmethod
    def notify_override_delivered(
        client_email: str,
        program_title: str,
        download_link: str,
        output_type: str, # Subtitle or Dub
        version: str
    ) -> bool:
        """
        Notify client of an output-only override delivery (Master not changed).
        """
        subject = f"Update Delivered: {program_title} ({output_type} v{version})"
        body = (
            f"Hello,\n\n"
            f"An update for the {output_type} track of '{program_title}' has been delivered (v{version}).\n"
            f"This is a specific output update; the Master Script remains unchanged.\n\n"
            f"Download: {download_link}\n\n"
            f"Best,\nOmega Team"
        )
        return send_email(subject=subject, body=body, to_addrs=client_email)

    @staticmethod
    def notify_stuck_jobs(
        stuck_jobs: list,
        dashboard_url: str = "http://127.0.0.1:8080"
    ) -> bool:
        """
        Alert Operator that one or more jobs are stuck and may need attention.
        """
        operator_email = NotificationManager._get_operator_email()
        if not operator_email:
            logger.warning("No operator email configured for stuck job alerts.")
            return False

        if not stuck_jobs:
            return False

        job_list = "\n".join([
            f"  - {j.get('stem', 'Unknown')}: {j.get('stage', '?')} for {j.get('stuck_duration', '?')}"
            for j in stuck_jobs
        ])

        subject = f"⚠️ OMEGA ALERT: {len(stuck_jobs)} Job(s) May Be Stuck"
        body = (
            f"Attention,\n\n"
            f"The following jobs appear to be stuck and may need attention:\n\n"
            f"{job_list}\n\n"
            f"Possible actions:\n"
            f"1. Check the dashboard: {dashboard_url}\n"
            f"2. Run health diagnostics: GET {dashboard_url}/api/v2/health/diagnose\n"
            f"3. Auto-fix issues: POST {dashboard_url}/api/v2/health/fix with {{\"fix_all\": true}}\n\n"
            f"System"
        )
        return send_email(subject=subject, body=body, to_addrs=operator_email)

    @staticmethod
    def notify_dead_jobs(
        dead_jobs: list,
        dashboard_url: str = "http://127.0.0.1:8080"
    ) -> bool:
        """
        Alert Operator that jobs have entered DEAD state and require intervention.
        """
        operator_email = NotificationManager._get_operator_email()
        if not operator_email:
            logger.warning("No operator email configured for dead job alerts.")
            return False

        if not dead_jobs:
            return False

        job_list = "\n".join([
            f"  - {j.get('stem', 'Unknown')}: {j.get('error', 'Unknown error')}"
            for j in dead_jobs
        ])

        subject = f"🛑 OMEGA CRITICAL: {len(dead_jobs)} Job(s) Marked DEAD"
        body = (
            f"CRITICAL ALERT,\n\n"
            f"The following jobs have failed permanently and require manual intervention:\n\n"
            f"{job_list}\n\n"
            f"These jobs will not auto-retry. Please review and take action:\n"
            f"Dashboard: {dashboard_url}\n\n"
            f"System"
        )
        return send_email(subject=subject, body=body, to_addrs=operator_email)

    @staticmethod
    def notify_system_health(
        problems: list,
        fixed: list = None,
        dashboard_url: str = "http://127.0.0.1:8080"
    ) -> bool:
        """
        Send a summary of system health issues detected and/or fixed.
        """
        operator_email = NotificationManager._get_operator_email()
        if not operator_email:
            logger.warning("No operator email configured for health alerts.")
            return False

        if not problems and not fixed:
            return False

        problem_text = ""
        if problems:
            problem_list = "\n".join([
                f"  - {p.get('stem', '?')}: {p.get('description', 'Unknown issue')}"
                for p in problems
            ])
            problem_text = f"Detected Issues ({len(problems)}):\n{problem_list}\n\n"

        fixed_text = ""
        if fixed:
            fixed_list = "\n".join([
                f"  - {f.get('stem', '?')}: {f.get('action', 'Fixed')}"
                for f in fixed
            ])
            fixed_text = f"Auto-Fixed ({len(fixed)}):\n{fixed_list}\n\n"

        subject = f"OMEGA Health Report: {len(problems or [])} issues, {len(fixed or [])} fixed"
        body = (
            f"Health Report\n\n"
            f"{problem_text}"
            f"{fixed_text}"
            f"Dashboard: {dashboard_url}\n\n"
            f"System"
        )
        return send_email(subject=subject, body=body, to_addrs=operator_email)
