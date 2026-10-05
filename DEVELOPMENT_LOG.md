# Hanarr development log

`TRANSITION_PLAN.md` documented the `job-search-copilot` → Hanarr transition and is retired now
that the transition it describes is complete (full text remains in git history if it's ever
needed). **README.md stays the current-state reference** — setup, status, and what's verified
live there. This file is the ongoing one: a running log of notable changes as they ship, and a
place to keep real design plans for what's next before they're built, so a plan doesn't have to
live only in a chat transcript.

## Planned

Nothing is currently scoped and unstarted — networking outreach email drafts shipped, see the Log
below.

## Deferred / low priority

Real ideas, but nobody's actually blocked by either — no need pulling on them right now, so they
sit here instead of Planned. Revisit if a concrete case for one comes up.

- **Fuzzy/synonym skill matching** (e.g. "JS" ↔ "JavaScript") — would reduce occasional false
  "missing" flags, but a wrong synonym mapping risks the opposite failure (a false *positive* —
  claiming a skill that isn't really there), which is worse than what it fixes.
- **Persisted, cross-run activity history** — the Jobs page already shows a live scrolling log
  during an active search; this would only add value across restarts/past runs (an audit
  nice-to-have), not something blocking real use today.
- **A Hanarr MCP server** (`hanarr mcp` subcommand) — read-only tools (`get_resume`,
  `list_matched_jobs`, `get_skill_gaps`, `get_coaching_projects`, `get_application_pipeline`), each
  just wrapping the same queries the dashboard already runs, exposed over MCP instead of HTTP+Jinja
  so an MCP-capable AI client (Claude Desktop, Claude Code, etc.) can answer questions about your
  job search without switching to the dashboard. Deliberately separate from the in-app chatbot
  above — that one talks to the already-configured LLM provider in-process and has no process
  boundary to cross, so MCP would be pure overhead there. This is real value only insofar as
  *someone* actually has an MCP client open regularly; for a typical Hanarr user who doesn't, it's
  a power-user feature with no payoff, which is why it sits here rather than in Planned. Revisit if
  that changes.

## Log

Dated entries go here as work ships, newest first. Not a full history — `git log` is authoritative
for that; this captures the *why* behind notable changes, the way commit messages don't always
carry forward into a skimmable list.

### 2026-10-05 — v0.1.38

Shipped: dealbreakers moved out of the LLM's job entirely, into a fully deterministic prefilter
check (see below).

### 2026-10-05 — Dealbreakers are now a deterministic prefilter check, not an LLM judgment

Follow-up to v0.1.37's hallucinated-dealbreaker fix. User's framing: "if we're doing it
deterministically, then the LLM should not need to consider dealbreakers. we just need robust
checks." Rather than keep validating the LLM's dealbreaker claims after the fact,
`prefilter_rejection_reason()` now rejects a posting outright if a stated dealbreaker
keyword-matches its title/description -- the exact same substring check `_rule_based_score()`
already did for the no-LLM case, just run earlier, for everyone, before a posting is ever scored.

- `SYSTEM_PROMPT` no longer asks for (or mentions) `dealbreaker_hit` at all; `score_fit()` simply
  ignores the field if an older-prompt-trained model still includes one. The LLM's job is now only
  the required-qualifications check (`fails_minimum_requirements`) and the 0-100 fit score --
  dealbreakers are never its concern.
- `_dealbreaker_plausible()` (v0.1.37's post-hoc hallucination guard) and `_rule_based_score()`'s
  own now-redundant dealbreaker check are both removed -- dead code once dealbreakers are caught
  before either path is ever reached.
- A posting rejected for a dealbreaker no longer appears on the Jobs list at all (same as any
  other prefilter rejection -- excluded keyword, wrong location, below salary floor): it shows up
  in "Why were jobs filtered out?" with the specific dealbreaker named, not as a saved 0-scored
  card with a written rationale the way an LLM-flagged dealbreaker used to appear.
- Location remains handled by its own, already-deterministic, more sophisticated mechanism
  (`_detected_other_country_restriction`'s regex patterns, plus remote_ok/onsite_ok/locations) --
  it was never part of the freeform `preferences.dealbreakers` list this change touches, so there
  was no location-specific LLM carve-out to add.
- Guide updated: the match/fit-score explanation already listed dealbreakers as part of the
  prefilter (written in v0.1.35, now actually true), plus a new note that dealbreaker matching is
  keyword-based, not semantic -- phrasing close to how postings actually word something matters.

### 2026-10-05 — v0.1.37

Shipped: a hallucinated dealbreaker-hit claim is now rejected the same way a blank rationale is
(see below) -- v0.1.36's fix wasn't the whole story.

### 2026-10-05 — Hallucinated dealbreaker hits were also silently zeroing scores

User report (after v0.1.36 had already shipped, and after an actual restart this time): "scoring is
absolutely fucked right now. nearly everything is zeroed out." v0.1.36 only caught a blank
rationale; a *second*, more common failure mode in the same rescore run was the model confidently
claiming `dealbreaker_hit: true` with a plausible-sounding rationale ("requires a security
clearance") on 50+ completely unrelated software engineering postings at companies like Stripe and
Coinbase -- the word "clearance" didn't appear in a single one of those job descriptions. A hard,
score-zeroing disqualifier with zero textual basis is exactly as costly as a blank rationale, and
the existing guard didn't catch it because the rationale wasn't blank, just wrong.

Added a loose keyword-overlap sanity check (`_dealbreaker_plausible`): before trusting a
`dealbreaker_hit`, at least one substantive word from one of the candidate's stated dealbreakers
must appear somewhere in the posting's own title/description. Deliberately loose -- it doesn't
require the posting to use the dealbreaker's exact wording (a real dealbreaker phrased differently
still passes), only that there's *some* textual basis at all. No overlap raises
`LLMScoringFailedError`, same retry path as the blank-rationale and LLM-unreachable cases.

### 2026-10-05 — v0.1.36

Shipped: a malformed-but-valid LLM response (score 0, no rationale) is now rejected and retried
instead of silently trusted (see below).

### 2026-10-05 — Degenerate LLM responses silently zeroed out real scores

User report: "why did almost all my job scores go to zero?" after running "Rescore all jobs" --
121 of 128 saved jobs, including clearly strong-fit postings (Software Engineer roles at OpenAI,
Toast, Samsara, Airbnb), came back with `fit_score: 0.0` and a **blank** rationale. SYSTEM_PROMPT
requires a 1-3 sentence rationale on every response; a blank one is the model (qwen2.5:14b, freshly
switched to) giving up under Ollama's forced `"format": "json"` grammar on a long, detailed prompt
-- valid JSON, but not a genuine judgment. `score_fit()` had no check for this: it trusted
`{"score": 0, "rationale": ""}` exactly the same as a real, reasoned 0, overwriting whatever
(often-correct) score was there before with no indication anything was wrong.

Fixed: a response reaching the plain-score path (not the dealbreaker/fails-minimum-requirements
paths, which already supply their own fallback rationale) with an empty rationale now raises
`LLMScoringFailedError` instead of being accepted -- the same path a real LLM failure already takes,
so the caller's pause-and-retry (`pipeline._score_with_pause`) gets a shot at a real answer instead
of silently keeping a degenerate one. Does not retroactively fix scores already corrupted by this --
running "Rescore all jobs" again, now with this guard in place, will.

### 2026-10-05 — v0.1.35

Shipped: renamed "Rematch" to "Rescore" everywhere (buttons, routes, code, docs) and explained the
match-vs-fit distinction in the Guide (see below).

### 2026-10-05 — "Rematch" renamed to "Rescore"; match vs. fit explained in the Guide

User feedback: "we need to choose either 'fit' or 'match' and be consistent in the language... it
will confuse the user." On inspection these are genuinely two different concepts, not two words for
the same thing -- "matched" is the search pipeline's one-time pass/fail outcome (cleared the
prefilter and minimum score, got saved to the Jobs list), while "fit score" is the 0-100 quality
number itself. The real problem was narrower: "Rematch all/this job" used match-language for an
action that's actually about the fit side -- it only recomputes the score, and never re-runs the
matched/not-matched decision (a job stays on the list even if a rescore brings its number down).

Renamed throughout: buttons ("Rescore this job" / "Rescore all jobs"), routes
(`/api/jobs/rescore*`), `pipeline.rescore_all_jobs`, `search_state.py`'s rescore state helpers, CSS/JS
identifiers, and the same fix applied to the resume-approval flow's own identical case (resume.html's
"Approve and re-parse/rematch" -> "re-parse/rescore"). Also added a short explanation of the
match/fit distinction to the Guide's "Understanding fit scores" section, since it wasn't written down
anywhere -- the Guide's own old wording even blurred the two together ("a fit score reflects how
well a posting matches...").

### 2026-10-05 — v0.1.34

Shipped: a fifth instance of the hidden/display bug (the search-activity badge showing on every
page at all times), a clearer Model dropdown, and a renamed Settings section (see below).

### 2026-10-05 — Search-activity-badge hidden/display bug (fifth instance), clearer Model dropdown

User report: "why is there a magnifying glass button on pages that are not Jobs page? ... it's not
even clickable, what is it supposed to do?" A fifth instance of the recurring hidden/display bug
(v0.1.17, v0.1.18, and twice already this week): every page that includes
`_search_activity_badge.html` defines its own `.search-activity-badge { display: inline-flex; ... }`
CSS rule (no shared base template to put a single override in), which beat the `[hidden]` user-agent
rule by cascade origin -- so the badge's `badge.hidden = true/false` toggle alone had no visual
effect, and it showed (with its static, non-animating 🔍) on every page at all times instead of only
during an active search. Fixed in the badge's own JS this time (`badge.style.display` toggled
alongside `.hidden`, the same shape as the tutorial banner and update banner) rather than patching
nine separate page-level CSS blocks with the same override nine times.

Also, two Settings -> App config fixes from a user report ("the shown selection for Model should be
the current selection. changing that setting should change and save"):

- Saving already worked correctly (confirmed with a new round-trip regression test) -- but when the
  configured model isn't in the installed/recommended list, the dropdown fell back to a generic
  "Custom model name..." label with no indication of what the actual value was (it was only visible
  in the adjacent text box). The selected option's own label now shows it directly ("Custom:
  qwen2.5:7b").
- Renamed the "Optional local setup" sub-heading to "LLM setup" per direct request.

### 2026-10-05 — v0.1.33

Shipped: fixed the new per-job action menu showing open on every page load instead of staying
closed until clicked (see below).

### 2026-10-05 — job-menu-panel hidden/display bug (fourth instance)

User report: "the menu we just added should not always show." Same bug class as the v0.1.17/v0.1.18
update-banner fix, a fourth instance: `.job-menu-panel`'s own `display: flex` rule (needed to lay
out its contents once open) beat the `[hidden]` user-agent rule by cascade *origin*, not
specificity, so every job card's action menu rendered open by default regardless of the `hidden`
attribute on its markup. Fixed the same way as the other three instances: an explicit
`.job-menu-panel[hidden] { display: none; }` override, pinned by a test extending the existing
hidden/display audit.

### 2026-10-05 — v0.1.32

Shipped: per-job rematch, a tidied-up job card (secondary actions moved into a menu), a "Resume
now" button wherever a pause is shown, and a clickable LLM status light.

### 2026-10-05 — Per-job action menu, single-job rematch, Resume now, clickable LLM status light

User report: "the job entries on dashboard are now feeling cluttered" -- Improve my fit, Draft
cover letter, and Draft outreach email (plus the new Rematch this job) were always-visible buttons
on every card. Moved all four behind a single kebab-menu button (&#8942;) in each job card's
top-right corner (`.job-menu`/`.job-menu-panel` in `_jobs_panel.html`); only Status and (when
relevant) "Practice for this interview" stay always-visible, since those are used on essentially
every job. One menu open at a time; clicking anywhere outside a job's menu closes it.

- **Rematch this job** (`POST /api/jobs/{id}/rematch`) -- the single-job counterpart to v0.1.31's
  "Rematch all jobs", synchronous like the other per-job actions (one LLM call, not the
  background-thread-plus-polling shape the bulk version needs). Same `LLMScoringFailedError`
  handling as the manual-add route.
- **"Resume now" button** -- v0.1.31 shipped the pause-and-auto-retry behavior itself but only an
  automatic retry every 5s; this adds an explicit way to cut that wait short right after fixing the
  LLM, instead of waiting out the interval. `pipeline._score_with_pause()` takes an optional
  `threading.Event` and waits on it (`Event.wait(timeout=...)`) instead of a plain `time.sleep()`
  when one's given -- `POST /search/resume-now` / `POST /api/jobs/rematch/resume-now` just `.set()`
  it. Shown on the Jobs page's progress panel, the rematch controls, and the site-wide activity
  badge, only while actually paused.
- **Clickable LLM status light** -- clicking it now POSTs to `/api/llm/status` instead of just
  polling GET, which forces a real check/call bypassing the 30s cache, for "I just started Ollama,
  tell me right now" instead of waiting up to 30s for the next passive poll.

### 2026-10-04 — v0.1.31

Shipped: an unreachable LLM mid-search now pauses instead of silently scoring with the much weaker
keyword fallback, plus a way to see and fix it (see below).

### 2026-10-04 — Pause on LLM failure, scoring-method visibility, rematch-all, LLM status light

User report: "I think ollama wasn't running and it fell back to word match scoring. I don't want
word match scoring." `check_llm_available()` (v0.1.x) already aborted a search *up front* if a
configured LLM was unreachable before touching any connector -- but if the LLM dropped mid-search
(Ollama crashed, closed, network blip), `matching.score_fit()`'s own blanket `except Exception` still
silently substituted the rule-based keyword-overlap heuristic for every remaining posting, one job at
a time, with nothing but a log line. That silent substitution is now gone: `score_fit()` only uses
rule-based scoring for the deliberate `llm.provider = "none"` case (tagged `method="rule_based"`); a
*configured* LLM that fails raises `LLMScoringFailedError` instead.

`run_search_cycle()` (and the new `rematch_all_jobs()`, see below) catch that via a shared
`_score_with_pause()` helper: pause, poll `check_llm_available()` every 5s, and retry the *same*
posting once it's reachable again -- nothing already scored this run is lost or silently downgraded
over what's often a transient outage. `should_stop()` still works while paused, so Stop Search cancels
cleanly instead of hanging. The Jobs page's search-progress panel and every page's activity badge show
"Paused — <reason>" instead of looking stalled.

Also shipped, since the root complaint was really "I can't tell which jobs were scored how" and "now
that I noticed one wasn't, I want to fix it without a full re-search":

- `JobPosting.fit_score_method` ("llm" or "rule_based", nullable for postings scored before this
  column existed) -- each job card on the Jobs page now shows an "LLM scored" or "Keyword match"
  badge (the latter visually flagged, since it means a materially weaker score).
- **Rematch all jobs** button (Jobs page) -- `pipeline.rematch_all_jobs()` re-scores every saved
  posting in place against the current resume/preferences/LLM, with the same pause-on-failure
  behavior, for after fixing an LLM that had been down (or just after editing preferences).
- A small status light next to "Hanarr" on the Jobs page: green when a configured LLM is actually
  reachable, red for anything else (not configured, unreachable, a bad model/key) -- hover for why.
  Cached 30s server-side so polling it doesn't rack up real Anthropic API calls.

Separately: the search-activity badge shown on every other page (Coaching, Settings, etc.) was a
link back to "/" labeled "view progress" -- a pointless detour, since there's nothing to interact
with there besides the same running/paused state the badge itself already shows. Replaced with a
plain, non-clickable badge with a small cycling-emoji "still searching" animation.

### 2026-10-04 — v0.1.30

Shipped: clicking "Install" on an update now actually tells you what happened once it's done,
instead of going silent.

### 2026-10-04 — Update-install flow gave no feedback once the update finished

User report: "clicking update didn't show anything to the user, especially when the update was
finished." `/config/update/install`'s response only confirms the installer *launched* (Inno Setup's
own CloseApplications/RestartApplications closes and relaunches Hanarr, not this code) -- the old JS
set a static "Installing… will restart shortly" message and never checked again, so the page sat on
that forever even once the new version was already up and running.

Fixed by reusing the same reconnect-and-reload pattern the App tab's restart button already has:
after the install call, the page polls `/` until the (new) process answers, then reloads itself. On
reload, it compares the version that came back against the one that was requested and reports
"Updated to vX.Y.Z" or, if they don't match, re-arms the wait a few more times before reporting the
install may not have finished -- guards against reconnecting to the old process in the brief window
before Inno Setup actually closes it, rather than declaring success (or failure) too early.

### 2026-10-04 — v0.1.29

Shipped: a second config-corruption gap closed (a forked profile's own preferences had no backup),
plus a tutorial-banner visual fix.

### 2026-10-04 — Profile preferences backups + tutorial banner fix

Found a second, previously-undiscovered consequence of the same historical stale-autosave incident
`save_settings_to_yaml`'s v0.1.27 fix addressed: match criteria (`preferences.*`) are actually stored
per-profile, in `profiles.preferences_json`, once a profile's Preferences tab is first saved --
config.yaml's own `preferences` block becomes a dead fallback from that point on (see the "forks a
profile" comment in `app.py`'s config-save route and `config.effective_preferences`). The earlier
fix only ever restored/backed up config.yaml, so the forked profile's actual, live salary floor and
dealbreakers were still sitting at blank in the database the whole time, with zero backup history to
recover from -- restoring config.yaml did nothing, because nothing was still reading it.

Fixed the data directly (the profile's salary floor and dealbreakers restored from the matching
config.yaml backup) and closed the structural gap: `backup_profile_preferences()` mirrors
`_backup_config()` -- every Preferences-tab save now keeps a timestamped copy of the profile's prior
`preferences_json` in `data/backups/`, pruned the same way, before overwriting it. If a forked
profile's preferences are ever found blanked again, there's now a trail to recover from, the same
guarantee config.yaml itself has had since v0.1.27.

Also fixed: the dismissible tutorial banner at the top of Coaching/Coach/Prep/Skills (
`_tutorial_banner.html`) was styled as a `border-radius: 999px` pill. With these banners' actual
one-sentence messages wrapping across multiple lines inside that shape, it rendered as a large
rounded blob rather than a clean banner -- easy to mistake for an editable input, when it's static,
dismissible text. Changed to a plain rectangular banner (`border-radius: 8px`, normal block layout).

### 2026-10-04 — v0.1.28

Shipped: input-chip boxes for every list field in Settings, plus 11 more verified Recruitee
defaults (see below).

### 2026-10-04 — Input-chip list fields + expanded Recruitee defaults

Every list-type field in Settings (target titles, keyword boosts/excludes, locations, industries,
dealbreakers, and every job source's company boards/tags/queries -- 15 fields in total) is now an
input-chip box instead of a large freeform textarea: a small text input, Enter to add a value as a
removable bubble, an "×" to remove one. Pure front-end progressive enhancement -- the real
`<textarea>` is kept (just hidden) as the actual form field, re-synced to newline-separated text on
every add/remove, so every server-side save/autosave code path keeps reading it exactly as before.
Two iterations based on direct feedback once it was actually used:

- A field with many entries (Greenhouse's 67 default boards) wrapped into a tall, page-pushing
  block with no cap -- capped the box at a fixed max-height with internal scrolling instead.
- Each field's label now sits to the left of its chip box at a uniform width, instead of stacked
  above a box whose width/position varied field to field -- `keywords_boost`/`keywords_exclude`
  and `industries_include`/`industries_exclude` were pulled out of their side-by-side two-column
  grid into the same full-width row layout as everything else, for one consistent alignment down
  the whole page.
- Chips display in alphabetical order (re-sorted on every add/remove, and the underlying saved
  order matches what's displayed) rather than raw insertion order.

Separately: the Recruitee default company list grew from 7 to 18, each verified live the same way
as the original seven -- keolis (transit), pretamanger and boulangerieange (food retail), vion food
group (food processing), ballast nedam (construction), sircle collection (hospitality), better
collective (sports media), dpd (logistics), cm.com and livestorm (SaaS), and greenpeace cee (a
nonprofit) -- categorized in `company_categories.py` for the existing profile-based board filter.

### 2026-10-04 — v0.1.27

Shipped: config.yaml can no longer be silently wiped by an interrupted write (see below).

### 2026-10-04 — config.yaml corruption: atomic writes + backup recovery

Real incident, reported independently on two separate machines: every job source (and some
preferences) silently reset to blank defaults, with no error, no warning, nothing in the UI
pointing at what happened. Root cause: `save_settings_to_yaml()` opened `config.yaml` with plain
`open(path, "w")`, which truncates the file to zero bytes *before* writing a single line of the
new content. Anything that interrupts the process at that exact moment -- a crash, a forced quit,
the machine losing power, antivirus briefly locking the file -- leaves `config.yaml` empty.
`load_settings()` then reads that empty file, YAML parses it as nothing, and silently hands back
an all-Pydantic-defaults `Settings` object: every job source disabled, every preference blanked --
indistinguishable from someone deliberately clearing their config, and exactly the failure mode
`tests/conftest.py`'s `_protect_real_config_yaml` fixture was *already* built to catch in the test
suite (a prior, related incident) but which had no equivalent protection for a real running
install.

Two-part fix:
1. **Atomic writes.** `save_settings_to_yaml()` now writes to a staged temp file
   (`.config.yaml.tmp`), `fsync`s it, and only then atomically swaps it into place with
   `os.replace()` -- `config.yaml` is always either the complete old content or the complete new
   content, never a partial write caught mid-truncate. A failure during the write now leaves the
   real file completely untouched instead of corrupted, and cleans up the staged file rather than
   leaving it behind.
2. **Load-time recovery.** `load_settings()` now treats an existing-but-empty config.yaml as a
   corruption signal (a fresh install never reaches this state -- it always gets a copied template
   first) rather than a legitimate "nothing configured" state, and automatically recovers from the
   newest valid backup in `data/backups/config-*.yaml` (the same backups `_backup_config` already
   wrote before every prior save) before falling back to defaults -- trying progressively older
   backups if the newest one is also unusable. This catches the corruption even for a config.yaml
   written before this fix, or corrupted by some other cause the atomic write doesn't cover.

Recovered the actual lost values on the reporting machine from a backup taken before the
corruption (job source company lists, salary floor, a dealbreaker) rather than leaving them lost.

### 2026-10-04 — v0.1.26

Shipped: networking outreach email drafts on the Jobs page (see below).

### 2026-10-04 — Networking outreach email drafts

The last Planned item: a "Draft outreach email" button on each job card, next to the existing
"Draft cover letter" one, same "LLM drafts, you send it yourself" shape and same
no-approval-needed, overwrite-on-regenerate behavior (`outreach.py`, modeled directly on
`cover_letter.py`). A text field next to the button takes a contact the user already found
themselves -- a name, email, or LinkedIn URL -- and the draft addresses them by it. Hanarr never
looks up who to contact: that's the part that would need LinkedIn people search or a scraped-data
contact-finder service, both ruled out when this was scoped. Four new columns on `job_postings`
(`outreach_contact`, `outreach_email`, `outreach_email_source`, `outreach_email_generated_at`),
same deterministic-fallback pattern as the cover letter (never fabricates a fact about the resume
or the contact beyond their name).

### 2026-10-03 — v0.1.25

Shipped: Coach phase 2 -- a confirm-before-execute action registry (see below).

### 2026-10-03 — Coach phase 2: actions, confirm-before-execute

Second and final v1 phase of the Coach plan: Coach can now propose doing something ("create a
project for Kubernetes on the Acme job") instead of only answering questions, through a fixed,
reviewed action registry (`coach_actions.py`) -- never an arbitrary write. The LLM's response
shape grew from `{"type": "answer", ...}` to also allow `{"type": "action", "action": ...,
"params": ..., "summary": ...}`; it resolves which skill/job a request means against the id lists
now included in the context bundle (`build_context()` gained TRACKED SKILLS and ALL SAVED JOBS
sections) rather than inventing one, and is told to ask a clarifying question instead of guessing
when a request is ambiguous.

An action is only ever a *proposal* until the user clicks Confirm on a card rendered in the chat
(`POST /api/coach/messages/{id}/confirm`) -- no exception for "the user asked nicely in one
sentence." `coach.ask_coach()` never executes an action itself; `confirm_action()` is the one and
only place `coach_actions.run_action()` is ever called from, gated behind that explicit click, the
same principle the rest of the app already holds to (approving a resume proposal, cancelling a
project). Declining a proposal (`POST .../decline`) just marks it so without running anything.

Starting registry, each a thin wrapper around an existing backend function -- `create_coaching_project`
(reuses the curriculum LLM's existing project-design flow), `start_skill_interview`,
`generate_star_questions`, `update_job_status`. Credentials are not and will never be an action;
those stay in the dedicated masked-password Settings fields. More actions can be added later with
zero changes to the confirm/decline plumbing -- just a new `ActionSpec` in the registry.

### 2026-10-03 — v0.1.24

Shipped: Coach phase 1 -- a new `/coach` page with grounded, read-only chat (see below).

### 2026-10-03 — Coach phase 1: grounded chat, read-only

First phase of the segmented Coach plan: a new `/coach` page and persisted conversation
(`ChatMessage`, profile-scoped). Every turn builds a bounded plain-text context bundle (resume
summary or raw text, top fit-scored jobs with their actual stored rationale, active coaching
projects) plus a short constant summary of how Hanarr's own features work, and hands both to the
LLM alongside the question (`coach.py`'s `build_context`/`ask_coach`). Deliberately full-context
rather than real retrieval/embeddings -- at this app's actual scale (one person's job search,
dozens not millions of rows) everything relevant fits a modern context window, so a vector index
would solve a scale problem this app doesn't have.

Added a sixth agent role (`coach`) alongside profiler/market_analysis/curriculum/evaluator/
resume_writer, so Coach gets its own per-task model override in Settings → App config like every
other LLM-backed role. Disabled with a clear reason under `provider: none` or an unready Ollama
model -- there's no deterministic-fallback equivalent for open-ended chat the way job scoring has
a keyword-overlap fallback. No actions yet (phase 2, still in Planned): this phase is read-only
Q&A only, answering only from the context bundle and refusing to guess at anything not in it.

### 2026-10-03 — v0.1.23

Shipped: three new job source connectors -- Workable, Recruitee, USAJOBS (see below).

### 2026-10-03 — Three new job source connectors: Workable, Recruitee, USAJOBS

Scoped as a single Planned item ("more job source connectors"), researched and built as three
genuinely different shapes rather than one pattern stamped out three times -- each API's actual
contract was verified live before writing any code, the same discipline the Workday connector was
built with:

- **Recruitee** (`connectors/recruitee.py`) -- same per-company-board shape as Greenhouse/Lever/
  Ashby (`https://<slug>.recruitee.com/api/offers/`, public, no auth). Ships with 7 default boards,
  each verified live, skewing European and toward small/mid-size employers (energy, construction,
  retail, automotive) rather than funded startups -- categorized in `company_categories.py` for the
  existing profile-based board filter.
- **Workable** -- turned out *not* to fit the per-company-board pattern at all: most individual
  Workable accounts (`www.workable.com/api/accounts/<slug>`) return zero current postings, since
  Workable has no stable public directory of slugs the way the other sources do. Built instead
  against Workable's own public cross-employer search (`jobs.workable.com/api/v1/jobs?query=...`),
  shaped like `RemoteOKSource.tags` -- free-text keyword queries rather than company slugs.
- **USAJOBS** (`connectors/usajobs.py`) -- the official US federal government jobs API, a third
  different shape again: requires a free API key registered to an email
  (`developer.usajobs.gov/apirequest`), sent as `Authorization-Key`/`User-Agent` headers, with no
  anonymous access at all. The key gets the same OS-keyring treatment as the Anthropic API key
  (`secrets_store.USAJOBS_API_KEY`, never written to config.yaml) with the registered email as a
  plain (non-secret) config field. Reaches federal/public-sector postings none of the existing
  sources touch at all -- the actual "more opportunities" goal, not just more of the same
  startup-board postings.

All three wired into Settings → Preferences → Job sources alongside the existing six, and into
`connectors/registry.py`'s `build_enabled_connectors()`. No database migration needed.

### 2026-10-02 — v0.1.22

Shipped: removed four dead API endpoints found by audit (see below).

### 2026-10-02 — Removed four orphaned API endpoints

Found by a deliberate audit: `GET /api/skill-gaps`, `GET /api/skills`, `GET /api/coaching`, and
`GET /api/resume` had zero callers in any template -- only in tests, and only because the pages
they used to back (Skills, Coaching, Resume) are now server-rendered directly from the same
underlying functions (`saved_job_gaps`, `profile_skill_page`, `coaching_suggestions`,
`market_demand_summary`, `resume_status`). Removed the four routes; the tests that exercised them
now call those functions directly instead of through a dead HTTP layer. The functions themselves
are untouched and still back their pages -- this only removed the redundant API surface, not any
behavior.

### 2026-10-02 — v0.1.21

Shipped: three contextual tutorials on Coaching, Skills, and Prep (see below).

### 2026-10-02 — Contextual page tutorials

Extended the dismissible-tutorials mechanism past the one-off guide-pointer banner into a generic,
reusable partial (`_tutorial_banner.html`) that any page can drop in with two `{% set %}` lines
(`tutorial_key`, `tutorial_message`) before the include -- self-contained like the others, polling
its own `GET /api/tutorials/{key}/status`. Three tutorials added on top of it, one per page that
has a non-obvious first-visit behavior worth calling out:

- `coaching_intro` — Coaching: evidence submission is review-first, nothing touches the resume
  without that review.
- `skills_intro` — Skills: "Quick skill check" tests a claimed skill instead of trusting resume
  wording.
- `prep_intro` — Prep: write up a STAR story ahead of time instead of building one cold in the
  interview.

No migration needed, again — the narrow `dismissed_tutorials` table just gained three more
`tutorial_key` values. Each dismisses independently of the others and of the master switch.

### 2026-10-02 — v0.1.20

Shipped: a first-run tutorial pointing new users to the Guide page (see below).

### 2026-10-02 — Guide-pointer tutorial

The first tutorial popup built on the dismissible-tutorials mechanism shipped just before this:
a "New here? The Guide page walks through what each tab does." banner, shown site-wide (every
page except Guide itself) until dismissed. Self-contained like the update banner -- it polls its
own `GET /api/tutorials/{key}/status` rather than needing every route to compute and pass
`onboarding`-style context, which is exactly how the onboarding checklist ended up only ever
wired into 2 of 10 pages. Visiting the Guide page auto-dismisses it (`fetch(...dismiss...)` on
page load), since following its own link already satisfies its purpose -- no redundant second
click needed. `guide_pointer` added to `tutorials.TUTORIAL_KEYS`, no migration required (the
narrow table design paying off exactly as planned when it was built).

### 2026-10-02 — v0.1.19

Shipped: dismissible tutorials with a master switch and per-tutorial "don't show this again" (see
below).

### 2026-10-02 — Dismissible tutorials

User request: a way to turn off tutorial popups, either globally or one at a time ("don't show
again"). Built as a general mechanism rather than a one-off for the existing onboarding checklist,
since more contextual tutorial popups are planned for other pages later.

Design decided with the user: a narrow `dismissed_tutorials` table
(`profile_id`, `tutorial_key`, `dismissed_at`, unique on the pair) rather than one boolean column
per tutorial on `Profile` — adding a new tutorial later needs zero migrations, just a new
`tutorial_key` value written/read against the same table. The master on/off switch
(`settings.ui.tutorials_enabled`) is separate: a single global behavior flag in `config.yaml`
alongside the rest of Settings, not per-profile state, since "turn off all tutorials" is one
decision, not one per tutorial.

- `tutorials.py`: `is_tutorial_visible()` (true only when the global switch is on AND this profile
  hasn't dismissed that key), `dismiss_tutorial()`, `reset_dismissed_tutorials()`.
- The onboarding banner (`_onboarding_banner.html`) now has its own "✕ don't show this again"
  button, posting to `/api/tutorials/dismiss`. `_onboarding_status()` gained a `visible` field
  folding in both the dismissal and the global switch, on top of the existing `all_done` check.
- Settings → App config has a new "Tutorials" section: a "Show tutorials" checkbox
  (`ui.tutorials_enabled`) and a "Reset dismissed tutorials" button (`/api/tutorials/reset`) that
  un-dismisses everything for the active profile, independent of the global switch.

### 2026-10-01 — v0.1.18

Shipped: a follow-up audit of the v0.1.17 update-banner fix found the same
`hidden`-defeated-by-`display` bug in two more spots (see below).

### 2026-10-01 — Same hidden/display bug, two more instances

A deliberate audit after the v0.1.17 fix (grep every template for the same
shape: an element with `hidden` plus an inline `style="display:..."` or an
author CSS rule setting `display` on its own selector) turned up two more
real instances, both silently non-functional the same way the update
banner was:

- Settings → Updates' own install-progress indicator (`config.html`) had
  the same inline-style-defeats-`hidden` shape as the banner; fixed the
  same way (`style.display` toggled explicitly alongside `hidden`).
- Settings → App config's Ollama-setup spinner used a bare
  `.spinner { display: inline-block }` class rule with no `[hidden]`
  override — author-level `display` beats the user-agent `[hidden]` rule
  by cascade *origin*, not specificity, so a class rule causes the exact
  same defect as inline style. Fixed with a more-specific
  `.spinner[hidden] { display: none; }` override rather than touching the
  single JS toggle site. The Skills page's `.interview-questions` grid had
  the identical latent defect (low visual impact today since the div is
  empty while hidden) and got the same override for consistency.

### 2026-10-01 — v0.1.17

Shipped: three user-reported update-flow/documentation bugs fixed (see below).

### 2026-10-01 — Update banner visibility bug, version display, and AI-first docs

User report: "the update now button always shows", "no easy way to see what version the user is
on", and the Guide/README/onboarding text told people to upload a resume before setting up an AI
model even though resume parsing silently degrades to keyword matching without one.

Three independent fixes:

1. **Real bug**: `_update_available_banner.html`'s root div carried both the `hidden` attribute
   and an inline `style="display:inline-flex; ..."`. Inline `style` always wins over the
   `[hidden] { display: none }` user-agent rule, so the banner — "Update now" button included —
   was visible on every page at all times, regardless of whether an update was actually staged.
   `banner.hidden = true` in the polling JS had no visual effect. Fixed by moving `display`
   toggling into JS (`banner.style.display`) instead of relying on the attribute alone, in both
   the site-wide banner and the Settings → Updates tab's own install button.
2. Added a simulated/indeterminate progress bar (CSS animation, since the actual install runs
   outside this process once launched) to both the site-wide banner's "installing" state and the
   Settings tab's "Downloading and verifying…" / "Installing…" states — previously the only
   feedback was a text string that didn't appear until the whole blocking download+install
   request resolved.
3. `app_version` is now a Jinja global (`templates.env.globals`) shown next to the page title on
   every page, and Settings → Updates states the running version plainly ("Running v0.1.17.") —
   previously the version was only visible in the Settings page's own `<h1>`.
4. Reordered and reworded the Guide page's "Recommended order of operations", the onboarding
   checklist banner, and README's getting-started steps: setting up a local AI model now comes
   *before* uploading a resume (not after, as an "optional" afterthought), and `provider: none`
   keyword-overlap matching is now described as a degraded fallback, not an equally valid default
   — matching how the app is actually meant to be run.

### 2026-10-01 — v0.1.16

Shipped: job-specific interview prep is now reachable from the UI (see below).

### 2026-10-01 — Job-specific interview prep, reachable

Real gap found by surveying the app: `star_stories.generate_star_questions()` already supported
weighting questions toward a specific saved job's title/description (`job_id` param), and
`POST /api/star/questions/generate` already accepted it, but nothing in the dashboard ever passed
one -- the Prep page's "Generate practice questions" button always called the generic path. The
job-specific code path was dead from a user's perspective.

A job marked **interviewing** now shows a "Practice for this interview" link on both the Jobs and
Applications pages, linking to `/prep?job_id={id}`. The Prep page validates that job belongs to the
active profile and, if so, shows a second, job-specific "Generate questions for the {title} @
{company} interview" button alongside the generic one (never auto-triggered — still one explicit
click, matching every other LLM-backed action in the app). `question_dict()` now includes the
linked job's id/title/company so a job-specific question's badge links back to the job instead of
just labeling it, and the deterministic fallback (bad LLM output) correctly still reports as
generic, never falsely claiming job-specific weighting it couldn't actually do.

Three new regression tests in `test_star_stories.py`.

### 2026-10-01 — v0.1.15

Shipped: Guide page catch-up and a manual "Mark complete" action for coaching projects (see below).

### 2026-10-01 — Guide page catch-up and manual project completion

Two small, real gaps found by surveying the app rather than inventing new work:

1. **Guide page was stale.** "What each page is for" still listed only the original six pages —
   no card for **Prep** (shipped in v0.1.13), and the Skills/Settings cards said nothing about
   "Quick skill check" (v0.1.12) or "Per-task model sizing" (v0.1.14). Caught up all three, plus
   the Coaching card now describes auto-complete-on-pass, manual completion, cancellation, and the
   stale-project nudge.
2. **No manual project completion.** The original coaching-project loop-closure scoping wanted
   completion "suggested once all tasks are done and/or a submission has passed, not
   auto-forced" — what shipped (v0.1.10) was only auto-complete-on-pass plus Cancel, with no way
   to mark a project complete without ever submitting evidence for an LLM review. New
   `POST /api/coaching-projects/{id}/complete` and a "Mark complete" button alongside the existing
   Cancel button. Deliberately asymmetric with a passed evaluation: it sets `Project.status` and
   `completed_at` only — it never touches `ProvenSkill`, bumps `ProfileSkill.confidence`, or marks
   a `JobSkill.gap_status` satisfied, since those are evidence-backed claims an unreviewed
   self-declaration shouldn't silently earn.

Three new regression tests across `test_dashboard_app.py` and `test_coaching_projects.py`; `/prep`
added to the existing nav-link and shared-shell-layout consistency tests (it was missing from both
page lists since it shipped).

### 2026-10-01 — v0.1.14

Shipped: per-task model sizing (see below).

### 2026-10-01 — Per-task model sizing

Closed the last Backlog item, starting with the research step the Backlog entry itself demanded
("not guessed at"): read every `orchestrator.client_for(...)` call site in `dashboard/app.py` to
characterize what each of the five roles actually does.

**The real finding, which changed the plan.** The original framing assumed frequency and
complexity move together — "lighter model for cheap/frequent calls, heavier for the calls that
need it." They don't. `market_analysis` (`matching.score_fit`, called once per posting in every
search — the single highest-frequency LLM call in the app) is also the most complex: full resume
text plus the full posting, careful "claimed vs. demonstrated experience" judgment (see
`matching.SYSTEM_PROMPT`). It's the one role that should shift to a *heavier* model, not a lighter
one. `curriculum` (coaching-project briefs, STAR practice questions) is the one role where
"lighter" is actually safe — short structured JSON, triggered manually and infrequently, and
already has a deterministic fallback on bad output. `profiler`, `evaluator`, and `resume_writer`
each mix simple and complex sub-tasks without a clear case to shift either way, so they stay at
the hardware-based baseline.

**Shipped.** `ollama_setup.recommend_model_for_role(role, hardware)`: the same hardware-tier
baseline as `recommend_model()`, shifted one tier up for `market_analysis`, one tier down for
`curriculum`, unchanged for the other three — and never shifted past the existing <10GB-free-
storage safety floor, even for a role that would otherwise want a heavier model. A new "Per-task
model sizing" section on Settings → App lists all five roles with a one-line description of what
each actually does, a dropdown (installed models + the role's suggestion + custom) defaulting to
"use the shared model above," and the reasoning behind each suggestion. `AgentRoute.model`
overrides were already the storage mechanism (`agent_orchestration.py`/`config.py`) — this just
exposes it; a blank field keeps inheriting the shared model, exactly as before.

Seven new regression tests across `test_ollama_setup.py` and `test_dashboard_app.py`.

### 2026-09-29 — v0.1.13

Shipped: STAR behavioral-story builder, and a new top-level **Prep** nav tab (see below).

### 2026-09-29 — STAR behavioral-story builder

Built the feature scoped in the removed Planned section above, resolving its own open nav
question: a new top-level **Prep** tab, added to all nine page templates (no shared nav partial
exists, so each got the same one-line insertion) since mini-interview shipped first and now
there's a real second interview-prep feature to justify a dedicated tab rather than guessing the
shape upfront.

New `StarQuestion`/`StarStory` tables (migration `0016`) and `star_stories.py`:
`generate_star_questions()` asks the LLM for up to 6 questions tagged by competency (leadership,
conflict, failure, ambiguity, technical tradeoff, cross-team collaboration), based on the resume's
titles/seniority/industries and, optionally, a specific saved job's title/description — falling
back to a fixed set of 6 competency-tagged questions (never job-weighted) on bad LLM output.
`review_story()` reviews a draft's four STAR fields in one pass and returns per-field "tightened"
wording suggestions the person can accept or ignore, plus feedback flagging vague components or a
Result with no measurable outcome — never invents a fact, and the deterministic fallback echoes
the original text back unchanged rather than guessing at a rewrite.

One deliberate scope cut from the original scoping: a single review pass over the whole draft
instead of a multi-turn "one component at a time with a clarifying follow-up" conversation — the
core value (catching vagueness, tightening wording, flagging an unmeasured Result) doesn't need a
stateful multi-turn conversation machine to deliver, and this is far simpler to build and reason
about. A story is upserted per question (edit and re-practice in place) rather than versioned.

New routes: `POST /api/star/questions/generate`, `POST /api/star/questions/{id}/story`,
`PATCH /api/star/stories/{id}`, `POST /api/star/stories/{id}/review`,
`POST /api/star/stories/{id}/status`. Seven new regression tests in `test_star_stories.py`.

### 2026-09-29 — v0.1.12

Shipped: an explicit improvement plan and structured resources on a mini-interview verdict, a
standalone "start a coaching project" action on the Skills page, and a real bug fix in reusable-
skill project creation (see below).

### 2026-09-29 — Mini-interview follow-through: explicit plan, structured resources, direct project start

Direct feedback: a person shouldn't be left to invent their own improvement plan after a skill
check. Three changes to `skill_interview.py`/`coaching_projects.py`/`skills.html`:

1. **Deterministic improvement plan.** `submit_interview()` now writes a fixed, ordered
   `plan_json` (new column, migration `0015`) keyed off the verdict — not LLM-generated, so it's
   reliable and free: `remediate` → review resources, then retake; `rebuild` → start a project,
   submit evidence, then retake; `solid`/`could_not_assess` get their own one-line plans. Rendered
   as an ordered checklist in both the live result panel and the history list.
2. **Structured resources.** `resources` changed from bare strings to `{"title", "why"}` objects
   so a remediation suggestion says what it addresses, not just a name — still explicitly
   plain-text/unverified LLM output, not fetched or checked. (Considered giving Hanarr a real
   web-search capability to fetch verifiable links instead — deliberately not doing that; it's a
   real architecture change against the "no scraping, legitimate APIs only" principle the rest of
   the app follows, and wasn't asked for.)
3. **Direct project start.** A "Start a coaching project for this skill" button on every skill row
   (not gated behind running an interview first) reuses the same reusable-skill
   `POST /api/coaching-projects` flow. Doing this surfaced a real bug: `create_coaching_project`'s
   reusable-skill branch built its `skills` list only from existing analyzed `JobSkill` gap rows —
   for a skill with zero of those (exactly the case a mini-interview verdict hits, since it never
   required a job analysis), the project silently ended up linked to *no* skill at all, with a
   blank title. Fixed: the branch now always includes the directly-looked-up skill.

Three new regression tests across `test_skill_interview.py` and `test_coaching_projects.py`.

### 2026-09-29 — v0.1.11

Shipped: mini-interview skill assessment (see below).

### 2026-09-29 — Mini-interview skill assessment

Built the feature scoped in the removed Planned section above, with one deliberate scope cut from
that scoping: an inline expansion under the existing skill row (`skills.html`) instead of the
dedicated `/skills/{id}/interview` page it leaned toward — kept it inline since the actual flow
(2-4 short questions, one round of free-text answers) turned out small enough not to need a
separate page, and it matches the existing `confidence-editor` inline-expansion pattern already on
that row.

New `SkillInterview` table (migration `0014`) and `skill_interview.py`: `start_interview()` asks
the LLM for 2-4 questions calibrated to the skill's claimed proficiency/confidence, existing
evidence, and the most recent prior interview's verdict, falling back to two fixed
still-non-generic questions on bad LLM output (`evaluator="deterministic"`); `submit_interview()`
classifies the answers into `solid`/`remediate`/`rebuild`, or `could_not_assess` on bad LLM output
(`evaluator="deterministic-fallback"`, no ProfileSkill change either way).

A `solid` verdict writes/updates a `source="interview"` `ProfileSkill` row at a fixed 0.75
confidence floor (not scaled by a score — unlike the coaching-project evaluator, this LLM call
returns a category, not a comparable 0-100 number) — same non-clobbering rule as the coaching
project bump: a higher-trust resume/manual source's confidence only ever rises, never silently
drops, and the corroboration is recorded in evidence text. A `rebuild` verdict offers a "Start a
coaching project" button that reuses the existing reusable-skill `POST /api/coaching-projects`
flow rather than a second project system. `remediate` surfaces the LLM's suggested refresher
resources, explicitly plain-text/unverified (Hanarr has no web-search capability to check them).
Past interviews for a skill show in a small history list on the row.

Two new routes: `POST /api/skills/{id}/interview/start`, `POST
/api/skills/{id}/interview/{interview_id}/submit`. Seven new regression tests in
`test_skill_interview.py`.

### 2026-09-29 — v0.1.10

Shipped: coaching project loop closure (see below), plus a README pass to fix the stale test count
(319 → 453) and refresh the Roadmap ideas section, which still described the Workday connector as
"next up" after it had already shipped.

### 2026-09-29 — Coaching project loop closure

Closed the gap traced and scoped in the removed Planned section above: a passed evaluation used
to update nothing beyond `ProvenSkill` and offering a resume proposal. Now, in
`evaluate_project_submission` (`dashboard/app.py`):
1. **Confidence bump, scaled by score.** A passed evaluation writes/updates a `source="project"`
   `ProfileSkill` row at `evaluation.score / 100`. An existing `source="project"` row is updated in
   place; a higher-trust `"resume"`/`"manual"` row is never silently overwritten downward -- its
   confidence only rises (never falls) and the corroboration is recorded in its evidence text, not
   applied silently.
2. **`JobSkill.gap_status` set to `SATISFIED`** for the project's affected jobs' matching skills,
   so gap cards on Jobs reflect a proven skill immediately instead of only after the job is
   re-analyzed.
3. **`POST /api/coaching-projects/{id}/cancel`** -- the missing "abandon this honestly" action;
   blocks cancelling an already-completed/cancelled project. Project completion itself already
   auto-fires on a passed evaluation (existing behavior, kept as-is rather than made a separate
   manual step).
4. **Stale-project nudge** -- an `active` project with no task-status change or submission in 14+
   days (fixed threshold; project task counts are too small for a per-project-cadence signal to be
   meaningful) shows a "stale" badge on the Coaching page. New `ProjectTask.updated_at` column
   (migration `0013`) tracks the signal; existing rows read as `NULL` and fall back to the
   project's `opted_in_at`.
Bridging a proven skill to a resume proposal was already wired in (`create_resume_proposal` on a
pass) -- confirmed working, not new. Six new regression tests in `test_coaching_projects.py`.

### 2026-09-29 — v0.1.9

Shipped: navigate straight to a newly created coaching project instead of reloading the Jobs page
(see below), plus the LICENSE audit and roadmap scoping entries from earlier today.

### 2026-09-29 — LICENSE audit: attribution, trademark, and distribution-scope gaps

Full adversarial re-read of `LICENSE`, prompted by a direct worry about someone taking the code,
reselling it, or claiming it as their own. Found and closed four real gaps: (1) "distributing the
software" didn't explicitly say "in whole or in part," leaving an excerpt arguably ambiguous; (2)
"making changes or new works based on the software" didn't explicitly cover a rewrite/reimplementation
based on having studied the code, only literal edits; (3) nothing barred removing the copyright
notice or claiming authorship of the software or a derivative of it -- added an explicit
Attribution and No False Claims section; (4) nothing addressed trademark/branding at all -- added a
No Trademark License section so "Hanarr" can't be used on a fork, rebrand, or competing product
even if someone otherwise had permission to use the code (which they don't, but this closes the gap
regardless). Also added an explicit "what a license can and can't do" note: license text is a legal
remedy (infringement claim, DMCA, cease-and-desist), not a technical lock -- it can't stop someone
with source access from copying files in the first place. The only real control over that is repo
access (public vs. private), which is a separate, still-open decision.

### 2026-09-29 — Fix: Patent License clause wasn't scoped to the permitted purpose

Real gap caught by a direct question about the patent clause: `LICENSE`'s Copyright License grant
is explicitly scoped to "any permitted purpose," but the Patent License grant right below it just
said "by using the software" — not tied to the permitted-purpose restriction at all. Since the
whole point of the narrowing above is "personal job search, nothing else, full stop," an
inconsistently-scoped patent grant undermined that. Added "for a permitted purpose" to the Patent
License clause so both grants are scoped identically.

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
