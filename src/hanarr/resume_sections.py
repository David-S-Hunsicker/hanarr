"""Heuristic, deterministic splitting of raw resume text into display
sections (contact/summary block, then EXPERIENCE, SKILLS, EDUCATION, etc.).

No LLM call: this is purely presentational, so the Resume page's "Active
resume" box can render each section on its own instead of one giant
unformatted text blob, even when no model is configured or pulled yet --
consistent with the rest of the app always having a deterministic path
that doesn't require an LLM.

This does not try to be a general resume parser. It only recognizes the
common convention of a short standalone line acting as a section header
(plain-text and Markdown resumes both tend to do this, e.g. "EXPERIENCE"
or "## Experience"); anything it can't confidently identify just stays in
one section, which reproduces the previous single-block behavior rather
than mangling unusual formatting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_HEADING_LENGTH = 45

_KNOWN_HEADINGS = {
    "summary", "objective", "profile", "about", "contact",
    "experience", "work experience", "professional experience", "employment",
    "employment history", "education", "skills", "technical skills",
    "core competencies", "projects", "certifications", "certification",
    "licenses", "awards", "honors", "publications", "languages",
    "volunteer", "volunteering", "interests", "references",
}


@dataclass(frozen=True)
class ResumeSection:
    heading: str | None  # None for the leading block (name/contact/summary)
    body: str


def _strip_markdown_heading(line: str) -> tuple[str, bool]:
    match = re.match(r"^#{1,6}\s+(.*)$", line.strip())
    if match:
        return match.group(1).strip(), True
    return line, False


def _looks_like_heading(raw_line: str) -> bool:
    line, was_markdown = _strip_markdown_heading(raw_line)
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_LENGTH:
        return False
    if stripped[-1] in ".,;:":
        return False
    if stripped[0] in "-•*·—" or re.match(r"^\d+[.)]", stripped):
        return False
    if "|" in stripped:
        return False
    if was_markdown:
        return True
    letters_only = re.sub(r"[^A-Za-z]", "", stripped)
    if not letters_only:
        return False
    if letters_only.isupper() and len(letters_only) >= 3:
        return True
    return stripped.lower().rstrip(":") in _KNOWN_HEADINGS


def split_resume_into_sections(content: str) -> list[ResumeSection]:
    """Splits on lines that look like section headers. The leading section
    (before the first detected header, if any) always has heading=None --
    that's the name/contact-info/summary block resumes conventionally open
    with, which reads fine as plain prose without a label. If nothing in
    the whole document looks like a header, the result is a single
    heading=None section containing everything, matching the pre-existing
    unsplit display exactly."""
    sections: list[ResumeSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for raw_line in content.splitlines():
        if _looks_like_heading(raw_line):
            if current_heading is not None or current_lines:
                sections.append(ResumeSection(current_heading, "\n".join(current_lines).strip("\n")))
            current_heading, _ = _strip_markdown_heading(raw_line)
            current_heading = current_heading.strip()
            current_lines = []
        else:
            current_lines.append(raw_line)

    if current_heading is not None or current_lines:
        sections.append(ResumeSection(current_heading, "\n".join(current_lines).strip("\n")))

    return [s for s in sections if s.heading or s.body.strip()]
