import datetime as dt

from hanarr.calendar_export import reminder_to_vevent, reminders_to_ics
from hanarr.models import JobPosting, Reminder, ReminderType


def _reminder(**overrides) -> Reminder:
    defaults = dict(
        id=1, profile_id=1, job_id=None, type=ReminderType.FOLLOW_UP,
        message="Follow up on your application.",
        due_at=dt.datetime(2026, 3, 5, 14, 0, 0),
        created_at=dt.datetime(2026, 3, 1, 9, 0, 0),
    )
    defaults.update(overrides)
    return Reminder(**defaults)


def test_reminder_to_vevent_includes_required_fields_and_utc_timestamps():
    reminder = _reminder()
    vevent = reminder_to_vevent(reminder)

    assert "BEGIN:VEVENT" in vevent
    assert "END:VEVENT" in vevent
    assert "UID:hanarr-reminder-1@localhost" in vevent
    assert "DTSTART:20260305T140000Z" in vevent
    assert "DTSTAMP:20260301T090000Z" in vevent
    assert "SUMMARY:Hanarr: Follow Up" in vevent
    assert "DESCRIPTION:Follow up on your application." in vevent


def test_reminder_to_vevent_includes_job_context_when_linked_to_a_job():
    job = JobPosting(
        id=7, profile_id=1, source="test", external_id="1",
        company="Acme", title="Backend Engineer", url="https://example.test",
    )
    reminder = _reminder(job_id=7, type=ReminderType.INTERVIEW_PREP)
    reminder.job = job

    vevent = reminder_to_vevent(reminder)

    assert "SUMMARY:Hanarr: Interview Prep — Acme" in vevent
    assert "Backend Engineer at Acme" in vevent


def test_reminder_to_vevent_escapes_special_characters_in_text_fields():
    reminder = _reminder(message="Prep notes: bring resume, cover letter; ask about pay.")

    vevent = reminder_to_vevent(reminder)

    assert "bring resume\\, cover letter\\; ask about pay." in vevent


def test_reminders_to_ics_wraps_events_in_a_valid_calendar():
    reminders = [_reminder(id=1), _reminder(id=2, due_at=dt.datetime(2026, 3, 6, 10, 0, 0))]

    ics = reminders_to_ics(reminders)

    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert ics.count("BEGIN:VEVENT") == 2
    assert "VERSION:2.0" in ics


def test_reminders_to_ics_handles_an_empty_list():
    ics = reminders_to_ics([])

    assert ics == "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Hanarr//Reminders//EN\r\nCALSCALE:GREGORIAN\r\nEND:VCALENDAR\r\n"


def test_fold_wraps_lines_over_75_octets():
    long_message = "x" * 200
    reminder = _reminder(message=long_message)

    vevent = reminder_to_vevent(reminder)

    for line in vevent.split("\r\n"):
        assert len(line) <= 75
