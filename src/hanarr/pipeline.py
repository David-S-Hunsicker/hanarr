"""Orchestrates one search cycle: fetch from all enabled connectors,
prefilter, score, and upsert into the DB. This is what `hanarr search`
and the scheduled background job both call.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .connectors import build_enabled_connectors
from .llm.base import LLMClient, NullLLMClient
from .matching import passes_prefilter, score_fit
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
                f"Model {llm.model!r} is not pulled in Ollama — run `ollama pull {llm.model}` "
                f"or update llm.model in config.yaml."
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


def run_search_cycle(
    session: Session,
    settings: Settings,
    profile: Profile,
    llm: LLMClient,
    on_progress: Optional[ProgressCallback] = None,
    should_stop: Optional[StopCheck] = None,
) -> int:
    """Returns the number of new postings that were scored and stored.
    Raises LLMUnavailableError up front if llm.provider is configured but
    not actually reachable/usable, before any connector or scoring work
    happens."""
    check_llm_available(llm, settings.data_dir)
    resume_summary = json.loads(profile.resume_summary_json or "{}")
    resume_text = profile.resume_text or ""
    connectors = build_enabled_connectors(settings.sources)
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

        for job in raw_jobs:
            if should_stop and should_stop():
                stopped = True
                break

            if not passes_prefilter(job, settings.preferences):
                if on_progress:
                    on_progress({"event": "considered"})
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
                    on_progress({"event": "considered"})
                continue  # fetched and scored on a previous search, whether matched or rejected

            if on_progress:
                on_progress({"event": "scoring", "source": job.source, "title": job.title, "company": job.company})

            score, rationale = score_fit(job, resume_summary, resume_text, settings.preferences, llm)
            session.add(SeenPosting(profile_id=profile.id, source=job.source, external_id=job.external_id))

            if score < settings.matching.min_fit_score:
                session.commit()
                if on_progress:
                    on_progress({"event": "considered"})
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

        session.commit()
        if on_progress:
            on_progress({"event": "source_done", "source": connector.name})

        if stopped:
            break

    if on_progress:
        on_progress({"event": "stopped" if stopped else "complete", "new_count": new_count})

    return new_count
