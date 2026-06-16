from __future__ import annotations

import asyncio
import logging

from app.core.projects import ProjectsConfig, iter_enabled_projects
from app.providers.factory import create_project_providers


logger = logging.getLogger(__name__)


async def _validate_provider(
    provider,
    timeout_seconds: float,
) -> dict[str, bool | str]:
    try:
        return await asyncio.wait_for(
            provider.validate_scopes(), timeout=timeout_seconds
        )
    except Exception as exc:
        return {"connectivity": False, "error": exc.__class__.__name__}
    finally:
        try:
            await provider.close()
        except Exception as exc:  # pragma: no cover - warning path
            logger.warning(
                "provider close failed: project=%s datasource=%s error=%s",
                provider.project_id,
                provider.datasource_type,
                exc.__class__.__name__,
            )


async def validate_configured_providers(
    config: ProjectsConfig,
    timeout_seconds: float = 1.0,
) -> dict[str, dict[str, dict[str, bool | str]]]:
    results: dict[str, dict[str, dict[str, bool | str]]] = {}
    tasks: list[asyncio.Task[dict[str, bool | str]]] = []
    task_targets: list[tuple[str, str]] = []

    for project_id, project in iter_enabled_projects(config):
        results.setdefault(project_id, {})
        try:
            providers = create_project_providers(project_id, project)
        except Exception as exc:
            results[project_id]["__error__"] = {
                "connectivity": False,
                "error": exc.__class__.__name__,
            }
            continue

        if not providers:
            continue

        for datasource_type, provider in providers.items():
            tasks.append(asyncio.create_task(_validate_provider(provider, timeout_seconds)))
            task_targets.append((project_id, datasource_type))

    if tasks:
        provider_results = await asyncio.gather(*tasks)
        for (project_id, datasource_type), provider_result in zip(
            task_targets, provider_results, strict=True
        ):
            results.setdefault(project_id, {})
            results[project_id][datasource_type] = provider_result

    return results
