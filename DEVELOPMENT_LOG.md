# Hanarr development log

`TRANSITION_PLAN.md` documented the `job-search-copilot` → Hanarr transition and is retired now
that the transition it describes is complete (full text remains in git history if it's ever
needed). **README.md stays the current-state reference** — setup, status, and what's verified
live there. This file is the ongoing one: a running log of notable changes as they ship, and a
place to keep real design plans for what's next before they're built, so a plan doesn't have to
live only in a chat transcript.

## Planned

Three features are scoped below, not yet implemented. Each still needs a design decision flagged
inline before work starts.

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

### "How to use Hanarr" guide page

**Problem.** The "Get set up" checklist (Jobs/Settings) gets a new user through initial
configuration, but there's no in-app explanation of the overall workflow or how to get the most
out of the app once it's set up — that context currently only lives in README.md, which a
packaged-app user (no terminal, no GitHub) never sees.

**Scope.** A new top-level nav tab (`/guide` or similar, alongside Jobs/Coaching/Resume/Skills/
Applications/Settings in `index.html`'s `.tabs` nav) with static, hand-written content — not
LLM-generated, since this is explaining Hanarr's own real behavior and should be accurate and
stable, not a model's guess. Content mirrors and trims README's "How it works" section for an
in-app audience: what each tab is for, the recommended order of operations (set up Settings →
review Jobs → work gaps in Coaching/Skills → track in Applications), what "Improve my fit" and
skill-gap indicators actually mean, and a short explanation of local-first/LLM-provider choices
so a non-technical user understands why Ollama setup matters before scoring works well. Once the
mini-interview and STAR features above exist, this page is also the natural place to point a new
user at them.

**No schema/API changes** — this is a template-only addition (a new Jinja page + one route +
one nav link), the lowest-risk of the three to build first.

## Log

Dated entries go here as work ships, newest first. Not a full history — `git log` is authoritative
for that; this captures the *why* behind notable changes, the way commit messages don't always
carry forward into a skimmable list.

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
