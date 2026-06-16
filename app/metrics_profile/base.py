from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class MetricProfileError(Exception):
    pass


class UnknownProfileError(MetricProfileError):
    pass


class UnknownMetricError(MetricProfileError):
    pass


class DuplicateProfileError(MetricProfileError):
    pass


@dataclass(frozen=True, slots=True)
class MetricProfile:
    name: str
    mappings: dict[str, str]

    def resolve(self, canonical_name: str) -> str:
        try:
            return self.mappings[canonical_name]
        except KeyError as exc:
            raise UnknownMetricError(
                f"unknown canonical metric: {canonical_name}"
            ) from exc

    def resolve_many(self, names: Iterable[str]) -> dict[str, str]:
        return {name: self.resolve(name) for name in names}

    def list_metrics(self) -> list[str]:
        return list(self.mappings)
