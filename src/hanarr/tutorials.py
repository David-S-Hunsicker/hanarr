"""Per-profile dismissible tutorials -- "don't show this again" on a single
popup/banner, independent of the global tutorials_enabled switch in
config.yaml (settings.ui). A tutorial is "visible" only when the global
switch is on AND this specific profile hasn't dismissed that tutorial_key.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .config import Settings
from .models import DismissedTutorial

# Every tutorial_key currently in the app. Keeping one explicit set here
# (rather than discovering keys ad hoc) is what makes "reset all" possible
# without a wildcard DELETE matching keys this profile never actually saw.
TUTORIAL_KEYS = ("onboarding_checklist",)


def is_tutorial_visible(session: Session, settings: Settings, profile_id: int, tutorial_key: str) -> bool:
    if not settings.ui.tutorials_enabled:
        return False
    dismissed = (
        session.query(DismissedTutorial)
        .filter_by(profile_id=profile_id, tutorial_key=tutorial_key)
        .first()
    )
    return dismissed is None


def dismiss_tutorial(session: Session, profile_id: int, tutorial_key: str) -> None:
    if tutorial_key not in TUTORIAL_KEYS:
        raise ValueError(f"Unknown tutorial_key {tutorial_key!r}.")
    existing = (
        session.query(DismissedTutorial)
        .filter_by(profile_id=profile_id, tutorial_key=tutorial_key)
        .first()
    )
    if existing is None:
        session.add(DismissedTutorial(profile_id=profile_id, tutorial_key=tutorial_key))
        session.flush()


def reset_dismissed_tutorials(session: Session, profile_id: int) -> None:
    session.query(DismissedTutorial).filter_by(profile_id=profile_id).delete()
    session.flush()
