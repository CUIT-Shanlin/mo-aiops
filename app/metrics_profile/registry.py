from __future__ import annotations

from app.metrics_profile.base import (
    DuplicateProfileError,
    MetricProfile,
    UnknownProfileError,
)

_PROFILES: dict[str, MetricProfile] = {}


def register_profile(profile: MetricProfile) -> None:
    if profile.name in _PROFILES:
        raise DuplicateProfileError(f"duplicate metric profile: {profile.name}")
    _PROFILES[profile.name] = profile


def get_profile(name: str) -> MetricProfile:
    try:
        return _PROFILES[name]
    except KeyError as exc:
        raise UnknownProfileError(f"unknown metric profile: {name}") from exc


def list_profiles() -> list[str]:
    return sorted(_PROFILES)
