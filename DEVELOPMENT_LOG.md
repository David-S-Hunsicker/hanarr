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

- **A Workday connector** — Greenhouse/Lever/Ashby all skew toward VC-funded tech/startup
  companies; Workday's public job-board API is where most traditional enterprises, healthcare
  systems, and large non-tech employers actually post. Needs the real API contract researched
  before writing anything — not something to guess at the way Greenhouse's was reused.

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
