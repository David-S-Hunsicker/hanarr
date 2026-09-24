"""Reminder generation and delivery.

Reminders are created reactively when a job's status changes (see
mark_status in cli.py) — e.g. marking a posting "applied" schedules a
follow-up reminder N days out. `check_due_reminders` is called
periodically (by the scheduler or `hanarr remind`) to find and
deliver anything due.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import RemindersConfig
from .models import JobPosting, Profile, Reminder, ReminderType, utc_now

logger = logging.getLogger(__name__)


def schedule_follow_up(session: Session, profile: Profile, job: JobPosting, cfg: RemindersConfig) -> Reminder:
    due = utc_now() + dt.timedelta(days=cfg.follow_up_after_days)
    reminder = Reminder(
        profile_id=profile.id,
        job_id=job.id,
        type=ReminderType.FOLLOW_UP,
        message=f"Follow up on your application to {job.title} at {job.company}",
        due_at=due,
    )
    session.add(reminder)
    session.commit()
    return reminder


def schedule_interview_prep(session: Session, profile: Profile, job: JobPosting, interview_at: dt.datetime) -> Reminder:
    due = interview_at - dt.timedelta(days=1)
    reminder = Reminder(
        profile_id=profile.id,
        job_id=job.id,
        type=ReminderType.INTERVIEW_PREP,
        message=f"Prep for your interview at {job.company} ({job.title})",
        due_at=due,
    )
    session.add(reminder)
    session.commit()
    return reminder


def get_due_reminders(session: Session, profile: Profile) -> list[Reminder]:
    now = utc_now()
    return list(
        session.execute(
            select(Reminder).where(
                Reminder.profile_id == profile.id,
                Reminder.completed.is_(False),
                Reminder.due_at <= now,
            )
        ).scalars()
    )


def deliver_reminders(reminders: list[Reminder], cfg: RemindersConfig) -> None:
    if not reminders:
        return
    for r in reminders:
        logger.info("Reminder due: %s", r.message)
    if cfg.desktop_notifications:
        _send_desktop_notifications(reminders)
    if cfg.email.enabled:
        _send_email_digest(reminders, cfg)


def _send_desktop_notifications(reminders: list[Reminder]) -> None:
    try:
        from plyer import notification
    except Exception:  # noqa: BLE001
        logger.warning("plyer not available; skipping desktop notifications")
        return
    for r in reminders:
        try:
            notification.notify(title="job-search-copilot", message=r.message, timeout=10)
        except Exception:  # noqa: BLE001
            logger.warning("Desktop notification failed (no notification backend on this OS?)")
            break


def _send_email_digest(reminders: list[Reminder], cfg: RemindersConfig) -> None:
    if not (cfg.email.smtp_host and cfg.email.to_address and cfg.email.smtp_password):
        logger.warning("Email reminders enabled but SMTP settings/SMTP_PASSWORD are incomplete; skipping")
        return
    body = "\n".join(f"- {r.message} (due {r.due_at.isoformat()})" for r in reminders)
    msg = MIMEText(body)
    msg["Subject"] = f"job-search-copilot: {len(reminders)} reminder(s) due"
    msg["From"] = cfg.email.smtp_user
    msg["To"] = cfg.email.to_address

    try:
        with smtplib.SMTP(cfg.email.smtp_host, cfg.email.smtp_port) as server:
            server.starttls()
            server.login(cfg.email.smtp_user, cfg.email.smtp_password)
            server.send_message(msg)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send reminder email")


def mark_completed(session: Session, reminders: list[Reminder]) -> None:
    for r in reminders:
        r.completed = True
    session.commit()
