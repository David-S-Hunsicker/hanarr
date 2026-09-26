"""Live search progress, shared between the dashboard's manual "Run search
now" button and the background scheduler's automatic runs.

Before this module existed, only a manual search updated anything visible
on the dashboard -- the scheduled search ran with no on_progress callback
at all, so an automatic background run (which can mean minutes of
sustained LLM/GPU load) happened completely invisibly. A user watching the
dashboard had no way to tell Ollama was actively churning through
postings unless they'd personally clicked the button a moment earlier.
"""
from __future__ import annotations

import time
from typing import Any

MAX_LOG_ENTRIES = 25
# Deliberately larger than MAX_LOG_ENTRIES -- the activity log is a
# scrolling narrative of the run, but the filtered-postings list is a
# reference someone tunes preferences against afterward, so it's worth
# keeping more of it. Still bounded: a huge run with a badly-tuned
# prefilter shouldn't grow this without limit.
MAX_FILTERED_LOG_ENTRIES = 300


def new_search_state() -> dict[str, Any]:
    return {
        "search_running": False,
        "run_id": 0,
        # Who started this run -- "manual" (the dashboard button) or
        # "scheduled" (the background interval timer) -- so the dashboard
        # can make clear when GPU/CPU load wasn't something the user just
        # clicked.
        "trigger": None,
        "sources_done": 0,
        "sources_total": 0,
        "current_source": None,
        "matched_count": 0,
        # considered_total grows as each source's fetch completes (we
        # don't know the grand total upfront -- sources are fetched one at
        # a time); considered_done counts postings actually looked at
        # (skipped or scored, either way) toward that running total.
        "considered_total": 0,
        "considered_done": 0,
        # Postings skipped because they were already scored in a previous
        # run (SeenPosting dedup) -- visible evidence that an interrupted
        # search "resumes" rather than redoing already-scored work when
        # restarted, since the expensive part (the LLM call) isn't
        # repeated for these.
        "already_seen_count": 0,
        # Real LLM calls made this run -- the actual count of times Ollama
        # (or another provider) was asked to score something, as distinct
        # from "considered" (which also includes free prefilter rejects
        # and dedup skips that never touch the LLM at all).
        "scoring_count": 0,
        "log": [],
        # Ephemeral, not persisted -- only reflects the most recent search
        # run on this server process. Feeds the "why was this filtered
        # out" debug view.
        "filtered_log": [],
        "last_search_result": None,
    }


def reset_for_run(state: dict[str, Any], run_id: int, trigger: str) -> None:
    state["search_running"] = True
    state["run_id"] = run_id
    state["trigger"] = trigger
    state["sources_done"] = 0
    state["sources_total"] = 0
    state["current_source"] = None
    state["matched_count"] = 0
    state["considered_total"] = 0
    state["considered_done"] = 0
    state["already_seen_count"] = 0
    state["scoring_count"] = 0
    state["log"] = []
    state["filtered_log"] = []


def log_event(state: dict[str, Any], entry: dict[str, Any]) -> None:
    entry["at"] = time.time()
    state["log"].append(entry)
    if len(state["log"]) > MAX_LOG_ENTRIES:
        state["log"] = state["log"][-MAX_LOG_ENTRIES:]


def on_progress(state: dict[str, Any], event: dict[str, Any]) -> None:
    kind = event["event"]
    if kind == "considered" and event.get("rejected"):
        state["filtered_log"].append({
            "title": event.get("title", ""),
            "company": event.get("company", ""),
            "source": event.get("source", ""),
            "reason": event.get("reason", ""),
        })
        if len(state["filtered_log"]) > MAX_FILTERED_LOG_ENTRIES:
            state["filtered_log"] = state["filtered_log"][-MAX_FILTERED_LOG_ENTRIES:]
    if kind == "source_start":
        state["current_source"] = event["source"]
        log_event(state, {"kind": "source_start", "text": f"Searching {event['source']}…"})
    elif kind == "source_fetched":
        state["considered_total"] += event["count"]
        log_event(state, {"kind": "info", "text": f"{event['source']}: {event['count']} posting(s) fetched"})
    elif kind == "source_error":
        log_event(state, {"kind": "error", "text": f"{event['source']}: fetch failed, skipping"})
    elif kind == "scoring":
        state["scoring_count"] += 1
        log_event(state, {"kind": "scoring", "text": f"Scoring: {event['title']} at {event['company']}"})
    elif kind == "considered":
        state["considered_done"] += 1
        if event.get("already_seen"):
            state["already_seen_count"] += 1
    elif kind == "matched":
        state["matched_count"] += 1
        score = event["fit_score"]
        log_event(state, {"kind": "matched", "text": f"Matched ({score:.0f}): {event['title']} at {event['company']}"})
    elif kind == "source_done":
        state["sources_done"] += 1
        state["current_source"] = None
    elif kind == "complete":
        log_event(state, {"kind": "done", "text": f"Search complete — {event['new_count']} new posting(s)."})
    elif kind == "stopped":
        log_event(state, {"kind": "error", "text": f"Search stopped — {event['new_count']} new posting(s) kept."})
