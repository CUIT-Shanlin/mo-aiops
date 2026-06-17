import re

import pytest

from app.alerts.fingerprint import compute_fingerprint
from app.alerts.state_machine import (
    InvalidAlertTransition,
    transition_alert_status,
)


def test_compute_fingerprint_is_stable_for_label_order():
    labels_a = {"pod": "message-0", "namespace": "prod", "service": "message"}
    labels_b = {"service": "message", "pod": "message-0", "namespace": "prod"}

    first = compute_fingerprint("HighLatency", labels_a)
    second = compute_fingerprint("HighLatency", labels_b)

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_compute_fingerprint_changes_when_identity_changes():
    labels = {"service": "message"}

    assert compute_fingerprint("HighLatency", labels) != compute_fingerprint(
        "HighErrorRate", labels
    )
    assert compute_fingerprint("HighLatency", labels) != compute_fingerprint(
        "HighLatency", {"service": "user"}
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("firing", "processing"),
        ("firing", "acknowledged"),
        ("firing", "suppressed"),
        ("firing", "resolved"),
        ("acknowledged", "processing"),
        ("acknowledged", "suppressed"),
        ("acknowledged", "resolved"),
        ("processing", "healing"),
        ("processing", "resolved"),
        ("processing", "failed"),
        ("healing", "resolved"),
        ("healing", "failed"),
    ],
)
def test_transition_alert_status_allows_expected_edges(current, target):
    assert transition_alert_status(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("resolved", "firing"),
        ("failed", "healing"),
        ("suppressed", "processing"),
        ("healing", "processing"),
        ("processing", "acknowledged"),
        ("acknowledged", "healing"),
        ("firing", "healing"),
    ],
)
def test_transition_alert_status_rejects_invalid_edges(current, target):
    with pytest.raises(InvalidAlertTransition) as exc_info:
        transition_alert_status(current, target)

    assert current in str(exc_info.value)
    assert target in str(exc_info.value)
