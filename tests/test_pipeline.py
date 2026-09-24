import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from hanarr.config import Settings
from hanarr.connectors.base import RawJobPosting
from hanarr.llm.base import LLMClient
from hanarr.models import Base, JobPosting, Profile, ScoreSnapshot, SeenPosting
from hanarr.pipeline import run_search_cycle


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _CountingLLM(LLMClient):
    """Returns a fixed score/rationale and counts how many times it's called."""

    def __init__(self, score: float, rationale: str = "test rationale"):
        self.score = score
        self.rationale = rationale
        self.call_count = 0

    def complete_json(self, system: str, user: str) -> str:
        self.call_count += 1
        return json.dumps({"score": self.score, "dealbreaker_hit": False, "rationale": self.rationale})


class _FakeConnector:
    def __init__(self, name: str, postings: list[RawJobPosting]):
        self.name = name
        self._postings = postings

    def fetch(self) -> list[RawJobPosting]:
        return list(self._postings)


def _make_job(external_id="1") -> RawJobPosting:
    return RawJobPosting(
        source="arbeitnow",
        external_id=external_id,
        company="Acme",
        title="Engineer",
        location="Remote",
        remote=True,
        url="http://example.com",
        description="d",
    )


def test_rejected_posting_is_not_rescored_on_a_later_search(monkeypatch):
    session = _make_session()
    profile = Profile(name="Test")
    session.add(profile)
    session.commit()

    settings = Settings()
    settings.matching.min_fit_score = 60

    connector = _FakeConnector("arbeitnow", [_make_job()])
    monkeypatch.setattr("hanarr.pipeline.build_enabled_connectors", lambda sources: [connector])

    llm = _CountingLLM(score=30)  # below threshold, gets rejected

    first_count = run_search_cycle(session, settings, profile, llm)
    assert first_count == 0
    assert llm.call_count == 1
    assert session.query(JobPosting).count() == 0
    assert session.query(SeenPosting).count() == 1

    second_count = run_search_cycle(session, settings, profile, llm)
    assert second_count == 0
    assert llm.call_count == 1, "a previously-rejected posting should not be re-scored"


def test_matched_posting_is_not_readded_or_rescored_on_a_later_search(monkeypatch):
    session = _make_session()
    profile = Profile(name="Test")
    session.add(profile)
    session.commit()

    settings = Settings()
    settings.matching.min_fit_score = 60

    connector = _FakeConnector("arbeitnow", [_make_job()])
    monkeypatch.setattr("hanarr.pipeline.build_enabled_connectors", lambda sources: [connector])

    llm = _CountingLLM(score=85)  # above threshold, gets matched

    first_count = run_search_cycle(session, settings, profile, llm)
    assert first_count == 1
    assert llm.call_count == 1
    assert session.query(JobPosting).count() == 1
    snapshot = session.query(ScoreSnapshot).one()
    assert snapshot.fit_score == 85
    assert snapshot.trigger == "initial"

    second_count = run_search_cycle(session, settings, profile, llm)
    assert second_count == 0
    assert llm.call_count == 1, "an already-matched posting should not be re-scored"
    assert session.query(JobPosting).count() == 1, "should not create a duplicate JobPosting row"


def test_different_postings_are_scored_independently(monkeypatch):
    session = _make_session()
    profile = Profile(name="Test")
    session.add(profile)
    session.commit()

    settings = Settings()
    settings.matching.min_fit_score = 60

    connector = _FakeConnector("arbeitnow", [_make_job("1"), _make_job("2")])
    monkeypatch.setattr("hanarr.pipeline.build_enabled_connectors", lambda sources: [connector])

    llm = _CountingLLM(score=85)

    count = run_search_cycle(session, settings, profile, llm)
    assert count == 2
    assert llm.call_count == 2
    assert session.query(SeenPosting).count() == 2


def test_considered_event_fires_exactly_once_per_posting_across_outcomes(monkeypatch):
    """Covers all four ways a posting can be handled -- prefiltered out,
    already-seen (skipped before scoring), scored-and-rejected, and
    scored-and-matched -- and confirms each fires exactly one "considered"
    event, which is what the dashboard's progress bar denominator relies on."""
    session = _make_session()
    profile = Profile(name="Test")
    session.add(profile)
    session.commit()

    settings = Settings()
    settings.matching.min_fit_score = 60
    settings.preferences.keywords_exclude = ["blocked"]

    prefiltered_job = _make_job("prefiltered")
    prefiltered_job.description = "this posting is blocked by a keyword"

    already_seen_job = _make_job("already-seen")
    session.add(SeenPosting(profile_id=profile.id, source="arbeitnow", external_id="already-seen"))
    session.commit()

    rejected_job = _make_job("rejected")
    matched_job = _make_job("matched")

    connector = _FakeConnector(
        "arbeitnow", [prefiltered_job, already_seen_job, rejected_job, matched_job]
    )
    monkeypatch.setattr("hanarr.pipeline.build_enabled_connectors", lambda sources: [connector])

    scores = {"rejected": 30, "matched": 85}

    class _PerJobLLM(LLMClient):
        def complete_json(self, system: str, user: str) -> str:
            payload = json.loads(user)
            title = payload["job"]["title"]
            # external_id isn't in the payload, but our jobs all share a
            # title/company; use call order against the two real LLM calls
            # expected (rejected, then matched) to pick the right score.
            score = scores["rejected"] if self.calls == 0 else scores["matched"]
            self.calls += 1
            return json.dumps({"score": score, "dealbreaker_hit": False, "rationale": "r"})

        calls = 0

    llm = _PerJobLLM()
    considered_events = []

    def on_progress(event):
        if event["event"] == "considered":
            considered_events.append(event)

    new_count = run_search_cycle(session, settings, profile, llm, on_progress=on_progress)

    assert new_count == 1  # only "matched" cleared the threshold
    assert len(considered_events) == 4, "one considered event per posting, regardless of outcome"
