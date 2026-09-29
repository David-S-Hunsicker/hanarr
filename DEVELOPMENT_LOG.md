# Hanarr development log

`TRANSITION_PLAN.md` documented the `job-search-copilot` → Hanarr transition and is retired now
that the transition it describes is complete (full text remains in git history if it's ever
needed). **README.md stays the current-state reference** — setup, status, and what's verified
live there. This file is the ongoing one: a running log of notable changes as they ship, and a
place to keep real design plans for what's next before they're built, so a plan doesn't have to
live only in a chat transcript.

## Planned

Two features are scoped below, not yet implemented. Each still needs a design decision flagged
inline before work starts. (The third planned feature, the "How to use Hanarr" guide page, shipped
— see the Log below.)

## Backlog

## Deferred / low priority

Real ideas, but nobody's actually blocked by either — no need pulling on them right now, so they
sit here instead of Planned. Revisit if a concrete case for one comes up.

- **Fuzzy/synonym skill matching** (e.g. "JS" ↔ "JavaScript") — would reduce occasional false
  "missing" flags, but a wrong synonym mapping risks the opposite failure (a false *positive* —
  claiming a skill that isn't really there), which is worse than what it fixes.
- **Persisted, cross-run activity history** — the Jobs page already shows a live scrolling log
  during an active search; this would only add value across restarts/past runs (an audit
  nice-to-have), not something blocking real use today.

### Mini-interview skill assessment

**Problem.** The Skills page's confidence is either resume-extracted (a guess from wording) or
manually self-reported. Neither actually tests whether the person can still produce the
knowledge — someone can claim "Kubernetes: 70%" and be right, rusty, or wrong, and the page can't
tell the difference today.

**Flow.**
1. From a skill row on the Skills page, a "Quick skill check" action starts a short, bounded
   Q&A: the LLM generates 2–4 targeted questions about that skill, calibrated to the claimed
   proficiency and any existing evidence (resume wording, project evidence, prior interview
   results) so the questions aren't generic trivia.
2. The user answers in free text, one round — this is not an open-ended chat, to keep it
   explainable and bounded like every other LLM-backed step in the app.
3. One evaluation call classifies the result into one of three outcomes, per your framing:
   - **Solid** — confidence is corroborated; bump `ProfileSkill.confidence` (new
     `source="interview"` evidence row, evaluated the same way `sync_github_skills` avoids
     clobbering an existing row — this one should be allowed to *update* an interview-sourced
     row but never silently overwrite a higher-trust "resume"/"manual" source without saying so).
   - **Remediation** — the person clearly has real experience but the answers show memory decay,
     not absent skill. Suggest one or two named information sources (docs page, canonical
     article/book, course) as a light refresher — no project needed.
   - **Rebuild** — either a greenfield skill with no real depth yet, or experience that's degraded
     badly enough that a refresher wouldn't cut it. Offer to start a coaching project instead,
     reusing the existing `POST /api/coaching-projects` reusable-skill flow rather than building a
     second project system.

**Data model.** A new table (`SkillInterview` or similar): `profile_id`, `skill_id`, the
questions/answers actually asked (for audit/review, same spirit as `AgentRun`/evaluation
records elsewhere), the verdict (`solid` / `remediate` / `rebuild`), suggested resources (plain
text/LLM-authored, clearly labeled as unverified — Hanarr has no web-search capability, so these
are the model's suggestions, not fetched/validated links), and a link to a created project when
the verdict is `rebuild` and the user opts in. Manual model output failures fall back to "could
not assess" rather than guessing a verdict, matching the deterministic-fallback pattern used
everywhere else (`extract_profile_summary`, coaching briefs, evaluations).

**Open question.** Does this live as an inline expansion under the existing skill row
(`skills.html`), or as its own small full-page flow launched from there? Inline keeps everything
on one page but the Skills page is already dense; a dedicated page is cleaner for a
multi-question flow. Leaning toward a dedicated page (`/skills/{id}/interview`) linked from the
row, mirroring how Coaching is its own page rather than crammed into Jobs.

### STAR story builder

**Problem.** Behavioral ("tell me about a time you...") questions reward a well-rehearsed,
specific story more than raw ability — most candidates have the underlying experience but never
turn it into a structured, memorable answer ahead of time.

**Flow.**
1. Generate candidate behavioral questions from the resume's actual work history (titles,
   seniority, industries) — optionally weighted toward a specific saved job's stated
   requirements/culture signals when launched from a job rather than generically. Group by common
   competency buckets (leadership, conflict, failure/mistake, ambiguity, technical trade-off,
   cross-team collaboration, etc.) so the user can see coverage gaps, not just a flat list.
2. For a question the user picks, an interactive builder walks the four STAR components
   (Situation, Task, Action, Result) one at a time: the user writes in their own words, the LLM
   asks a clarifying follow-up if a component is vague or the Result has no concrete/measurable
   outcome, and tightens wording — it never invents an achievement the user didn't state. This is
   the same "capability vs. evidence vs. wording" boundary the Resume/Skills pages already
   enforce, applied to interview stories instead of resume bullets.
3. Finished (and in-progress) stories persist in a per-profile story bank: revisit, edit, mark
   complete, and re-practice, instead of rebuilding a story from scratch every job search cycle.

**Data model.** New `StarQuestion` (profile_id, question text, competency tag, source: generic vs.
job-specific with an optional `job_id`) and `StarStory` (question_id, situation/task/action/result
text fields, status: draft/complete, timestamps) tables — additive migration, same pattern as
every prior schema addition (`0001`–`0009` in `alembic/versions/`).

**Open question — nav placement.** This doesn't fit cleanly into Skills (not skill-specific),
Resume (not resume content), or Coaching (not a project). Combined with the mini-interview
feature above, there may be a case for a new top-level **Interview Prep** tab covering both —
but that's a nav-structure decision worth deciding deliberately rather than bolting one more
thing onto an existing page. Proposal: hold off on adding a new top-level nav tab until both
this and the mini-interview feature are at least partially built, then decide once there's real
content to place, rather than guessing the right shape upfront.

## Log

Dated entries go here as work ships, newest first. Not a full history — `git log` is authoritative
for that; this captures the *why* behind notable changes, the way commit messages don't always
carry forward into a skimmable list.

### 2026-09-29 — v0.1.8

Shipped: the license narrowed to personal job-search use only, and the org-exemption removal
before it (both described below).

### 2026-09-29 — v0.1.7

Shipped: the license change, SQLite WAL mode + checkpoint-before-backup, and file-backed logging
with a catch-all exception handler, all described below.

### 2026-09-29 — License narrowed to personal job-search use only

Follow-up to the license change below: "any noncommercial purpose" was still broader than
intended — it would have covered unrelated hobby projects, research, or general tinkering, not
just the thing Hanarr is actually for. Replaced PolyForm Strict's "Noncommercial Purposes"/
"Personal Uses" sections with a custom "Permitted Purpose" section: the *only* permitted use is
running Hanarr, unmodified, for your own personal job search (searching postings, tracking your
own applications, preparing your own materials). Everything else — any organizational or
commercial use, or any use unrelated to your own job search — needs the copyright holder's
express permission, no exceptions. This is now enough of a departure from the original license
text that it's named/framed in `LICENSE` as a custom license based on PolyForm Strict, rather than
"PolyForm Strict, modified" — the underlying mechanics (no modification, no redistribution,
patent/liability terms) are still borrowed from it, but what counts as permitted use is entirely
custom now.

### 2026-09-29 — License changed from MIT to a modified PolyForm Strict 1.0.0

Repo stays public/source-available, but the license itself now restricts what people can do with
the code: view and run it for personal/noncommercial use, but not modify it, redistribute it
(modified or not), or use it commercially without a separate agreement with the copyright holder.
PolyForm Strict does exactly this out of the box, with one adjustment: its stock text carves out a
blanket exemption for any charity/school/government/public-research/etc. organization regardless
of funding source — removed here, since the ask was no organization gets a free pass, full stop.
"Any noncommercial purpose is a permitted purpose" is now the only test, applied the same way to
an individual, a business, or a nonprofit alike. See `LICENSE` (full text, with the removed
section and the reasoning called out at the top, plus a plain-English summary at the bottom) and
the polyformproject.org
source this was pulled from verbatim, rather than drafting custom legal text from scratch.
(Narrowed further the same day — see the entry above.)

### 2026-09-29 — SQLite WAL mode, for the same bug report

Follow-up to the logging fix below, same bug report (a 500 after starting a search, on the
Coaching page). Couldn't get a real traceback yet (the installed build predates the logging fix),
but the shape fits a known SQLite footgun well enough to fix proactively: a background search
commits frequently (once per matched/rejected posting) while the dashboard's own page loads run
concurrent reads on separate connections from the same pool. SQLite's default rollback-journal
mode lets a writer's transaction briefly block readers — under real contention (slow disk,
antivirus intercepting file I/O, a slower LLM stretching out how long the search runs) that can
exceed the default lock wait and surface as an unhandled "database is locked" error on whatever
page was loading. `db.py` now sets `PRAGMA journal_mode=WAL` and a 30s `busy_timeout` on every
connection — WAL is SQLite's standard fix for readers not blocking on an in-progress writer.

Side effect worth calling out: WAL mode means a recent commit can sit only in the `-wal` file
until checkpointed, so the existing pre-migration backup (a plain `shutil.copy2` of `hanarr.db`)
needed a `PRAGMA wal_checkpoint` added right before it, or it could silently copy a stale (in one
tested case, nearly-empty) snapshot. Tried `TRUNCATE`/`FULL` checkpoint mode first — both block
against a concurrent reader/writer, and tested directly against one, `TRUNCATE` actually corrupted
the checkpoint. Landed on `PASSIVE` (the default, no argument): never blocks or forces anything,
just folds in whatever it safely can, which is what an already-single-threaded startup moment
needs anyway.

This may or may not be the actual bug behind the report — still waiting on a real traceback to
confirm — but it's a real, independently-worth-fixing class of issue either way.

### 2026-09-29 — File-backed logging for the packaged build

Real gap found from a live bug report: a 500 on the Jobs page after starting a search, reported
against the standalone packaged build, with nothing to go on beyond "internal server error" — the
packaged executable runs `--windowed` (no console), so every unhandled exception's traceback was
going to stderr and vanishing into nothing. `logging_setup.configure_file_logging()` adds a
rotating file handler (`<data_dir>/logs/hanarr.log`, capped at ~2MB × 3 files) alongside the
existing console logging; `create_app()` also gained a catch-all FastAPI exception handler so an
unhandled error is guaranteed to log a full traceback before returning a plain 500, rather than
relying on uvicorn's own exception logging path. This doesn't fix the reported bug by itself —
still needs a real reproduction with a traceback in hand — but makes any future occurrence (this
one included) actually diagnosable instead of a dead end.

### 2026-09-29 — Workday connector

Greenhouse/Lever/Ashby all skew toward VC-funded tech/startup companies; this reaches the
traditional-enterprise/healthcare/large-non-tech employers that mostly post through Workday
instead. Unlike the others, this is an *undocumented* endpoint (the same JSON API a Workday
careers page's own JS calls, not something Workday publishes or supports) — researched against
several independent real-world write-ups before writing anything, then live-verified against
NVIDIA's actual public Workday careers site.

Two things that make it a genuinely different shape from the other connectors: (1) the list
endpoint doesn't include a job description at all, only a second per-job request does, so this
is real N+1 request cost bounded by a `MAX_POSTINGS_PER_SITE` cap (200) rather than the single
request per company the others need; (2) configuring a company means pasting its full public
careers URL (tenant/shard/site aren't discoverable any other way — there's no directory), parsed
server-side rather than asking for three separate fields. `postedOn`/`startDate` in Workday's
response are either a localized relative string or ambiguous in meaning, so `posted_at` is left
unset rather than guessed.

### 2026-09-29 — v0.1.6

Shipped: the version number is now visible in the app itself (Settings page header), not just
`pyproject.toml`/`__init__.py` — no reason to have to dig into the source tree to tell what
you're running.

### 2026-09-29 — Per-profile search preferences

Multiple local profiles have shared one set of match criteria (target titles, locations, salary
floor, dealbreakers, etc.) from `config.yaml` since profiles first existed — real for a solo user,
but not meaningfully multi-user for a household sharing an instance. `Profile.preferences_json`
(empty by default) plus `config.effective_preferences(profile, settings)` (falls back live to the
shared value until a profile saves its own) makes this per-profile with zero migration step for an
existing single-profile install. Settings → Preferences now saves onto the active profile; a new
profile starts from bare defaults, not a copy of whatever's already configured — likely a
different person. Job sources, LLM provider, and schedule stay shared.

Found a real bug via the isolation test itself, not from a bug report: the stale-save conflict
check used one global version counter for every tab *and* every profile, so one profile saving
would spuriously block a different profile's unrelated save. Fixed with a separate per-profile
`preferences_version` counter (migration 0012) that only the Preferences tab checks.

### 2026-09-29 — Manual job entry

Third smaller backlog item: a "+ Add a job manually" form on the Jobs page for a posting
Hanarr's own connectors didn't find. Scored through the exact same `matching.score_fit` path a
real search cycle uses, so it gets a real fit score/rationale, not a placeholder. No hard-delete
exists yet for a stray manual entry (only status changes) — worth adding if this gets used a lot.

### 2026-09-29 — .ics calendar export

Second smaller backlog item: `GET /reminders/{id}.ics` and `GET /reminders.ics` export
follow-up/interview-prep reminders as real calendar events. Hand-wrote minimal RFC 5545 rather
than adding a dependency. See `calendar_export.py`.

### 2026-09-29 — Cover-letter drafting

Shipped the first of the smaller backlog items: cover-letter drafting for a saved job, reusing
the existing `resume_writer` agent role with a deterministic mail-merge fallback. Simpler than
the resume-proposal loop by design — a draft never needs approval to "activate" anything, so it's
just stored text the user edits and copies themselves. See `cover_letter.py`.

### 2026-09-29 — v0.1.5

Shipped: profile renaming (a profile named for a one-off purpose, e.g. "UI Check" from a QA
pass, was stuck that way with no fix short of editing the database directly), and a company-name
styling fix — dropped an unwanted "Company:" prefix in favor of a dedicated `--company-accent`
color so the company name stands out without a label.

### 2026-09-29 — Guide page

Shipped the first of the three planned features: an in-app `/guide` page explaining Hanarr's
workflow, what each page is for, what fit scores/skill-gap statuses mean, and the local-Ollama-
vs-hosted-Anthropic-vs-none trade-off. Hand-written content, not LLM-generated, since it explains
Hanarr's own real behavior rather than something a model should guess at.

### 2026-09-29 — v0.1.4

Shipped the `DEVELOPMENT_LOG.md`/`TRANSITION_PLAN.md` restructure described above, plus two Jobs-
page UX fixes reported directly: a duplicate header link ("Preferences & config") pointed at the
exact same route as the Settings tab immediately below it, and job posting title links had no
underline and the same color as body text, so they didn't read as clickable until hovered — both
fixed, plus an explicit "Apply" link and a "Company:" label on the company line.

### 2026-09-29 — v0.1.3

Shipped: the config-autosave data-loss fix (optimistic concurrency control + `config.yaml`
backups — the incident that prompted it is worth remembering: a stale autosave tab silently wiped
`locations`/`salary_floor_usd`/job-source config because the form always resubmitted every field,
not just the one edited), the "Why this score" duplicate-label fix, "Improve my fit" staleness
detection (resurfaces the button when the active resume changes after an analysis) and real error
surfacing instead of silently reverting on failure, a "View on Skills page" deep link next to
"Start project for {skill}" on a job's gap list, and best-effort skill-evidence sync from a
GitHub profile URL found in the resume (`source="github"` `ProfileSkill` rows from public,
non-fork repo languages — niche, mainly useful for software engineers, never overrides existing
evidence).

Retired `TRANSITION_PLAN.md` in favor of this file, and scoped the three planned features above.
