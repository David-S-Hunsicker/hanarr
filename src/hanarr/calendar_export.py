"""Minimal RFC 5545 (iCalendar) export for reminders.

No external calendar library -- a reminder maps to a handful of required
VEVENT fields, so hand-writing the text format is simpler than adding a
dependency for it.
"""
from __future__ import annotations

import datetime as dt

from .models import Reminder

_ICS_ESCAPE = {"\\": "\\\\", ";": "\\;", ",": "\\,", "\n": "\\n"}


def _escape(text: str) -> str:
    return "".join(_ICS_ESCAPE.get(ch, ch) for ch in text)


def _fold(line: str) -> str:
    """RFC 5545 lines must not exceed 75 octets; a continuation line starts
    with a single space. Reminder text is usually short, but this keeps
    output correct if it isn't."""
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    rest = line[75:]
    while rest:
        parts.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(parts)


def _format_dt(value: dt.datetime) -> str:
    # Reminder timestamps are naive-but-UTC (see models.utc_now) -- format
    # as a UTC timestamp so every calendar app interprets it the same way
    # regardless of the viewer's own local timezone.
    return value.strftime("%Y%m%dT%H%M%SZ")


def reminder_to_vevent(reminder: Reminder) -> str:
    summary = f"Hanarr: {reminder.type.value.replace('_', ' ').title()}"
    description = reminder.message
    if reminder.job is not None:
        summary += f" — {reminder.job.company}"
        description = f"{reminder.job.title} at {reminder.job.company}\n{description}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:hanarr-reminder-{reminder.id}@localhost",
        f"DTSTAMP:{_format_dt(reminder.created_at)}",
        f"DTSTART:{_format_dt(reminder.due_at)}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "END:VEVENT",
    ]
    return "\r\n".join(_fold(line) for line in lines)


def reminders_to_ics(reminders: list[Reminder]) -> str:
    header = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Hanarr//Reminders//EN\r\nCALSCALE:GREGORIAN"
    events = "\r\n".join(reminder_to_vevent(r) for r in reminders)
    body = f"{header}\r\n{events}\r\n" if events else f"{header}\r\n"
    return f"{body}END:VCALENDAR\r\n"
