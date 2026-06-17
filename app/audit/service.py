"""Thin audit service over the audit log repository."""
from __future__ import annotations

from typing import Any

from app.models.audit_log import AuditLog
from app.repositories.audit import AuditLogCreate, AuditLogRepository


class AuditService:
    """Application-facing audit writer."""

    def __init__(self, repository: AuditLogRepository) -> None:
        self.repository = repository

    async def record(
        self,
        *,
        operator_type: str,
        operator_name: str,
        action: str,
        resource_type: str,
        result: str,
        operator_uid: str | None = None,
        resource_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        ip_address: str | None = None,
        reason: str | None = None,
    ) -> AuditLog:
        """Write one audit row through the repository."""
        return await self.repository.create(
            AuditLogCreate(
                operator_type=operator_type,
                operator_name=operator_name,
                action=action,
                resource_type=resource_type,
                result=result,
                operator_uid=operator_uid,
                resource_id=resource_id,
                before_state=before_state,
                after_state=after_state,
                ip_address=ip_address,
                reason=reason,
            )
        )
