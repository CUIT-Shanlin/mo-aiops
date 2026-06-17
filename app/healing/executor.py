"""Healing execution boundary."""
from __future__ import annotations

from typing import Protocol

from app.models.heal_action import HealAction


class HealingExecutor(Protocol):
    """Executes one persisted healing action."""

    async def execute(self, action: HealAction) -> str:
        """Run the action and return a human-readable result message."""


class NoopHealingExecutor:
    """Deterministic executor for development and tests."""

    async def execute(self, action: HealAction) -> str:
        """Return a stable message without touching external systems."""
        return (
            f"noop executed {action.action_type} on "
            f"{action.target_namespace or 'default'}/{action.target_resource}"
        )
