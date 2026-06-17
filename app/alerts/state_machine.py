"""Alert status transition rules."""
from __future__ import annotations


class InvalidAlertTransition(ValueError):
    """Raised when an alert status transition is not allowed."""


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "firing": frozenset({"processing", "acknowledged", "suppressed", "resolved"}),
    "acknowledged": frozenset({"processing", "suppressed", "resolved"}),
    "processing": frozenset({"healing", "resolved", "failed"}),
    "healing": frozenset({"resolved", "failed"}),
}


def transition_alert_status(current: str, target: str) -> str:
    """Validate and return the target alert status."""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidAlertTransition(
            f"invalid alert transition from {current!r} to {target!r}"
        )
    return target
