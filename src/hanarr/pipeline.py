"""Orchestrates one search cycle: fetch from all enabled connectors,
prefilter, score, and upsert into the DB. This is what `hanarr search`
and the scheduled background job both call.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, effective_preferences
from .connectors import build_enabled_connectors
from .connectors.base import RawJobPosting
from .llm.base import LLMClient, NullLLMClient
from .matching import LLMScoringFailedError, prefilter_rejection_reason, score_fit
from .models import JobPosting, Profile, ScoreSnapshot, SeenPosting

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised by check_llm_available() when llm.provider is configured (not
    "none") but the model isn't actually reachable/usable. Callers should
    let this abort the whole search cycle before any connectors are hit --
    otherwise every posting scored during the cycle silently falls back to
    rule-based scoring one at a time, which is slow (each attempt still
    pays the LLM's own connection/timeout cost) and, before matching.py
    started logging fallbacks, was completely invisible."""


def check_llm_available(llm: LLMClient, data_dir) -> None:
    """llm.provider = "none" (NullLLMClient) is a deliberate, expected
    configuration -- not checked. For Ollama, this hits the fast /api/tags
    endpoint (introspecting the actual resolved client's model/base_url,
    which may differ from settings.llm if an agent-specific override
    applies) rather than paying for a real generation. For Anthropic, which
    has no equivalent fast introspection here, this makes one trivial real
    call instead. Any other LLMClient implementation (e.g. a test double)
    is left unchecked -- this function exists to guard the two providers
    this project actually ships network calls for, not to impose a
    liveness contract on arbitrary custom clients."""
    if isinstance(llm, NullLLMClient):
        return

    from .llm.ollama_client import OllamaClient

    if isinstance(llm, OllamaClient):
        from .ollama_setup import detect_ollama

        diagnostics = detect_ollama(llm.model, llm.base_url, data_dir)
        if not diagnostics.service_reachable:
            detail = f" ({diagnostics.service_error})" if diagnostics.service_error else ""
            raise LLMUnavailableError(
                f"Ollama isn't reachable at {llm.base_url}{detail} — start Ollama and try again."
            )
        if not diagnostics.configured_model_available:
            raise LLMUnavailableError(
                f"Model {llm.model!r} isn't downloaded yet — open Settings to download it "
                f"(or run `ollama pull {llm.model}` from a terminal)."
            )
        return

    from .llm.anthropic_client import AnthropicClient

    if isinstance(llm, AnthropicClient):
        try:
            llm.complete_json("Respond with JSON only.", '{"ping": true}')
        except Exception as exc:  # noqa: BLE001 - normalized into one error type
            raise LLMUnavailableError(f"LLM check failed before starting search: {exc}") from exc

# Fired with a small dict describing pipeline progress; see the individual
# call sites below for the event shapes. Optional — CLI and scheduler
# callers pass nothing and get no overhead beyond one None-check per job.
ProgressCallback = Callable[[dict], None]

# Polled between postings and between sources — not mid-LLM-call, since an
# in-flight score_fit() can't be interrupted without real complexity (killing
# an HTTP request to Ollama mid-response). Optional; defaults to "never stop"
# so CLI/scheduler callers are unaffected.
StopCheck = Callable[[], bool]

# How often to re-check whether a configured LLM has come back after a
# mid-search failure. Deliberately not instant -- a tight loop would hammer
# Ollama's /api/tags endpoint (or, for Anthropic, nothing further since
# check_llm_available only re-probes Ollama cheaply; Anthropic failures are
# re-tried at this same cadence via one real call each time, so this also
# bounds that cost).
LLM_RETRY_INTERVAL_SECONDS = 5

# A real incident: the LLM was reachable the entire time (check_llm_available
# kept succeeding instantly) but gave a malformed answer -- blank rationale --
# for the SAME posting on every single retry, for 8 hours straight, before a
# user finally gave up and stopped it manually. Reachability and "produces a
# usable answer for this specific input" are different failures; only the
# first one is worth retrying indefinitely. After this many consecutive
# failures scoring the same posting, give up on that one posting (see
# LLMScoringGaveUpError) rather than retrying it forever and blocking
# everything after it in the batch.
MAX_CONSECUTIVE_SCORE_FAILURES = 5


class LLMScoringGaveUpError(RuntimeError):
    """Raised by _score_with_pause after MAX_CONSECUTIVE_SCORE_FAILURES
    consecutive failures scoring the same posting. Distinct from a plain
    LLMScoringFailedError (which triggers a pause-and-retry) and from a
    should_stop()-interrupted pause (which returns None) -- callers catch
    this specifically to skip just this one posting and move on to the
    rest of the batch, rather than retrying forever or aborting
    everything over one posting a model can't seem to answer."""


def _score_with_pause(
    job: RawJobPosting,
    resume_summary: dict,
    resume_text: str,
    prefs,
    llm: LLMClient,
    data_dir,
    on_progress: Optional[ProgressCallback],
    should_stop: Optional[StopCheck],
    resume_event: Optional[threading.Event] = None,
) -> tuple[float, str, str] | None:
    """Scores one posting. A configured LLM (not llm.provider = "none")
    that fails no longer silently falls back to rule-based scoring one job
    at a time -- see LLMScoringFailedError -- it pauses here instead,
    polling until the LLM is reachable again (or the user cancels) and then
    retrying the SAME job, so nothing already scored this cycle is lost or
    silently downgraded over what's often a transient outage (Ollama not
    started yet, a momentary network blip). Returns None if the pause was
    interrupted by should_stop(). Raises LLMScoringGaveUpError after
    MAX_CONSECUTIVE_SCORE_FAILURES straight failures on this same posting.

    resume_event, if given, lets a caller (the dashboard's "Resume now"
    button) cut the wait short instead of waiting out the full retry
    interval -- Event.wait(timeout) returns as soon as either happens.
    Cleared right after waking so it doesn't short-circuit every
    subsequent wait if the LLM is still down after this one click."""
    attempts = 0
    while True:
        try:
            return score_fit(job, resume_summary, resume_text, prefs, llm)
        except LLMScoringFailedError as exc:
            attempts += 1
            if attempts >= MAX_CONSECUTIVE_SCORE_FAILURES:
                raise LLMScoringGaveUpError(
                    f"Gave up scoring {job.title!r} at {job.company!r} after {attempts} "
                    f"consecutive failures: {exc}"
                ) from exc
            if on_progress:
                on_progress({"event": "llm_paused", "message": str(exc)})
            while True:
                if should_stop and should_stop():
                    return None
                if resume_event is not None:
                    resume_event.wait(timeout=LLM_RETRY_INTERVAL_SECONDS)
                    resume_event.clear()
                else:
                    time.sleep(LLM_RETRY_INTERVAL_SECONDS)
                try:
                    check_llm_available(llm, data_dir)
                    break
                except LLMUnavailableError:
                    continue
            if on_progress:
                on_progress({"event": "llm_resumed"})
            # Loop back around and retry score_fit on this same job.


def _score_batch_concurrently(
    jobs: list[RawJobPosting],
    resume_summary: dict,
    resume_text: str,
    prefs,
    llm: LLMClient,
    data_dir,
    on_progress: Optional[ProgressCallback],
    should_stop: Optional[StopCheck],
    resume_event: Optional[threading.Event],
    max_workers: int,
):
    """Scores multiple postings against the LLM with up to max_workers in
    flight at once, instead of one at a time -- see
    Settings.llm.max_concurrent_scoring for why a bounded pool, not
    unlimited or purely sequential. Yields (job, outcome) for every job
    that was actually submitted, as each one completes (not necessarily
    input order): outcome is a (score, rationale, method) tuple on
    success, None if a mid-pause should_stop() interrupted that one job
    specifically (see _score_with_pause), or the LLMScoringGaveUpError
    raised for it.

    should_stop is checked before each new submission, not just once up
    front, so a stop requested partway through a large batch still takes
    effect for jobs not yet started. Jobs already in flight when that
    happens are drained and their real results still yielded -- an
    in-flight HTTP call to the LLM can't be safely interrupted anyway, and
    discarding a result that came back successfully would be a worse
    outcome than the one or two extra seconds it costs to let it finish.

    Fires the "scoring" progress event per job at the moment it's actually
    dispatched to a worker, not before -- with max_workers > 1 this can
    mean several "Scoring: ..." lines appear at once, which is an honest
    reflection of real concurrent work rather than a sequential narrative
    that isn't true anymore."""
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        next_idx = 0
        in_flight: dict = {}

        def _try_submit():
            nonlocal next_idx
            while len(in_flight) < max_workers and next_idx < len(jobs):
                if should_stop and should_stop():
                    next_idx = len(jobs)  # stop handing out new work
                    return
                job = jobs[next_idx]
                next_idx += 1
                if on_progress:
                    on_progress({
                        "event": "scoring", "source": job.source, "title": job.title, "company": job.company,
                    })
                future = pool.submit(
                    _score_with_pause, job, resume_summary, resume_text, prefs, llm, data_dir,
                    on_progress, should_stop, resume_event,
                )
                in_flight[future] = job

        _try_submit()
        while in_flight:
            done, _ = wait_for_futures(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                job = in_flight.pop(future)
                try:
                    yield job, future.result()
                except LLMScoringGaveUpError as exc:
                    yield job, exc
            _try_submit()


def run_search_cycle(
    session: Session,
    settings: Settings,
    profile: Profile,
    llm: LLMClient,
    on_progress: Optional[ProgressCallback] = None,
    should_stop: Optional[StopCheck] = None,
    resume_event: Optional[threading.Event] = None,
) -> int:
    """Returns the number of new postings that were scored and stored.
    Raises LLMUnavailableError up front if llm.provider is configured but
    not actually reachable/usable, before any connector or scoring work
    happens. resume_event lets a "Resume now" button cut a mid-search pause
    short -- see _score_with_pause."""
    check_llm_available(llm, settings.data_dir)
    prefs = effective_preferences(profile, settings)
    resume_summary = json.loads(profile.resume_summary_json or "{}")
    resume_text = profile.resume_text or ""
    connectors = build_enabled_connectors(
        settings.sources, resume_summary=resume_summary, preferences=prefs,
    )
    new_count = 0
    stopped = False

    for connector in connectors:
        if should_stop and should_stop():
            stopped = True
            break

        if on_progress:
            on_progress({"event": "source_start", "source": connector.name})
        try:
            raw_jobs = connector.fetch()
        except Exception:  # noqa: BLE001
            logger.exception("Connector %s failed; skipping", connector.name)
            if on_progress:
                on_progress({"event": "source_error", "source": connector.name})
            continue

        if on_progress:
            on_progress({"event": "source_fetched", "source": connector.name, "count": len(raw_jobs)})

        # Prefilter + dedup are cheap, deterministic, and read from `session`
        # -- kept strictly sequential here, unlike the LLM scoring below.
        # Only postings that clear both go on to be scored at all.
        jobs_to_score: list[RawJobPosting] = []
        for job in raw_jobs:
            if should_stop and should_stop():
                stopped = True
                break

            prefilter_reason = prefilter_rejection_reason(job, prefs)
            if prefilter_reason is not None:
                if on_progress:
                    on_progress({
                        "event": "considered", "rejected": True, "reason": prefilter_reason,
                        "title": job.title, "company": job.company, "source": job.source,
                    })
                continue

            already_seen = session.execute(
                select(SeenPosting.id).where(
                    SeenPosting.profile_id == profile.id,
                    SeenPosting.source == job.source,
                    SeenPosting.external_id == job.external_id,
                )
            ).scalars().first()
            if already_seen is not None:
                if on_progress:
                    on_progress({"event": "considered", "already_seen": True})
                continue  # fetched and scored on a previous search, whether matched or rejected

            jobs_to_score.append(job)

        # Scoring is the one part of this loop that's actually safe (and
        # worth it) to parallelize -- it's a pure LLM call with no DB
        # access, unlike everything above. DB writes below still happen
        # one at a time on this thread as each result comes back, never
        # inside a worker thread -- see _score_batch_concurrently.
        if not stopped and jobs_to_score:
            for job, outcome in _score_batch_concurrently(
                jobs_to_score, resume_summary, resume_text, prefs, llm, settings.data_dir, on_progress,
                should_stop, resume_event, settings.llm.max_concurrent_scoring,
            ):
                if isinstance(outcome, LLMScoringGaveUpError):
                    # Not marked seen -- left for a future search (hopefully
                    # with a more reliable model/connection by then) to try
                    # again, rather than permanently skipping a posting that
                    # might score fine next time.
                    logger.warning(str(outcome))
                    if on_progress:
                        on_progress({
                            "event": "gave_up", "title": job.title, "company": job.company, "message": str(outcome),
                        })
                    continue
                if outcome is None:
                    continue  # this one job's pause was interrupted by should_stop; drain the rest of the batch
                score, rationale, method = outcome
                session.add(SeenPosting(profile_id=profile.id, source=job.source, external_id=job.external_id))

                if score < settings.matching.min_fit_score:
                    session.commit()
                    if on_progress:
                        on_progress({
                            "event": "considered", "rejected": True,
                            "reason": f"Fit score {score:.0f} is below your minimum of {settings.matching.min_fit_score:.0f}",
                            "title": job.title, "company": job.company, "source": job.source,
                        })
                    continue

                posting = JobPosting(
                    profile_id=profile.id,
                    source=job.source,
                    external_id=job.external_id,
                    company=job.company,
                    title=job.title,
                    location=job.location,
                    remote=job.remote,
                    url=job.url,
                    description=job.description,
                    salary_min=job.salary_min,
                    salary_max=job.salary_max,
                    posted_at=job.posted_at,
                    fit_score=score,
                    fit_rationale=rationale,
                    fit_score_method=method,
                )
                session.add(posting)
                session.commit()
                session.add(
                    ScoreSnapshot(
                        profile_id=profile.id,
                        job_id=posting.id,
                        fit_score=score,
                        fit_rationale=rationale,
                        trigger="initial",
                    )
                )
                session.commit()
                new_count += 1
                if on_progress:
                    on_progress(
                        {
                            "event": "matched",
                            "source": posting.source,
                            "title": posting.title,
                            "company": posting.company,
                            "fit_score": posting.fit_score,
                        }
                    )
                    on_progress({"event": "considered"})

        if should_stop and should_stop():
            stopped = True

        session.commit()
        if on_progress:
            on_progress({"event": "source_done", "source": connector.name})

        if stopped:
            break

    if on_progress:
        on_progress({"event": "stopped" if stopped else "complete", "new_count": new_count})

    return new_count


def rescore_all_jobs(
    session: Session,
    settings: Settings,
    profile: Profile,
    llm: LLMClient,
    on_progress: Optional[ProgressCallback] = None,
    should_stop: Optional[StopCheck] = None,
    resume_event: Optional[threading.Event] = None,
) -> int:
    """Re-scores every saved JobPosting for this profile against the
    current resume/preferences/LLM, in place (fit_score, fit_rationale,
    fit_score_method all overwritten, with a ScoreSnapshot kept for
    history). For after fixing an LLM that had been unreachable (postings
    scored while it was down say so via fit_score_method="rule_based" --
    see matching.score_fit), or just to re-score everything after changing
    preferences without waiting for a fresh search to surface new postings.

    Raises LLMUnavailableError up front, same as run_search_cycle. Pauses
    (via the same _score_with_pause used there) rather than silently
    falling back if the LLM drops mid-run. Returns the number of jobs
    actually rescored (fewer than the total if stopped partway through)."""
    check_llm_available(llm, settings.data_dir)
    prefs = effective_preferences(profile, settings)
    resume_summary = json.loads(profile.resume_summary_json or "{}")
    resume_text = profile.resume_text or ""

    jobs = session.execute(
        select(JobPosting).where(JobPosting.profile_id == profile.id)
    ).scalars().all()

    # RawJobPosting is the shape _score_with_pause/score_fit expect; it's a
    # plain (unhashable -- field-based __eq__ with no __hash__) dataclass,
    # so it can't be a dict key itself. Pairing by id() instead of value
    # lets two jobs with identical title/company/etc. map back to the
    # right JobPosting row rather than colliding.
    pairs = [
        (job, RawJobPosting(
            source=job.source, external_id=job.external_id, company=job.company, title=job.title,
            location=job.location, remote=job.remote, url=job.url, description=job.description,
            salary_min=job.salary_min, salary_max=job.salary_max, posted_at=job.posted_at,
        ))
        for job in jobs
    ]
    raw_to_orm = {id(raw): orm_job for orm_job, raw in pairs}
    raw_jobs = [raw for _, raw in pairs]

    rescored = 0
    for raw_job, outcome in _score_batch_concurrently(
        raw_jobs, resume_summary, resume_text, prefs, llm, settings.data_dir, on_progress,
        should_stop, resume_event, settings.llm.max_concurrent_scoring,
    ):
        job = raw_to_orm[id(raw_job)]
        if isinstance(outcome, LLMScoringGaveUpError):
            # Leaves this job's existing score/rationale untouched -- better
            # than either overwriting it with a guess or getting stuck
            # retrying one posting forever and never reaching the rest.
            logger.warning(str(outcome))
            if on_progress:
                on_progress({
                    "event": "gave_up", "title": job.title, "company": job.company, "message": str(outcome),
                })
            continue
        if outcome is None:
            continue  # this one job's pause was interrupted by should_stop; drain the rest of the batch

        score, rationale, method = outcome
        before = job.fit_score
        job.fit_score, job.fit_rationale, job.fit_score_method = score, rationale, method
        session.add(ScoreSnapshot(
            profile_id=profile.id, job_id=job.id, fit_score=score, fit_rationale=rationale,
            trigger="rescore_all",
            scorer_metadata_json=json.dumps({"before_score": before, "after_score": score, "method": method}),
        ))
        session.commit()
        rescored += 1
        if on_progress:
            on_progress({
                "event": "rescored", "source": job.source, "title": job.title,
                "company": job.company, "fit_score": score,
            })

    stopped = bool(should_stop and should_stop())
    if on_progress:
        on_progress({"event": "stopped" if stopped else "complete", "rescored_count": rescored})

    return rescored
