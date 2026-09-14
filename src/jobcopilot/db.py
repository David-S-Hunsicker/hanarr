"""Engine/session setup and a helper to fetch (or create) the single profile."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Profile


def make_session_factory(settings: Settings) -> sessionmaker:
    db_path = Path(settings.data_dir) / "jobcopilot.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def get_or_create_profile(session: Session, settings: Settings) -> Profile:
    profile = session.execute(select(Profile)).scalars().first()
    if profile is None:
        profile = Profile(name=settings.profile.name)
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile
