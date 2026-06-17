"""Notification read/list service."""
from __future__ import annotations

from app.models.notification import Notification
from app.repositories.notifications import NotificationRepository


class NotificationService:
    """Application service for project-scoped notifications."""

    def __init__(self, repository: NotificationRepository) -> None:
        self.repository = repository

    async def list_recent(self, *, limit: int) -> tuple[int, list[Notification]]:
        unread_count = await self.repository.unread_count()
        items = await self.repository.list_recent(limit=limit)
        return unread_count, items

    async def mark_read(self, notification_id: int) -> Notification:
        row = await self.repository.mark_read(notification_id)
        if row is None:
            raise LookupError(notification_id)
        return row
