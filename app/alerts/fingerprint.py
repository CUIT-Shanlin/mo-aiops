"""Stable alert fingerprint calculation."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def compute_fingerprint(alert_name: str, labels: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint for an alert identity."""
    payload = {
        "alert_name": alert_name,
        "labels": {key: labels[key] for key in sorted(labels)},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

